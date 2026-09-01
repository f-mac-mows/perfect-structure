# HCX-007 단일 콜로 로컬에 적재된 표 전체 목록에서 claim이 가리키는 표를 고릅니다.
"""[2026-08-21 신규 - Task #80 확장, README "열세 번째" 항목 참고] Stage 1
(표 선택) 대안 경로 - run02(키워드 생성)/run03(KOSIS 라이브 검색)/로컬
FTS(`kosis_local_search.search_local`) 세 겹을 거치는 대신, 로컬에 이미
적재된 표 전체 목록과 claim을 HCX-007 한 콜에 담아 직접 표를 고르게
한다.

## 왜 이 경로가 필요한가

1. run02/run03도 이제 사용자 담당으로 스코프가 바뀌었다(CLAUDE.md
   "담당 범위(scope) 정정" 참고) - run03가 의존하는 KOSIS 라이브
   통합검색 자체가 이미 실측으로 불안정하다고 확인돼 있다(README 2.1).
2. 실측 사례(A93bfa851-C018): run03가 패러프레이즈 10개를 전부 시도해도
   0건이었는데, 정답 표는 로컬 DB에 이미 있었다 - 라이브 검색을 거칠
   필요가 원래 없었던 케이스다.
3. 로컬 FTS 폴백(원문장 직접 토큰화)도 별도 버그(순수 숫자 토큰이
   무관한 표의 축 코드와 우연히 걸리는 문제, 같은 README 항목에서
   수정함)에 취약하다는 게 같은 사례에서 드러났다.
4. Stage 2(항목/축 확정)에서 이미 검증된 "표 하나를 통째로 한 콜에"
   패턴(`hcx_stage2_resolver.py`)을 표 선택(Stage 1)에도 그대로
   확장하는 것 - 로컬 표 개수가 아직 작아서(2026-08-21 실측: 19개,
   표 이름+통계명 총 272자) 전체 목록을 한 콜에 넣는 게 충분히
   가능하다(`kosis_local_search.list_registered_tables` 참고).

## 이 모듈이 하는 일

`resolve_table_with_hcx007(table_list, claim_text, ...)` 하나가 로컬
`tables_registry` 전체(`list_registered_tables`가 이미 만들어주는 것)와
claim 원문을 HCX-007 Chat Completions v3 한 콜에 담아, "이 claim이
가리키는 표의 index"를 하나만(또는 확신이 없으면 없음을) 받아온다.
반환된 표는 기존 Stage 2(`resolve_evidence_by_flat_match` + 이미 있는
HCX-007 Stage 2 갭 폴백)로 그대로 넘어간다 - 이 모듈은 표 선택까지만
책임지고, 그 표 안의 항목/축 확정은 건드리지 않는다.

## API 스킴을 새로 추측하지 않는다

`hcx_stage2_resolver.py`와 완전히 같은 이유로, 이미 실측 검증된
`hcx_client.call_hcx`/`extract_hcx_content` 클라이언트와 "단일 index
또는 null" 파싱 관용을 그대로 재사용한다(`parse_resolved_cell_index`를
직접 재사용 - 파싱 계약이 표/셀 어느 쪽이든 완전히 동일하므로 중복
구현하지 않는다). 프롬프트 문구만 표 선택 작업에 맞게 새로 썼다 - 이
프롬프트에 HCX-007이 실제로 안정적으로 응답하는지는 아직 실측 전이라
`test_hcx_stage1_resolver.py`에서는 결정적 fake call_hcx로 파싱/재시도
로직만 검증한다. 실 API 검증은 다음 세션의 실측 대상으로 남긴다.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from hcx_client import HCXClientError, call_hcx, extract_hcx_content
from hcx_stage2_resolver import ResolvedIndexParseError, parse_resolved_cell_index
from api_usage_logger import extract_hcx_usage, record_api_usage

LOGGER = logging.getLogger("Task2.KosisChatAgent")


def build_hcx007_stage1_resolve_messages(
    table_list: List[Dict[str, Any]],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """table_list(list_registered_tables 형식: org_id/tbl_id/tbl_nm/
    stat_nm 딕셔너리 리스트)와 claim을 HCX-007에 한 번에 보여주고, claim이
    가리키는 표 하나의 index만(또는 확신이 없으면 null을) 받는 messages를
    만든다.

    claimed_value/unit/period는 참고용 문맥 힌트일 뿐 강제하지 않는다 -
    이 함수는 "어떤 표가 이 claim의 개념을 다루는가"만 판단하고, 그 표
    안의 실제 셀 값이 맞는지는 이후 Stage 2/3(값 조회 + 기존
    disambiguate_by_value)가 책임진다."""

    system_prompt = """당신은 한국 공식통계(KOSIS)에 정통한 전문가입니다.
아래 candidates는 로컬 데이터베이스에 실제로 적재된 통계표 목록입니다 - 표 이름, 소속 통계명, 그리고 표 내부 분류축 이름과 그 최상위 분류값 샘플(axis_hints)이 같이 주어집니다.
사용자가 제시한 claim이 이 표들 중 어떤 표를 가리키는지 판단하세요.

반드시 지킬 것:
- claim이 다루는 구체적인 개념(품목/분류 등)이 axis_hints의 실제 분류값과 일치하는 표를 우선하세요 - 표 이름만으로는 구분이 안 되는 경우가 있습니다(예: "지출목적별"과 "품목성질별" 소비자물가지수는 이름만 비슷할 뿐 분류 기준과 포함된 세부 항목이 다릅니다). axis_hints에 claim의 핵심 개념이 실제로 등장하는 표를 우선하세요.
- 표 이름이 비슷해 보여도 분류 기준이 다르면(예: 지출목적별 vs 품목성질별) claim이 실제로 요구하는 분류 기준에 맞는 쪽을 고르세요.
- claim이 원자료 값을 요구하는데 후보 표가 등락률/비율처럼 파생 지표 전용 표라면(또는 그 반대라면), claim의 요구와 어긋나지 않는 표를 우선하세요.
- axis_hints는 각 축의 최상위 값 일부만 보여주는 샘플입니다(전체 목록이 아님) - 여기 없다고 해서 그 표에 해당 개념이 전혀 없다고 단정하지 말고, 다른 후보들과 비교해 가장 그럴듯한 표를 고르세요.
- 축값 샘플(최상위 값)이 여러 후보 표에서 겹쳐 보여도, "세부 항목 예시"(그 축의 실제 리프 이름 샘플)가 다르면 두 표의 세분화 수준이 다른 것입니다 - 세부 항목 예시가 "쌀", "사과"처럼 구체적인 개별 품목이면 그 표는 개별 품목 단위이고, "곡물및식량작물"처럼 여러 품목을 묶은 이름이면 그 표는 분류군 단위입니다. claim이 언급하는 개념의 구체성 수준과 실제로 맞는 세부 항목 예시를 가진 표를 고르세요(트리 깊이나 코드 형식 같은 간접 지표에 기대지 말고, 세부 항목 예시에 실제로 나열된 이름을 직접 보고 판단하세요).
- 후보 중 어느 것도 claim의 개념과 맞지 않거나 확신할 수 없으면 추측해서 아무거나 고르지 말고 null을 반환하세요.
- 설명하지 마세요. Markdown을 사용하지 마세요. 번호를 붙이지 마세요. index 정수 하나 또는 null만 반환하세요.
정상 예: 3
정상 예: null
잘못된 예: 정답은 3번입니다. 또는 ```json\n3\n```"""

    def _format_table(t: Dict[str, Any]) -> str:
        line = t.get("tbl_nm") or ""
        if t.get("stat_nm"):
            line += f" ({t['stat_nm']})"
        for hint in t.get("axis_hints") or []:
            values = ", ".join(hint.get("values") or [])
            if values:
                line += f" | {hint.get('axis_label') or '분류'}: {values}"
                # [2026-08-22 신규 - Task #1, max_depth 실측 반증 후
                # 교체] 트리 깊이 같은 간접 수치 대신, 실제 리프 이름
                # 샘플을 그대로 보여줘서 "개별 품목이냐 분류군이냐"
                # 판단 자체를 HCX-007에게 맡긴다 - kosis_local_search.
                # _axis_leaf_samples 참고.
                leaf_samples = hint.get("leaf_samples")
                if leaf_samples:
                    line += f" (세부 항목 예시: {', '.join(leaf_samples)})"
        return line

    indexed_candidates = "\n".join(
        f"{i}: {_format_table(t)}" for i, t in enumerate(table_list)
    )
    context_lines = [f"claim: {claim_text}"]
    if claimed_value is not None:
        context_lines.append(
            f"claim이 언급하는 값(참고용): {claimed_value}{claimed_unit or ''}"
        )
    if claimed_period:
        context_lines.append(f"claim이 언급하는 시점(참고용): {claimed_period}")
    user_prompt = "\n".join(context_lines) + f"\n\ncandidates:\n{indexed_candidates}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_hcx007_stage1_retry_messages(
    table_list: List[Dict[str, Any]],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """HCX-007 응답 형식이 올바르지 않을 때 1회 재시도에 쓸 messages."""

    messages = build_hcx007_stage1_resolve_messages(
        table_list, claim_text, claimed_value, claimed_unit, claimed_period,
    )
    retry_instruction = """
직전 응답은 형식이 올바르지 않았습니다. 같은 판단을 다시 수행하세요.
설명하지 마세요. Markdown을 사용하지 마세요.
반드시 정수 하나 또는 null만 반환하세요. candidates에 실제 존재하는 index만 반환하세요.
정상 예: 3
정상 예: null"""
    return [
        {"role": "system", "content": messages[0]["content"] + retry_instruction},
        messages[1],
    ]


def resolve_table_with_hcx007(
    table_list: List[Dict[str, Any]],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
    timeout: int = 30,
    call_hcx_fn: Callable[..., Dict[str, Any]] = call_hcx,
) -> Optional[int]:
    """table_list(로컬에 적재된 표 전체)와 claim을 HCX-007 한 콜에 담아
    claim이 가리키는 표의 index를 반환한다. 후보가 없으면(table_list가
    비어 있으면) 호출 자체를 생략하고 None을 반환한다 -
    resolve_cell_with_hcx007과 같은 계약.

    call_hcx_fn: [테스트용] 기본값은 진짜 hcx_client.call_hcx - 회귀
    테스트는 결정적 fake로 이 인자를 바꿔치기해서 네트워크 없이 파싱/
    재시도 로직만 검증한다."""

    if not table_list:
        return None

    messages = build_hcx007_stage1_resolve_messages(
        table_list, claim_text, claimed_value, claimed_unit, claimed_period,
    )

    def _call(msgs):
        t0 = time.perf_counter()
        # [2026-08-26 신규 - 실측 발견] temperature를 안 넘기면 hcx_client.
        # call_hcx 기본값(thinking_effort 사용 시 0.5)이 적용돼 이 호출이
        # 비결정적이었다 - 실측(probe_fruit_stage1_diagnosis.py)으로 같은
        # claim("토마토 가격")을 3번 호출해 서로 다른 표 3개가 나오는 걸
        # 확인함(A2e46e4ac-C022/C023/C024 조사 계기). 판정 시스템은 같은
        # 입력에 같은 결과를 내야 하므로(Task #29 item_diff에서 이미 같은
        # 이유로 temperature=0.0을 쓴 전례 - README "스물여덟 번째"), 이
        # 표 선택 콜에도 명시적으로 0.0을 고정한다.
        response_json = call_hcx_fn(
            "HCX-007", msgs, timeout=timeout, thinking_effort="low", temperature=0.0
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        input_tokens, output_tokens, total_tokens = extract_hcx_usage(response_json)
        record_api_usage(
            "HCX-007", input_tokens, output_tokens, total_tokens, latency_ms=latency_ms,
        )
        return response_json

    # [설계 - hcx_stage2_resolver.resolve_cell_with_hcx007과 동일한 원칙]
    # 재시도까지 전부 실패하면 여기서 조용히 None을 반환하지 않고 예외를
    # 그대로 올려 보낸다 - "HCX가 확신 없다고 답함(None)"과 "HCX 호출/
    # 파싱 자체가 실패해서 아무 판단도 못 함"은 다른 사유라 뭉개면 안
    # 된다. 호출부가 try/except로 이 claim 하나의 실패를 격리한다.
    try:
        response_json = _call(messages)
        return parse_resolved_cell_index(
            extract_hcx_content(response_json), len(table_list)
        )
    except ResolvedIndexParseError as first_error:
        LOGGER.warning(
            "[Stage 1 HCX-007 표 선택 - 응답 형식 오류, 1회 재시도] %s", first_error
        )
        retry_messages = build_hcx007_stage1_retry_messages(
            table_list, claim_text, claimed_value, claimed_unit, claimed_period,
        )
        retry_response_json = _call(retry_messages)
        return parse_resolved_cell_index(
            extract_hcx_content(retry_response_json), len(table_list)
        )

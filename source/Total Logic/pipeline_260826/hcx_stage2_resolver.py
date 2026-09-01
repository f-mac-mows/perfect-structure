# HCX-007 단일 콜로 표 전체 셀 목록에서 claim이 가리키는 셀을 고릅니다.
"""[2026-08-21 신규 - Task #80 전환] 셀 단위 임베딩 폴백(vdb_discovery.
embedding_expand_phrases)을 대체하는 새 Stage 2 갭 폴백 - "표 전체를
한 번에 밀어 넣고 HCX-007이 직접 고르게 하자"는 방향으로 전환한 결과다
(README.md "열 번째" 항목 참고).

## 왜 임베딩(셀당 1콜)에서 이 방식(표당 1콜)으로 바꿨나

1. QPM 병목: 이번 세션 내내 CLOVA Embedding v2가 429를 반복하고(15분~93분
   대기 관측), 셀 개수가 큰 표(최대 11,032개 실측)는 `max_cells` 안전
   장치로 아예 폴백 자체를 포기해야 했다. 셀마다 콜을 쓰는 구조가
   근본 원인이라, 표 하나당 1콜로 줄이면 이 병목이 구조적으로 준다.
2. top-k truncation 문제: 임베딩 방식은 유사도 top_k=5로 후보를 미리
   잘라내는데, 실측(probe_national_debt_full_pipeline.py, "나랏빚이
   눈덩이처럼 불어났다")에서 정답 셀이 그 top_k 밖으로 밀려나 값 기반
   재검증조차 손을 못 대는 경우가 있었다. 표 전체를 한 번에 보여주면
   이 truncation 자체가 없어진다.

## 이 모듈이 하는 일

`resolve_cell_with_hcx007(cell_texts, claim_text, ...)` 하나가 표 하나의
distinct 셀 텍스트 전체(`kosis_local_search.iter_table_cell_texts`가 이미
만들어주는 것)와 claim 원문을 HCX-007 Chat Completions v3 한 콜에 담아,
"이 claim이 가리키는 셀의 index"를 하나만(또는 확신이 없으면 없음을)
받아온다.

## API 스킴을 새로 추측하지 않는다

이 프로젝트에서 HCX-007 Chat Completions v3를 실제로 호출/파싱해본 코드가
이미 있다(`hcx_client.py`의 `call_hcx`/`extract_hcx_content`,
`hcx_keyword_expander.py`의 index 배열 파싱 + 파싱 실패 시 1회 재시도
패턴 - 다른 파트인 키워드 생성/필터링 단계에서 실측 검증됨). 이 모듈은
그 검증된 클라이언트/파싱 패턴을 그대로 재사용한다 - 응답
스킴(`result.message.content`)을 새로 짐작하지 않는다. 프롬프트 문구와
"단일 index 또는 null"이라는 반환 형식만 이 작업(항목/축 확정)에 맞게
새로 설계했다 - 이 부분(HCX-007이 실제로 이 프롬프트에 이 형식으로
안정적으로 응답하는지)은 아직 실측 전이라 `test_hcx_stage2_resolver.py`
에서는 결정적 fake call_hcx로 파싱/재시도 로직만 검증하고,
`probe_national_debt_full_pipeline_hcx.py`(로컬 실행용)에서 실 API로
검증한다.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from hcx_client import HCXClientError, call_hcx, extract_hcx_content
from api_usage_logger import extract_hcx_usage, record_api_usage

LOGGER = logging.getLogger("Task2.KosisChatAgent")


class ResolvedIndexParseError(ValueError):
    """HCX-007 응답이 유효한 '단일 index 또는 null' 형식이 아닐 때 발생한다."""


def build_hcx007_stage2_resolve_messages(
    cell_texts: List[str],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """표 하나의 실제 셀 목록 전체와 claim을 HCX-007에 한 번에 보여주고,
    claim이 가리키는 셀 하나의 index만(또는 확신이 없으면 null을) 받는
    messages를 만든다.

    claimed_value/unit/period는 참고용으로만 넘긴다 - HCX-007에는 각
    셀의 실제 facts 값이 안 주어지므로(이 함수는 항목/축 breadcrumb
    텍스트만 보고 의미로 판단하는 단계다), "이 정도 규모/시점일 것"이라는
    문맥 힌트 이상으로 강제하지 않는다. 값으로 최종 검증하고 싶으면
    호출부(local_db_agent.resolve_claim_evidence)가 이미 가진
    kosis_local_search.disambiguate_by_value를 이 함수의 결과 위에
    추가로 적용하면 된다 - 이 함수 자체는 "값이 맞는지"가 아니라 "어떤
    셀을 말하는 것 같은지"만 판단한다."""

    system_prompt = """당신은 한국 공식통계(KOSIS) 표의 항목·분류축 구조를 정확히 해석하는 전문가입니다.
아래 candidates는 한 통계표에 실제로 존재하는 데이터 셀(항목명 + 분류축 전체 경로를 이어붙인 텍스트) 목록입니다.
사용자가 제시한 claim이 이 표의 어떤 셀 하나를 가리키는지 판단하세요.

반드시 지킬 것:
- claim이 명확히 하나의 셀을 가리키면 그 index만 정수로 반환하세요.
- claim이 특정 축 값(비율/GDP 대비/부분 항목 등)이 아니라 가장 포괄적인/원자료값을 가리키면, 그런 하위·파생 축이 아니라 상위(가장 일반적인) 셀을 선택하세요. 예: "GDP 대비"·"비율"·"증감률"처럼 원자료가 아닌 파생 수치를 claim이 요구하지 않는 한, 그런 축은 고르지 마세요.
- "중앙정부"·"지방정부"처럼 전체 중 일부만 가리키는 축과, 전체를 가리키는 축을 명확히 구분하세요. claim이 전체를 말하면 부분 집합 축을 고르면 안 됩니다.
- 후보 중 어느 것이 맞는지 확신할 수 없거나, 후보들이 claim이 원하는 개념과 근본적으로 다르면 추측해서 아무거나 고르지 말고 null을 반환하세요.
- 설명하지 마세요. Markdown을 사용하지 마세요. 번호를 붙이지 마세요. index 정수 하나 또는 null만 반환하세요.
정상 예: 3
정상 예: null
잘못된 예: 정답은 3번입니다. 또는 ```json\n3\n```"""

    indexed_candidates = "\n".join(
        f"{index}: {text}" for index, text in enumerate(cell_texts)
    )
    context_lines = [f"claim: {claim_text}"]
    if claimed_value is not None:
        context_lines.append(
            f"claim이 언급하는 값(참고용 - 각 셀의 실제 값은 여기 주어지지 않음): "
            f"{claimed_value}{claimed_unit or ''}"
        )
    if claimed_period:
        context_lines.append(f"claim이 언급하는 시점(참고용): {claimed_period}")
    user_prompt = "\n".join(context_lines) + f"\n\ncandidates:\n{indexed_candidates}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_hcx007_stage2_retry_messages(
    cell_texts: List[str],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """HCX-007 응답 형식이 올바르지 않을 때 1회 재시도에 쓸 messages."""

    messages = build_hcx007_stage2_resolve_messages(
        cell_texts, claim_text, claimed_value, claimed_unit, claimed_period,
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


def parse_resolved_cell_index(content: str, candidate_count: int) -> Optional[int]:
    """HCX-007 응답에서 '단일 index 또는 null'을 뽑는다 - hcx_keyword_
    expander.parse_candidate_indices와 같은 관용(직접 JSON 파싱 우선,
    실패하면 응답 문자열에서 첫 정수/null 토큰을 정규식으로 회수)을
    따르되, 배열이 아니라 값 하나만 기대한다."""

    # [2026-08-21 실측 진단성 개선] 원래 모든 ResolvedIndexParseError
    # 메시지가 "무엇이 문제인지"만 말하고 "HCX가 실제로 뭐라고 답했는지"는
    # 안 담고 있었다 - 호출부(hcx_stage1/2_resolver의 LOGGER.warning)가
    # 이 예외를 그대로 로그로 찍는데, 실제 원본 응답이 없으면 왜 파싱이
    # 실패했는지(예: 빈 문자열/거절 문구/마크다운 등) 재현 없이는 알 수
    # 없다 - 90개 claim 배치 실행 중 "정수/null을 찾지 못했습니다"만
    # 찍히고 원인을 못 보는 문제로 실측 발견(사용자 보고). 원본 응답을
    # 길이 제한(200자)해서 모든 메시지에 포함시킨다 - 다음 실패부터는
    # 로그만 보고 원인을 알 수 있다.
    preview = content[:200] if isinstance(content, str) else repr(content)

    if not isinstance(content, str):
        raise ResolvedIndexParseError(f"HCX 응답이 문자열이 아닙니다: {preview}")

    stripped = content.strip()

    try:
        direct = json.loads(stripped)
        if direct is None:
            return None
        if isinstance(direct, bool):
            raise ResolvedIndexParseError(f"HCX 응답이 정수/null이 아니라 bool입니다: {preview}")
        if isinstance(direct, int):
            if 0 <= direct < candidate_count:
                return direct
            raise ResolvedIndexParseError(
                f"HCX가 candidates 범위 밖의 index({direct})를 반환했습니다: {preview}"
            )
    except json.JSONDecodeError:
        pass

    lowered = stripped.lower()
    if lowered == "null" or lowered == "none":
        return None

    import re

    numbers = re.findall(r"(?<![\d.])-?\d+(?![\d.])", stripped)
    if not numbers:
        raise ResolvedIndexParseError(f"HCX 응답에서 정수/null을 찾지 못했습니다: {preview!r}")
    # [실측 전 - 방어적 선택] 응답에 정수가 여러 개 섞여 있을 수 있으므로
    # (예: 모델이 형식을 어기고 짧은 설명을 덧붙인 경우) candidates 범위
    # 안에 드는 첫 번째 정수를 채택한다 - 범위 밖 숫자는 index가 아닐
    # 가능성이 높다고 보고 건너뛴다.
    for raw in numbers:
        value = int(raw)
        if 0 <= value < candidate_count:
            return value
    raise ResolvedIndexParseError(
        f"HCX 응답의 정수들이 모두 candidates 범위 밖입니다: {numbers} (원본: {preview!r})"
    )


def resolve_cell_with_hcx007(
    cell_texts: List[str],
    claim_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
    timeout: int = 30,
    call_hcx_fn=call_hcx,
) -> Optional[int]:
    """cell_texts(표 하나의 distinct 셀 텍스트 전체)와 claim을 HCX-007
    한 콜에 담아 claim이 가리키는 셀의 index를 반환한다. 후보가 없으면
    (cell_texts가 비어 있으면) 호출 자체를 생략하고 None을 반환한다 -
    embedding_expand_phrases가 빈 cell_texts에 빈 리스트를 돌려주던 것과
    같은 계약.

    call_hcx_fn: [테스트용] 기본값은 진짜 hcx_client.call_hcx - 회귀
    테스트는 결정적 fake로 이 인자를 바꿔치기해서 네트워크 없이 파싱/
    재시도 로직만 검증한다(vdb_discovery의 embed_fn 주입 패턴과 동일한
    이유)."""

    if not cell_texts:
        return None

    messages = build_hcx007_stage2_resolve_messages(
        cell_texts, claim_text, claimed_value, claimed_unit, claimed_period,
    )

    def _call(msgs):
        t0 = time.perf_counter()
        # [2026-08-26 신규 - hcx_stage1_resolver.py와 동일한 실측 발견/이유]
        # temperature 미지정 시 기본값(0.5)이 적용돼 비결정적이었다 - Stage 1
        # 표 선택 콜에서 같은 claim이 호출마다 다른 결과를 내는 게 실측
        # 확인돼(probe_fruit_stage1_diagnosis.py) 이 Stage 2 셀 선택 콜에도
        # 같은 위험이 있다고 보고 동일하게 0.0으로 고정한다.
        response_json = call_hcx_fn(
            "HCX-007", msgs, timeout=timeout, thinking_effort="low", temperature=0.0
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        input_tokens, output_tokens, total_tokens = extract_hcx_usage(response_json)
        record_api_usage(
            "HCX-007", input_tokens, output_tokens, total_tokens, latency_ms=latency_ms,
        )
        return response_json

    # [설계 - vdb_discovery.embedding_expand_phrases와 동일한 원칙] 재시도까지
    # 전부 실패하면(네트워크 오류든 응답 형식 오류든) 여기서 조용히 None을
    # 반환하지 않고 예외를 그대로 올려 보낸다 - "HCX가 확신 없다고 답함
    # (None)"과 "HCX 호출/파싱 자체가 실패해서 아무 판단도 못 함"은 다른
    # 사유라 뭉개면 안 된다(embedding_fallback_error와 같은 이유로
    # hcx_fallback_error를 호출부(local_db_agent.resolve_claim_evidence)가
    # 남길 수 있어야 진단이 가능하다). 이 claim 하나의 실패가 다른 claim
    # 들까지 죽이면 안 된다는 요구사항은 이 함수가 아니라 호출부의
    # try/except가 맡는다(임베딩 폴백에서도 같은 책임 분리).
    try:
        response_json = _call(messages)
        return parse_resolved_cell_index(
            extract_hcx_content(response_json), len(cell_texts)
        )
    except ResolvedIndexParseError as first_error:
        LOGGER.warning(
            "[Stage 2 HCX-007 폴백 - 응답 형식 오류, 1회 재시도] %s", first_error
        )
        retry_messages = build_hcx007_stage2_retry_messages(
            cell_texts, claim_text, claimed_value, claimed_unit, claimed_period,
        )
        retry_response_json = _call(retry_messages)
        return parse_resolved_cell_index(
            extract_hcx_content(retry_response_json), len(cell_texts)
        )

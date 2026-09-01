# HCX-007 단일 콜로 "압축된 축 트리"에서 claim이 가리키는 축 코드 조합을 고릅니다.
"""[2026-08-22 신규 - 사용자 설계, HCX 토큰 폭발(429) 대응] hcx_stage2_
resolver.resolve_cell_with_hcx007은 표 전체를 "카테시안 곱 flat text
목록"으로 펼쳐서 보내는데, 축이 여러 개인 큰 표(예: DT_1J22001 - 지역
19개 × 지출목적별 581개)에서 이게 673,343자까지 폭발해 HCX 분당 토큰
한도(60,000)를 요청 한 번으로 다 써버리는 게 실측 확인됐다
(probe_bread_ricecake_hcx_tiebreak.py, x-ratelimit-remaining-tokens=0).

## 이 모듈이 다른 점

카테시안 곱을 펼치지 않고, `kosis_local_search.build_axis_trees`가 만든
"축 자체의" 압축 트리(각 노드 이름이 한 번만 등장, 조상 반복 없음)를
축(axis_position)별로 그대로 HCX에 보여준다. claim이 가리키는 각 축의
코드를 axis_position -> code 매핑(JSON object)으로 돌려받는다 - 단일
index가 아니라 "축마다 코드 하나"라는 점이 hcx_stage2_resolver와 다르다.

claim이 언급 안 한 축(예: 지역)은 이 함수가 강제로 정하지 않는다 - HCX가
확신 없는 축은 그냥 응답에서 빼면 되고, 호출부(local_db_agent.py)가
기존에 이미 쓰던 합계/전체 기본값 로직(kosis_local_search._axis_total_
code)으로 채운다. 이러면 "카테시안 곱은 없애되, 축 자체는 안 잘라서
동점 후보 밖의 정답도 여전히 찾을 수 있어야 한다"(README "스물한 번째"
항목, "정부 빚" 실측 버그)는 기존 요구사항이 그대로 유지된다.

## API 스킴을 새로 추측하지 않는다

hcx_stage2_resolver.py와 완전히 같은 이유로, 이미 실측 검증된 hcx_client.
call_hcx/extract_hcx_content를 그대로 재사용한다 - 프롬프트 문구와 "축별
코드 매핑 JSON 또는 null"이라는 반환 형식만 이 작업에 맞게 새로 설계했다.
이 형식으로 HCX-007이 실제로 안정적으로 응답하는지는 실측 전이라
test_hcx_tree_resolver.py는 결정적 fake call_hcx로 파싱/재시도 로직만
검증하고, 실 API 검증은 로컬 probe로 한다."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from hcx_client import call_hcx, extract_hcx_content
from api_usage_logger import extract_hcx_usage, record_api_usage

LOGGER = logging.getLogger("Task2.KosisChatAgent")


class ResolvedAxisCodesParseError(ValueError):
    """HCX-007 응답이 유효한 '축별 코드 매핑 JSON 또는 null' 형식이 아닐 때 발생한다."""


def build_hcx007_axis_resolve_messages(
    axis_trees: Dict[int, Dict[str, Any]],
    claim_text: str,
    item_context: Optional[str] = None,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """axis_trees(kosis_local_search.build_axis_trees의 반환값)와 claim을
    HCX-007에 한 번에 보여주고, claim이 가리키는 축별 코드 매핑(axis
    position -> code)만(또는 확신이 없으면 null을) 받는 messages를
    만든다."""

    system_prompt = """당신은 한국 공식통계(KOSIS) 표의 분류축 구조를 정확히 해석하는 전문가입니다.
아래 axis_trees는 한 통계표의 분류축(axis)마다, 그 축에 실제 존재하는 코드 트리(들여쓰기로 부모-자식 표현, "이름 [코드]" 형식)입니다.
사용자가 제시한 claim이 각 축에서 어떤 코드를 가리키는지 판단하세요.

반드시 지킬 것:
- claim이 특정 축의 값을 명확히 가리키면 그 축의 code를 반환하세요.
- claim이 그 축을 전혀 언급하지 않았고 무엇을 골라야 할지 확신이 없으면, 그 축은 응답 JSON에서 아예 빼세요(호출부가 별도로 기본값을 처리합니다) - 임의로 아무 code나 추측해서 채우지 마세요.
- claim이 특정 축 값(비율/GDP 대비/부분 항목 등)이 아니라 가장 포괄적인/원자료값을 가리키면, 그런 하위·파생 축이 아니라 상위(가장 일반적인) code를 선택하세요.
- "중앙정부"·"지방정부"처럼 전체 중 일부만 가리키는 code와, 전체를 가리키는 code를 명확히 구분하세요. claim이 전체를 말하면 부분 집합 code를 고르면 안 됩니다.
- 모든 축에 대해 확신이 없거나, 트리에 있는 code들이 claim이 원하는 개념과 근본적으로 다르면 추측하지 말고 null을 반환하세요.
- 설명하지 마세요. Markdown을 사용하지 마세요. 축 position을 key로, code를 value로 하는 JSON object 하나 또는 null만 반환하세요.
정상 예: {"1": "T10", "2": "A01116"}
정상 예: {"2": "A011"}
정상 예: null
잘못된 예: 정답은 A01116입니다. 또는 ```json\n{"2": "A01116"}\n```"""

    tree_lines = []
    for axis_position in sorted(axis_trees.keys()):
        tree = axis_trees[axis_position]
        label = tree.get("axis_label") or f"axis {axis_position}"
        tree_lines.append(f"[axis {axis_position} - {label}]\n{tree.get('tree_text', '')}")
    axis_trees_text = "\n\n".join(tree_lines)

    context_lines = [f"claim: {claim_text}"]
    if item_context:
        context_lines.append(f"이 표의 항목(참고용): {item_context}")
    if claimed_value is not None:
        context_lines.append(
            f"claim이 언급하는 값(참고용 - 각 code의 실제 값은 여기 주어지지 않음): "
            f"{claimed_value}{claimed_unit or ''}"
        )
    if claimed_period:
        context_lines.append(f"claim이 언급하는 시점(참고용): {claimed_period}")
    user_prompt = "\n".join(context_lines) + f"\n\naxis_trees:\n{axis_trees_text}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_hcx007_axis_retry_messages(
    axis_trees: Dict[int, Dict[str, Any]],
    claim_text: str,
    item_context: Optional[str] = None,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> List[Dict[str, str]]:
    """HCX-007 응답 형식이 올바르지 않을 때 1회 재시도에 쓸 messages."""

    messages = build_hcx007_axis_resolve_messages(
        axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period,
    )
    retry_instruction = """
직전 응답은 형식이 올바르지 않았습니다. 같은 판단을 다시 수행하세요.
설명하지 마세요. Markdown을 사용하지 마세요.
반드시 axis position을 key로, code를 value로 하는 JSON object 하나 또는 null만 반환하세요.
정상 예: {"2": "A01116"}
정상 예: null"""
    return [
        {"role": "system", "content": messages[0]["content"] + retry_instruction},
        messages[1],
    ]


def parse_resolved_axis_codes(
    content: str, axis_trees: Dict[int, Dict[str, Any]]
) -> Optional[Dict[int, str]]:
    """HCX-007 응답에서 '축별 코드 매핑 JSON 또는 null'을 뽑는다. 각
    code는 그 axis_position의 실제 codes 집합(axis_trees[...]["codes"])에
    있는지 검증한다 - 트리에 없는 code(지어낸 값)를 반환하면 그 axis는
    조용히 버리지 않고 파싱 오류로 취급한다(재시도해야 함). 단, code
    자체가 null/"null"인 axis는 "이 axis는 확신 없음"이라는 뜻이라
    [2026-08-24 신규] 에러 없이 그 axis만 건너뛴다(추측해서 채우지
    않음 - 지어낸 값과 확신 없음은 다른 사유라 구분한다)."""

    preview = content[:200] if isinstance(content, str) else repr(content)

    if not isinstance(content, str):
        raise ResolvedAxisCodesParseError(f"HCX 응답이 문자열이 아닙니다: {preview}")

    stripped = content.strip()

    # 마크다운 코드펜스를 감싸 응답하는 경우가 실측으로 종종 있어(다른
    # 리졸버들과 같은 관례) 벗겨내고 다시 시도한다.
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    # [2026-08-24 신규 - 2026-08-23 90건 배치 max_completion_tokens=2000
    # 실측에서 관측, hcx_stage3_resolver.parse_stage3_response와 동일 이유]
    # 예전엔 stripped 전체가 정확히 "null"/"none"일 때만 인정했는데, 실제로
    # "null\n\n**설명:** ..." 처럼 null 뒤에 설명을 덧붙이는 응답이 나왔다.
    # 이건 HCX 자연어 응답의 형식 관용일 뿐 KOSIS API/DB 스키마 추측이
    # 아니라 "실측 우선" 원칙과 무관하다.
    if not stripped or re.match(r"^(null|none)\b", stripped, re.IGNORECASE):
        return None

    try:
        direct = json.loads(stripped)
    except json.JSONDecodeError as error:
        # [2026-08-24 신규 - 같은 실측에서 관측, Stage 3와 동일 관용을
        # 이식] 유효한 JSON 객체 뒤에 설명이 붙어 파싱이 실패하는 경우가
        # 있었다(예: '{"1": [...]}  \n**오류 수정**: ...') - 응답 안에서
        # 가장 먼저 나오는 {...} 조각만 잘라서 재시도한다. 예전엔 이
        # 관용이 hcx_stage1/2/3_resolver에는 있었는데 이 파일에는 없어서
        # 똑같이 재시도해도 못 건졌다.
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise ResolvedAxisCodesParseError(
                f"HCX 응답을 JSON으로 파싱할 수 없습니다: {preview!r}"
            ) from error
        try:
            direct = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise ResolvedAxisCodesParseError(
                f"HCX 응답을 JSON으로 파싱할 수 없습니다: {preview!r}"
            ) from error

    if direct is None:
        return None
    if not isinstance(direct, dict):
        raise ResolvedAxisCodesParseError(
            f"HCX 응답이 JSON object/null이 아닙니다: {preview!r}"
        )

    result: Dict[int, str] = {}
    for key, code in direct.items():
        try:
            axis_position = int(key)
        except (TypeError, ValueError):
            raise ResolvedAxisCodesParseError(
                f"HCX 응답의 축 key가 정수로 해석되지 않습니다: {key!r} (원본: {preview!r})"
            )
        if axis_position not in axis_trees:
            raise ResolvedAxisCodesParseError(
                f"HCX가 존재하지 않는 axis position({axis_position})을 반환했습니다: {preview!r}"
            )
        # [2026-08-24 신규 - 같은 실측에서 관측] 모델이 "이 축은 확신
        # 없음"을 그 axis를 아예 생략하는 대신 code=null(또는 문자열
        # "null")로 표현하는 경우가 나왔다({"1": null, "2": null}) -
        # 의미상 "이 axis는 못 정함"이므로 조용히 건너뛴다(추측해서
        # 채우지 않음 - 아래에서 result가 비면 전체 None을 반환하는
        # 기존 계약과 일관됨).
        if code is None or (isinstance(code, str) and code.strip().lower() in ("null", "none")):
            continue
        if not isinstance(code, str) or code not in axis_trees[axis_position]["codes"]:
            raise ResolvedAxisCodesParseError(
                f"HCX가 axis {axis_position}에 트리에 없는 code({code!r})를 반환했습니다: {preview!r}"
            )
        result[axis_position] = code

    return result if result else None


def resolve_axis_codes_with_hcx007(
    axis_trees: Dict[int, Dict[str, Any]],
    claim_text: str,
    item_context: Optional[str] = None,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
    timeout: int = 30,
    call_hcx_fn=call_hcx,
) -> Optional[Dict[int, str]]:
    """axis_trees(표 하나의 축별 압축 트리)와 claim을 HCX-007 한 콜에
    담아 claim이 가리키는 축별 코드 매핑(axis_position -> code)을
    반환한다. axis_trees가 비어 있으면 호출 자체를 생략하고 None을
    반환한다.

    call_hcx_fn: [테스트용] 기본값은 진짜 hcx_client.call_hcx - 회귀
    테스트는 결정적 fake로 이 인자를 바꿔치기한다(다른 리졸버들과 같은
    이유)."""

    if not axis_trees:
        return None

    messages = build_hcx007_axis_resolve_messages(
        axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period,
    )

    def _call(msgs):
        t0 = time.perf_counter()
        response_json = call_hcx_fn("HCX-007", msgs, timeout=timeout, thinking_effort="low")
        latency_ms = (time.perf_counter() - t0) * 1000
        input_tokens, output_tokens, total_tokens = extract_hcx_usage(response_json)
        record_api_usage(
            "HCX-007", input_tokens, output_tokens, total_tokens, latency_ms=latency_ms,
        )
        return response_json

    # [설계 - hcx_stage2_resolver.resolve_cell_with_hcx007과 동일한 원칙]
    # 재시도는 "응답 형식 오류"에만 건다 - 네트워크/HTTP 오류(HCXClientError)는
    # 여기서 삼키지 않고 그대로 올려 보낸다(call_hcx 자체가 429 재시도는
    # 이미 처리하므로 이중 재시도가 아니다). 재시도까지 형식 오류면 예외를
    # 그대로 올려서 "확신 없음(None)"과 "파싱 자체가 실패함"을 호출부가
    # 구분할 수 있게 한다.
    try:
        response_json = _call(messages)
        content = extract_hcx_content(response_json)
        return parse_resolved_axis_codes(content, axis_trees)
    except ResolvedAxisCodesParseError as first_error:
        LOGGER.warning(
            f"[축 코드 리졸버 - 응답 형식 오류, 1회 재시도] {first_error}"
        )
        retry_messages = build_hcx007_axis_retry_messages(
            axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period,
        )
        response_json = _call(retry_messages)
        content = extract_hcx_content(response_json)
        return parse_resolved_axis_codes(content, axis_trees)

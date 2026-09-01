# HCX-007 단일 콜로 claim의 비교/파생 모드(mode)와 reference_period를 고릅니다.
"""[2026-08-22 신규 - Task #29 Step 2] Stage 1(표 확정, hcx_stage1_resolver.py)
/ Stage 2(항목·축 확정, hcx_stage2_resolver.py)와 같은 "표/셀 전체를 한 번에
밀어 넣고 HCX-007이 직접 고르게 하자" 패턴을, 이번엔 "이 claim이 비교/파생을
요구하는가, 요구한다면 어떤 두 시점을 비교하는가"에 적용한다.

## 왜 필요한가 - 기존 정규식 휴리스틱과의 경계

지금까지 local_db_agent.py는 `_needs_rate_derivation`/`_claim_number_change_
window`/`_RATE_OF_CHANGE_MARKERS` 같은 정규식/키워드 기반 휴리스틱으로
"이 claim이 등락률 파생을 요구하는가"와 "reference_period가 몇 년 전인가"를
판단해왔다(전년동월비=1년 전 고정, 5년전 비교는 raw_sentence 안의 숫자
윈도우를 파싱). 이 방식은 C018(단서가 window/prefix에 없고 metric 접미사에만
있던 경우, Task #25)처럼 계속 새 반례가 나올 때마다 규칙을 하나씩 늘려야
했다 - 사용자가 이전에 지적한 대로("세부 컬럼 + 메타를 HCX에 넣고 알아서
값을 선택하는 방향") 정규식 대신 HCX가 claim 문장을 직접 읽고 판단하게
바꾼다.

이 모듈은 "어떤 표/항목인가"(Stage 1/2가 이미 확정)는 건드리지 않는다 -
그건 그대로 두고, "그 항목에 대해 값을 어떻게 조합해야 하는가"만 판단한다.

## 반환하는 세 가지 모드(Decision 003 - 실측된 케이스만, 추측 없음)

- "single": target_period 시점의 값 자체(또는 KOSIS가 이미 발표한 등락률
  항목)만 필요 - 비교/파생 없음.
- "period_change": target_period와 reference_period(같은 항목) 두 시점의
  값을 비교(등락률/차이) - kosis_local_search.resolve_period_change가
  담당(전년동월비도 이 모드 안에서 HCX가 reference_period를 직접 계산해
  반환한다 - 별도 "yoy" 모드를 안 둔다, 어차피 resolve_period_change가
  받는 reference_period는 임의의 시점이라 구분할 필요가 없다).
- "item_diff": target_period/reference_period 두 시점에서, "같은 항목의
  시점 차이"가 아니라 "이 항목의 등락률"과 "같은 표의 총계/전체(총지수 등)
  항목의 등락률" 사이의 차이 - kosis_local_search.resolve_item_diff_change가
  담당(Task #27/#29 Step 1, C003/C004류). item B가 총계/전체가 아니라 다른
  구체적 항목이어야 하는 케이스는 아직 실측된 적이 없어 다루지 않는다.

확신이 없으면(위 세 경우 중 어느 것도 아니거나 애매하면) 전체 응답으로
null을 반환하게 하고, 그러면 호출부는 derivation을 시도하지 않는다(추측
안 함 - Stage 2의 "확신 없으면 null" 원칙과 동일).

## API 스킴을 새로 추측하지 않는다

hcx_stage2_resolver.py와 마찬가지로 `hcx_client.call_hcx`/`extract_hcx_
content`(이미 실측 검증된 클라이언트)를 그대로 재사용한다. 프롬프트 문구와
"JSON 객체 하나 또는 null" 응답 형식만 이 작업에 맞게 새로 설계했다 - HCX-007이
실제로 이 프롬프트에 이 형식으로 안정적으로 응답하는지는 아직 실측 전이라
test_hcx_stage3_resolver.py에서는 결정적 fake로 파싱/재시도 로직만 검증한다.
실 API 검증은 로컬 probe 스크립트(추후 필요시 작성)로 한다.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from hcx_client import HCXClientError, call_hcx, extract_hcx_content
from api_usage_logger import extract_hcx_usage, record_api_usage

LOGGER = logging.getLogger("Task2.KosisChatAgent")

_VALID_MODES = ("single", "period_change", "item_diff")


class Stage3ParseError(ValueError):
    """HCX-007 응답이 유효한 '{"mode","reference_period"} 객체 또는 null' 형식이 아닐 때 발생한다."""


def build_hcx007_stage3_resolve_messages(
    claim_text: str,
    target_period: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
) -> List[Dict[str, str]]:
    """claim 문장과 이미 알려진 target_period(Stage 1/2 뒤 claim.period에서
    이미 확정됨 - 이 함수가 새로 추론하지 않는다)를 HCX-007에 보여주고,
    mode(single/period_change/item_diff)와 필요하면 reference_period를
    받는 messages를 만든다."""

    system_prompt = """당신은 한국 뉴스 문장이 KOSIS 공식통계 수치를 어떻게 조회·비교·파생하는지 정확히 해석하는 전문가입니다.
claim은 이미 통계표/항목(축)이 확정된 상태이고, target_period(주어진 시점)의 값을 조회하려 합니다.
claim 문장이 실제로 요구하는 것이 무엇인지 판단해 아래 세 모드 중 하나로 분류하세요.

- "single": claim이 target_period 시점의 값 자체(또는 KOSIS가 이미 공식 발표한 등락률 항목 값)만 필요하고, 다른 시점/다른 항목과의 비교·차이를 별도로 계산할 필요가 없는 경우.
- "period_change": claim이 target_period와 다른 한 시점(reference_period)의 "같은 항목" 값을 비교(등락률 또는 차이)하는 경우. "전년동월비"·"전년대비"처럼 1년 전을 뜻하면 target_period와 같은 달(분기), 1년 전 시점을 reference_period로 직접 계산해서 반환하세요(예: target_period=202509, "전년동월비" -> reference_period=202409). "2020년 9월 대비"처럼 기준 시점이 명시돼 있으면 그 시점을 그대로 reference_period로 반환하세요(예: 202009).
- "item_diff": claim이 "이 항목의 등락률"과 "같은 표의 총계/전체(총지수 등) 항목의 등락률" 사이의 차이(%포인트 등)를 말하는 경우 - 예: "A 물가지수는 OO% 올랐다. 같은 기간 전체 물가지수 상승률(XX%)보다 N%포인트 높다." 이때도 reference_period는 위 period_change와 같은 규칙으로 계산해 반환하세요.

item_diff와 period_change를 헷갈리지 않도록 예시로 구분을 보여드립니다:

예시 1(item_diff) - claim: "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다. 같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트 가까이 높은 수치다." / target_period: 202509
-> {"mode": "item_diff", "reference_period": "202009"}
(이 항목 자신의 등락률(22.9%)과 "전체"의 등락률(16.2%)을 비교하는 두 번째 문장이 있으므로 item_diff)

예시 2(period_change, item_diff 아님) - claim: "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다." / target_period: 202509
-> {"mode": "period_change", "reference_period": "202009"}
(예시 1과 항목은 같지만 "전체/총지수와 비교"하는 두 번째 문장이 없으므로 그냥 period_change - item_diff이려면 반드시 "전체/총지수 대비 몇 %포인트"라는 명시적 비교 문구가 있어야 합니다)

예시 3(single) - claim: "전국 실업률은 3.2%였다." / target_period: 202509
-> {"mode": "single", "reference_period": null}

예시 4(period_change, 전년동월비 자동계산) - claim: "건설업 취업자 수는 전년동월비 5.1% 감소했다." / target_period: 202509
-> {"mode": "period_change", "reference_period": "202409"}

확신이 없거나(어느 모드인지 애매함) 위 세 경우 중 어느 것도 아닌 것 같으면 전체 응답으로 null을 반환하세요 - 추측하지 마세요.

반드시 JSON 객체 하나(키: mode, reference_period) 또는 null만 반환하세요. mode="single"이면 reference_period는 null입니다. 설명하지 마세요. Markdown을 사용하지 마세요.
정상 예: {"mode": "period_change", "reference_period": "202409"}
정상 예: {"mode": "single", "reference_period": null}
정상 예: null
잘못된 예: 이 claim은 전년동월비를 뜻합니다. {"mode": "period_change", "reference_period": "202409"}"""

    context_lines = [f"claim: {claim_text}", f"target_period(이미 확정됨, 참고용): {target_period}"]
    if claimed_value is not None:
        context_lines.append(f"claim이 언급하는 값(참고용): {claimed_value}{claimed_unit or ''}")
    user_prompt = "\n".join(context_lines)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_hcx007_stage3_retry_messages(
    claim_text: str,
    target_period: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
) -> List[Dict[str, str]]:
    """HCX-007 응답 형식이 올바르지 않을 때 1회 재시도에 쓸 messages."""

    messages = build_hcx007_stage3_resolve_messages(claim_text, target_period, claimed_value, claimed_unit)
    retry_instruction = """
직전 응답은 형식이 올바르지 않았습니다. 같은 판단을 다시 수행하세요.
설명하지 마세요. Markdown을 사용하지 마세요.
반드시 JSON 객체({"mode": ..., "reference_period": ...}) 또는 null만 반환하세요.
정상 예: {"mode": "single", "reference_period": null}
정상 예: null"""
    return [
        {"role": "system", "content": messages[0]["content"] + retry_instruction},
        messages[1],
    ]


def parse_stage3_response(content: str) -> Optional[Dict[str, Any]]:
    """HCX-007 응답에서 '{"mode","reference_period"} 객체 또는 null'을
    뽑는다 - hcx_stage2_resolver.parse_resolved_cell_index와 같은 관용
    (직접 JSON 파싱 우선, 실패하면 응답 문자열에서 JSON 객체 조각을 정규식
    으로 회수)을 따르되, 정수 하나가 아니라 mode/reference_period 두 필드를
    기대한다.

    반환: None(확신 없음) 또는 {"mode": "single"|"period_change"|"item_diff",
    "reference_period": str|None}. mode가 유효한 값이 아니거나, mode가
    single이 아닌데 reference_period가 없으면 Stage3ParseError(추측해서
    받아들이지 않음)."""
    preview = content[:200] if isinstance(content, str) else repr(content)

    if not isinstance(content, str):
        raise Stage3ParseError(f"HCX 응답이 문자열이 아닙니다: {preview}")

    stripped = content.strip()

    # [2026-08-24 신규 - 2026-08-23 90건 배치 max_completion_tokens=2000
    # 실측에서 반복 관측] 토큰이 넉넉해져 truncation은 사라졌는데도,
    # "no explanation" 지침을 어기고 "null\n\n**설명:** ..." 식으로 null
    # 뒤에 설명을 덧붙이는 패턴이 계속 나왔다 - 기존 코드는 stripped 전체가
    # 정확히 "null"/"none"일 때만(아래 elif) null로 인정해서, 뒤에 설명이
    # 붙으면 이 조건에 안 걸리고 파싱 실패로 떨어졌다(실측된 실패 메시지:
    # "HCX 응답에서 JSON 객체/null을 찾지 못했습니다: 'null\n\n**설명:**..."').
    # 이건 KOSIS API/DB 스키마에 대한 추측이 아니라 HCX 자연어 응답의 형식
    # 관용을 넓히는 것뿐이라 "실측 우선" 원칙과 무관 - 모델이 말하려는 의도
    # (확신 없음=null)는 명확하므로, 접두사만 보고 흡수한다.
    if re.match(r"^(null|none)\b", stripped, re.IGNORECASE):
        return None

    direct = None
    parsed_ok = False
    try:
        direct = json.loads(stripped)
        parsed_ok = True
    except json.JSONDecodeError:
        # [관용 - Stage 2와 동일] 모델이 형식을 어기고 앞뒤에 설명을 덧붙인
        # 경우, 응답 안에서 가장 먼저 나오는 {...} 조각만 잘라서 재시도한다.
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            try:
                direct = json.loads(match.group(0))
                parsed_ok = True
            except json.JSONDecodeError:
                pass

    if not parsed_ok:
        raise Stage3ParseError(f"HCX 응답에서 JSON 객체/null을 찾지 못했습니다: {preview!r}")

    if direct is None:
        return None
    if not isinstance(direct, dict):
        raise Stage3ParseError(f"HCX 응답이 JSON 객체/null이 아닙니다: {preview}")

    mode = direct.get("mode")
    # [2026-08-24 신규 - 같은 배치 실측에서 관측] 모델이 "확신 없음"을
    # 최상위 null 대신 {"mode": null} 또는 {"mode": "null"}(문자열)로
    # 표현하는 경우가 반복 관측됐다 - 의미상 동일(추측 안 함)하므로 전체
    # None으로 흡수한다. 이것도 위와 같은 이유로 실측 원칙과 무관.
    if mode is None or (isinstance(mode, str) and mode.strip().lower() in ("null", "none")):
        return None
    if mode not in _VALID_MODES:
        raise Stage3ParseError(f"HCX가 알 수 없는 mode를 반환했습니다: {mode!r} (원본: {preview})")

    reference_period = direct.get("reference_period")
    if mode != "single" and not reference_period:
        raise Stage3ParseError(
            f"mode={mode!r}인데 reference_period가 없습니다 - 추측으로 채우지 않음(원본: {preview})"
        )

    return {"mode": mode, "reference_period": reference_period if mode != "single" else None}


def resolve_comparison_mode_with_hcx007(
    claim_text: str,
    target_period: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    timeout: int = 30,
    call_hcx_fn=call_hcx,
) -> Optional[Dict[str, Any]]:
    """claim_text/target_period를 HCX-007 한 콜에 담아 mode/reference_period를
    반환한다. claim_text 또는 target_period가 비어 있으면 호출 자체를
    생략하고 None을 반환한다(hcx_stage2_resolver.resolve_cell_with_hcx007의
    빈 cell_texts 처리와 같은 계약).

    call_hcx_fn: [테스트용] 기본값은 진짜 hcx_client.call_hcx - 회귀
    테스트는 결정적 fake로 이 인자를 바꿔치기한다(Stage 2와 동일 패턴)."""

    if not claim_text or not target_period:
        return None

    messages = build_hcx007_stage3_resolve_messages(claim_text, target_period, claimed_value, claimed_unit)

    def _call(msgs):
        t0 = time.perf_counter()
        # [2026-08-22 신규 - 사용자 제안, 실측 편차 대응] temperature=0.0 -
        # Stage 1/2(표/셀 이름을 유연하게 해석해야 함, 기존 기본값 0.3 유지)
        # 와 달리 Stage 3는 "정답이 3개 중 하나로 고정된 분류 문제"라
        # 창의성이 필요 없다. 90건 합성 평가셋 실측(README "스물다섯 번째")
        # 에서 item_diff 모드가 53% 정확도로 가장 약했고, 실 API 종단
        # 검증에서도 같은 claim에 대해 1차 실행은 실패, 재실행은 성공하는
        # 편차가 실측 확인됐다(README "스물여덟 번째") - temperature를
        # 낮춰 이 편차를 줄일 수 있는지 확인하기 위한 변경.
        response_json = call_hcx_fn("HCX-007", msgs, timeout=timeout, thinking_effort="low", temperature=0.0)
        latency_ms = (time.perf_counter() - t0) * 1000
        input_tokens, output_tokens, total_tokens = extract_hcx_usage(response_json)
        record_api_usage("HCX-007", input_tokens, output_tokens, total_tokens, latency_ms=latency_ms)
        return response_json

    # [설계 - Stage 2와 동일 원칙] 재시도까지 전부 실패하면 조용히 None으로
    # 삼키지 않고 예외를 그대로 올려 보낸다 - "HCX가 확신 없다고 답함(None)"
    # 과 "HCX 호출/파싱 자체가 실패해서 아무 판단도 못 함"은 다른 사유라
    # 뭉개면 안 된다(호출부가 hcx_fallback_error로 구분해서 남길 수 있어야
    # 진단 가능).
    try:
        response_json = _call(messages)
        return parse_stage3_response(extract_hcx_content(response_json))
    except Stage3ParseError as first_error:
        LOGGER.warning("[Stage 3 HCX-007 - 응답 형식 오류, 1회 재시도] %s", first_error)
        retry_messages = build_hcx007_stage3_retry_messages(claim_text, target_period, claimed_value, claimed_unit)
        retry_response_json = _call(retry_messages)
        return parse_stage3_response(extract_hcx_content(retry_response_json))

# HCX-007 단일 콜로, 이미 확정된 표 하나의 "작성 목적"이 claim의 의도와
# 맞는지 검증합니다.
"""[2026-08-28 신규 - 배추가격/DT_114054_112 사례로 사용자가 지적한 아키텍처
갭 대응] Stage 1/2(표 선택, 항목/축 확정)가 이름과 분류축 기준으로는 완전히
맞아떨어져도, 그 표의 실제 작성 목적(조사 대상/범위)이 claim이 암묵적으로
전제하는 의도와 다를 수 있다는 문제를 다룬다.

## 실측으로 확인된 문제 사례 (2026-08-28)

사용자가 KOSIS 실제 URL 2건을 제시하고 Claude in-app Browser로 직접 확인함:

- `DT_114054_112`("식재료 구매 행태_채소류 월평균 구매량 및 구매금액") - 이름만
  보면 "채소류 가격" 표처럼 보이지만, 실제로는 "일반음식점 업종별"/"한식
  세분류별" 외식업 실태조사의 하위 표다. "배추 소매가"를 찾는 claim이 이
  표에 걸리면, 실제로는 "식당이 배추를 사입하는 평균 가격"인데 "소비자가
  배추를 사는 가격"인 것처럼 오인될 수 있다.
- `DT_143002_E002`("소득분석표_노지채소") - "농가수취가격"(농가가 받는
  가격)이 있어 얼핏 가격 데이터처럼 보이지만, 실제로는 농가경제조사(소득/
  생산비 분석)의 일부다. 이것도 소비자 소매가와는 다른 개념이다.

두 표 모두 현재 로컬 웨어하우스에 적재돼 있지 않아 이 실패가 아직 실제로
발생하지는 않지만("나랏빚" 검증 때와 같은 패턴 - 실측 부재 상태를 정직하게
표시), 표/축 이름 매칭만으로는 이런 "목적 불일치"를 구조적으로 걸러낼 수
없다는 게 확인됐다.

## 왜 모든 후보가 아니라 "최종 확정된 표 1개"에만 적용하는가

사용자가 직접 절충안을 제시함(2026-08-28 대화): 모든 Stage 1 후보에 목적
검증을 걸면 이미 자명하게 확정되는 표까지 매번 추가 API 콜(get_stat_explanation
+ HCX-007)이 드는 비용이 든다. 정확도를 최우선으로 하되 비용을 아끼려면,
표/항목이 이미 (org_id, tbl_id)로 확정된 **이후에** 딱 한 번만 그 표의 공식
작성 목적(`client.get_stat_explanation`이 이미 실측 검증돼 있음 - writingPurps/
examinObjrange 등)을 claim과 대조하면 된다 - local_db_agent.resolve_claim_evidence
의 성공 경로 마지막에서 딱 1회 호출.

## 반드시 "실제 게이트"여야 한다

사용자가 명시적으로 요구함: 이 검증은 장식적인 RAG 설명 텍스트를 덧붙이는
용도가 아니라, 목적 불일치가 감지되면 최종 판정 자체를 낮춰야 한다
(judgment.py의 Decision 003 패턴 - derivation_used=True가 항상
UNVERIFIED_DERIVED_NEEDED로 강제하는 것과 동일한 설계). 그래서
judge_claim(judgment.py)이 _check_purpose_mismatch()를 RAW_ONLY 분기
직후, _check_record_claim보다도 먼저 검사한다 - 목적이 안 맞는 표라면
record-claim이나 값 비교 로직까지 갈 이유가 없다.

## API 스킴을 새로 추측하지 않는다

hcx_stage1_resolver.py/hcx_stage2_resolver.py와 완전히 같은 이유로, 이미
실측 검증된 `hcx_client.call_hcx`/`extract_hcx_content` 클라이언트를 그대로
재사용한다. 다만 이 모듈은 index가 아니라 "MATCH/MISMATCH + 이유"를 받아야
하므로 파싱 계약을 새로 설계했다 - JSON 우선 파싱 + 정규식 폴백은 기존
관용(parse_resolved_cell_index)과 동일한 패턴을 따르되, 반환 형태만 다르다.
이 프롬프트에 HCX-007이 실제로 안정적으로 이 JSON 형식을 지키는지는 아직
실측 전이라(신규 기능이므로), `test_hcx_purpose_resolver.py`에서는 결정적
fake call_hcx로 파싱/재시도 로직만 검증한다. 실 API 검증은 사용자가 로컬에서
실행해서 확인해야 한다(CLAUDE.md "실측 우선 원칙"/"샌드박스에서 직접 실행
금지").

temperature=0.0은 처음부터 명시적으로 고정한다 - hcx_stage1_resolver.py를
temperature 미지정으로 먼저 만들었다가 비결정성 버그를 실측으로 발견하고
나서야 고친 전례(2026-08-26)를 반복하지 않기 위함이다.
"""

import json
import logging
import re
import time
from typing import Any, Callable, Dict, Optional

from hcx_client import HCXClientError, call_hcx, extract_hcx_content
from api_usage_logger import extract_hcx_usage, record_api_usage

LOGGER = logging.getLogger("Task2.KosisChatAgent")


class PurposeVerdictParseError(ValueError):
    """HCX-007 응답이 유효한 '{"verdict": "MATCH"|"MISMATCH", "reason": ...}'
    형식이 아닐 때 발생한다."""


def build_hcx007_purpose_verify_messages(
    claim_text: str,
    table_nm: Optional[str],
    table_purpose_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> list:
    """이미 확정된 표 하나의 공식 작성 목적 설명(client.get_stat_explanation
    으로 실측 조회한 writingPurps/examinObjrange 등 원문)과 claim을 HCX-007에
    보여주고, 이 표가 claim의 실제 의도(예: "소비자 소매가"인지 "농가 수취가"/
    "특정 업종 사입가"인지)와 맞는지 판단하게 한다.

    claimed_value/unit/period는 참고용 문맥일 뿐이다 - 이 함수는 값이 맞는지가
    아니라 "이 표가 애초에 claim이 말하는 개념을 다루는 조사인가"만 판단한다."""

    system_prompt = """당신은 한국 공식통계(KOSIS)에 정통한 전문가입니다.
아래에는 어떤 claim(기사에서 뽑은 주장 문장)과, 이미 이름/분류축 매칭으로 확정된 통계표 하나의 "공식 작성 목적/조사 대상 범위" 설명(KOSIS가 직접 제공하는 메타데이터 원문)이 주어집니다.
이 표가 실제로 claim이 말하는 개념을 다루는 조사가 맞는지 판단하세요.

반드시 지킬 것:
- 표 이름이나 분류축 이름이 claim의 단어와 겹쳐도, 조사 목적/대상 범위 자체가 claim이 전제하는 개념과 다르면(예: claim은 "소비자가 시장에서 사는 소매가"를 말하는데 표는 "식당이 재료를 사입하는 가격"이거나 "농가가 출하할 때 받는 가격"인 경우) MISMATCH로 판단하세요.
- 표의 조사 목적이 claim의 개념과 실제로 일치하면(완전히 같은 조사가 아니어도, 같은 종류의 값을 다루면) MATCH로 판단하세요.
- 조사 목적 설명이 너무 짧거나 애매해서 판단할 근거가 부족하면, 성급하게 MISMATCH로 단정하지 말고 MATCH를 반환하세요(근거 없이 의심만으로 걸러내면 정상 케이스까지 막히므로, 확실한 불일치 증거가 있을 때만 MISMATCH).
- reason은 한국어로 1문장, 왜 그렇게 판단했는지 구체적으로 설명하세요(표의 실제 조사 대상/목적을 인용).
- 반드시 아래 JSON 형식 하나만 반환하세요. 다른 텍스트, 설명, Markdown 코드블록을 덧붙이지 마세요.
정상 예: {"verdict": "MATCH", "reason": "이 표는 소비자물가 조사의 일부로 소매 가격을 직접 조사한다."}
정상 예: {"verdict": "MISMATCH", "reason": "이 표는 외식업체의 식재료 사입가를 조사한 것으로, 소비자 소매가와 다르다."}
잘못된 예: MATCH입니다. 또는 ```json\n{"verdict": "MATCH"}\n```"""

    context_lines = [f"claim: {claim_text}"]
    if claimed_value is not None:
        context_lines.append(
            f"claim이 언급하는 값(참고용): {claimed_value}{claimed_unit or ''}"
        )
    if claimed_period:
        context_lines.append(f"claim이 언급하는 시점(참고용): {claimed_period}")
    context_lines.append(f"\n확정된 표: {table_nm or '(이름 미상)'}")
    context_lines.append(f"이 표의 공식 작성 목적/조사 대상 범위 설명:\n{table_purpose_text}")
    user_prompt = "\n".join(context_lines)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_hcx007_purpose_verify_retry_messages(
    claim_text: str,
    table_nm: Optional[str],
    table_purpose_text: str,
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
) -> list:
    """HCX-007 응답 형식이 올바르지 않을 때 1회 재시도에 쓸 messages."""

    messages = build_hcx007_purpose_verify_messages(
        claim_text, table_nm, table_purpose_text,
        claimed_value, claimed_unit, claimed_period,
    )
    retry_instruction = """
직전 응답은 형식이 올바르지 않았습니다. 같은 판단을 다시 수행하세요.
반드시 {"verdict": "MATCH"|"MISMATCH", "reason": "..."} 형식의 JSON 하나만 반환하세요.
다른 텍스트, 설명, Markdown 코드블록을 덧붙이지 마세요."""
    return [
        {"role": "system", "content": messages[0]["content"] + retry_instruction},
        messages[1],
    ]


def parse_purpose_verdict(content: str) -> Dict[str, Any]:
    """HCX-007 응답에서 {"verdict": "MATCH"|"MISMATCH", "reason": str}를
    뽑는다. JSON 직접 파싱을 우선하고, 실패하면 응답 문자열에서 MATCH/
    MISMATCH 토큰과 reason 후보를 정규식으로 회수한다(parse_resolved_
    cell_index와 동일한 "직접 파싱 우선 -> 정규식 폴백" 관용)."""

    preview = content[:300] if isinstance(content, str) else repr(content)

    if not isinstance(content, str):
        raise PurposeVerdictParseError(f"HCX 응답이 문자열이 아닙니다: {preview}")

    stripped = content.strip()
    # 코드블록으로 감싸서 온 경우(금지했지만 방어적으로 벗겨낸다)
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()

    try:
        direct = json.loads(stripped)
        if isinstance(direct, dict) and "verdict" in direct:
            verdict = str(direct.get("verdict") or "").strip().upper()
            if verdict not in ("MATCH", "MISMATCH"):
                raise PurposeVerdictParseError(
                    f"HCX 응답의 verdict 값이 MATCH/MISMATCH가 아닙니다: {preview}"
                )
            reason = direct.get("reason")
            return {
                "mismatch": verdict == "MISMATCH",
                "reason": str(reason).strip() if reason else None,
            }
    except json.JSONDecodeError:
        pass

    # 정규식 폴백 - "verdict": "MISMATCH" 형태를 문자열 어디서든 찾는다.
    verdict_match = re.search(r'"?verdict"?\s*[:=]\s*"?(MATCH|MISMATCH)"?', stripped, re.IGNORECASE)
    if verdict_match:
        verdict = verdict_match.group(1).upper()
        reason_match = re.search(r'"?reason"?\s*[:=]\s*"([^"]*)"', stripped)
        return {
            "mismatch": verdict == "MISMATCH",
            "reason": reason_match.group(1).strip() if reason_match else None,
        }

    # 마지막 폴백 - 응답 전체에 MATCH/MISMATCH 토큰만 단독으로 있는 경우.
    bare_match = re.search(r"\b(MATCH|MISMATCH)\b", stripped, re.IGNORECASE)
    if bare_match:
        verdict = bare_match.group(1).upper()
        return {"mismatch": verdict == "MISMATCH", "reason": None}

    raise PurposeVerdictParseError(
        f"HCX 응답에서 MATCH/MISMATCH verdict를 찾지 못했습니다: {preview!r}"
    )


def resolve_purpose_with_hcx007(
    claim_text: str,
    table_nm: Optional[str],
    table_purpose_text: Optional[str],
    claimed_value: Optional[float] = None,
    claimed_unit: Optional[str] = None,
    claimed_period: Optional[str] = None,
    timeout: int = 30,
    call_hcx_fn: Callable[..., Dict[str, Any]] = call_hcx,
) -> Optional[Dict[str, Any]]:
    """이미 확정된 표(org_id, tbl_id)의 공식 작성 목적 설명 텍스트와 claim을
    HCX-007 한 콜에 담아 {"mismatch": bool, "reason": str|None}을 반환한다.

    table_purpose_text가 비어 있으면(get_stat_explanation이 실패했거나 빈
    응답을 준 경우) 판단할 근거 자체가 없으므로 호출을 생략하고 None을
    반환한다 - resolve_table_with_hcx007이 후보가 없을 때 None을 반환하는
    것과 같은 계약(폴백 원칙: 근거 없이 게이트를 걸지 않는다).

    call_hcx_fn: [테스트용] 기본값은 진짜 hcx_client.call_hcx - 회귀 테스트는
    결정적 fake로 이 인자를 바꿔치기해서 네트워크 없이 파싱/재시도 로직만
    검증한다."""

    if not table_purpose_text or not table_purpose_text.strip():
        return None

    messages = build_hcx007_purpose_verify_messages(
        claim_text, table_nm, table_purpose_text,
        claimed_value, claimed_unit, claimed_period,
    )

    def _call(msgs):
        t0 = time.perf_counter()
        # [2026-08-28 - hcx_stage1_resolver.py/hcx_stage2_resolver.py와
        # 동일한 이유로 처음부터 고정] 이 프로젝트에서 temperature 미지정
        # 콜이 비결정적이라는 게 이미 두 번 실측 확인됐다(README "스물여덟
        # 번째"/"쉰 번째") - 같은 실수를 세 번째로 반복하지 않는다.
        response_json = call_hcx_fn(
            "HCX-007", msgs, timeout=timeout, thinking_effort="low", temperature=0.0
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        input_tokens, output_tokens, total_tokens = extract_hcx_usage(response_json)
        record_api_usage(
            "HCX-007", input_tokens, output_tokens, total_tokens, latency_ms=latency_ms,
        )
        return response_json

    # [설계 - hcx_stage1/2_resolver와 동일한 원칙] 재시도까지 전부 실패하면
    # 조용히 None을 반환하지 않고 예외를 그대로 올려 보낸다 - 호출부
    # (local_db_agent._attach_purpose_check)가 이 claim 하나의 실패를
    # try/except로 격리하고 "목적 검증 시도했으나 실패"를 진단 필드로
    # 남긴다(hcx_fallback_error와 동일한 투명성 원칙).
    try:
        response_json = _call(messages)
        return parse_purpose_verdict(extract_hcx_content(response_json))
    except PurposeVerdictParseError as first_error:
        LOGGER.warning(
            "[목적 검증 HCX-007 - 응답 형식 오류, 1회 재시도] %s", first_error
        )
        retry_messages = build_hcx007_purpose_verify_retry_messages(
            claim_text, table_nm, table_purpose_text,
            claimed_value, claimed_unit, claimed_period,
        )
        retry_response_json = _call(retry_messages)
        return parse_purpose_verdict(extract_hcx_content(retry_response_json))

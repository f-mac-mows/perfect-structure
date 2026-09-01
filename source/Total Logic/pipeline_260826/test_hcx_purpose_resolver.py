"""[2026-08-28 신규] hcx_purpose_resolver.py(최종 확정된 표 1건의 공식
작성 목적 설명 + claim을 HCX-007 한 콜에 담아 MATCH/MISMATCH + 이유를
받는 목적 검증 게이트)의 프롬프트/파싱/재시도/사용량 로깅 배관을 검증한다.
hcx_stage1_resolver.py/hcx_stage2_resolver.py와 완전히 같은 검증 원칙 -
실제 HCX-007 호출은 네트워크가 필요해 이 샌드박스에서 못 하므로,
hcx_client.call_hcx와 같은 시그니처의 결정적 fake로 배관만 확인한다.
판단 품질(실제로 목적 불일치를 정확히 가려내는지)은 이 테스트의 관심사가
아니다 - 그건 실 API로 로컬에서 검증해야 한다(CLAUDE.md "실측 우선 원칙").

local_db_agent.py 쪽 연동(_attach_purpose_check)은 별도로
test_local_db_agent_derivation.py에 추가한다 - 이 파일은
hcx_purpose_resolver.py 자체의 단위 동작만 본다.

사용법: python test_hcx_purpose_resolver.py (종료 코드 0 = 전체 PASS)
"""

import sys

import hcx_purpose_resolver as hp

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------
# ① 프롬프트 빌더
# ---------------------------------------------------------------------

def test_build_messages_includes_claim_table_and_purpose_text():
    messages = hp.build_hcx007_purpose_verify_messages(
        "금년 배추 가격이 6700원이 됐다.",
        "채소류 월평균 구매량 및 구매금액",
        "일반음식점의 업종별/한식 세분류별 식재료 구매행태(구매량/구매금액)를 조사",
        claimed_value=6700, claimed_unit="원", claimed_period="2026-08",
    )
    _check("messages는 system+user 2개", len(messages) == 2, str(messages))
    _check("system role 확인", messages[0]["role"] == "system")
    _check("user role 확인", messages[1]["role"] == "user")
    user_content = messages[1]["content"]
    _check("user content에 claim 원문 포함", "배추 가격" in user_content)
    _check("user content에 표 이름 포함", "채소류 월평균 구매량 및 구매금액" in user_content)
    _check("user content에 작성 목적 설명 포함", "식재료 구매행태" in user_content)
    _check("user content에 참고용 값/단위 포함", "6700원" in user_content)
    _check("user content에 참고용 시점 포함", "2026-08" in user_content)


def test_build_messages_omits_optional_context_when_not_given():
    messages = hp.build_hcx007_purpose_verify_messages(
        "claim 텍스트", "표 이름", "작성 목적 설명",
    )
    user_content = messages[1]["content"]
    _check("값/시점 없이 호출해도 claim 줄은 그대로 있음", "claim 텍스트" in user_content)
    _check("참고용 값 줄은 안 생김", "값(참고용" not in user_content)


def test_build_messages_handles_missing_table_nm_gracefully():
    messages = hp.build_hcx007_purpose_verify_messages(
        "claim 텍스트", None, "작성 목적 설명",
    )
    _check("table_nm이 None이어도 예외 없이 메시지 생성됨", "(이름 미상)" in messages[1]["content"])


# ---------------------------------------------------------------------
# ② parse_purpose_verdict: JSON 우선 파싱 + 정규식 폴백
# ---------------------------------------------------------------------

def test_parse_purpose_verdict_direct_json_match():
    result = hp.parse_purpose_verdict('{"verdict": "MATCH", "reason": "같은 조사다"}')
    _check("정상 JSON MATCH 파싱", result == {"mismatch": False, "reason": "같은 조사다"}, str(result))


def test_parse_purpose_verdict_direct_json_mismatch():
    result = hp.parse_purpose_verdict('{"verdict": "MISMATCH", "reason": "다른 조사다"}')
    _check("정상 JSON MISMATCH 파싱", result == {"mismatch": True, "reason": "다른 조사다"}, str(result))


def test_parse_purpose_verdict_strips_code_fence():
    result = hp.parse_purpose_verdict('```json\n{"verdict": "MATCH", "reason": "일치"}\n```')
    _check("코드블록으로 감싸도 파싱됨(방어적)", result == {"mismatch": False, "reason": "일치"}, str(result))


def test_parse_purpose_verdict_regex_fallback_on_loose_json():
    result = hp.parse_purpose_verdict('앞에 잡담: "verdict": "MISMATCH", "reason": "다른 목적"')
    _check(
        "직접 JSON 파싱 실패해도 정규식 폴백으로 verdict/reason 회수",
        result == {"mismatch": True, "reason": "다른 목적"},
        str(result),
    )


def test_parse_purpose_verdict_bare_token_fallback():
    # [주의] "MISMATCH" 바로 뒤에 한글 음절이 공백 없이 붙으면(예:
    # "MISMATCH입니다") Python re의 \b가 그 경계를 단어 경계로 보지 않을
    # 수 있다(한글 음절도 유니코드 단어 문자로 취급되므로) - 그래서 이
    # 테스트는 토큰 뒤에 공백/구두점을 명시적으로 둔다.
    result = hp.parse_purpose_verdict("최종 판단: MISMATCH.")
    _check("정규식도 실패하면 단독 토큰이라도 회수(reason은 None)", result == {"mismatch": True, "reason": None}, str(result))


def test_parse_purpose_verdict_raises_when_nothing_found():
    try:
        hp.parse_purpose_verdict("모르겠습니다")
        raised = False
    except hp.PurposeVerdictParseError:
        raised = True
    _check("MATCH/MISMATCH를 전혀 못 찾으면 예외 발생", raised)


def test_parse_purpose_verdict_raises_on_invalid_verdict_value():
    try:
        hp.parse_purpose_verdict('{"verdict": "UNKNOWN", "reason": "x"}')
        raised = False
    except hp.PurposeVerdictParseError:
        raised = True
    _check("verdict 값이 MATCH/MISMATCH가 아니면 예외 발생", raised)


# ---------------------------------------------------------------------
# ③ resolve_purpose_with_hcx007: fake call_hcx로 호출/재시도/실패 배관 검증
# ---------------------------------------------------------------------

def _make_fake_call_hcx(contents):
    calls = []

    def _fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
        calls.append({
            "model_name": model_name, "messages": messages,
            "thinking_effort": thinking_effort, "temperature": temperature,
        })
        index = len(calls) - 1
        content = contents[index] if index < len(contents) else contents[-1]
        return {
            "result": {
                "message": {"content": content},
                "usage": {"promptTokens": 10, "completionTokens": 1, "totalTokens": 11},
            }
        }

    return _fake, calls


def test_resolve_purpose_with_hcx007_success_single_call():
    fake, calls = _make_fake_call_hcx(['{"verdict": "MISMATCH", "reason": "외식업 사입가라 소매가와 다름"}'])
    result = hp.resolve_purpose_with_hcx007(
        "배추 가격이 6700원이 됐다.", "채소류 구매금액", "외식업 식재료 구매행태 조사",
        call_hcx_fn=fake,
    )
    _check("정상 응답이면 mismatch/reason 반환", result == {"mismatch": True, "reason": "외식업 사입가라 소매가와 다름"}, str(result))
    _check("콜은 1번만 발생(재시도 없음)", len(calls) == 1, str(len(calls)))
    _check("HCX-007 모델로 호출함", calls[0]["model_name"] == "HCX-007")
    _check(
        "temperature=0.0 명시적으로 고정됨(비결정성 방지, README 쉰 번째 항목과 동일 원칙)",
        calls[0]["temperature"] == 0.0, str(calls[0]["temperature"]),
    )


def test_resolve_purpose_with_hcx007_empty_purpose_text_skips_call():
    fake, calls = _make_fake_call_hcx(['{"verdict": "MATCH", "reason": "x"}'])
    result = hp.resolve_purpose_with_hcx007("claim", "표", "", call_hcx_fn=fake)
    _check("작성 목적 설명이 빈 문자열이면 호출 자체를 생략하고 None", result is None)
    _check("HCX 콜이 아예 안 나감(비용 없음)", len(calls) == 0, str(len(calls)))


def test_resolve_purpose_with_hcx007_none_purpose_text_skips_call():
    fake, calls = _make_fake_call_hcx(['{"verdict": "MATCH", "reason": "x"}'])
    result = hp.resolve_purpose_with_hcx007("claim", "표", None, call_hcx_fn=fake)
    _check("작성 목적 설명이 None이면 호출 자체를 생략하고 None", result is None)
    _check("HCX 콜이 아예 안 나감", len(calls) == 0)


def test_resolve_purpose_with_hcx007_retries_once_on_malformed_response():
    fake, calls = _make_fake_call_hcx(["아무튼 판단했습니다", '{"verdict": "MATCH", "reason": "일치"}'])
    result = hp.resolve_purpose_with_hcx007("claim", "표", "목적 설명", call_hcx_fn=fake)
    _check("1차 응답이 파싱 안 되면 재시도해서 2차 응답으로 회수", result == {"mismatch": False, "reason": "일치"}, str(result))
    _check("콜은 정확히 2번(1차+재시도 1회)", len(calls) == 2, str(len(calls)))
    _check(
        "재시도 messages의 system 프롬프트에 재시도 안내가 덧붙음",
        "직전 응답은 형식이 올바르지 않았습니다" in calls[1]["messages"][0]["content"],
    )


def test_resolve_purpose_with_hcx007_raises_after_retry_exhausted():
    fake, calls = _make_fake_call_hcx(["모르겠습니다", "역시 모르겠습니다"])
    try:
        hp.resolve_purpose_with_hcx007("claim", "표", "목적 설명", call_hcx_fn=fake)
        raised = False
    except hp.PurposeVerdictParseError:
        raised = True
    _check("재시도까지 실패하면 예외가 전파됨(조용히 None으로 삼키지 않음)", raised)
    _check("콜은 정확히 2번(1차+재시도 1회, 무한 재시도 아님)", len(calls) == 2, str(len(calls)))


def test_resolve_purpose_with_hcx007_client_error_propagates():
    def _raising_fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
        raise hp.HCXClientError("네트워크 오류(재현용 가짜)")

    try:
        hp.resolve_purpose_with_hcx007("claim", "표", "목적 설명", call_hcx_fn=_raising_fake)
        raised = False
    except hp.HCXClientError:
        raised = True
    _check("call_hcx 자체가 실패하면(HCXClientError) 그대로 전파됨", raised)


if __name__ == "__main__":
    test_build_messages_includes_claim_table_and_purpose_text()
    test_build_messages_omits_optional_context_when_not_given()
    test_build_messages_handles_missing_table_nm_gracefully()
    test_parse_purpose_verdict_direct_json_match()
    test_parse_purpose_verdict_direct_json_mismatch()
    test_parse_purpose_verdict_strips_code_fence()
    test_parse_purpose_verdict_regex_fallback_on_loose_json()
    test_parse_purpose_verdict_bare_token_fallback()
    test_parse_purpose_verdict_raises_when_nothing_found()
    test_parse_purpose_verdict_raises_on_invalid_verdict_value()
    test_resolve_purpose_with_hcx007_success_single_call()
    test_resolve_purpose_with_hcx007_empty_purpose_text_skips_call()
    test_resolve_purpose_with_hcx007_none_purpose_text_skips_call()
    test_resolve_purpose_with_hcx007_retries_once_on_malformed_response()
    test_resolve_purpose_with_hcx007_raises_after_retry_exhausted()
    test_resolve_purpose_with_hcx007_client_error_propagates()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

"""[2026-08-22 신규 - Task #29 Step 2] hcx_stage3_resolver.py(claim의
비교/파생 모드를 HCX-007 한 콜로 판단하는 새 Stage 3)의 프롬프트/파싱/
재시도/사용량 로깅 배관을 검증한다. 실제 HCX-007 호출은 네트워크가 필요해
이 샌드박스에서 못 하므로, hcx_client.call_hcx와 같은 시그니처의 결정적
fake로 배관만 확인한다(test_hcx_stage2_resolver.py와 완전히 같은 패턴).

local_db_agent.py 쪽 연동(Task #29 Step 3, 아직 배선 전)은 별도 테스트에서
다룰 것 - 이 파일은 hcx_stage3_resolver.py 자체의 단위 동작만 본다.

사용법: python test_hcx_stage3_resolver.py (종료 코드 0 = 전체 PASS)
"""

import sys

import hcx_stage3_resolver as hr

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------
# ① 프롬프트 빌더
# ---------------------------------------------------------------------

def test_build_messages_includes_claim_and_target_period():
    messages = hr.build_hcx007_stage3_resolve_messages(
        "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다.",
        "202509", claimed_value=22.9, claimed_unit="%",
    )
    _check("messages는 system+user 2개", len(messages) == 2, str(messages))
    _check("system role 확인", messages[0]["role"] == "system")
    _check("user role 확인", messages[1]["role"] == "user")
    user_content = messages[1]["content"]
    _check("user content에 claim 원문 포함", "22.9% 올랐다" in user_content)
    _check("user content에 target_period 포함", "202509" in user_content)
    _check("user content에 참고용 값/단위 포함", "22.9%" in user_content)
    _check("system 프롬프트에 세 모드 전부 설명됨", all(m in messages[0]["content"] for m in ("single", "period_change", "item_diff")))
    _check(
        "system 프롬프트에 item_diff vs period_change 구분 few-shot 예시가 포함됨(2026-08-22 신규 - 편차 대응)",
        "예시 1(item_diff)" in messages[0]["content"] and "예시 2(period_change" in messages[0]["content"],
    )


def test_build_messages_omits_optional_value_when_not_given():
    messages = hr.build_hcx007_stage3_resolve_messages("질의문", "202509")
    user_content = messages[1]["content"]
    _check("값 없이 호출해도 claim/target_period 줄은 그대로 있음", "질의문" in user_content and "202509" in user_content)
    # [주의] target_period 줄 자체가 "target_period(이미 확정됨, 참고용): ..."로
    # 항상 "참고용"을 포함하므로(claimed_value 유무와 무관), claimed_value
    # 줄만 특정하는 "값(참고용" 문구로 확인해야 한다 - "참고용" 단독으로
    # 검사하면 이 테스트가 잘못 FAIL한다(2026-08-22 실측으로 발견).
    _check("참고용 값 줄은 안 생김", "값(참고용" not in user_content)


# ---------------------------------------------------------------------
# ② parse_stage3_response: 정상/None/필수 필드 누락/지저분한 응답
# ---------------------------------------------------------------------

def test_parse_stage3_response_single_mode():
    result = hr.parse_stage3_response('{"mode": "single", "reference_period": null}')
    _check("single 모드 정상 파싱", result == {"mode": "single", "reference_period": None}, str(result))


def test_parse_stage3_response_period_change_mode():
    result = hr.parse_stage3_response('{"mode": "period_change", "reference_period": "202409"}')
    _check(
        "period_change 모드 정상 파싱",
        result == {"mode": "period_change", "reference_period": "202409"},
        str(result),
    )


def test_parse_stage3_response_item_diff_mode():
    result = hr.parse_stage3_response('{"mode": "item_diff", "reference_period": "202009"}')
    _check(
        "item_diff 모드 정상 파싱",
        result == {"mode": "item_diff", "reference_period": "202009"},
        str(result),
    )


def test_parse_stage3_response_null_means_not_confident():
    _check("JSON null -> None(확신 없음)", hr.parse_stage3_response("null") is None)
    _check("대문자 None도 None으로 처리", hr.parse_stage3_response("None") is None)


def test_parse_stage3_response_unknown_mode_raises():
    try:
        hr.parse_stage3_response('{"mode": "yoy", "reference_period": "202409"}')
        raised = False
    except hr.Stage3ParseError:
        raised = True
    _check("정의되지 않은 mode(예: 'yoy')는 파싱 실패로 처리(추측해서 받아들이지 않음)", raised)


def test_parse_stage3_response_missing_reference_period_raises():
    """mode가 single이 아닌데 reference_period가 없으면(추측으로 채우지
    않고) 파싱 실패로 처리해야 한다."""
    try:
        hr.parse_stage3_response('{"mode": "period_change", "reference_period": null}')
        raised = False
    except hr.Stage3ParseError:
        raised = True
    _check("period_change인데 reference_period 누락 시 파싱 실패", raised)


def test_parse_stage3_response_recovers_from_prose_wrapping():
    """모델이 형식을 어기고 짧은 설명을 덧붙인 경우, 응답 안의 JSON 객체
    조각만 잘라서 회수한다(Stage 2의 prose-wrapping 관용과 동일 원칙)."""
    result = hr.parse_stage3_response(
        '이 claim은 전년동월비를 뜻합니다. {"mode": "period_change", "reference_period": "202409"}'
    )
    _check(
        "설명이 덧붙은 응답도 JSON 객체 조각을 회수해서 파싱",
        result == {"mode": "period_change", "reference_period": "202409"},
        str(result),
    )


def test_parse_stage3_response_null_prefix_with_prose_is_none():
    """[2026-08-24 신규 - 2026-08-23 90건 배치(max_completion_tokens=2000)
    실측에서 반복 관측] "no explanation" 지침을 어기고 null 뒤에 설명을
    덧붙이는 패턴이 실제로 여러 건 나왔다 - 예전 코드는 stripped 전체가
    정확히 "null"일 때만 인정해서 이 경우 파싱 실패로 떨어졌었다."""
    result = hr.parse_stage3_response(
        "null\n\n**설명:**  \nclaim은 target_period와의 직접적인 비교 대상이 불분명합니다"
    )
    _check("null 뒤에 설명이 붙어도 None(확신 없음)으로 처리", result is None, str(result))


def test_parse_stage3_response_mode_null_field_is_none():
    """[2026-08-24 신규 - 같은 배치 실측] 모델이 최상위 null 대신
    {"mode": null}로 확신 없음을 표현하는 경우가 반복 관측됐다 - 의미상
    동일하므로 전체 None으로 처리해야지 알 수 없는 mode 오류로 취급하면
    안 된다."""
    result = hr.parse_stage3_response('{"mode": null}')
    _check('{"mode": null}도 None(확신 없음)으로 처리', result is None, str(result))


def test_parse_stage3_response_mode_null_string_is_none():
    """[2026-08-24 신규 - 같은 배치 실측] {"mode": "null"}(문자열 "null")
    로 오는 변형도 같은 의미로 취급한다."""
    result = hr.parse_stage3_response('{"mode": "null"}')
    _check('{"mode": "null"}(문자열)도 None(확신 없음)으로 처리', result is None, str(result))


def test_parse_stage3_response_garbage_raises():
    try:
        hr.parse_stage3_response("모르겠습니다")
        raised = False
    except hr.Stage3ParseError:
        raised = True
    _check("JSON도 null도 아닌 순수 텍스트는 파싱 실패로 처리", raised)


def test_parse_stage3_response_non_dict_json_raises():
    try:
        hr.parse_stage3_response("3")
        raised = False
    except hr.Stage3ParseError:
        raised = True
    _check("숫자 하나만 온 경우(JSON 객체/null이 아님)는 파싱 실패로 처리", raised)


# ---------------------------------------------------------------------
# ③ resolve_comparison_mode_with_hcx007: 결정적 fake로 콜/재시도/전파 확인
# ---------------------------------------------------------------------

def _make_fake_call_hcx(contents):
    """hcx_stage2의 _make_fake_call_hcx와 완전히 같은 패턴 - 호출될 때마다
    contents 리스트에서 하나씩 꺼내 그걸 result.message.content로 담은
    응답을 돌려주는 결정적 fake."""
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


def test_resolve_comparison_mode_success_single_call():
    fake, calls = _make_fake_call_hcx(['{"mode": "item_diff", "reference_period": "202009"}'])
    result = hr.resolve_comparison_mode_with_hcx007(
        "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다. 같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트 가까이 높은 수치다.",
        "202509", call_hcx_fn=fake,
    )
    _check("정상 응답이면 mode/reference_period를 그대로 반환", result == {"mode": "item_diff", "reference_period": "202009"}, str(result))
    _check("콜은 1번만 발생(재시도 없음)", len(calls) == 1, str(len(calls)))
    _check("HCX-007 모델로 호출함", calls[0]["model_name"] == "HCX-007")
    _check(
        "temperature=0.0으로 호출됨(분류 작업이라 창의성 불필요 - 편차 감소 목적)",
        calls[0]["temperature"] == 0.0, str(calls[0].get("temperature")),
    )


def test_resolve_comparison_mode_null_response():
    fake, calls = _make_fake_call_hcx(["null"])
    result = hr.resolve_comparison_mode_with_hcx007("애매한 claim", "202509", call_hcx_fn=fake)
    _check("HCX가 null을 반환하면 None(확신 없음)", result is None)
    _check("콜은 1번만 발생", len(calls) == 1)


def test_resolve_comparison_mode_retries_once_on_malformed_response():
    fake, calls = _make_fake_call_hcx(
        ["정답은 모르겠고 아무튼 이렇습니다", '{"mode": "single", "reference_period": null}']
    )
    result = hr.resolve_comparison_mode_with_hcx007("질의", "202509", call_hcx_fn=fake)
    _check("1차 응답이 파싱 안 되면 재시도해서 2차 응답으로 회수", result == {"mode": "single", "reference_period": None}, str(result))
    _check("콜은 정확히 2번(1차+재시도 1회)", len(calls) == 2, str(len(calls)))
    _check(
        "재시도 messages의 system 프롬프트에 재시도 안내가 덧붙음",
        "직전 응답은 형식이 올바르지 않았습니다" in calls[1]["messages"][0]["content"],
    )


def test_resolve_comparison_mode_raises_after_retry_exhausted():
    fake, calls = _make_fake_call_hcx(["모르겠습니다", "역시 모르겠습니다"])
    try:
        hr.resolve_comparison_mode_with_hcx007("질의", "202509", call_hcx_fn=fake)
        raised = False
    except hr.Stage3ParseError:
        raised = True
    _check("재시도까지 실패하면 예외가 전파됨(조용히 None으로 삼키지 않음)", raised)
    _check("콜은 정확히 2번(1차+재시도 1회, 무한 재시도 아님)", len(calls) == 2, str(len(calls)))


def test_resolve_comparison_mode_empty_inputs_skip_call():
    fake, calls = _make_fake_call_hcx(["null"])
    result1 = hr.resolve_comparison_mode_with_hcx007("", "202509", call_hcx_fn=fake)
    result2 = hr.resolve_comparison_mode_with_hcx007("질의", "", call_hcx_fn=fake)
    _check("claim_text가 비어 있으면 호출 생략하고 None", result1 is None)
    _check("target_period가 비어 있으면 호출 생략하고 None", result2 is None)
    _check("HCX 콜이 아예 안 나감(비용 없음)", len(calls) == 0, str(len(calls)))


def test_resolve_comparison_mode_client_error_propagates():
    def _raising_fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
        raise hr.HCXClientError("네트워크 오류(재현용 가짜)")

    try:
        hr.resolve_comparison_mode_with_hcx007("질의", "202509", call_hcx_fn=_raising_fake)
        raised = False
    except hr.HCXClientError:
        raised = True
    _check("call_hcx 자체가 실패하면(HCXClientError) 그대로 전파됨", raised)


if __name__ == "__main__":
    test_build_messages_includes_claim_and_target_period()
    test_build_messages_omits_optional_value_when_not_given()
    test_parse_stage3_response_single_mode()
    test_parse_stage3_response_period_change_mode()
    test_parse_stage3_response_item_diff_mode()
    test_parse_stage3_response_null_means_not_confident()
    test_parse_stage3_response_null_prefix_with_prose_is_none()
    test_parse_stage3_response_mode_null_field_is_none()
    test_parse_stage3_response_mode_null_string_is_none()
    test_parse_stage3_response_unknown_mode_raises()
    test_parse_stage3_response_missing_reference_period_raises()
    test_parse_stage3_response_recovers_from_prose_wrapping()
    test_parse_stage3_response_garbage_raises()
    test_parse_stage3_response_non_dict_json_raises()
    test_resolve_comparison_mode_success_single_call()
    test_resolve_comparison_mode_null_response()
    test_resolve_comparison_mode_retries_once_on_malformed_response()
    test_resolve_comparison_mode_raises_after_retry_exhausted()
    test_resolve_comparison_mode_empty_inputs_skip_call()
    test_resolve_comparison_mode_client_error_propagates()

    print()
    if _failures:
        print(f"FAIL: {len(_failures)}건 실패 - {_failures}")
        sys.exit(1)
    print("PASS: 전체 통과")

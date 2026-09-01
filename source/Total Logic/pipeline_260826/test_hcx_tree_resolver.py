"""[2026-08-22 신규 - HCX 토큰 폭발(429) 대응] hcx_tree_resolver.py(카테시안
곱 없이 압축된 축 트리 + claim을 HCX-007 한 콜에 담아 축별 코드 매핑을
받는 weak_literal_tie 전용 리졸버)의 프롬프트/파싱/재시도/사용량 배관을
검증한다. test_hcx_stage2_resolver.py와 같은 구조 - 실제 HCX-007 호출은
네트워크가 필요해 이 샌드박스에서 못 하므로 결정적 fake로 배관만 확인한다.

local_db_agent.py 쪽 연동(hcx_axis_resolve_fn 배선, hcx_resolve_fn과의
우선순위, _lookup_cell_by_axis_codes)은 test_local_db_agent_derivation.py
에 있다.

사용법: python test_hcx_tree_resolver.py (종료 코드 0 = 전체 PASS)
"""

import sys

import hcx_tree_resolver as tr

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


_SAMPLE_TREES = {
    1: {"axis_label": "시도별", "tree_text": "전국 [T10]\n지역1 [T11]", "codes": {"T10", "T11"}},
    2: {
        "axis_label": "지출목적별",
        "tree_text": "빵 및 곡물 [A011]\n  빵 [A01116]\n  떡 [A01117]",
        "codes": {"A011", "A01116", "A01117"},
    },
}


# ---------------------------------------------------------------------
# ① 프롬프트 빌더: axis_trees가 축별로 전부 나열되는지
# ---------------------------------------------------------------------

def test_build_messages_lists_all_axis_trees():
    messages = tr.build_hcx007_axis_resolve_messages(
        _SAMPLE_TREES, "빵(38.5%)이 크게 올랐다", item_context="소비자물가지수",
        claimed_value=38.5, claimed_unit="%", claimed_period="202509",
    )
    _check("messages는 system+user 2개", len(messages) == 2, str(messages))
    _check("system role 확인", messages[0]["role"] == "system")
    _check("user role 확인", messages[1]["role"] == "user")
    user_content = messages[1]["content"]
    _check("user content에 claim 원문 포함", "빵(38.5%)" in user_content)
    _check("user content에 항목 참고 정보 포함", "소비자물가지수" in user_content)
    _check("user content에 참고용 값/단위 포함", "38.5%" in user_content)
    _check("user content에 참고용 시점 포함", "202509" in user_content)
    _check("axis 1 트리가 라벨과 함께 포함됨", "[axis 1 - 시도별]" in user_content)
    _check("axis 2 트리가 라벨과 함께 포함됨", "[axis 2 - 지출목적별]" in user_content)
    _check("축 2 트리의 leaf 노드(빵)가 그대로 포함됨", "빵 [A01116]" in user_content)


def test_build_messages_omits_optional_context_when_not_given():
    messages = tr.build_hcx007_axis_resolve_messages(_SAMPLE_TREES, "질의문")
    user_content = messages[1]["content"]
    _check("값/시점/항목 없이 호출해도 claim 줄은 그대로 있음", "질의문" in user_content)
    _check("항목 참고 줄은 안 생김", "이 표의 항목" not in user_content)
    _check("참고용 값 줄은 안 생김", "값(참고" not in user_content)


# ---------------------------------------------------------------------
# ② parse_resolved_axis_codes: 정상/None/존재하지 않는 code/지저분한 응답
# ---------------------------------------------------------------------

def test_parse_valid_json_object():
    result = tr.parse_resolved_axis_codes('{"1": "T10", "2": "A01116"}', _SAMPLE_TREES)
    _check("정상 JSON object 파싱", result == {1: "T10", 2: "A01116"}, str(result))


def test_parse_partial_axis_mapping():
    """일부 축만 응답해도(예: claim이 지역을 언급 안 함) 그대로 받는다 -
    나머지 축의 기본값 채우기는 local_db_agent._lookup_cell_by_axis_codes
    책임이지 이 파싱 함수 책임이 아니다."""
    result = tr.parse_resolved_axis_codes('{"2": "A01116"}', _SAMPLE_TREES)
    _check("축 1개만 있는 매핑도 그대로 반환", result == {2: "A01116"}, str(result))


def test_parse_null_variants():
    _check("JSON null -> None", tr.parse_resolved_axis_codes("null", _SAMPLE_TREES) is None)
    _check("빈 문자열 -> None", tr.parse_resolved_axis_codes("", _SAMPLE_TREES) is None)
    _check("빈 JSON object -> None(확신 없음과 동일 취급)", tr.parse_resolved_axis_codes("{}", _SAMPLE_TREES) is None)


def test_parse_null_prefix_with_prose_is_none():
    """[2026-08-24 신규 - 2026-08-23 90건 배치(max_completion_tokens=2000)
    실측에서 관측] null 뒤에 "no explanation" 지침을 어기고 설명을
    덧붙이는 응답이 나왔다(hcx_stage3_resolver와 동일 패턴) - 예전엔
    stripped 전체가 정확히 "null"일 때만 인정해서 파싱 실패로 떨어졌다."""
    result = tr.parse_resolved_axis_codes(
        "null\n\n**설명:**  \n제공된 axis_trees에는 해당 축이 없습니다", _SAMPLE_TREES,
    )
    _check("null 뒤에 설명이 붙어도 None(확신 없음)으로 처리", result is None, str(result))


def test_parse_recovers_from_prose_wrapping_after_valid_json():
    """[2026-08-24 신규 - 같은 실측] 유효한 JSON 객체 뒤에 설명이 붙어
    파싱이 실패하는 경우(hcx_stage1/2/3_resolver엔 이미 있던 관용이
    이 파일엔 없었음) - 첫 {...} 조각만 잘라서 회수해야 한다."""
    result = tr.parse_resolved_axis_codes(
        '{"2": "A01116"}  \n**오류 수정**: 앞서 분석에서...', _SAMPLE_TREES,
    )
    _check("설명이 덧붙은 응답도 JSON 객체 조각을 회수해서 파싱", result == {2: "A01116"}, str(result))


def test_parse_per_axis_null_code_is_skipped():
    """[2026-08-24 신규 - 같은 실측] 모델이 "이 축은 확신 없음"을 axis를
    생략하는 대신 code=null(또는 문자열 "null")로 표현하는 경우가
    나왔다({"1": null, "2": "A01116"}) - 그 axis만 건너뛰고 나머지는
    그대로 반환해야지, 트리에 없는 code를 지어낸 것처럼 에러로 취급하면
    안 된다."""
    result = tr.parse_resolved_axis_codes('{"1": null, "2": "A01116"}', _SAMPLE_TREES)
    _check("null인 axis는 건너뛰고 나머지만 반환", result == {2: "A01116"}, str(result))

    result_str_null = tr.parse_resolved_axis_codes('{"1": "null", "2": "A01116"}', _SAMPLE_TREES)
    _check('문자열 "null"도 동일하게 건너뜀', result_str_null == {2: "A01116"}, str(result_str_null))

    result_all_null = tr.parse_resolved_axis_codes('{"1": null, "2": null}', _SAMPLE_TREES)
    _check("모든 axis가 null이면 전체 None(빈 결과와 동일 취급)", result_all_null is None, str(result_all_null))


def test_parse_strips_markdown_fence():
    result = tr.parse_resolved_axis_codes('```json\n{"2": "A01116"}\n```', _SAMPLE_TREES)
    _check("마크다운 코드펜스를 벗기고 파싱함", result == {2: "A01116"}, str(result))


def test_parse_nonexistent_axis_position_raises():
    try:
        tr.parse_resolved_axis_codes('{"9": "A01116"}', _SAMPLE_TREES)
        raised = False
    except tr.ResolvedAxisCodesParseError:
        raised = True
    _check("존재하지 않는 axis position은 파싱 실패로 처리(추측 안 함)", raised)


def test_parse_nonexistent_code_raises():
    try:
        tr.parse_resolved_axis_codes('{"2": "존재하지않는코드"}', _SAMPLE_TREES)
        raised = False
    except tr.ResolvedAxisCodesParseError:
        raised = True
    _check("트리에 없는 code를 지어내면 파싱 실패로 처리(추측 안 함)", raised)


def test_parse_non_dict_raises():
    try:
        tr.parse_resolved_axis_codes('"A01116"', _SAMPLE_TREES)
        raised = False
    except tr.ResolvedAxisCodesParseError:
        raised = True
    _check("JSON object가 아닌 응답(문자열 단독)은 파싱 실패로 처리", raised)


def test_parse_garbage_raises():
    try:
        tr.parse_resolved_axis_codes("모르겠습니다", _SAMPLE_TREES)
        raised = False
    except tr.ResolvedAxisCodesParseError:
        raised = True
    _check("JSON도 null도 아닌 응답은 파싱 실패로 처리", raised)


# ---------------------------------------------------------------------
# ③ resolve_axis_codes_with_hcx007: fake call_hcx로 호출/재시도/실패 배관 검증
# ---------------------------------------------------------------------

def _make_fake_call_hcx(contents):
    calls = []

    def _fake(model_name, messages, timeout=30, thinking_effort=None):
        calls.append({"model_name": model_name, "messages": messages, "thinking_effort": thinking_effort})
        index = len(calls) - 1
        content = contents[index] if index < len(contents) else contents[-1]
        return {
            "result": {
                "message": {"content": content},
                "usage": {"promptTokens": 10, "completionTokens": 1, "totalTokens": 11},
            }
        }

    return _fake, calls


def test_resolve_axis_codes_success_single_call():
    fake, calls = _make_fake_call_hcx(['{"2": "A01116"}'])
    result = tr.resolve_axis_codes_with_hcx007(_SAMPLE_TREES, "빵이 올랐다", call_hcx_fn=fake)
    _check("정상 응답이면 그 매핑을 그대로 반환", result == {2: "A01116"}, str(result))
    _check("콜은 1번만 발생(재시도 없음)", len(calls) == 1, str(len(calls)))
    _check("HCX-007 모델로 호출함", calls[0]["model_name"] == "HCX-007")


def test_resolve_axis_codes_null_response():
    fake, calls = _make_fake_call_hcx(["null"])
    result = tr.resolve_axis_codes_with_hcx007(_SAMPLE_TREES, "질의", call_hcx_fn=fake)
    _check("HCX가 null을 반환하면 None(확신 없음)", result is None)
    _check("콜은 1번만 발생", len(calls) == 1)


def test_resolve_axis_codes_retries_once_on_malformed_response():
    fake, calls = _make_fake_call_hcx(["정답은 모르겠고 아무튼 골랐습니다", '{"2": "A01117"}'])
    result = tr.resolve_axis_codes_with_hcx007(_SAMPLE_TREES, "질의", call_hcx_fn=fake)
    _check("1차 응답이 파싱 안 되면 재시도해서 2차 응답으로 매핑 회수", result == {2: "A01117"}, str(result))
    _check("콜은 정확히 2번(1차+재시도 1회)", len(calls) == 2, str(len(calls)))
    _check(
        "재시도 messages의 system 프롬프트에 재시도 안내가 덧붙음",
        "직전 응답은 형식이 올바르지 않았습니다" in calls[1]["messages"][0]["content"],
    )


def test_resolve_axis_codes_raises_after_retry_exhausted():
    fake, calls = _make_fake_call_hcx(["모르겠습니다", "역시 모르겠습니다"])
    try:
        tr.resolve_axis_codes_with_hcx007(_SAMPLE_TREES, "질의", call_hcx_fn=fake)
        raised = False
    except tr.ResolvedAxisCodesParseError:
        raised = True
    _check("재시도까지 실패하면 예외가 전파됨(조용히 None으로 삼키지 않음)", raised)
    _check("콜은 정확히 2번(1차+재시도 1회, 무한 재시도 아님)", len(calls) == 2, str(len(calls)))


def test_resolve_axis_codes_empty_axis_trees_skips_call():
    fake, calls = _make_fake_call_hcx(['{"2": "A01116"}'])
    result = tr.resolve_axis_codes_with_hcx007({}, "질의", call_hcx_fn=fake)
    _check("axis_trees가 비어 있으면 호출 자체를 생략하고 None", result is None)
    _check("HCX 콜이 아예 안 나감(비용 없음)", len(calls) == 0, str(len(calls)))


def test_resolve_axis_codes_client_error_propagates_without_retry():
    """[hcx_stage2_resolver와 같은 원칙] 재시도는 응답 형식 오류에만
    건다 - 네트워크/HTTP 오류(call_hcx 자체가 이미 429는 내부에서
    재시도하므로 여기서 또 재시도하면 이중 재시도가 된다)는 그대로
    전파하고 콜 1번으로 끝나야 한다."""

    def _raising_fake(model_name, messages, timeout=30, thinking_effort=None):
        raise RuntimeError("네트워크 오류(재현용 가짜)")

    calls_before = []

    def _counting_raising_fake(model_name, messages, timeout=30, thinking_effort=None):
        calls_before.append(1)
        return _raising_fake(model_name, messages, timeout, thinking_effort)

    try:
        tr.resolve_axis_codes_with_hcx007(_SAMPLE_TREES, "질의", call_hcx_fn=_counting_raising_fake)
        raised = False
    except RuntimeError:
        raised = True
    _check("call_hcx 자체가 실패하면 그대로 전파됨", raised)
    _check("재시도 없이 콜 1번만 발생", len(calls_before) == 1, str(len(calls_before)))


if __name__ == "__main__":
    test_build_messages_lists_all_axis_trees()
    test_build_messages_omits_optional_context_when_not_given()
    test_parse_valid_json_object()
    test_parse_partial_axis_mapping()
    test_parse_null_variants()
    test_parse_null_prefix_with_prose_is_none()
    test_parse_recovers_from_prose_wrapping_after_valid_json()
    test_parse_per_axis_null_code_is_skipped()
    test_parse_strips_markdown_fence()
    test_parse_nonexistent_axis_position_raises()
    test_parse_nonexistent_code_raises()
    test_parse_non_dict_raises()
    test_parse_garbage_raises()
    test_resolve_axis_codes_success_single_call()
    test_resolve_axis_codes_null_response()
    test_resolve_axis_codes_retries_once_on_malformed_response()
    test_resolve_axis_codes_raises_after_retry_exhausted()
    test_resolve_axis_codes_empty_axis_trees_skips_call()
    test_resolve_axis_codes_client_error_propagates_without_retry()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

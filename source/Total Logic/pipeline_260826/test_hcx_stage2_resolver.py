"""[2026-08-21 신규 - Task #80 전환] hcx_stage2_resolver.py(표 전체 셀
목록 + claim을 HCX-007 한 콜에 담아 정답 셀 index를 받는 새 Stage 2 갭
폴백)의 프롬프트/파싱/재시도/사용량 로깅 배관을 검증한다. 실제 HCX-007
호출은 네트워크가 필요해 이 샌드박스에서 못 하므로, hcx_client.call_hcx와
같은 시그니처의 결정적 fake로 배관만 확인한다 - 판단 품질(실제로 맞는
셀을 고르는지)은 이 테스트의 관심사가 아니다(그건
probe_national_debt_full_pipeline_hcx.py로 로컬에서 실측한다).

local_db_agent.py 쪽 연동(hcx_resolve_fn 배선, embed_fn과의 우선순위,
예외 처리, top_k truncation 없음 확인)은 test_local_db_agent_derivation.py
에 있다 - 이 파일은 hcx_stage2_resolver.py 자체의 단위 동작만 본다.

사용법: python test_hcx_stage2_resolver.py (종료 코드 0 = 전체 PASS)
"""

import sys

import hcx_stage2_resolver as hr

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------
# ① 프롬프트 빌더: candidates가 index로 정확히 나열되는지
# ---------------------------------------------------------------------

def test_build_messages_lists_all_candidates_with_index():
    cell_texts = ["국가채무(D1) 국가채무", "국가채무(D1) 국가채무 GDP 대비"]
    messages = hr.build_hcx007_stage2_resolve_messages(
        cell_texts, "나랏빚", claimed_value=1175.0, claimed_unit="조원", claimed_period="2024",
    )
    _check("messages는 system+user 2개", len(messages) == 2, str(messages))
    _check("system role 확인", messages[0]["role"] == "system")
    _check("user role 확인", messages[1]["role"] == "user")
    user_content = messages[1]["content"]
    _check("user content에 claim 원문 포함", "나랏빚" in user_content)
    _check("user content에 참고용 값/단위 포함", "1175.0조원" in user_content)
    _check("user content에 참고용 시점 포함", "2024" in user_content)
    _check("candidate 0이 index와 함께 나열됨", "0: 국가채무(D1) 국가채무" in user_content)
    _check("candidate 1이 index와 함께 나열됨", "1: 국가채무(D1) 국가채무 GDP 대비" in user_content)


def test_build_messages_omits_optional_context_when_not_given():
    messages = hr.build_hcx007_stage2_resolve_messages(["a", "b"], "질의문")
    user_content = messages[1]["content"]
    _check("값/시점 없이 호출해도 claim 줄은 그대로 있음", "질의문" in user_content)
    _check("참고용 값 줄은 안 생김", "참고용" not in user_content or "값(참고용" not in user_content)


# ---------------------------------------------------------------------
# ② parse_resolved_cell_index: 정상/None/범위 밖/지저분한 응답
# ---------------------------------------------------------------------

def test_parse_resolved_cell_index_valid_int():
    _check("정상 정수 파싱", hr.parse_resolved_cell_index("2", 5) == 2)
    _check("공백 섞인 정수도 파싱", hr.parse_resolved_cell_index("  3  ", 5) == 3)


def test_parse_resolved_cell_index_null_variants():
    _check("JSON null -> None", hr.parse_resolved_cell_index("null", 5) is None)
    _check("대문자 None도 None으로 처리", hr.parse_resolved_cell_index("None", 5) is None)


def test_parse_resolved_cell_index_out_of_range_raises():
    try:
        hr.parse_resolved_cell_index("99", 5)
        raised = False
    except hr.ResolvedIndexParseError:
        raised = True
    _check("범위 밖 index는 파싱 실패로 처리(추측해서 받아들이지 않음)", raised)


def test_parse_resolved_cell_index_negative_raises():
    try:
        hr.parse_resolved_cell_index("-1", 5)
        raised = False
    except hr.ResolvedIndexParseError:
        raised = True
    _check("음수 index도 파싱 실패로 처리", raised)


def test_parse_resolved_cell_index_recovers_from_prose_wrapping():
    """모델이 형식을 어기고 짧은 설명을 덧붙인 경우(예: '정답은 3번입니다')
    - candidates 범위 안의 첫 정수를 회수한다."""
    _check(
        "설명 문장에 섞인 정수도 회수",
        hr.parse_resolved_cell_index("정답은 3번입니다.", 5) == 3,
    )


def test_parse_resolved_cell_index_garbage_raises():
    try:
        hr.parse_resolved_cell_index("모르겠습니다", 5)
        raised = False
    except hr.ResolvedIndexParseError:
        raised = True
    _check("정수도 null도 없는 응답은 파싱 실패로 처리", raised)


# ---------------------------------------------------------------------
# ③ resolve_cell_with_hcx007: fake call_hcx로 호출/재시도/실패 배관 검증
# ---------------------------------------------------------------------

def _make_fake_call_hcx(contents):
    """호출될 때마다 contents 리스트에서 하나씩 꺼내 그걸 result.message.
    content로 담은 응답을 돌려주는 결정적 fake - hcx_client.call_hcx와
    같은 시그니처((model_name, messages, timeout=, thinking_effort=))로
    받는다. 호출 횟수/받은 messages를 calls에 기록해 검증에 쓴다."""
    calls = []

    def _fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
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


def test_resolve_cell_with_hcx007_success_single_call():
    fake, calls = _make_fake_call_hcx(["1"])
    result = hr.resolve_cell_with_hcx007(
        ["a", "b", "c"], "질의", call_hcx_fn=fake,
    )
    _check("정상 응답이면 그 index를 그대로 반환", result == 1, str(result))
    _check("콜은 1번만 발생(재시도 없음)", len(calls) == 1, str(len(calls)))
    _check("HCX-007 모델로 호출함", calls[0]["model_name"] == "HCX-007")


def test_resolve_cell_with_hcx007_null_response():
    fake, calls = _make_fake_call_hcx(["null"])
    result = hr.resolve_cell_with_hcx007(["a", "b"], "질의", call_hcx_fn=fake)
    _check("HCX가 null을 반환하면 None(확신 없음)", result is None)
    _check("콜은 1번만 발생", len(calls) == 1)


def test_resolve_cell_with_hcx007_retries_once_on_malformed_response():
    fake, calls = _make_fake_call_hcx(["정답은 모르겠고 아무튼 골랐습니다", "2"])
    result = hr.resolve_cell_with_hcx007(
        ["a", "b", "c", "d"], "질의", call_hcx_fn=fake,
    )
    _check("1차 응답이 파싱 안 되면 재시도해서 2차 응답으로 index 회수", result == 2, str(result))
    _check("콜은 정확히 2번(1차+재시도 1회)", len(calls) == 2, str(len(calls)))
    _check(
        "재시도 messages의 system 프롬프트에 재시도 안내가 덧붙음",
        "직전 응답은 형식이 올바르지 않았습니다" in calls[1]["messages"][0]["content"],
    )


def test_resolve_cell_with_hcx007_raises_after_retry_exhausted():
    """[설계 - vdb_discovery.embedding_expand_phrases의 total-failure-
    still-raises와 동일한 원칙] 재시도까지 전부 형식이 틀리면 조용히
    None으로 삼키지 않고 예외를 올려 이 claim의 실패 사유(hcx_fallback_
    error)가 호출부(local_db_agent.resolve_claim_evidence)에 남게 한다."""
    fake, calls = _make_fake_call_hcx(["모르겠습니다", "역시 모르겠습니다"])
    try:
        hr.resolve_cell_with_hcx007(["a", "b"], "질의", call_hcx_fn=fake)
        raised = False
    except hr.ResolvedIndexParseError:
        raised = True
    _check("재시도까지 실패하면 예외가 전파됨(조용히 None으로 삼키지 않음)", raised)
    _check("콜은 정확히 2번(1차+재시도 1회, 무한 재시도 아님)", len(calls) == 2, str(len(calls)))


def test_resolve_cell_with_hcx007_empty_cell_texts_skips_call():
    fake, calls = _make_fake_call_hcx(["0"])
    result = hr.resolve_cell_with_hcx007([], "질의", call_hcx_fn=fake)
    _check("cell_texts가 비어 있으면 호출 자체를 생략하고 None", result is None)
    _check("HCX 콜이 아예 안 나감(비용 없음)", len(calls) == 0, str(len(calls)))


def test_resolve_cell_with_hcx007_client_error_propagates():
    def _raising_fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
        raise hr.HCXClientError("네트워크 오류(재현용 가짜)")

    try:
        hr.resolve_cell_with_hcx007(["a", "b"], "질의", call_hcx_fn=_raising_fake)
        raised = False
    except hr.HCXClientError:
        raised = True
    _check("call_hcx 자체가 실패하면(HCXClientError) 그대로 전파됨", raised)


if __name__ == "__main__":
    test_build_messages_lists_all_candidates_with_index()
    test_build_messages_omits_optional_context_when_not_given()
    test_parse_resolved_cell_index_valid_int()
    test_parse_resolved_cell_index_null_variants()
    test_parse_resolved_cell_index_out_of_range_raises()
    test_parse_resolved_cell_index_negative_raises()
    test_parse_resolved_cell_index_recovers_from_prose_wrapping()
    test_parse_resolved_cell_index_garbage_raises()
    test_resolve_cell_with_hcx007_success_single_call()
    test_resolve_cell_with_hcx007_null_response()
    test_resolve_cell_with_hcx007_retries_once_on_malformed_response()
    test_resolve_cell_with_hcx007_raises_after_retry_exhausted()
    test_resolve_cell_with_hcx007_empty_cell_texts_skips_call()
    test_resolve_cell_with_hcx007_client_error_propagates()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

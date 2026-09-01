"""[2026-08-21 신규 - Task #80 확장] hcx_stage1_resolver.py(로컬
tables_registry 전체 + claim을 HCX-007 한 콜에 담아 정답 표 index를
받는 Stage 1 대안 경로)의 프롬프트/파싱/재시도/사용량 로깅 배관을
검증한다. hcx_stage2_resolver.py와 완전히 같은 검증 원칙 - 실제
HCX-007 호출은 네트워크가 필요해 이 샌드박스에서 못 하므로,
hcx_client.call_hcx와 같은 시그니처의 결정적 fake로 배관만 확인한다.
판단 품질(실제로 맞는 표를 고르는지)은 이 테스트의 관심사가 아니다 -
그건 실 API로 로컬에서 검증해야 한다(다음 세션 실측 대상).

parse_resolved_cell_index는 hcx_stage2_resolver.py 걸 그대로 재사용
하므로(파싱 계약이 표/셀 어느 쪽이든 동일) 이 파일에서 다시 테스트하지
않는다 - test_hcx_stage2_resolver.py에 이미 있음.

local_db_agent.py 쪽 연동은 별도로 test_local_db_agent_derivation.py에
추가한다 - 이 파일은 hcx_stage1_resolver.py 자체의 단위 동작만 본다.

사용법: python test_hcx_stage1_resolver.py (종료 코드 0 = 전체 PASS)
"""

import sys

import hcx_stage1_resolver as h1

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------
# ① 프롬프트 빌더: table candidates가 index/이름/통계명과 함께 나열되는지
# ---------------------------------------------------------------------

def test_build_messages_lists_all_tables_with_index():
    table_list = [
        {"org_id": "101", "tbl_id": "DT_1J22001", "tbl_nm": "지출목적별 소비자물가지수", "stat_nm": None},
        {"org_id": "101", "tbl_id": "DT_2IFS002", "tbl_nm": "소비자물가지수", "stat_nm": "IMF"},
    ]
    messages = h1.build_hcx007_stage1_resolve_messages(
        table_list, "주류 물가 상승률", claimed_value=13.1, claimed_unit="%", claimed_period="2025-09",
    )
    _check("messages는 system+user 2개", len(messages) == 2, str(messages))
    _check("system role 확인", messages[0]["role"] == "system")
    _check("user role 확인", messages[1]["role"] == "user")
    user_content = messages[1]["content"]
    _check("user content에 claim 원문 포함", "주류 물가 상승률" in user_content)
    _check("user content에 참고용 값/단위 포함", "13.1%" in user_content)
    _check("user content에 참고용 시점 포함", "2025-09" in user_content)
    _check("candidate 0이 index+표이름과 함께 나열됨", "0: 지출목적별 소비자물가지수" in user_content)
    _check("candidate 1이 index+표이름+통계명과 함께 나열됨", "1: 소비자물가지수 (IMF)" in user_content)


def test_build_messages_omits_optional_context_when_not_given():
    table_list = [{"org_id": "1", "tbl_id": "T1", "tbl_nm": "표A", "stat_nm": None}]
    messages = h1.build_hcx007_stage1_resolve_messages(table_list, "질의문")
    user_content = messages[1]["content"]
    _check("값/시점 없이 호출해도 claim 줄은 그대로 있음", "질의문" in user_content)
    _check("참고용 값 줄은 안 생김", "값(참고용" not in user_content)


def test_build_messages_includes_axis_hints_when_present():
    """[2026-08-21 실측 재현 계기 - 실 API 검증에서 표 이름만으로는
    "지출목적별"/"품목성질별"/"품목별" 소비자물가지수 셋을 HCX-007이
    구분 못 하고 콜마다 다른(잘못된) 표를 고르는 게 확인됐다
    (probe_c018_stage1_llm_table_select.py 실행 결과). axis_hints
    (분류축 이름 + 최상위 분류값 샘플)를 프롬프트에 포함시켜, claim의
    핵심 개념("주류")이 실제로 등장하는 축값이 프롬프트에 보이는지
    확인한다."""
    table_list = [
        {
            "org_id": "101", "tbl_id": "DT_1J22001", "tbl_nm": "지출목적별 소비자물가지수", "stat_nm": None,
            "axis_hints": [{"axis_label": "지출목적별", "values": ["0 총지수", "01 식료품 및 비주류음료", "02 주류 및 담배"]}],
        },
        {
            "org_id": "101", "tbl_id": "DT_1J22002", "tbl_nm": "품목성질별 소비자물가지수", "stat_nm": None,
            "axis_hints": [{"axis_label": "품목성질별", "values": ["총지수", "상품", "서비스"]}],
        },
    ]
    messages = h1.build_hcx007_stage1_resolve_messages(table_list, "주류 물가 상승률")
    user_content = messages[1]["content"]
    _check(
        "candidate 0에 축 이름과 '주류 및 담배' 값이 그대로 노출됨",
        "지출목적별: 0 총지수, 01 식료품 및 비주류음료, 02 주류 및 담배" in user_content,
        user_content,
    )
    _check(
        "candidate 1에는 '주류'가 없는 축값만 노출됨(구분 정보 제공)",
        "품목성질별: 총지수, 상품, 서비스" in user_content,
        user_content,
    )


def test_build_messages_handles_missing_axis_hints_key_gracefully():
    """axis_hints 키 자체가 없는 table_list(구 호출부/테스트)도 예외
    없이 동작해야 한다 - 하위 호환."""
    table_list = [{"org_id": "1", "tbl_id": "T1", "tbl_nm": "표A", "stat_nm": None}]
    messages = h1.build_hcx007_stage1_resolve_messages(table_list, "질의")
    _check("axis_hints 키가 없어도 예외 없이 메시지 생성됨", "0: 표A" in messages[1]["content"])


def test_build_messages_handles_empty_stat_nm_gracefully():
    table_list = [{"org_id": "1", "tbl_id": "T1", "tbl_nm": "표A", "stat_nm": None}]
    messages = h1.build_hcx007_stage1_resolve_messages(table_list, "질의")
    user_content = messages[1]["content"]
    _check("stat_nm이 없으면 괄호 없이 표 이름만 나열됨", "0: 표A" in user_content and "()" not in user_content)


# ---------------------------------------------------------------------
# ② resolve_table_with_hcx007: fake call_hcx로 호출/재시도/실패 배관 검증
# ---------------------------------------------------------------------

def _make_fake_call_hcx(contents):
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


def _sample_tables():
    return [
        {"org_id": "101", "tbl_id": "DT_1J22001", "tbl_nm": "지출목적별 소비자물가지수", "stat_nm": None},
        {"org_id": "343", "tbl_id": "DT_343_2010_S0043", "tbl_nm": "유가증권 순위별 거래", "stat_nm": None},
        {"org_id": "184", "tbl_id": "DT_102006_001", "tbl_nm": "국가채무(D1)", "stat_nm": None},
    ]


def test_resolve_table_with_hcx007_success_single_call():
    fake, calls = _make_fake_call_hcx(["0"])
    result = h1.resolve_table_with_hcx007(_sample_tables(), "주류 물가 상승률", call_hcx_fn=fake)
    _check("정상 응답이면 그 index를 그대로 반환", result == 0, str(result))
    _check("콜은 1번만 발생(재시도 없음)", len(calls) == 1, str(len(calls)))
    _check("HCX-007 모델로 호출함", calls[0]["model_name"] == "HCX-007")


def test_resolve_table_with_hcx007_null_response():
    fake, calls = _make_fake_call_hcx(["null"])
    result = h1.resolve_table_with_hcx007(_sample_tables(), "무관한 claim", call_hcx_fn=fake)
    _check("HCX가 null을 반환하면 None(확신 없음)", result is None)
    _check("콜은 1번만 발생", len(calls) == 1)


def test_resolve_table_with_hcx007_retries_once_on_malformed_response():
    fake, calls = _make_fake_call_hcx(["아무튼 골랐습니다", "2"])
    result = h1.resolve_table_with_hcx007(_sample_tables(), "국가채무 관련 claim", call_hcx_fn=fake)
    _check("1차 응답이 파싱 안 되면 재시도해서 2차 응답으로 index 회수", result == 2, str(result))
    _check("콜은 정확히 2번(1차+재시도 1회)", len(calls) == 2, str(len(calls)))
    _check(
        "재시도 messages의 system 프롬프트에 재시도 안내가 덧붙음",
        "직전 응답은 형식이 올바르지 않았습니다" in calls[1]["messages"][0]["content"],
    )


def test_resolve_table_with_hcx007_raises_after_retry_exhausted():
    fake, calls = _make_fake_call_hcx(["모르겠습니다", "역시 모르겠습니다"])
    try:
        h1.resolve_table_with_hcx007(_sample_tables(), "질의", call_hcx_fn=fake)
        raised = False
    except h1.ResolvedIndexParseError:
        raised = True
    _check("재시도까지 실패하면 예외가 전파됨(조용히 None으로 삼키지 않음)", raised)
    _check("콜은 정확히 2번(1차+재시도 1회, 무한 재시도 아님)", len(calls) == 2, str(len(calls)))


def test_resolve_table_with_hcx007_empty_table_list_skips_call():
    fake, calls = _make_fake_call_hcx(["0"])
    result = h1.resolve_table_with_hcx007([], "질의", call_hcx_fn=fake)
    _check("table_list가 비어 있으면 호출 자체를 생략하고 None", result is None)
    _check("HCX 콜이 아예 안 나감(비용 없음)", len(calls) == 0, str(len(calls)))


def test_resolve_table_with_hcx007_client_error_propagates():
    def _raising_fake(model_name, messages, timeout=30, thinking_effort=None, temperature=None):
        raise h1.HCXClientError("네트워크 오류(재현용 가짜)")

    try:
        h1.resolve_table_with_hcx007(_sample_tables(), "질의", call_hcx_fn=_raising_fake)
        raised = False
    except h1.HCXClientError:
        raised = True
    _check("call_hcx 자체가 실패하면(HCXClientError) 그대로 전파됨", raised)


def test_resolve_table_with_hcx007_out_of_range_index_raises_then_retries():
    fake, calls = _make_fake_call_hcx(["99", "1"])
    result = h1.resolve_table_with_hcx007(_sample_tables(), "질의", call_hcx_fn=fake)
    _check("범위 밖 index는 1차 실패로 처리되고 재시도로 회수됨", result == 1, str(result))
    _check("콜은 정확히 2번", len(calls) == 2, str(len(calls)))


if __name__ == "__main__":
    test_build_messages_lists_all_tables_with_index()
    test_build_messages_omits_optional_context_when_not_given()
    test_build_messages_includes_axis_hints_when_present()
    test_build_messages_handles_missing_axis_hints_key_gracefully()
    test_build_messages_handles_empty_stat_nm_gracefully()
    test_resolve_table_with_hcx007_success_single_call()
    test_resolve_table_with_hcx007_null_response()
    test_resolve_table_with_hcx007_retries_once_on_malformed_response()
    test_resolve_table_with_hcx007_raises_after_retry_exhausted()
    test_resolve_table_with_hcx007_empty_table_list_skips_call()
    test_resolve_table_with_hcx007_client_error_propagates()
    test_resolve_table_with_hcx007_out_of_range_index_raises_then_retries()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

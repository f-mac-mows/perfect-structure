"""[2026-08-28 신규] "목적 불일치(purpose mismatch)" 게이트(Verdict.
UNVERIFIED_PURPOSE_MISMATCH) 배선 검증용 - judgment.py의
_check_purpose_mismatch/judge_claim만 순수 로직으로 테스트한다(실제
kosis_client.get_stat_explanation/HCX-007 호출은 안 함 - local_db_agent.py의
_attach_purpose_check가 채워 넣는 값을 그대로 흉내 낸 ActualEvidence를
손으로 구성해서 넣는다). test_record_claim_wiring.py와 완전히 같은 검증
원칙/스타일.

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 이 파일을
직접 실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_purpose_mismatch_wiring.py

전부 통과하면 "전체 통과" 메시지가, 하나라도 실패하면 AssertionError와 함께
어느 케이스인지 출력됩니다.
"""

from judgment import (
    ActualEvidence,
    Claim,
    Mode,
    SearchLog,
    UnitCategory,
    Verdict,
    judge_claim,
)


def _search_log_resolved():
    return SearchLog(retrieval_status="RESOLVED", confident=True, candidates_tried=["채소류 월평균 구매금액"])


def case_a_mismatch_true_overrides_otherwise_matching_value():
    """[핵심 사례 - 배추가격/DT_114054_112] 표/축 이름 매칭까지 성공해서
    값도 claim과 정확히 일치하지만(6700원 == 6700원), purpose_mismatch=True면
    VERIFIED가 아니라 UNVERIFIED_PURPOSE_MISMATCH여야 한다 - "값이 맞아
    보여도 실제로는 근거로 쓰면 안 된다"는 게 이 게이트의 핵심."""
    claim = Claim(
        raw_sentence="금년 배추 가격이 6700원이 됐다.",
        claimed_value=6700.0,
        claimed_unit="원",
        claimed_period="2026-08",
        unit_category=UnitCategory.MONEY,
    )
    actual = ActualEvidence(
        value=6700.0, unit="원",
        table_org_id="114", table_tbl_id="DT_114054_112", table_nm="채소류 월평균 구매량 및 구매금액",
        purpose_mismatch=True,
        purpose_mismatch_note="이 표는 외식업체의 식재료 사입가를 조사한 것으로, 소비자 소매가와 다르다.",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.UNVERIFIED_PURPOSE_MISMATCH, result.verdict
    assert "외식업체" in result.explanation, result.explanation
    assert result.claimed_value == 6700.0, result.claimed_value
    assert result.actual_value == 6700.0, result.actual_value


def case_b_mismatch_false_falls_through_to_normal_verified():
    """purpose_mismatch=False(명시적으로 검증했고 일치)면 게이트를 통과해서
    기존처럼 일반 허용오차 비교로 VERIFIED가 나와야 한다."""
    claim = Claim(
        raw_sentence="소비자물가지수 기준 배추 가격이 6700원이 됐다.",
        claimed_value=6700.0,
        claimed_unit="원",
        claimed_period="2026-08",
        unit_category=UnitCategory.MONEY,
    )
    actual = ActualEvidence(
        value=6700.0, unit="원",
        table_org_id="101", table_tbl_id="DT_1J22112", table_nm="품목별 소비자물가지수",
        purpose_mismatch=False,
        purpose_mismatch_note="목적이 일치함",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.VERIFIED, result.verdict


def case_c_mismatch_none_unchecked_falls_through_unaffected():
    """[회귀 방지 - 가장 중요] purpose_mismatch가 아예 None이면(검증을
    시도조차 안 한 대부분의 호출부 - kosis_client/hcx_purpose_verify_fn을
    안 넘긴 기존 파이프라인 전부) 이 게이트가 존재하지 않는 것처럼 조용히
    통과해서 기존 로직 그대로 VERIFIED가 나와야 한다 - 이 신규 기능이 기존
    동작을 하나도 안 바꾼다는 걸 확인한다."""
    claim = Claim(
        raw_sentence="15세 이상 취업자는 2909만1000명이다.",
        claimed_value=29091000.0,
        claimed_unit="명",
        claimed_period="2025-06",
        unit_category=UnitCategory.OTHER,
    )
    actual = ActualEvidence(
        value=29091000.0, unit="명",
        table_org_id="101", table_tbl_id="DT_1DA7024S", table_nm="성/연령별 취업자",
        # purpose_mismatch 기본값 None - 검증 자체를 안 한 상황을 흉내냄
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.VERIFIED, result.verdict


def case_d_raw_only_mode_bypasses_purpose_gate():
    """RAW_ONLY 모드는 _check_unverified 다음, _check_purpose_mismatch보다
    먼저 반환된다(judge_claim 우선순위) - purpose_mismatch=True인 claim도
    RAW_ONLY 모드에서는 여전히 원자료를 그대로 보여주는 RAW_ONLY verdict가
    나와야 한다(판정 자체를 안 하는 모드이므로 이 게이트도 적용 안 됨)."""
    claim = Claim(
        raw_sentence="금년 배추 가격이 6700원이 됐다.",
        claimed_value=6700.0,
        claimed_unit="원",
        claimed_period="2026-08",
        unit_category=UnitCategory.MONEY,
    )
    actual = ActualEvidence(
        value=6700.0, unit="원",
        table_org_id="114", table_tbl_id="DT_114054_112", table_nm="채소류 월평균 구매량 및 구매금액",
        purpose_mismatch=True,
        purpose_mismatch_note="목적 불일치(테스트용)",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.RAW_ONLY)
    assert result.verdict == Verdict.RAW_ONLY, result.verdict


def case_e_purpose_mismatch_takes_priority_over_record_claim():
    """[우선순위 확인] "역대 최고/최저" claim이면서 동시에 목적 불일치면,
    _check_purpose_mismatch가 _check_record_claim보다 먼저 실행되므로
    UNVERIFIED_RECORD_CLAIM이 아니라 UNVERIFIED_PURPOSE_MISMATCH가 나와야
    한다 - 목적이 안 맞는 표라면 애초에 "역대 기록"류 대조로 넘어갈 이유가
    없다는 설계 의도를 그대로 검증한다."""
    claim = Claim(
        raw_sentence="배추 가격이 역대 최고치를 기록했다.",
        claimed_value=6700.0,
        claimed_unit="원",
        claimed_period="2026-08",
        unit_category=UnitCategory.MONEY,
    )
    actual = ActualEvidence(
        value=6700.0, unit="원",
        table_org_id="114", table_tbl_id="DT_114054_112", table_nm="채소류 월평균 구매량 및 구매금액",
        purpose_mismatch=True,
        purpose_mismatch_note="목적 불일치(테스트용)",
        # record_* 필드는 일부러 안 채움 - 이 케이스가 record-claim 체크까지
        # 도달하면(잘못된 우선순위) None 값들 때문에 UNVERIFIED_RECORD_CLAIM이
        # 나올 텐데, 그게 아니라 UNVERIFIED_PURPOSE_MISMATCH가 나와야 한다.
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.UNVERIFIED_PURPOSE_MISMATCH, result.verdict


if __name__ == "__main__":
    cases = [
        case_a_mismatch_true_overrides_otherwise_matching_value,
        case_b_mismatch_false_falls_through_to_normal_verified,
        case_c_mismatch_none_unchecked_falls_through_unaffected,
        case_d_raw_only_mode_bypasses_purpose_gate,
        case_e_purpose_mismatch_takes_priority_over_record_claim,
    ]
    for fn in cases:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\n전체 통과: {len(cases)}/{len(cases)}")

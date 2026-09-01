"""[2026-08-24 신규] "역대 최고/최저" claim(UNVERIFIED_RECORD_CLAIM) 배선
검증용 - judgment.py의 _check_record_claim/judge_claim만 순수 로직으로
테스트한다(실제 kosis_warehouse.db 조회는 안 함 - local_db_agent.py의
_attach_record_extremes가 DB에서 채워 넣는 값을 그대로 흉내 낸
ActualEvidence를 손으로 구성해서 넣는다).

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 이 파일을
직접 실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_record_claim_wiring.py

전부 통과하면 "PASS" 메시지가, 하나라도 실패하면 AssertionError와 함께
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
    return SearchLog(retrieval_status="RESOLVED", confident=True, candidates_tried=["합계출산율"])


def case_a_polarity_undetermined():
    """"역대급"처럼 최고/최저 단어가 없으면 방향을 못 정해 declining."""
    claim = Claim(
        raw_sentence="이 회사 매출이 역대급을 기록했다",
        claimed_value=100.0,
        claimed_unit="억원",
        claimed_period="2025-06",
        unit_category=UnitCategory.MONEY,
    )
    actual = ActualEvidence(
        value=100.0, unit="억원",
        table_org_id="101", table_tbl_id="DT_TEST", table_nm="테스트표",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.UNVERIFIED_RECORD_CLAIM, result.verdict
    assert "방향" in result.explanation, result.explanation


def case_b_no_record_data():
    """records 테이블에 이 계열 요약이 없으면(record_max_value=None) declining."""
    claim = Claim(
        raw_sentence="출산율이 역대 최저치를 기록했다",
        claimed_value=0.72,
        claimed_unit="명",
        claimed_period="2025",
        unit_category=UnitCategory.OTHER,
    )
    actual = ActualEvidence(
        value=0.72, unit="명",
        table_org_id="101", table_tbl_id="DT_1B81A17", table_nm="합계출산율",
        # record_* 전부 기본값 None - "아직 적재 안 됨" 상황을 흉내냄
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.UNVERIFIED_RECORD_CLAIM, result.verdict
    assert "records 테이블" in result.explanation or "웨어하우스" in result.explanation, result.explanation


def case_c_period_mismatch_forces_mismatch():
    """진짜 최저 기록은 다른 시점(202312)에 났는데 claim은 이번 달(202506)이
    역대 최저라고 주장 - 값이 우연히 비슷해도 MISMATCH여야 한다."""
    claim = Claim(
        raw_sentence="출산율이 역대 최저치를 기록했다",
        claimed_value=0.72,
        claimed_unit="명",
        claimed_period="202506",
        unit_category=UnitCategory.OTHER,
    )
    actual = ActualEvidence(
        value=0.72, unit="명",
        table_org_id="101", table_tbl_id="DT_1B81A17", table_nm="합계출산율",
        record_min_value=0.70, record_min_period="202312",
        record_period_matches_min=False,
        record_coverage_strt="200001", record_coverage_end="202506",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.MISMATCH, result.verdict
    assert "202312" in result.explanation, result.explanation


def case_d_period_match_falls_through_to_verified():
    """진짜 최저 기록 시점(202506)과 claim 시점이 일치 + 값도 허용오차
    이내로 일치 -> 일반 tolerance 로직을 거쳐 VERIFIED, 설명에 records
    대조 확인 문구가 붙어야 한다."""
    claim = Claim(
        raw_sentence="출산율이 역대 최저치를 기록했다",
        claimed_value=0.72,
        claimed_unit="명",
        claimed_period="202506",
        unit_category=UnitCategory.OTHER,
    )
    actual = ActualEvidence(
        value=0.72, unit="명",
        table_org_id="101", table_tbl_id="DT_1B81A17", table_nm="합계출산율",
        record_min_value=0.72, record_min_period="202506",
        record_period_matches_min=True,
        record_coverage_strt="200001", record_coverage_end="202506",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.VERIFIED, result.verdict
    assert "records 테이블" in result.explanation, result.explanation


def case_e_period_match_but_value_off_is_mismatch():
    """시점은 일치하지만 claim이 주장한 숫자 자체가 실제 값과 크게 다르면
    (역대 여부와 무관하게) 그냥 일반 MISMATCH."""
    claim = Claim(
        raw_sentence="출산율이 역대 최저치를 기록했다",
        claimed_value=0.50,  # 실제(0.72)와 크게 다름
        claimed_unit="명",
        claimed_period="202506",
        unit_category=UnitCategory.OTHER,
    )
    actual = ActualEvidence(
        value=0.72, unit="명",
        table_org_id="101", table_tbl_id="DT_1B81A17", table_nm="합계출산율",
        record_min_value=0.72, record_min_period="202506",
        record_period_matches_min=True,
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.MISMATCH, result.verdict


def case_f_max_polarity_basic_verified():
    """"역대 최고"(max 방향) 기본 케이스도 동일하게 동작하는지 확인."""
    claim = Claim(
        raw_sentence="고용률이 역대 최고치를 기록했다",
        claimed_value=63.6,
        claimed_unit="%",
        claimed_period="202506",
        unit_category=UnitCategory.PERCENT,
    )
    actual = ActualEvidence(
        value=63.6, unit="%",
        table_org_id="101", table_tbl_id="DT_TEST2", table_nm="테스트표2",
        record_max_value=63.6, record_max_period="202506",
        record_period_matches_max=True,
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.VERIFIED, result.verdict
    assert "최댓값" in result.explanation, result.explanation


if __name__ == "__main__":
    cases = [
        case_a_polarity_undetermined,
        case_b_no_record_data,
        case_c_period_mismatch_forces_mismatch,
        case_d_period_match_falls_through_to_verified,
        case_e_period_match_but_value_off_is_mismatch,
        case_f_max_polarity_basic_verified,
    ]
    for fn in cases:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\n전체 통과: {len(cases)}/{len(cases)}")

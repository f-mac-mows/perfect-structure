"""[2026-08-28 신규] 단일 시점(is_comparison=False) change_amount/rate
claim에서 KOSIS 원본 컬럼이 이미 부호 있는 증감값으로 적재된 경우(예:
"자연증감" - 감소는 음수)와 뉴스 claim의 "부호 없는 크기 + 별도 방향 단어"
관례가 충돌해 생기던 허위 MISMATCH 수정을 검증한다.

실측 계기(팀원 110차 DB확장 실측보고, 2026-08-27, §3-4번 유형):
`Aeb3233ab-C019` "자연 감소 9124명" vs 조회값 -9149 - 부호까지 다른 값으로
착각해 18273 차이로 MISMATCH가 났으나, 실제로는 절댓값 기준 25명(0.3%)
차이로 사실상 일치.

test_record_claim_wiring.py/test_purpose_mismatch_wiring.py와 완전히 같은
검증 원칙 - judgment.py의 judge_claim만 순수 로직으로 테스트한다.

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 이 파일을
직접 실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_direction_sign_wiring.py

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
    return SearchLog(retrieval_status="RESOLVED", confident=True, candidates_tried=["월별 인구동향"])


def case_a_real_bug_reproduction_sign_consistent_now_verified():
    """[실측 재현] Aeb3233ab-C019 그대로 - claim은 부호 없는 9124(감소),
    실제 KOSIS 값은 부호 있는 -9149(자연증감 컬럼). 방향이 일치하므로(둘 다
    감소) 절댓값 기준으로 비교해야 하고, 25명(0.3%) 차이는 tolerance 허용
    범위 안이라 VERIFIED가 나와야 한다 - 수정 전에는 9124 vs -9149를 그대로
    비교해 18273 차이로 MISMATCH였다."""
    claim = Claim(
        raw_sentence="자연 감소 9124명을 기록했다",
        claimed_value=9124.0,
        claimed_unit="명",
        claimed_period="2025-06",
        unit_category=UnitCategory.PERSON,
        direction="decrease",
    )
    actual = ActualEvidence(
        value=-9149.0, unit="명",
        table_org_id="101", table_tbl_id="DT_1B8000G", table_nm="월별 인구동향",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.VERIFIED, (result.verdict, result.explanation)
    assert result.actual_value == 9149.0, result.actual_value
    assert result.claimed_value == 9124.0, result.claimed_value


def case_b_sign_inconsistent_is_genuine_mismatch():
    """claim은 "감소"라고 주장하는데 실제 값이 양수(=증가)면, 절댓값으로
    뭉개면 안 되고 방향 반전 자체로 즉시 MISMATCH여야 한다 - 크기가 아무리
    가까워도 방향이 반대면 안전장치가 걸려야 한다."""
    claim = Claim(
        raw_sentence="자연 감소 9124명을 기록했다",
        claimed_value=9124.0,
        claimed_unit="명",
        claimed_period="2025-06",
        unit_category=UnitCategory.PERSON,
        direction="decrease",
    )
    actual = ActualEvidence(
        value=9149.0, unit="명",  # 실제로는 양수(증가) - claim과 방향이 반대
        table_org_id="101", table_tbl_id="DT_1B8000G", table_nm="월별 인구동향",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.MISMATCH, (result.verdict, result.explanation)
    assert "방향" in result.explanation, result.explanation


def case_c_no_direction_leaves_existing_behavior_unchanged():
    """[회귀 방지] claim.direction이 없으면(대부분의 level claim) 이 신규
    정규화가 아예 개입하지 않는다 - 부호 있는 값과 그냥 비교돼서(기존 동작)
    tolerance를 크게 벗어나므로 MISMATCH가 그대로 나와야 한다."""
    claim = Claim(
        raw_sentence="인구 순유입이 9124명이다",
        claimed_value=9124.0,
        claimed_unit="명",
        claimed_period="2025-06",
        unit_category=UnitCategory.PERSON,
        direction=None,
    )
    actual = ActualEvidence(
        value=-9149.0, unit="명",
        table_org_id="101", table_tbl_id="DT_1B8000G", table_nm="월별 인구동향",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    assert result.verdict == Verdict.MISMATCH, (result.verdict, result.explanation)
    assert result.actual_value == -9149.0, result.actual_value  # 정규화 안 됨 - 원본 부호 그대로


def case_d_no_change_direction_is_not_touched_by_this_gate():
    """direction="no_change"는 increase/decrease가 아니므로 이 신규
    정규화가 개입하지 않는다(기존 hedge/tolerance 로직에 그대로 맡김) -
    이 게이트가 "no_change" claim의 기존 동작(README 3장 E)을 깨지 않는지
    확인한다."""
    claim = Claim(
        raw_sentence="실업률이 동결됐다",
        claimed_value=0.0,
        claimed_unit="%p",
        claimed_period="2025-06",
        unit_category=UnitCategory.PERCENT,
        direction="no_change",
    )
    actual = ActualEvidence(
        value=0.05, unit="%p",
        table_org_id="101", table_tbl_id="DT_TEST", table_nm="테스트표",
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    # 부호 반전 게이트가 끼어들지 않았다는 것만 확인(정규화로 인한 크래시나
    # 예기치 못한 MISMATCH 강제가 없어야 함) - 최종 판정은 기존 tolerance
    # 로직(approx 완화 등)에 달려 있으므로 여기서는 verdict를 못박지 않는다.
    assert result.verdict in (Verdict.VERIFIED, Verdict.MISMATCH), result.verdict
    assert result.claimed_value == 0.0, result.claimed_value  # abs() 정규화가 안 건드림


def case_e_is_comparison_path_untouched_by_this_gate():
    """[격리 확인] is_comparison=True 경로는 이미 _resolve_comparison_
    evidence가 자체적으로 방향을 계산하므로, 이 신규 게이트(단일 시점 전용)
    는 아예 개입하면 안 된다 - 게이트가 `not actual.is_comparison` 조건으로
    격리돼 있는지 확인."""
    from judgment import EvidencePoint

    claim = Claim(
        raw_sentence="인구가 9124명 감소했다",
        claimed_value=9124.0,
        claimed_unit="명",
        claimed_period="2025-06",
        unit_category=UnitCategory.PERSON,
        direction="decrease",
    )
    actual = ActualEvidence(
        table_org_id="101", table_tbl_id="DT_1B8000G", table_nm="월별 인구동향",
        is_comparison=True,
        values=[
            EvidencePoint(period="2024-06", value=1000000.0, unit="명"),
            EvidencePoint(period="2025-06", value=990876.0, unit="명"),  # 실제 diff = -9124
        ],
    )
    result = judge_claim(claim, actual, _search_log_resolved(), mode=Mode.TOLERANCE)
    # _resolve_comparison_evidence가 이미 abs(diff)로 비교하므로 정상적으로
    # VERIFIED가 나와야 한다(diff=-9124, claimed=9124, 방향도 decrease로 일치).
    assert result.verdict == Verdict.VERIFIED, (result.verdict, result.explanation)


if __name__ == "__main__":
    cases = [
        case_a_real_bug_reproduction_sign_consistent_now_verified,
        case_b_sign_inconsistent_is_genuine_mismatch,
        case_c_no_direction_leaves_existing_behavior_unchanged,
        case_d_no_change_direction_is_not_touched_by_this_gate,
        case_e_is_comparison_path_untouched_by_this_gate,
    ]
    for fn in cases:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\n전체 통과: {len(cases)}/{len(cases)}")

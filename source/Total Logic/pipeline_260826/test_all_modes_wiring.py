"""[2026-08-24 신규] adapter.run_search_and_judge/run_pipeline_from_files의
`all_modes=True` 배선 검증 - 실제 kosis_warehouse.db 없이, agent 자체를
가짜(FakeAgent)로 주입해서 순수 배선/출력 구조만 확인한다(agent.
process_claim_group_keywords(claims, keywords_by_claim_id, category_hint)
-> Dict[claim_id, evidence_payload] 계약만 지키면 되므로, 실제 검색
로직과 무관하게 테스트할 수 있다 - run_search_and_judge 자체 docstring이
언급하는 monkeypatch 패턴).

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 직접
실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_all_modes_wiring.py
"""

from adapter import run_search_and_judge


class FakeAgent:
    """claim_id별로 미리 정해둔 evidence_payload를 그대로 돌려주는 가짜
    agent - 검색(4번 역할)을 건너뛰고 판정(5번 역할)/배선만 검증한다."""

    def __init__(self, payload_by_claim_id):
        self._payload_by_claim_id = payload_by_claim_id

    def process_claim_group_keywords(self, claims, keywords_by_claim_id, category_hint=None):
        return dict(self._payload_by_claim_id)


def case_all_modes_true_returns_nested_shape():
    """query_status=success + claim 값과 정확히 일치 -> 세 mode 모두
    VERIFIED가 나와야 한다(diff=0은 strict 허용오차 안에서도 항상 통과).
    출력 구조 자체가 {"modes": {"strict":..., "tolerance":..., "raw_only":...},
    "evidence": {...}}인지도 함께 확인한다."""
    claims = [{
        "claim_id": "T-001", "claim": "테스트 claim - 값 100 일치",
        "value": "100", "unit": "명", "period": "2025-06",
    }]
    agent = FakeAgent({
        "T-001": {
            "org_id": "101", "table_id": "DT_TEST", "table_name": "테스트표",
            "normalized_value": 100.0, "normalized_unit": "명",
            "query_status": "success",
            "derivation": {"used": False, "note": None},
            "confident": True,
        }
    })
    results = run_search_and_judge(claims, {}, agent=agent, all_modes=True)
    assert len(results) == 1, results
    r = results[0]
    assert "modes" in r and "verdict" not in r, r
    assert set(r["modes"].keys()) == {"strict", "tolerance", "raw_only"}, r["modes"].keys()
    assert r["modes"]["strict"]["verdict"] == "VERIFIED", r["modes"]["strict"]
    assert r["modes"]["tolerance"]["verdict"] == "VERIFIED", r["modes"]["tolerance"]
    assert r["modes"]["raw_only"]["verdict"] == "RAW_ONLY", r["modes"]["raw_only"]
    assert r["evidence"]["table_tbl_id"] == "DT_TEST", r["evidence"]
    # raw_only는 판정을 안 하므로 hedge_type이 없어야 한다(None).
    assert r["modes"]["raw_only"]["hedge_type"] is None, r["modes"]["raw_only"]


def case_all_modes_false_keeps_flat_shape():
    """all_modes=False(기본값)면 기존처럼 평평한 구조 그대로여야 한다 -
    하위 호환 확인."""
    claims = [{
        "claim_id": "T-002", "claim": "테스트 claim - 하위 호환",
        "value": "100", "unit": "명", "period": "2025-06",
    }]
    agent = FakeAgent({
        "T-002": {
            "org_id": "101", "table_id": "DT_TEST", "table_name": "테스트표",
            "normalized_value": 100.0, "normalized_unit": "명",
            "query_status": "success",
            "derivation": {"used": False, "note": None},
            "confident": True,
        }
    })
    results = run_search_and_judge(claims, {}, agent=agent)  # all_modes 기본값
    r = results[0]
    assert "modes" not in r, r
    assert r["verdict"] == "VERIFIED", r
    assert r["mode"] == "tolerance", r


def case_not_eligible_stays_flat_even_with_all_modes():
    """NOT_ELIGIBLE은 all_modes=True여도 여전히 평평한 구조여야 한다."""
    claims = [{
        "claim_id": "T-003", "claim": "테스트 claim - 대상 아님",
        "value": "100", "unit": "명", "period": "2025-06",
        "exclusion_code": "FORECAST",
    }]
    agent = FakeAgent({"T-003": {"query_status": "not_eligible"}})
    results = run_search_and_judge(claims, {}, agent=agent, all_modes=True)
    r = results[0]
    assert "modes" not in r, r
    assert r["verdict"] == "NOT_ELIGIBLE", r
    assert "FORECAST" in r["explanation"], r


def case_error_stays_flat_even_with_all_modes():
    """claim에 value가 아예 없어 build_inputs가 예외를 내는 경우 -
    all_modes=True여도 ERROR는 평평한 구조여야 한다."""
    claims = [{"claim_id": "T-004", "claim": "value 필드 자체가 없음"}]
    agent = FakeAgent({
        "T-004": {
            "org_id": "101", "table_id": "DT_TEST", "table_name": "테스트표",
            "normalized_value": 100.0, "normalized_unit": "명",
            "query_status": "success",
        }
    })
    results = run_search_and_judge(claims, {}, agent=agent, all_modes=True)
    r = results[0]
    assert "modes" not in r, r
    assert r["verdict"] == "ERROR", r


if __name__ == "__main__":
    cases = [
        case_all_modes_true_returns_nested_shape,
        case_all_modes_false_keeps_flat_shape,
        case_not_eligible_stays_flat_even_with_all_modes,
        case_error_stays_flat_even_with_all_modes,
    ]
    for fn in cases:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\n전체 통과: {len(cases)}/{len(cases)}")

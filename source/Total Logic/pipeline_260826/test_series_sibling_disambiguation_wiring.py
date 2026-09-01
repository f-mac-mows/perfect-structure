"""[2026-08-28 신규 - 포팅] local_db_agent._resolve_series_siblings(구
kosis_agent.py _disambiguate_table_candidates를 로컬 웨어하우스 조회로
이식)의 우선순위(명시어 > 실값 대조 > 모호하면 원래대로)를 검증한다.
test_record_claim_wiring.py/test_purpose_mismatch_wiring.py와 같은 원칙
(관심사 하나만 순수하게 검증)이지만, 이 함수는 facts/tables_registry를
직접 조회하므로 judgment.py처럼 순수 dataclass만으로는 테스트할 수 없다 -
in-memory DB 연결을 실제로 열어서 검증한다.

실측 사례(전산업생산지수 = 101/DT_1JH20201 원지수 / 101/DT_1JH20202
계절조정지수, README 2.4절, 2026-08-05 KOSIS 통합검색으로 확인됨)를 그대로
재사용한다 - 형제 판정 로직 자체는 test_series_siblings.py가 이미
검증했으므로, 이 파일은 "disambiguation 우선순위"만 본다.

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 이 파일을
직접 실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_series_sibling_disambiguation_wiring.py
"""

import sys

import kosis_warehouse as wh
import local_db_agent as lda

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def _seed_industrial_production_index_siblings(conn):
    """test_series_siblings.py와 완전히 같은 픽스처(실측 사례 재사용)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_1JH20201', '전산업생산지수(원지수)')"
    )
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_1JH20202', '전산업생산지수(계절조정지수)')"
    )
    for tbl_id in ("DT_1JH20201", "DT_1JH20202"):
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            f"VALUES ('101', '{tbl_id}', 'ITEM', 0, '항목', 'T001', '전산업생산지수', NULL, NULL)"
        )
    conn.execute(
        "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, value, unit) "
        "VALUES ('101', 'DT_1JH20201', 'T001', '202506', 'M', 108.2, '2020=100')"
    )
    conn.execute(
        "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, value, unit) "
        "VALUES ('101', 'DT_1JH20202', 'T001', '202506', 'M', 110.5, '2020=100')"
    )
    conn.commit()


def test_explicit_qualifier_switches_to_correct_sibling():
    """Stage 1이 "원지수"(DT_1JH20201)로 잘못 확정했어도, claim 원문에
    "계절조정"이 명시돼 있으면 그 형제 표(DT_1JH20202, 값 110.5)로
    갈아타야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    claim = {"claim_id": "TEST-SIB-1", "value_num": None, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_1JH20201", "전산업생산지수(원지수)",
        "T001", {}, "202506", claim,
        "전산업생산지수(계절조정)가 2025년 6월 110을 넘어섰다.",
    )
    _check("switched=True", result.get("switched") is True, str(result))
    _check("계절조정지수 표로 갈아탐", result.get("tbl_id") == "DT_1JH20202", str(result))
    _check("값도 110.5로 갱신됨", result.get("value") == 110.5, str(result))
    _check("사유에 명시어가 남음", "계절조정" in (result.get("note") or ""), str(result))
    conn.close()


def test_no_qualifier_switches_via_value_comparison():
    """명시어가 없어도, claim의 claimed_value가 다른 형제 표의 값과 훨씬
    가까우면(원지수 108.2보다 계절조정지수 110.5와 훨씬 가까움) 실값
    대조로 올바른 표로 갈아타야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    claim = {"claim_id": "TEST-SIB-2", "value_num": 110.4, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_1JH20201", "전산업생산지수(원지수)",
        "T001", {}, "202506", claim,
        "전산업생산지수가 110을 넘어섰다.",  # 명시어 없음
    )
    _check("switched=True", result.get("switched") is True, str(result))
    _check("계절조정지수 표로 갈아탐(값이 더 가까움)", result.get("tbl_id") == "DT_1JH20202", str(result))
    _check("값도 110.5로 갱신됨", result.get("value") == 110.5, str(result))
    conn.close()


def test_ambiguous_value_match_does_not_switch():
    """claim 값이 두 형제 표 값의 정확히 중간이라 어느 쪽이 더 맞는지
    구분이 안 되면(동점), 추측하지 않고 원래 확정된 표를 그대로 둔다."""
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    midpoint = (108.2 + 110.5) / 2  # 109.35 - 두 표 모두 상대오차 동일
    claim = {"claim_id": "TEST-SIB-3", "value_num": midpoint, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_1JH20201", "전산업생산지수(원지수)",
        "T001", {}, "202506", claim,
        "전산업생산지수가 발표됐다.",
    )
    _check("동점이면 switched=False(추측 안 함)", result.get("switched") is False, str(result))
    conn.close()


def test_value_too_far_from_all_siblings_does_not_switch():
    """claim 값이 모든 형제 표 값과 5%보다 멀면(근거가 약함), 갈아타지
    않는다 - "더 잘 맞는 근거가 있을 때만" 갈아탄다는 원칙."""
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    claim = {"claim_id": "TEST-SIB-4", "value_num": 200.0, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_1JH20201", "전산업생산지수(원지수)",
        "T001", {}, "202506", claim,
        "전산업생산지수가 발표됐다.",
    )
    _check("근거 없으면 switched=False", result.get("switched") is False, str(result))
    conn.close()


def test_no_siblings_returns_switched_false():
    """형제 표가 아예 없으면(자기 자신뿐) 즉시 switched=False - 값 조회
    조차 시도하지 않는다."""
    conn = wh.get_connection(":memory:")
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_LONELY', '성/연령별 취업자')"
    )
    conn.commit()
    claim = {"claim_id": "TEST-SIB-5", "value_num": 100.0, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_LONELY", "성/연령별 취업자",
        "T001", {}, "202506", claim, "취업자가 늘었다.",
    )
    _check("형제 없으면 switched=False", result.get("switched") is False, str(result))
    conn.close()


def test_qualifier_already_matches_confirmed_table_no_switch():
    """claim이 이미 확정된 표와 같은 계열을 명시하면(예: 확정된 표가
    "계절조정"인데 원문도 "계절조정"), 갈아탈 필요가 없으므로 switched=
    False(불필요한 재조회/재확정 없음)."""
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    claim = {"claim_id": "TEST-SIB-6", "value_num": None, "unit": None, "period": "2025-06"}
    result = lda._resolve_series_siblings(
        conn, "101", "DT_1JH20202", "전산업생산지수(계절조정지수)",
        "T001", {}, "202506", claim,
        "전산업생산지수(계절조정)가 110을 넘어섰다.",
    )
    _check("이미 맞는 표면 switched=False", result.get("switched") is False, str(result))
    conn.close()


if __name__ == "__main__":
    test_explicit_qualifier_switches_to_correct_sibling()
    test_no_qualifier_switches_via_value_comparison()
    test_ambiguous_value_match_does_not_switch()
    test_value_too_far_from_all_siblings_does_not_switch()
    test_no_siblings_returns_switched_false()
    test_qualifier_already_matches_confirmed_table_no_switch()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

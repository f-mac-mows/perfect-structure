"""[2026-08-28 신규 - 포팅] "동명표"(원지수/계절조정 등, 같은 조사가 계열별로
별도 TBL_ID를 쓰는 경우) 판정 로직(kosis_local_search.py의
_series_stripped_tbl_nm/detect_series_qualifier/find_sibling_tables/
fetch_cell_value)을 검증한다.

구 아키텍처(kosis_agent.py, `backup/20260815_kosis_refactor/kosis_agent.py`)
에서 2026-08-05/08-10에 실제 KOSIS 데이터로 이미 검증된 설계를 로컬
웨어하우스 조회로 그대로 옮긴 것이다 - 이 테스트는 그 실측 확인된 사례
(전산업생산지수 = 101/DT_1JH20201(원지수) / 101/DT_1JH20202(계절조정지수))
를 그대로 재사용한다(CLAUDE.md "실측 우선 원칙" - 검증된 사실 재사용,
새로 추측하지 않음).

CLAUDE.md "샌드박스에서 직접 실행 금지" 규칙에 따라 이 세션에서는 이 파일을
직접 실행하지 않았다 - 로컬에서 아래 명령으로 실행해서 결과를 알려주세요:

    python3 test_series_siblings.py

전부 통과하면 "전체 PASS" 메시지가, 하나라도 실패하면 실패 목록이 출력됩니다.
"""

import sys

import kosis_local_search as kls
import kosis_warehouse as wh

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


# ---------------------------------------------------------------------
# ① _series_stripped_tbl_nm / detect_series_qualifier
# ---------------------------------------------------------------------

def test_strips_known_series_suffixes():
    _check(
        "'전산업생산지수(원지수)' -> '전산업생산지수'",
        kls._series_stripped_tbl_nm("전산업생산지수(원지수)") == "전산업생산지수",
    )
    _check(
        "'전산업생산지수(계절조정지수)' -> '전산업생산지수'",
        kls._series_stripped_tbl_nm("전산업생산지수(계절조정지수)") == "전산업생산지수",
    )
    _check(
        "접미사 없는 표제목은 그대로",
        kls._series_stripped_tbl_nm("성/연령별 취업자") == "성/연령별 취업자",
    )
    _check("None 입력은 None 그대로", kls._series_stripped_tbl_nm(None) is None)


def test_does_not_strip_unknown_parenthetical_suffix():
    """[H-3 실측 재현 - 구 세션에서 실제로 문제가 됐던 케이스] "(1인당
    월평균)"은 계열 접미사 사전에 없으므로 그대로 남아야 한다 - 안 그러면
    "임금 동향"과 "종사상지위별 임금 동향"처럼 축 구조 자체가 다른 표들이
    형제로 잘못 묶인다."""
    _check(
        "'임금 동향(1인당 월평균)'은 안 떼어짐(사전에 없는 접미사)",
        kls._series_stripped_tbl_nm("임금 동향(1인당 월평균)") == "임금 동향(1인당 월평균)",
    )


def test_detect_series_qualifier_finds_literal_word():
    _check(
        "'계절조정'이 원문에 있으면 그대로 반환",
        kls.detect_series_qualifier("전산업생산지수(계절조정)가 110을 넘어섰다") == "계절조정",
    )
    _check(
        "명시어 없으면 None",
        kls.detect_series_qualifier("전산업생산지수가 110을 넘어섰다") is None,
    )
    _check("빈 문자열/None도 안전하게 None", kls.detect_series_qualifier("") is None)


def test_detect_series_qualifier_prefers_compound_word():
    """'계절조정지수'라는 복합어가 원문에 있으면, 그 안에 부분 문자열로
    포함될 수 있는 '계절조정'이 아니라 '계절조정지수' 자체가 반환돼야
    한다(_SERIES_QUALIFIER_WORDS 순서로 이미 보장됨 - 리스트에서 복합어가
    먼저 검사됨)."""
    qualifier = kls.detect_series_qualifier("전산업생산지수(계절조정지수)가 발표됐다")
    _check("복합어 '계절조정지수'가 그대로 반환됨", qualifier == "계절조정지수", qualifier)


# ---------------------------------------------------------------------
# ② find_sibling_tables / fetch_cell_value - 실측 사례(전산업생산지수) 그대로 재사용
# ---------------------------------------------------------------------

def _seed_industrial_production_index_siblings(conn):
    """[실측 그대로 재사용] 101/DT_1JH20201(원지수) / 101/DT_1JH20202
    (계절조정지수) - 2026-08-05 KOSIS 통합검색으로 실제 확인된 형제 표
    쌍(README 2.4절). 같은 itm_id/axis_codes 구조를 공유한다고 가정하고
    (진짜 형제 표의 전제) 서로 다른 값을 심는다 - 원지수 108.2, 계절조정
    지수 110.5(예시 값, 실측 수치 아님 - 형제 판정/값 대조 로직만 검증
    하는 합성 데이터)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_1JH20201', '전산업생산지수(원지수)')"
    )
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_1JH20202', '전산업생산지수(계절조정지수)')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('101', 'DT_1JH20201', 'ITEM', 0, '항목', 'T001', '전산업생산지수', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('101', 'DT_1JH20202', 'ITEM', 0, '항목', 'T001', '전산업생산지수', NULL, NULL)"
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


def test_find_sibling_tables_groups_series_suffix_pair():
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    siblings = kls.find_sibling_tables(conn, "101", "DT_1JH20201", "전산업생산지수(원지수)")
    ids = {(s["org_id"], s["tbl_id"]) for s in siblings}
    _check(
        "형제 표 둘 다(자기 자신 포함) 반환됨",
        ids == {("101", "DT_1JH20201"), ("101", "DT_1JH20202")},
        str(ids),
    )
    conn.close()


def test_find_sibling_tables_returns_only_self_when_no_siblings():
    conn = wh.get_connection(":memory:")
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES "
        "('101', 'DT_LONELY', '성/연령별 취업자')"
    )
    conn.commit()
    siblings = kls.find_sibling_tables(conn, "101", "DT_LONELY", "성/연령별 취업자")
    _check("형제 없으면 자기 자신뿐(길이 1)", len(siblings) == 1, str(siblings))
    conn.close()


def test_fetch_cell_value_reads_correct_sibling_values():
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    raw = kls.fetch_cell_value(conn, "101", "DT_1JH20201", "T001", {}, "202506")
    seasonal = kls.fetch_cell_value(conn, "101", "DT_1JH20202", "T001", {}, "202506")
    _check("원지수 값 108.2 조회됨", raw == {"value": 108.2, "unit": "2020=100"}, str(raw))
    _check("계절조정지수 값 110.5 조회됨", seasonal == {"value": 110.5, "unit": "2020=100"}, str(seasonal))
    conn.close()


def test_fetch_cell_value_returns_none_when_missing():
    conn = wh.get_connection(":memory:")
    _seed_industrial_production_index_siblings(conn)
    missing = kls.fetch_cell_value(conn, "101", "DT_1JH20201", "T001", {}, "999912")
    _check("없는 시점은 None", missing is None)
    conn.close()


if __name__ == "__main__":
    test_strips_known_series_suffixes()
    test_does_not_strip_unknown_parenthetical_suffix()
    test_detect_series_qualifier_finds_literal_word()
    test_detect_series_qualifier_prefers_compound_word()
    test_find_sibling_tables_groups_series_suffix_pair()
    test_find_sibling_tables_returns_only_self_when_no_siblings()
    test_fetch_cell_value_reads_correct_sibling_values()
    test_fetch_cell_value_returns_none_when_missing()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

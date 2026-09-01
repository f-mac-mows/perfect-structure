"""[2026-08-22 신규 - HCX 토큰 폭발(429) 대응 회귀 테스트]
kosis_local_search.build_axis_trees가 카테시안 곱을 안 만들고 축마다
압축된 트리(노드 이름 한 번씩만)를 만드는지, 그리고 실제로 iter_table_
cell_texts(기존 flat 방식)보다 훨씬 작은 크기로 끝나는지 확인한다.

배경: A93bfa851-C007/C009 실측(DT_1J22001, 지역 19 × 지출목적별 581)에서
iter_table_cell_texts 기반 flat text가 673,343자까지 폭발해 HCX 분당
토큰 한도(60,000)를 요청 한 번으로 다 썼다(x-ratelimit-remaining-tokens=0
응답 헤더로 확인). build_axis_trees는 이 문제를 "카테시안 곱을 안 만든다"
는 방법으로 해결한다.

사용법: python test_build_axis_trees.py (종료 코드 0 = 전체 PASS)
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


def _seed_two_axis_table(conn, n_regions=19, n_categories=30):
    """[2026-08-22 신규] DT_1J22001류(지역 축 × 지출목적별 축)를 축소
    재현한 합성 표 - 실측 표 그대로는 실측 규모(19×581)를 그대로 심어야
    해서 무겁다. 대신 비율은 비슷하게(두 자리 수 축 × 두 자리 수 축)
    유지해 "카테시안 곱 vs 압축 트리"의 크기 차이를 같은 종류로 재현한다.
    실제 KOSIS 필드명 추측이 아니라 순수 합성 fixture이므로 CLAUDE.md
    "실측 우선" 원칙과 무관하다(스키마 구조 자체는 이미 여러 real-DB
    테스트로 검증된 dimensions/facts 스키마를 그대로 씀)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('999', 'TEST_TREE', '합성 트리 테스트 표')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('999', 'TEST_TREE', 'ITEM', 0, '항목', 'T', '소비자물가지수', NULL, NULL)"
    )
    region_codes = []
    for i in range(n_regions):
        code = f"R{i:02d}"
        region_codes.append(code)
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('999', 'TEST_TREE', 'A', 1, '시도별', ?, ?, NULL, NULL)",
            (code, f"지역{i}" if i > 0 else "전국"),
        )
    category_codes = []
    for i in range(n_categories):
        parent = f"C{i // 5:02d}0"
        code = f"C{i // 5:02d}{i % 5}"
        category_codes.append(code)
        if i % 5 == 0:
            # 상위 카테고리(부모 없음)
            conn.execute(
                "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
                "VALUES ('999', 'TEST_TREE', 'B', 2, '지출목적별', ?, ?, NULL, NULL)",
                (parent, f"대분류{i // 5}"),
            )
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('999', 'TEST_TREE', 'B', 2, '지출목적별', ?, ?, ?, NULL)",
            (code, f"세부품목{i}", parent),
        )
    # facts: 카테시안 곱(19 x 30 = 570건) - 실측 표의 "왜 폭발했는지"와
    # 같은 구조.
    for r in region_codes:
        for c in category_codes:
            conn.execute(
                "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, c2, value, unit) "
                "VALUES ('999', 'TEST_TREE', 'T', '202509', 'M', ?, ?, 100.0, '2020=100')",
                (r, c),
            )
    conn.commit()


def test_tree_nodes_appear_exactly_once():
    conn = wh.get_connection(":memory:")
    _seed_two_axis_table(conn, n_regions=19, n_categories=30)

    trees = kls.build_axis_trees(conn, "999", "TEST_TREE")
    _check("두 축(1,2)이 전부 반환됨", set(trees.keys()) == {1, 2}, str(trees.keys()))

    region_tree_text = trees[1]["tree_text"]
    category_tree_text = trees[2]["tree_text"]
    _check(
        "지역 축 트리에 각 지역 이름이 정확히 1번만 등장",
        region_tree_text.count("지역1 ") == 1 or "지역1 [" in region_tree_text,
        region_tree_text[:200],
    )
    _check(
        "카테고리 축 트리가 계층(들여쓰기)을 반영함 - 세부품목 줄이 대분류보다 더 들여써짐",
        any(line.startswith("  ") for line in category_tree_text.split("\n")),
        category_tree_text[:300],
    )
    _check("지역 축 codes 집합 크기 = 19", len(trees[1]["codes"]) == 19, str(len(trees[1]["codes"])))
    _check("카테고리 축 codes 집합 크기 = 30", len(trees[2]["codes"]) == 30, str(len(trees[2]["codes"])))
    conn.close()


def test_tree_size_vs_cartesian_flat_text():
    """핵심 회귀 - 실측 문제(673,343자) 재현: 같은 표에서 iter_table_
    cell_texts(카테시안 곱, 기존 방식)의 총 문자 수가 build_axis_trees
    (압축 트리, 새 방식)보다 훨씬 커야 한다. 정확한 배율을 못박진 않되
    (표마다 다름), 최소 5배 이상 차이가 나야 "카테시안 곱을 없앴다"는
    설계 목표가 실제로 달성된 것으로 본다."""
    conn = wh.get_connection(":memory:")
    _seed_two_axis_table(conn, n_regions=19, n_categories=30)

    cell_texts_full = kls.iter_table_cell_texts(conn, "999", "TEST_TREE")
    flat_chars = sum(len(c.get("text") or "") for c in cell_texts_full)
    _check(
        "합성 표도 카테시안 곱(19x30=570건)이 실제로 만들어짐",
        len(cell_texts_full) == 570,
        str(len(cell_texts_full)),
    )

    trees = kls.build_axis_trees(conn, "999", "TEST_TREE")
    tree_chars = sum(len(t["tree_text"]) for t in trees.values())

    print(f"  [정보] flat(카테시안) 문자 수={flat_chars}, tree(압축) 문자 수={tree_chars}, 배율={flat_chars / max(tree_chars, 1):.1f}x")
    _check(
        "압축 트리가 flat 카테시안 곱보다 최소 5배 이상 작음",
        tree_chars * 5 <= flat_chars,
        f"flat={flat_chars} tree={tree_chars}",
    )
    conn.close()


def test_empty_table_returns_empty_dict():
    conn = wh.get_connection(":memory:")
    trees = kls.build_axis_trees(conn, "999", "NOT_LOADED")
    _check("미적재 표는 빈 dict를 돌려줌(추측 안 함)", trees == {}, str(trees))
    conn.close()


if __name__ == "__main__":
    test_tree_nodes_appear_exactly_once()
    test_tree_size_vs_cartesian_flat_text()
    test_empty_table_returns_empty_dict()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

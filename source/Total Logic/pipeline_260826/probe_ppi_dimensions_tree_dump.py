"""[2026-08-22 신규 - 실측 진단] 방금 probe_ppi_item_level_vs_basic_
classification_stage1.py 결과가 예상과 정반대로 나왔다 - 실 kosis_
table_info로 확인했던 건 "016(품목별)이 014(기본분류)보다 더 깊다(리프가
개별 품목)"였는데, 로컬 DB의 max_depth는 016=3, 014=5로 거꾸로 나왔다.

이 스크립트는 아무것도 추측하지 않고 로컬 DB(읽기 전용)에 실제로 뭐가
적재돼 있는지를 그대로 덤프한다 - depth 계산 로직이 잘못됐는지, 아니면
로컬 적재 자체가 실제 KOSIS 구조와 다른지(예: parent_code 체인이 깨져
있거나, obj_id='G'가 여러 개의 서로 다른 축을 한데 묶고 있거나)를
구분하기 위함.

사용법(로컬에서): python3 probe_ppi_dimensions_tree_dump.py
"""

import sqlite3

TARGETS = [("301", "DT_404Y016", "품목별"), ("301", "DT_404Y014", "기본분류")]


def main() -> None:
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        for org_id, tbl_id, label in TARGETS:
            print("=" * 70)
            print(f"[{tbl_id}] {label}")
            rows = conn.execute(
                "SELECT obj_id, axis_position, axis_label, code, name, parent_code "
                "FROM dimensions WHERE org_id=? AND tbl_id=? AND obj_id != 'ITEM' "
                "ORDER BY obj_id, axis_position",
                (org_id, tbl_id),
            ).fetchall()
            print(f"  총 행 수(ITEM 제외): {len(rows)}")

            obj_ids = sorted({r[0] for r in rows})
            print(f"  obj_id 종류: {obj_ids}")
            axis_positions = sorted({r[1] for r in rows})
            print(f"  axis_position 종류: {axis_positions}")

            for obj_id in obj_ids:
                sub = [r for r in rows if r[0] == obj_id]
                codes = {r[3] for r in sub if r[3]}
                # 부모 코드가 이 obj_id 안에 실제로 존재하는지 확인 -
                # 존재하지 않으면 "끊어진 체인"이라 뿌리로 잘못 취급될 수 있음.
                broken_parent = [
                    (r[3], r[5]) for r in sub
                    if r[5] is not None and r[5] not in codes
                ]
                roots = [r for r in sub if r[5] is None]
                print(f"  - obj_id={obj_id}: 행 {len(sub)}개, 뿌리(parent_code IS NULL) {len(roots)}개")
                if broken_parent:
                    print(f"    [주의] parent_code가 같은 obj_id 안에 없는 행 {len(broken_parent)}개(끊어진 체인 후보): {broken_parent[:10]}")
                for r in roots:
                    print(f"    루트: code={r[3]!r} name={r[4]!r} axis_label={r[2]!r}")

            # 실제 parent_code 체인을 몇 단계까지 볼 수 있는지 레벨별로 이름 샘플 출력.
            by_code = {r[3]: r for r in rows if r[3]}
            children_of = {}
            for r in rows:
                key = r[5] if r[5] in by_code else None
                children_of.setdefault(key, []).append(r)

            level = 0
            current = children_of.get(None, [])
            while current and level < 10:
                names = [r[4] for r in current]
                print(f"  레벨 {level} ({len(current)}개): {names[:8]}" + (" ..." if len(names) > 8 else ""))
                nxt = []
                for r in current:
                    nxt.extend(children_of.get(r[3], []))
                current = nxt
                level += 1
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()

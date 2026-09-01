"""[2026-08-22 신규, max_depth 실측 반증 후 leaf_samples로 갱신 - Task #1
실측 검증, 로컬 실행용] PPI 품목별(DT_404Y016)/기본분류(DT_404Y014)
구분 불가 문제에 추가한 leaf_samples 힌트(kosis_local_search.
_axis_leaf_samples, hcx_stage1_resolver.py)가 실제 HCX-007 호출에서 두
표를 구분하는 데 도움이 되는지 검증한다.

## 왜 이 둘이 특별히 어려운 쌍인가(README "PPI 3종 axis_hints 붕괴" 참고)

axis_hints는 최상위(또는 최상위가 "총지수" 하나뿐이면 그 자식) 레벨만
샘플링한다. 실측 확인 결과 016/014는 그 자식 레벨의 이름까지도 완전히
동일하다("농림수산품"/"광산품"/"공산품"/"전력가스수도및폐기물"/"서비스").
즉 axis_hints의 값 목록만으로는 두 표가 글자 그대로 겹쳐 보인다 - 이건
이번에 새로 심은 스트레스 세트가 겨냥한 문제가 아니라 애초부터 있던
별개의 어려운 쌍으로, 지금까지 미해결로 기록만 돼 있었다.

**1차 시도(max_depth, 실측으로 반증됨)**: "분류 트리가 깊을수록
세분화됐다"는 일반 원칙으로 트리 깊이를 계산해 프롬프트에 얹었으나,
사용자가 로컬 DB를 직접 덤프해보니 정반대였다 - 016은 깊이 3에서 바로
"쌀"/"보리쌀" 같은 개별 품목이 나오는데 014는 깊이 5까지 가도 리프가
"곡류"/"콩류" 같은 분류군 이름이었다. 깊이 힌트가 오히려 HCX를 틀린
방향으로 밀었을 가능성이 있고, 실제로 3번 실행 중 결과가 매번 달랐다.

**2차 시도(leaf_samples, 지금 이 버전)**: 숫자로 손수 압축하는 대신
실제 리프 이름 몇 개를 그대로 노출해서, "이게 개별 품목이냐 분류군
이냐"라는 사람이 보면 자명한 판단 자체를 HCX-007에게 맡긴다.

## 케이스

1. 쌀(개별 품목, 016에만 leaf로 존재) 언급 -> DT_404Y016(품목별) 기대
2. 곡물및식량작물(분류군, 014의 leaf 이름 자체) 언급 -> DT_404Y014(기본분류) 기대

사용법(로컬에서): python3 probe_ppi_item_level_vs_basic_classification_stage1.py
"""

import sqlite3

import kosis_local_search as kls
from hcx_stage1_resolver import resolve_table_with_hcx007

CASES = [
    (
        "개별 품목(쌀) 언급 - 품목별(016) 기대",
        "생산자물가 중 쌀 가격이 크게 올랐다",
        None, "%", None, "301", "DT_404Y016",
    ),
    (
        "분류군(곡물및식량작물) 언급 - 기본분류(014) 기대",
        "생산자물가 중 곡물 및 식량작물 가격이 올랐다",
        None, "%", None, "301", "DT_404Y014",
    ),
]


def main() -> None:
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        table_list = kls.list_registered_tables(conn)
        print(f"[로컬 표 목록] {len(table_list)}개\n")

        # 016/014의 axis_hints를 먼저 그대로 찍어서, 실제로 values는
        # 겹치고 leaf_samples만 다른지 눈으로 먼저 확인한다(실측 우선).
        for t in table_list:
            if t.get("org_id") == "301" and t.get("tbl_id") in ("DT_404Y016", "DT_404Y014"):
                print(f"[{t['tbl_id']}] {t['tbl_nm']}")
                for hint in t.get("axis_hints") or []:
                    print(f"  {hint.get('axis_label')}: {hint.get('values')} | leaf_samples={hint.get('leaf_samples')}")
        print()

        results = []
        for label, claim_text, value, unit, period, exp_org, exp_tbl in CASES:
            print(f"=== {label} - \"{claim_text}\" ===")
            try:
                idx = resolve_table_with_hcx007(
                    table_list, claim_text, claimed_value=value, claimed_unit=unit, claimed_period=period,
                )
            except Exception as e:
                print(f"  [EXCEPTION] {type(e).__name__}: {e}")
                results.append((label, False, None))
                print()
                continue

            if idx is None:
                print("  resolved: None(확신 없음)")
                results.append((label, False, None))
            else:
                chosen = table_list[idx]
                ok = chosen["org_id"] == exp_org and chosen["tbl_id"] == exp_tbl
                print(f"  resolved: {chosen['tbl_nm']} ({chosen['org_id']}/{chosen['tbl_id']})")
                print(f"  기대: {exp_org}/{exp_tbl} -> [{'PASS' if ok else 'FAIL'}]")
                results.append((label, ok, f"{chosen['org_id']}/{chosen['tbl_id']}"))
            print()

        n_pass = sum(1 for _, ok, _ in results if ok)
        print("=" * 70)
        print(f"[요약] {n_pass}/{len(results)} PASS")
        for label, ok, got in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" (got={got})" if not ok else ""))
        print()
        print("[주의] N=2인 미시 검증이다 - \"이 두 케이스에서 leaf_samples 힌트가")
        print("작동했다/안 했다\" 수준으로만 해석하고, 일반화하지 않는다.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

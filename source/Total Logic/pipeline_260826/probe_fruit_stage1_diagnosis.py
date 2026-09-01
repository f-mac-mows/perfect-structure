"""[2026-08-26 신규 - A2e46e4ac-C022/C023/C024 실측 미스터리 진단] 같은 표
(DT_1J22112, "품목별 소비자물가지수(품목성질별)"), 같은 축 레벨의 "토마토"는
Stage 1(llm_table_select)에서 찾았는데 "딸기"/"바나나"는 UNVERIFIED_NOT_FOUND로
빠졌다 - 왜 그런지 두 가지 가설을 실측으로 확인한다.

## 가설 1 - axis_hints/leaf_samples 절단(truncation)

`kosis_local_search.list_registered_tables`는 표당 축 최대 4개
(`max_axes`), 축당 최상위 값 최대 6개(`max_axis_values`)만 보여주고,
`_axis_leaf_samples`는 그 축의 리프를 **정렬 없이(ORDER BY 없음)** DB
저장 순서 그대로 최대 5개만 잘라 보여준다. "토마토"는 이 잘린 샘플
안에 우연히 들어갔고 "딸기"/"바나나"는 안 들어갔을 수 있다.

## 가설 2 - Stage 1 HCX-007 호출의 비결정성

`resolve_table_with_hcx007`은 temperature를 명시적으로 안 넘겨서
`hcx_client.call_hcx`의 기본값(thinking_effort 사용 시 0.5)이 적용된다 -
완전히 결정적이지 않다. 같은 claim을 여러 번 호출하면 결과가 흔들릴 수
있다.

## 사전 조건

DT_1J22112(품목별 소비자물가지수(품목성질별))가 로컬 DB에 이미 적재돼
있어야 한다(이번 배치 결과 자체가 이미 그 표를 대상으로 돌았으므로
당연히 있을 것). DB는 mode=ro로만 연다(CLAUDE.md 준수, 읽기만 함).

**로컬에서 직접 실행** - 이 세션은 네트워크가 막혀 있어 여기선 못 돌린다.

사용법:
    python3 probe_fruit_stage1_diagnosis.py
"""

import sqlite3

import kosis_local_search as kls
from hcx_stage1_resolver import resolve_table_with_hcx007

DB_PATH = "kosis_warehouse.db"
ORG_ID = "101"
TBL_ID = "DT_1J22112"

QUERIES = ["토마토 가격", "딸기 가격", "바나나 가격"]
REPEAT = 3  # 가설 2(비결정성) 확인용 - 같은 질의를 이만큼 반복 호출


def _check_axis_hints_contains(conn: sqlite3.Connection):
    """[가설 1 확인] list_registered_tables가 DT_1J22112에 대해 실제로
    만드는 axis_hints/leaf_samples 안에 "토마토"/"딸기"/"바나나"가 각각
    문자 그대로 등장하는지 직접 확인한다."""
    table_list = kls.list_registered_tables(conn)
    target = next(
        (t for t in table_list if t["org_id"] == ORG_ID and t["tbl_id"] == TBL_ID),
        None,
    )
    if target is None:
        print(f"!! {ORG_ID}/{TBL_ID}를 로컬 표 목록에서 못 찾음 - 적재 여부 확인 필요")
        return

    print(f"=== DT_1J22112의 axis_hints (list_registered_tables 실제 출력) ===")
    print(f"tbl_nm={target.get('tbl_nm')!r} stat_nm={target.get('stat_nm')!r}")
    if not target.get("axis_hints"):
        print(
            "  axis_hints=[] (confusable로 안 걸려서 애초에 축 상세를 안 보여줌 - "
            "이러면 표 이름/통계명만으로 Stage 1이 판단한다는 뜻)"
        )
    for hint in target.get("axis_hints") or []:
        print(f"  axis_label={hint.get('axis_label')!r}")
        print(f"    values(최상위 최대 6개)={hint.get('values')}")
        print(f"    leaf_samples(리프 최대 5개, 정렬 없음)={hint.get('leaf_samples')}")

    print()
    print("=== 과일 이름이 axis_hints 텍스트 어디에라도 등장하는가 ===")
    full_text = str(target.get("axis_hints") or [])
    for fruit in ["토마토", "딸기", "바나나"]:
        print(f"  {fruit!r} in axis_hints 텍스트: {fruit in full_text}")
    print()


def _check_determinism(conn: sqlite3.Connection):
    """[가설 2 확인] 같은 claim을 여러 번 호출해서 resolve_table_with_hcx007
    결과가 흔들리는지(index가 매번 같은지) 확인한다."""
    table_list = kls.list_registered_tables(conn)

    print(f"=== 반복 호출로 비결정성 확인 (각 질의 {REPEAT}회) ===")
    for query in QUERIES:
        indices = []
        for i in range(REPEAT):
            idx = resolve_table_with_hcx007(table_list, query)
            indices.append(idx)
            chosen = (
                f"{table_list[idx]['org_id']}/{table_list[idx]['tbl_id']}"
                if idx is not None and 0 <= idx < len(table_list)
                else "None(확신없음)"
            )
            print(f"  [{i+1}/{REPEAT}] {query!r} -> index={idx} ({chosen})")
        consistent = len(set(indices)) == 1
        print(f"  -> {'일관됨' if consistent else '!! 호출마다 결과가 다름(비결정성 확인됨)'}\n")


def main() -> None:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        _check_axis_hints_contains(conn)
        _check_determinism(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

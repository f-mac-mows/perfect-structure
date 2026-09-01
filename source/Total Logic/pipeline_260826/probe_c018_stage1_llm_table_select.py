"""[2026-08-21 신규 - Task #80 확장, 로컬 실행용] Stage 1 대안 경로
(local_db_agent.resolve_claim_evidence, stage1_keywords="llm_table_
select")를 실 HCX-007 API로 검증하는 프로브 - 이 샌드박스는 네트워크가
막혀 있어(HCXRequestError로 확인됨) 이 스크립트는 사용자가 실 API 키가
있는 로컬 환경에서 직접 실행해야 한다.

검증 대상: A93bfa851-C018("주류 및 담배는 상승률이 5.0%에 그쳤지만 이
중 주류만 보면 13.1%였다") - run03 라이브 검색 10개 패러프레이즈가 전부
실패하고, 로컬 FTS 폴백도 순수 숫자 토큰 충돌로 완전히 무관한 표를
골랐던 실측 버그 사례(README "열세 번째" 항목, CLAUDE.md "담당 범위
정정" 계기). 숫자 토큰 필터로 로컬 FTS 경로는 이미 고쳤지만(정답표
DT_1J22001로 1위 복구, test_local_search_tokenize.py로 검증), 이
스크립트는 그것과 완전히 별개인 "run03/로컬 FTS를 아예 건너뛰고 로컬
표 목록을 HCX-007에 직접 보여주는" 대안 경로 자체가 실제로 정답표를
찾아내는지를 검증한다.

기대 결과: org_id=101, table_id=DT_1J22001("지출목적별 소비자물가지수")
가 선택되면 성공. 로컬 표가 19개뿐이라(2026-08-21 실측) 한 콜로 충분.

사용법(로컬에서): python3 probe_c018_stage1_llm_table_select.py
"""

import json
import sqlite3

import kosis_local_search as kls
from hcx_stage1_resolver import resolve_table_with_hcx007
import local_db_agent as lda

EXPECTED_ORG_ID = "101"
EXPECTED_TBL_ID = "DT_1J22001"


def main() -> None:
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        table_list = kls.list_registered_tables(conn)
        print(f"[로컬 표 목록] {len(table_list)}개")
        for i, t in enumerate(table_list):
            print(f"  {i}: {t['tbl_nm']}" + (f" ({t['stat_nm']})" if t.get("stat_nm") else ""))
        print()

        claim = {
            "claim_id": "A93bfa851-C018",
            "claim": "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다.",
            "metric": "주류 물가 상승률",
            "metric_normalized": "주류 물가 상승률",
            "value": "13.1",
            "unit": "%",
            "period": "2025-09",
        }

        print("=== ① resolve_table_with_hcx007 직접 호출 ===")
        idx = resolve_table_with_hcx007(
            table_list, claim["metric_normalized"], claimed_value=13.1,
            claimed_unit="%", claimed_period="2025-09",
        )
        print(f"resolved index: {idx}")
        if idx is not None:
            chosen = table_list[idx]
            print(f"resolved table: {chosen}")
            ok = chosen["org_id"] == EXPECTED_ORG_ID and chosen["tbl_id"] == EXPECTED_TBL_ID
            print(f"[{'PASS' if ok else 'FAIL'}] 기대한 정답표(DT_1J22001)와 일치")
        else:
            print("[FAIL] HCX가 확신 없음(None)을 반환 - 기대한 정답을 못 골랐음")
        print()

        print("=== ② resolve_claim_evidence 전체 경로(stage1_keywords=llm_table_select) ===")
        result = lda.resolve_claim_evidence(
            conn, claim, keywords=[],
            stage1_keywords="llm_table_select",
            hcx_table_resolve_fn=resolve_table_with_hcx007,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        ok2 = result.get("org_id") == EXPECTED_ORG_ID and result.get("table_id") == EXPECTED_TBL_ID
        print(f"[{'PASS' if ok2 else 'FAIL'}] 전체 경로도 기대한 정답표를 선택함")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

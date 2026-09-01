"""[2026-08-22 신규 - Task #23/#28 실 API 백필 검증 + C003/C004 완전 claim
테스트(test_local_search_special_tables.py)의 실측 데이터 공백 해결을 겸함]

test_food_cpi_vs_headline_diff_C003_C004_complete_claim이 FAIL한 원인을
fact_coverage/facts 직접 조회로 확인한 결과, DT_1J22001(101, 지출목적별
소비자물가지수, wide 표)의 월별(M) 배치 적재가 최근 5년(202107~202607)만
커버하고 있었다 - claim이 필요로 하는 reference_period(202009, 지수 기준
연도인 2020년 9월)는 이 캡 밖이라 로컬에 없다. 이건 코드 버그가 아니라
Task #23이 말하던 바로 그 "기간 미보유" 상황이고, probe_fetch_scoped_slice.py
가 이미 실측 검증해둔 wh.fetch_scoped_slice() 온디맨드 백필 경로가 정확히
이 상황을 위한 것이다(narrow만큼 저렴함이 이미 실측 확인됨 - README 참고).

이 스크립트는 그 경로를 "라면"이 아니라 실제로 필요한 item(식료품 및
비주류음료 / 총지수)에 대해 실행한다:

1. resolve_evidence_by_flat_match로 item A(식료품 및 비주류음료)의 실제
   itm_id/axis_codes를 (테스트와 동일하게) 확인한다 - 추측하지 않음.
2. item A와 item B(같은 축의 총지수, code="0")의 축 조합 각각에 대해
   fetch_scoped_slice(needed=202009~202009, objl_fixed=그 축조합)를 호출해
   온디맨드 백필한다 - compute_records=True 기본값이라 실제로는 그 항목의
   전체 수록기간(get_period_meta 기준)까지 확장되어 받아올 것으로 예상.
3. 백필 후 test_local_search_special_tables.py를 다시 실행하면 됨(이
   스크립트가 자동으로 재실행하지는 않음 - 결과를 직접 보고 판단할 것).

CLAUDE.md 규칙 준수:
- 이 세션(샌드박스)에서 직접 실행하지 않음 - 사용자가 로컬(네트워크+API
  키 있는 환경)에서 직접 실행.
- DB 쓰기/삭제가 필요한 작업 - 이 스크립트 자체가 facts/fact_coverage에
  쓰기를 하므로, 반드시 사용자가 직접 실행할 것(세션에서 실행 금지).
- itm_id="all"로 절대 부르지 않는다(probe_fetch_scoped_slice.py의 경고와
  동일한 이유 - wide 표 전체 훑기로 인한 API 비용 폭증 방지). 아래 코드는
  항상 축이 완전히 고정된(objl_fixed) 스코프드 호출만 한다.

사용법: python backfill_cpi_food_2020_09.py (kosis_warehouse.db가 있는
이 폴더에서, config.py에 KOSIS_API_KEY가 이미 설정돼 있어야 함)
"""

import sqlite3

import kosis_local_search as kls
import kosis_warehouse as wh
from client import KosisApiClient

DB_PATH = "kosis_warehouse.db"
ORG_ID = "101"
TBL_ID = "DT_1J22001"
PRD_SE = "M"
NEEDED_STRT = "202009"
NEEDED_END = "202009"


def main():
    conn = sqlite3.connect(DB_PATH)
    client = KosisApiClient()

    # 테스트와 완전히 같은 방식으로 item A를 확인한다(추측 없음).
    candidates = kls.resolve_evidence_by_flat_match(conn, ORG_ID, TBL_ID, ["식료품 및 비주류음료"], top_n=3)
    if not candidates:
        print("[중단] 식료품 및 비주류음료 항목을 못 찾음 - 표가 적재됐는지 확인 필요")
        conn.close()
        return
    item_a = candidates[0]
    itm_id = item_a["itm_id"]
    axis_codes_a = item_a["axis_codes"]
    print(f"[item A] itm_id={itm_id} axis_codes={axis_codes_a} itm_nm={item_a['itm_nm']}")

    # item B(총지수) 축 코드 - kosis_local_search의 _axis_total_code와
    # 같은 방식(테스트가 한 것과 동일하게 dimensions에서 "식료품"이 든 축을
    # 찾고, 그 축의 총계 코드를 직접 조회)으로 확인한다.
    axis_position = None
    for pos, code in axis_codes_a.items():
        row = conn.execute(
            "SELECT name FROM dimensions WHERE org_id=? AND tbl_id=? AND axis_position=? AND code=?",
            (ORG_ID, TBL_ID, pos, code),
        ).fetchone()
        if row and "식료품" in (row[0] or ""):
            axis_position = pos
            break
    if axis_position is None:
        print("[중단] 식료품 코드가 속한 axis_position을 못 찾음")
        conn.close()
        return
    axis_codes_b = dict(axis_codes_a)
    axis_codes_b[axis_position] = "0"  # 총지수 코드(kosis_table_info 실측 확인됨)
    print(f"[item B] axis_codes={axis_codes_b} (총지수)")

    for label, axis_codes in (("item A(식료품 및 비주류음료)", axis_codes_a), ("item B(총지수)", axis_codes_b)):
        print(f"\n[백필 시도] {label} - objl_fixed={axis_codes}")
        result = wh.fetch_scoped_slice(
            client, conn, ORG_ID, TBL_ID, PRD_SE, itm_id,
            NEEDED_STRT, NEEDED_END,
            objl_fixed=axis_codes, compute_records=True,
        )
        print(f"  결과: {result}")

    conn.close()
    print("\n완료 - test_local_search_special_tables.py를 다시 실행해서 확인해주세요.")


if __name__ == "__main__":
    main()

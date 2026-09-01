"""[2026-08-17 신규] 소비자물가지수 세부 분류 표 4개를 추가 시드로 적재하는
실행 스크립트 - seed_ingest.py/seed_ingest_extra.py와 같은 방식으로
로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.

## 왜 필요한가 (실측으로 확인된 커버리지 공백, 매칭 알고리즘 버그 아님)

`local_db_agent_result.json`에서 A93bfa851 계열(먹거리/생활 물가 claim
10건 이상)이 MISMATCH/UNVERIFIED_DERIVED_NEEDED로 나온 원인을 실제
tables_registry 내용을 조회해서 확인했다 - 지금 적재된 12개 표 중
소비자물가지수 관련은 `DT_2IFS002`(소비자물가지수 - **국제비교표**,
Task #29에서 국가축 기본값만 고쳤지 데이터 자체는 국내 세부분류가 아님)와
`DT_1J22041`(연도별 소비자물가 등락률)뿐인데, 후자는 실제로 조회해보니
prd_se='A'(연간)만 있고 ITEM도 "전년비" 1개뿐이다(전체 CPI 등락률 총계,
카테고리별 분류 없음). 즉 "가정용품 및 가사서비스", "음식 서비스",
"빵/케이크" 같은 카테고리별·품목별 claim을 검증할 표 자체가 애초에
로컬 DB에 없었다 - 검색 엔진이 후보를 잘못 고른 게 아니라, 있는 후보
중에서 최선을 고른 것뿐이었다(그래서 PPI 표 DT_404Y016이나 국제비교표
DT_2IFS002로 새어나갔다).

## 목록

- DT_1J22001 지출목적별 소비자물가지수(품목포함, 2020=100) - COICOP류
  대분류(가정용품 및 가사서비스/음식 및 숙박/음식 서비스/주택,수도,전기 및
  연료/의류 및 신발/교통/오락 및 문화/교육/보건/통신 등). A93bfa851의
  C022~C029 계열이 여기 해당.
- DT_1J22002 품목성질별 소비자물가지수(2020=100) - 성질별 분류(식료품/
  비주류음료 등 중분류). C001/C005/C011/C012/C014처럼 "기타 식료품",
  "육류", "어류 및 수산" 등 성질별 카테고리 claim이 여기 해당할 가능성.
- DT_1J22112 품목별 소비자물가지수(품목성질별: 2020=100) - 품목(leaf)
  단위 세부 - "빵", "케이크", "떡", "라면"처럼 개별 품목명이 그대로
  ITEM으로 있을 가능성이 높다(C008/C010이 필요로 하는 세분화 수준).
- DT_1J22042 월별 소비자물가 등락률 - **월별** + (DT_1J22041과 달리) 여러
  ITEM(카테고리별 전년동월비)을 포함할 가능성. 실제로 카테고리별 분류가
  있다면, 이 표의 ITEM들은 KOSIS가 이미 계산해서 공식 발표한 등락률이라
  measure_type='rate_of_change'로 분류돼 직접 조회만으로 끝난다 - 우리가
  두 시점을 빼서 만드는 파생값(derivation_used=True, 정책상 항상
  UNVERIFIED_DERIVED_NEEDED)이 아니라 VERIFIED까지 갈 수 있는 유일한
  경로다. **다만 이 표에 실제로 카테고리별 ITEM이 있는지는 실측 전이라
  미확정** - 적재해보고 dimensions 테이블에서 직접 확인해야 한다(추측 아님,
  이 스크립트가 확인해주는 것).

## 참고: CLAUDE.md 실측 원칙과의 관계

이건 KOSIS 응답 "형식"을 추측하는 게 아니라 "이미 알려진 형식으로 어떤
표를 새로 적재할지" 결정하는 것이라 그 원칙(스키마/구조를 실측 전엔 안
만든다)의 적용 대상이 아니다 - ingest_table()이 쓰는 getMeta/getList
포맷은 이미 12개 표로 실측 검증된 경로를 그대로 재사용한다.

사용법:
    python seed_ingest_cpi_breakdown.py                  # 이미 적재된 표는 건너뛰고 새 표만 적재(전체 기간)
    python seed_ingest_cpi_breakdown.py --force          # 강제 재적재
    python seed_ingest_cpi_breakdown.py --years-back 10  # 표당 최근 10년치만 적재
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)

돌리신 뒤 결과 로그(신규/건너뜀/실패 건수)를 알려주시면, 그 표들로
A93bfa851 계열 claim이 실제로 얼마나 개선되는지(특히 DT_1J22042에 카테고리별
ITEM이 있어서 VERIFIED까지 가는 case가 나오는지) 재검증하겠습니다.

## [2026-08-18 추가] 적재 범위 정책 3번 레버(narrow/wide + fact_coverage) 실측 확인용

이 4개 표는 지금 로컬 DB에 아직 없어서(이번 실행이 첫 적재), narrow/wide
분류와 fact_coverage 기록이 합성 데이터가 아니라 실제로 어떻게 동작하는지
확인하기 딱 좋은 대상이다 - 특히 `DT_1J22112`(품목별, leaf 단위 세분화)는
항목 수가 많아 wide로 분류될 가능성이 있고, 나머지는 narrow일 가능성이 높다
(실제로 어느 쪽인지는 이 스크립트가 돌려보고 알려준다 - 추측 안 함).
`main()` 끝에 표마다 dimensions/facts/fact_coverage/records 실제 적재
결과를 그대로 조회해서 출력하는 절이 추가됐다.
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

CPI_BREAKDOWN_CANDIDATES = [
    {"ORG_ID": "101", "TBL_ID": "DT_1J22001", "TBL_NM": "지출목적별 소비자물가지수(품목포함, 2020=100)"},
    {"ORG_ID": "101", "TBL_ID": "DT_1J22002", "TBL_NM": "품목성질별 소비자물가지수(2020=100)"},
    {"ORG_ID": "101", "TBL_ID": "DT_1J22112", "TBL_NM": "품목별 소비자물가지수(품목성질별: 2020=100)"},
    {"ORG_ID": "101", "TBL_ID": "DT_1J22042", "TBL_NM": "월별 소비자물가 등락률"},
]


def _parse_years_back(argv):
    """seed_ingest.py/seed_ingest_extra.py와 동일한 파싱 규칙."""
    for i, arg in enumerate(argv):
        if arg == "--years-back" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                logger.warning(f"[--years-back 파싱 실패] '{argv[i + 1]}'은 정수가 아님 - 무시하고 전체 기간 적재")
    return None


def main():
    force = "--force" in sys.argv
    years_back = _parse_years_back(sys.argv)
    logger.info(f"[CPI 세부분류 시드 목록] {len(CPI_BREAKDOWN_CANDIDATES)}개 표 (force={force}, years_back={years_back or '전체 기간'})")
    for c in CPI_BREAKDOWN_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, CPI_BREAKDOWN_CANDIDATES, force=force, years_back=years_back)

    skipped = [r for r in results if r.get("skipped")]
    ok = [r for r in results if r.get("success") and not r.get("skipped")]
    fail = [r for r in results if not r.get("success")]
    logger.info(
        f"[적재 완료] 신규/재적재 {len(ok)}건 / 건너뜀(이미 있음) {len(skipped)}건"
        f" / 실패 {len(fail)}건 -> {DB_PATH}"
    )
    for r in fail:
        logger.warning(f"  └─ 실패: {r.get('org_id')}/{r.get('tbl_id')} - {r.get('message')}")
    for r in ok:
        logger.info(f"  └─ 성공: {r['org_id']}/{r['tbl_id']} 차원 {r['dimension_rows']}건, 값 {r['fact_rows']}건")
    for r in skipped:
        logger.info(f"  └─ 건너뜀: {r['org_id']}/{r['tbl_id']} (이미 적재됨 - --force로 강제 재적재 가능)")

    # DT_1J22042에 실제로 카테고리별 ITEM이 있는지 - 이게 있어야 위
    # docstring에서 말한 "derivation 없이 바로 VERIFIED" 경로가 열린다.
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    items = conn.execute(
        "SELECT DISTINCT name FROM dimensions WHERE org_id='101' AND tbl_id='DT_1J22042' AND obj_id='ITEM'"
    ).fetchall()
    logger.info(f"[확인] DT_1J22042의 ITEM 개수: {len(items)} - {[i[0] for i in items[:15]]}")

    # [2026-08-18 신규] 적재 범위 정책 3번 레버(narrow/wide + fact_coverage)가
    # 실제로 어떻게 동작했는지 표마다 DB를 직접 조회해서 그대로 보여준다 -
    # 합성 데이터 테스트로는 확인 못 하는 "진짜 KOSIS 응답 기준 narrow/wide
    # 판정이 맞았는지, facts에 실제로 어느 기간이 들어갔는지, fact_coverage가
    # 블랭킷(narrow)인지 좁은 스코프(wide)인지"를 한눈에 보기 위함.
    logger.info("=" * 70)
    logger.info("[적재 범위 정책 3번 레버 - 실제 DB 적재 상태 확인]")
    for c in CPI_BREAKDOWN_CANDIDATES:
        org_id, tbl_id = c["ORG_ID"], c["TBL_ID"]
        reg = conn.execute(
            "SELECT tbl_nm, strt_prd_de, end_prd_de FROM tables_registry WHERE org_id=? AND tbl_id=?",
            (org_id, tbl_id),
        ).fetchone()
        if not reg:
            logger.info(f"  [{org_id}/{tbl_id}] tables_registry에 없음 - 적재 실패했거나 스킵됨")
            continue
        n_dims = conn.execute(
            "SELECT COUNT(*) FROM dimensions WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)
        ).fetchone()[0]
        n_items = conn.execute(
            "SELECT COUNT(*) FROM dimensions WHERE org_id=? AND tbl_id=? AND obj_id='ITEM'", (org_id, tbl_id)
        ).fetchone()[0]
        fact_row = conn.execute(
            "SELECT COUNT(*), MIN(prd_de), MAX(prd_de) FROM facts WHERE org_id=? AND tbl_id=?",
            (org_id, tbl_id),
        ).fetchone()
        n_facts, min_prd, max_prd = fact_row
        n_records = conn.execute(
            "SELECT COUNT(*) FROM records WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)
        ).fetchone()[0]
        coverage_rows = conn.execute(
            "SELECT prd_se, itm_id, axis_key, strt_prd_de, end_prd_de FROM fact_coverage "
            "WHERE org_id=? AND tbl_id=? ORDER BY prd_se, itm_id, axis_key",
            (org_id, tbl_id),
        ).fetchall()
        is_blanket = any(itm_id == "all" and axis_key == "all" for _, itm_id, axis_key, _, _ in coverage_rows)
        logger.info(
            f"  [{org_id}/{tbl_id}] {reg[0]} (원본 수록기간 광고값 {reg[1]}~{reg[2]})"
        )
        logger.info(
            f"    └─ dimensions {n_dims}건(ITEM {n_items}개) / facts {n_facts}건"
            f"(실제 적재 범위 {min_prd}~{max_prd}) / records {n_records}개 계열"
        )
        logger.info(
            f"    └─ fact_coverage {len(coverage_rows)}건 -"
            + (" narrow(블랭킷 all/all)" if is_blanket else " wide(좁은 스코프)로 판정된 것으로 보임")
        )
        for prd_se, itm_id, axis_key, strt, end in coverage_rows[:5]:
            logger.info(f"       - {prd_se} itm={itm_id} axis={axis_key} {strt}~{end}")
        if len(coverage_rows) > 5:
            logger.info(f"       ... 외 {len(coverage_rows) - 5}건")
    logger.info("=" * 70)
    conn.close()


if __name__ == "__main__":
    main()

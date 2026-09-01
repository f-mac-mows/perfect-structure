"""[2026-08-20 신규 - Task #80 핵심 시나리오 실측 검증용] "나랏빚"(구어체)이
실제 KOSIS item명("국가채무(D1)" 등)으로 임베딩 번역이 되는지 확인하려면
그 표가 로컬 DB에 있어야 하는데, read-only 쿼리로 확인한 결과 로컬엔
없었다(tables_registry에 "채무"/"부채"/"재정" 검색해 DT_2OEEO029 - 비율
표 - 하나뿐, 절대금액 국가채무 표가 없음). KOSIS MCP(kosis_search)로
실제 KOSIS 카탈로그를 검색해 확인한 후보:

- orgId=184 tblId=DT_102006_001 "국가채무(D1)" (기획예산처) - kosis_
  table_info(type=ITM)로 구조 확인 완료: item은 "국가채무(D1)" 1개뿐이고,
  축(A, 채무내역별)에 "국가채무"/"국가채무 GDP 대비"/"중앙정부 채무"/
  "국채"/"국고채권"/"국민주택채권"/"외평채권"/"차입금"/"국고채무부담행위"/
  "지방정부 순채무"/"기타" 11개 - 항목 1개 x 축 11개로 작다(iter_table_
  cell_texts 기준 최대 11셀 - max_cells=300에 전혀 안 걸림, quota 부담
  적음). "나랏빚"처럼 원문에 "국가채무"라는 단어가 아예 없는 claim이
  이 표의 진짜 item/축 어휘로 임베딩 번역되는지 최소 비용으로 직접
  확인하기에 적합.

seed_ingest_extra.py와 완전히 같은 패턴(wh.ingest_tables 재사용) - 새
적재 로직을 만들지 않는다. **CLAUDE.md "DB 파일에 직접 쓰기/삭제 금지"
규칙에 따라 이 스크립트는 이 세션(샌드박스)에서 직접 실행하지 않고,
사용자가 로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.**

사용법:
    python seed_ingest_national_debt.py                  # 이미 있으면 건너뜀
    python seed_ingest_national_debt.py --force           # 강제 재적재
    python seed_ingest_national_debt.py --years-back 10   # 최근 10년치만
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

NATIONAL_DEBT_CANDIDATES = [
    {"ORG_ID": "184", "TBL_ID": "DT_102006_001", "TBL_NM": "국가채무(D1)"},
]


def _parse_years_back(argv):
    """seed_ingest.py/seed_ingest_extra.py와 동일한 파싱 규칙(중복 정의 -
    공유 모듈을 두기엔 이 함수 하나뿐이라 기존 관례를 그대로 따름)."""
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
    logger.info(f"[국가채무 시드 목록] {len(NATIONAL_DEBT_CANDIDATES)}개 표 (force={force}, years_back={years_back or '전체 기간'})")
    for c in NATIONAL_DEBT_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, NATIONAL_DEBT_CANDIDATES, force=force, years_back=years_back)

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


if __name__ == "__main__":
    main()

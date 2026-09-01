"""[2026-08-22 신규 - Task #23, 기간 미보유 UNRESOLVED 백필] 90개 claim
배치 실측(`run04_local_llm_table_select_batch.py`)에서 `A93bfa851-C007/
C009/C011`("빵/케이크/떡/라면"류 물가 상승, "과자·빙과류·당류" 상승) 3건이
표는 정확히 찾았는데(145/TX_14503_A048 "품목군별 국내판매액 변동현황")
claim이 필요로 하는 시점(202509)의 데이터가 로컬 DB에 없어
UNVERIFIED_UNRESOLVED로 끝났다(README "스무 번째" 항목 이후 참고).

이 표는 README 여덟 번째 항목의 실측 distinct-cell 조사(사용자가 read-only
SELECT로 직접 확인)에서 112셀로 나왔다 - 소비자물가지수류 대형 표(수천~
1만+)에 비해 작아서 wide 표 정책(연도 캡)에 걸릴 가능성은 낮아 보이지만,
**실측 전이라 추정** - 왜 202509가 빠졌는지(wide 캡/데이터 발행 지연/적재
시점이 그보다 이전이었는지)는 아래 스크립트를 실행한 결과(`ingest_table`이
돌려주는 `dimension_rows`/`fact_rows`, 그리고 재검증 시 202509 여부)로
확인해야 한다.

seed_ingest_national_debt.py/seed_ingest_extra.py와 완전히 같은 패턴
(wh.ingest_tables 재사용) - 새 적재 로직을 만들지 않는다. **CLAUDE.md
"DB 파일에 직접 쓰기/삭제 금지" + "샌드박스에서 직접 실행 금지" 규칙에
따라 이 스크립트는 이 세션(샌드박스)에서 직접 실행하지 않고, 사용자가
로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.**

사용법:
    python seed_ingest_item_group_sales.py                  # 이미 있으면 건너뜀
    python seed_ingest_item_group_sales.py --force           # 강제 재적재(202509 등 최신 구간 포함해 다시 당겨옴)
    python seed_ingest_item_group_sales.py --years-back 10   # 최근 10년치만
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

ITEM_GROUP_SALES_CANDIDATES = [
    {"ORG_ID": "145", "TBL_ID": "TX_14503_A048", "TBL_NM": "품목군별 국내판매액 변동현황"},
]


def _parse_years_back(argv):
    """기존 seed_ingest_*.py와 동일한 파싱 규칙(중복 정의 - 공유 모듈을
    두기엔 이 함수 하나뿐이라 기존 관례를 그대로 따름)."""
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
    logger.info(f"[품목군별 국내판매액 시드 목록] {len(ITEM_GROUP_SALES_CANDIDATES)}개 표 (force={force}, years_back={years_back or '전체 기간'})")
    for c in ITEM_GROUP_SALES_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, ITEM_GROUP_SALES_CANDIDATES, force=force, years_back=years_back)

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

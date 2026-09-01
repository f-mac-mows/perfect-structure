"""[2026-08-16 신규] kosis_warehouse.py로 시드 표를 실제로 적재하는 실행
스크립트 - 로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.

시드 목록 결정 근거: README.md 9.3 + 이 세션 논의. kosis_factcheck.log의
실제 API 호출 기록은 axis 확장 재시도 때문에 노이즈가 많아(distinct
1,875개, freq>=5도 1,487개) 그대로 못 쓰고, 대신 run04_result.json(최근
90건 풀 파이프라인 실행)에서 "table+item+axis까지 전부 확정되고 실제
판정에 쓰인" 표(evidence.retrieval_status == "RESOLVED")만 추려 10개로
시작한다 - kosis_warehouse.extract_seed_candidates_from_run_result() 참고.

이 10개 안에는 이번 세션에 진단된 오탐 2건(101/DT_2IFS002=IMF 국제
소비자물가지수, 101/DT_2OEEO029=OECD 국제 기초재정수지)도 일부러 포함되어
있다 - 제외하는 게 아니라 STAT_NM(원본 필드)까지 정확히 채워서 적재하는
것 자체가 "표 이름만으로는 국내/국제를 구분 못 한다"는 문제의 실제
해법이다(제외해버리면 나중에 검색 엔진이 다시 이 표를 후보로 올렸을 때
국제기구 표라는 사실을 판별할 원본 STAT_NM 자체가 없어져 버린다 - 국제
기구 여부 판별 자체는 kosis_local_search.is_international_survey가
검색 시점에 STAT_NM/VW_CD를 보고 계산한다).

[2026-08-16 추가 - 재적재 스킵] "라이브 재검색 없이 DB에 직접 적재"하는
아키텍처를 택한 이상, 이미 tables_registry에 있는 표를 매번 다시 API
호출해서 갱신할 이유가 없다(사용자 지적). 기본 동작은 이미 적재된 표를
API 호출 없이 건너뛰고, 새 표만 실제로 적재한다 - 이 스크립트를 몇 번을
다시 돌려도 안전하고 빠르다. 값 자체를 최신화하고 싶을 때만 --force로
명시적으로 전체 재적재한다(증분 갱신은 아직 미구현이라 --force는 항상
전체 재적재).

사용법:
    python seed_ingest.py                     # 이미 적재된 표는 건너뛰고 새 표만 적재(전체 기간)
    python seed_ingest.py --force             # 전부 강제 재적재(값 최신화, 전체 기간)
    python seed_ingest.py --years-back 10     # 표당 최근 10년치만 적재(Research Overview 2
                                               # 적재 범위 정책 1번 레버 - 기준연도 비교 claim까지
                                               # 커버하려면 5~10년 권장). --force와 함께 써야
                                               # 이미 전체 기간으로 적재된 표에도 적용된다.
    (kosis_warehouse.db 파일이 이 디렉터리에 생성/갱신된다)
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"
RUN_RESULT_PATH = "run04_result.json"


def _parse_years_back(argv):
    """--years-back N을 찾아 정수로 돌려준다. 없으면 None(=전체 기간,
    기존 동작 그대로 - 기본값을 몰래 바꾸지 않는다)."""
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
    candidates = wh.extract_seed_candidates_from_run_result(RUN_RESULT_PATH)
    logger.info(
        f"[시드 목록] {RUN_RESULT_PATH}에서 {len(candidates)}개 표 추출됨"
        f" (force={force}, years_back={years_back or '전체 기간'})"
    )
    for c in candidates:
        tag = f" [국제기구:{c['STAT_NM']}]" if c.get("STAT_NM") else ""
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}{tag}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, candidates, force=force, years_back=years_back)

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

"""[2026-08-17 신규] 특이 표(축 구조가 복잡하거나 동명이의/재귀 구조가 있는
표)를 추가 시드로 적재하는 실행 스크립트 - seed_ingest.py와 같은 방식으로
로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.

## 왜 필요한가

기존 10개 시드(seed_ingest.py)는 run04_result.json의 RESOLVED claim에서만
뽑았는데, 그 claim 세트엔 이번 세션에 항목/축 확정 로직(resolve_evidence_
by_flat_match)을 검증하는 데 썼던 "특이 표"(독서, 유가증권)가 없다 - 그
표들은 이전 세션에 라이브 API로만 테스트했고 로컬 DB엔 한 번도 적재된 적이
없다(실측 확인: tables_registry에 없음). 이 표들을 실제로 적재해서 로컬
DB 기준으로도 재검증하기 위한 스크립트다.

## 목록

- orgId=113 tblId=DT_113_STBL_1024687 (학생 독서활동 실태 - 학교급*성별
  축에 "고등학교"/"학교급학년" 등 동명이의, 헤더=leaf 이름이 겹치는 구조가
  있음)
- orgId=343 tblId=DT_343_2010_S0043 (유가증권 순위별 거래 - "거래량"이라는
  이름이 헤더와 leaf에 동시에 존재하는 구조)

STAT_NM/VW_CD를 모르는 채로 넣으므로(search_metadata 캐시가 없음) 두 표
다 국제기구 표는 아니므로 검색 엔진의 is_international_survey 판별에는
영향 없다 - None으로 저장돼도 안전(비국제기구로 기본 처리됨).

사용법:
    python seed_ingest_extra.py                  # 이미 적재된 표는 건너뛰고 새 표만 적재(전체 기간)
    python seed_ingest_extra.py --force          # 강제 재적재
    python seed_ingest_extra.py --years-back 10  # 표당 최근 10년치만 적재 (seed_ingest.py와 동일 정책)
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

EXTRA_CANDIDATES = [
    {"ORG_ID": "113", "TBL_ID": "DT_113_STBL_1024687", "TBL_NM": "학생 독서활동 실태"},
    {"ORG_ID": "343", "TBL_ID": "DT_343_2010_S0043", "TBL_NM": "유가증권 순위별 거래"},
]


def _parse_years_back(argv):
    """seed_ingest.py와 동일한 파싱 규칙(중복 정의 - 두 스크립트가 공유
    모듈을 두기엔 이 함수 하나뿐이라 굳이 분리하지 않았다)."""
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
    logger.info(f"[추가 시드 목록] {len(EXTRA_CANDIDATES)}개 표 (force={force}, years_back={years_back or '전체 기간'})")
    for c in EXTRA_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, EXTRA_CANDIDATES, force=force, years_back=years_back)

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

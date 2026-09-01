"""[2026-08-27 신규 · 110차] DB 확장 시드 - 크롤 2,696기사 수요 실측 기반.

## 선정 근거 (Phase 1 실측)

- 인수인계 58기사 eligible claim 414건 도메인 분포: 수출입 88 · 고용 49 ·
  인구/외국인 39 · 물가 34 · 인구동향 24 · 사업자 12 · 주택 7 · 재정 6
- 게시글 5건에서 표를 못 찾은/미해소 claim 111건: 수출입 48 · 인구동향 24 ·
  고용 17 · 물가 13
- 크롤 전량 2,696건 제목 스캔: 수출입 285 · 증시 75 · 금리 62 · 대출 56 ·
  주택 53 · 고용 51 · 물가 50 · 환율 46 ...

현재 db(표 10개)에 무역 표가 0개인데 수요 1위가 무역이라, 무역 4표를
필두로 인구동향·고용률·가계신용·국제수지·재정·GDP·주택·외국인주민을 추가.

표 구조는 2026-08-27 KOSIS MCP로 사전 확인:
- DT_134001_002(국가별 수출입현황): 월간 2000.01~2026.06
- DT_1B8000G(인구동향): 월/분기/연 1981~2026.06
- DT_1DA7012S(성/연령별 경제활동인구): 고용률 itmId=T90 실재, 성별3x연령25
- DT_301Y013(국제수지): 월/분기/연 1980~2026.06

커버 불가로 판정(KOSIS 미수록 - 기록만): 한국은행 기준금리/원달러 환율(ECOS
전용, KOSIS 수록분은 2014 종료 구표뿐) · 증시(한국거래소 소관).

사용법 (seed_ingest_extra.py와 동일):
    python seed_ingest_expand110.py                  # 이미 적재된 표는 건너뜀
    python seed_ingest_expand110.py --force          # 강제 재적재
    (기본 years_back: 일반 표 10년, 대형 위험 표 5년 - 아래 그룹 참조)
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

# 일반 표: 최근 10년 (수요가 2025년 기사라 10년이면 5년 전 비교까지 여유)
CANDIDATES_10Y = [
    # -- 무역 (수요 1위: 미해소 48건 + 전량 285건, 현재 db 0개) --
    {"ORG_ID": "134", "TBL_ID": "DT_134001_001", "TBL_NM": "수출입총괄"},
    {"ORG_ID": "134", "TBL_ID": "DT_134001_002", "TBL_NM": "국가별 수출입현황"},
    {"ORG_ID": "134", "TBL_ID": "DT_134001_003", "TBL_NM": "대륙별 수출입 현황"},
    # -- 인구동향 (미해소 24건 - 출생아 기사 실증) --
    {"ORG_ID": "101", "TBL_ID": "DT_1B8000G", "TBL_NM": "월.분기.연간 인구동향(출생,사망,혼인,이혼)"},
    {"ORG_ID": "101", "TBL_ID": "DT_1B81A03", "TBL_NM": "시군구/성/출산순위별 출생"},
    # -- 고용 확장 (미해소 17건 - 연령별 고용률 축 부재였음) --
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7012S", "TBL_NM": "성/연령별 경제활동인구"},
    # -- 가계신용·대출 (전량 56건) --
    {"ORG_ID": "301", "TBL_ID": "DT_151Y001", "TBL_NM": "가계신용(업권별, 분기)"},
    {"ORG_ID": "301", "TBL_ID": "DT_151Y005", "TBL_NM": "예금취급기관 가계대출(용도별, 월)"},
    # -- 국제수지 (경상수지 기사 - 블라인드 세트 실증 수요) --
    {"ORG_ID": "301", "TBL_ID": "DT_301Y013", "TBL_NM": "국제수지"},
    # -- 재정 (전량 36건 - 국가채무 표는 기존재, 수지 축 보강) --
    {"ORG_ID": "184", "TBL_ID": "DT_102N_AD01", "TBL_NM": "통합재정수지"},
    # -- GDP·성장률 (전량 30건) --
    {"ORG_ID": "301", "TBL_ID": "DT_200Y102", "TBL_NM": "주요지표(분기지표)"},
    {"ORG_ID": "301", "TBL_ID": "DT_200Y101", "TBL_NM": "주요지표(연간지표)"},
    # -- 주택 (전량 53건 - 부동산원 월간, 2021 시작이라 10년 가드 무의미하지만 통일) --
    {"ORG_ID": "101", "TBL_ID": "DT_1YL13502E", "TBL_NM": "주택매매가격지수(시도/시/군/구)"},
    {"ORG_ID": "101", "TBL_ID": "DT_1YL20162E", "TBL_NM": "아파트매매가격지수(시도/시/군/구)"},
    # -- 외국인주민 (골든 실수요: 서울 45만/전국 258만 claim) --
    {"ORG_ID": "110", "TBL_ID": "TX_11025_A000_A", "TBL_NM": "시도별 외국인주민 현황"},
]

# 대형 위험 표: SITC 품목 분류가 수천 개일 수 있어 5년 가드
CANDIDATES_5Y = [
    {"ORG_ID": "360", "TBL_ID": "DT_1R11001_FRM101", "TBL_NM": "품목별 수출액, 수입액"},
]


def _report(results):
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
    return len(fail)


def main():
    force = "--force" in sys.argv
    logger.info(f"[확장 시드 110차] 일반 {len(CANDIDATES_10Y)}개(10년) + 대형 {len(CANDIDATES_5Y)}개(5년), force={force}")
    for c in CANDIDATES_10Y + CANDIDATES_5Y:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    fails = 0
    fails += _report(wh.ingest_tables(client, DB_PATH, CANDIDATES_10Y, force=force, years_back=10))
    fails += _report(wh.ingest_tables(client, DB_PATH, CANDIDATES_5Y, force=force, years_back=5))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

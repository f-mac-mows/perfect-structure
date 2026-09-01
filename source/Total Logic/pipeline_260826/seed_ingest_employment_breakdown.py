"""[2026-08-18 신규] 고용/취업자 세부 분류 표 2개를 추가 시드로 적재하는
실행 스크립트 - seed_ingest_cpi_breakdown.py와 완전히 같은 방식으로
로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.

## 왜 필요한가 (실측으로 확인된 커버리지 공백)

`local_db_agent_result_v3.json`에서 A82ae9f41 계열(고용/취업자 claim)이
MISMATCH로 나온 것들을 확인해보니, claim이 요구하는 세분화 수준의 로컬 표가
아예 없었다:

- C004: "보건·사회복지서비스업 취업자 수는 323만4000명"
- C005/C006: "전문·과학기술서비스업(10만2000명)과 교육서비스업(7만2000명)"
- C007: "제조업은 지난달 취업자 수가 441만4000명"
- C011: "청년층 고용률은 작년 5월(-0.7%포인트)부터 1년 넘게 감소세"

이 claim들에 필요한 산업 분류(보건업, 전문과학기술서비스업, 교육서비스업
개별)는 KOSIS 표준산업분류 대분류(21종) 수준이다 - 그런데 지금 로컬 DB에
이미 있던 산업별 표는 9종짜리 광의 대분류(예: "사업·개인·공공서비스 및
기타"로 뭉뚱그려짐)뿐이었다(claude가 kosis_table_info로 직접 실측 확인,
추측 아님). "청년층 고용률"도 마찬가지로 국내 표에 카테고리별 고용률이
직접 있는 표가 없었다.

## 실측으로 확인한 후보 (MCP kosis_search/kosis_table_info로 이번 세션에
직접 조회, 필드명/구조 전부 실제 응답 기준 - CLAUDE.md 실측 우선 원칙 준수)

- **DT_1DA7E06S_NEW** "산업별 취업자" (101, 경제활동인구조사, M/Q/Y
  2013~2026): itmId는 "T30"(취업자) 1개뿐이고 I축(산업별)이 표준산업분류
  대분류 21종 그대로 27개 코드로 있다 - `86`=Q 보건업 및 사회복지
  서비스업, `70`=M 전문 과학 및 기술 서비스업, `85`=P 교육 서비스업,
  `10`=C 제조업, `41`=F 건설업 등 claim이 요구하는 항목이 전부 개별
  코드로 존재함을 실측 확인했다. 총 28 dims(item×axis)뿐이라 추정 셀 수도
  작아(M 163개월 기준 대략 27×163≈4,400 - 임계값 40,000의 10분의 1
  수준) narrow로 분류될 가능성이 높다(실제 판정은 이 스크립트가 돌려보고
  알려준다 - classify_table_width가 하는 일).
- **DT_1DE9046S** "연령별 경제활동상태" (101, 경제활동인구조사, M만,
  2004.05~2026.05): itmId에 `T21`="고용률"이 KOSIS가 이미 계산해서
  발표한 값으로 직접 있고, A축(연령별)에 `A.20`="15~29세"(KOSIS가 쓰는
  "청년층" 표준 정의)가 있다 - 즉 이 표의 (itm=T21, A축=A.20) 셀이
  "청년층 고용률"을 파생 없이 바로 조회로 답할 수 있는 유일한 경로다(우리가
  두 시점을 빼서 만드는 파생값이 아니라 KOSIS 공식 발표값이라
  derivation_used=False로 VERIFIED까지 갈 수 있음). 16 dims뿐이라 확실히
  narrow.

## 참고: CLAUDE.md 실측 원칙과의 관계

이건 KOSIS 응답 "형식"을 추측하는 게 아니라(getMeta/getList 포맷은 이미
12+4개 표로 실측 검증된 경로를 그대로 재사용) "이미 알려진 형식으로 어떤
표를 새로 적재할지" 결정하는 것이고, 그 판단 근거(ITM 목록/PRD 정보)도
전부 MCP kosis_table_info 실제 응답을 그대로 인용한 것이라 원칙 위반이
아니다.

사용법:
    python seed_ingest_employment_breakdown.py                  # 새 표만 적재(전체 기간)
    python seed_ingest_employment_breakdown.py --force          # 강제 재적재
    python seed_ingest_employment_breakdown.py --years-back 10  # 표당 최근 10년치만
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)

돌리신 뒤 결과 로그를 알려주시면, A82ae9f41 계열 claim이 실제로 얼마나
개선되는지(특히 C011 청년층 고용률이 derivation 없이 VERIFIED로 가는지)
재검증하겠습니다.
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

EMPLOYMENT_BREAKDOWN_CANDIDATES = [
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7E06S_NEW", "TBL_NM": "산업별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DE9046S", "TBL_NM": "연령별 경제활동상태"},
]


def _parse_years_back(argv):
    """seed_ingest_cpi_breakdown.py와 동일한 파싱 규칙."""
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
    logger.info(f"[고용/취업자 세부분류 시드 목록] {len(EMPLOYMENT_BREAKDOWN_CANDIDATES)}개 표 (force={force}, years_back={years_back or '전체 기간'})")
    for c in EMPLOYMENT_BREAKDOWN_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, EMPLOYMENT_BREAKDOWN_CANDIDATES, force=force, years_back=years_back)

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

    # DT_1DE9046S에 실제로 T21(고용률)이 A.20(15~29세) 축과 교차해서
    # 있는지 - 이게 있어야 위 docstring에서 말한 "derivation 없이 바로
    # VERIFIED" 경로가 열린다.
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    items = conn.execute(
        "SELECT DISTINCT obj_id, code, name FROM dimensions WHERE org_id='101' AND tbl_id='DT_1DE9046S' "
        "AND (obj_id='ITEM' AND code='T21' OR obj_id='A' AND code='A.20')"
    ).fetchall()
    logger.info(f"[확인] DT_1DE9046S의 고용률(T21)/15~29세(A.20) 존재 여부: {items}")
    industry_items = conn.execute(
        "SELECT code, name FROM dimensions WHERE org_id='101' AND tbl_id='DT_1DA7E06S_NEW' AND obj_id='I' "
        "AND code IN ('86','70','85','10','41')"
    ).fetchall()
    logger.info(f"[확인] DT_1DA7E06S_NEW의 claim 대상 산업 코드 존재 여부: {industry_items}")

    # [적재 범위 정책 3번 레버] 실제 DB 적재 상태 확인 - CPI 시드 스크립트와
    # 동일한 절.
    logger.info("=" * 70)
    logger.info("[적재 범위 정책 3번 레버 - 실제 DB 적재 상태 확인]")
    for c in EMPLOYMENT_BREAKDOWN_CANDIDATES:
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

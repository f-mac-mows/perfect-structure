"""[2026-08-21 신규 - Task #80 확장] "비슷한 이름, 다른 분류축" 표를
의도적으로 늘려서 Stage 1 LLM 표 선택(hcx_stage1_resolver.py)을
스트레스 테스트하기 위한 추가 시드 - seed_ingest_cpi_breakdown.py와
완전히 같은 방식으로 로컬(네트워크+API 키 있는 환경)에서 직접 돌린다.

## 왜 이 표들인가

C018 실측 검증(README "열다섯 번째" 항목)에서, 표 제목만으로는
"지출목적별"/"품목성질별"/"품목별" 소비자물가지수 셋을 구분 못 하고
HCX-007이 콜마다 다른 표를 고르는 걸 확인했다 - axis_hints(분류축 이름
+ 최상위 값 샘플)를 프롬프트에 추가해 이 특정 사례는 고쳤지만, "시드가
늘어나도 이 구조가 버티는가"는 아직 검증 안 됐다(사용자 질문). 검증하려면
무작위로 시드를 늘리는 게 아니라, 이 실패 패턴(같은 조사에서 나온
비슷한 제목·다른 분류축 표들)을 의도적으로 더 심어서 스트레스를
줘야 한다.

## 실측으로 확인한 후보 (MCP kosis_search/kosis_table_info로 이번
세션에 직접 조회, 필드명/구조 전부 실제 응답 기준 - CLAUDE.md 실측
우선 원칙 준수)

**고용(경제활동인구조사, 101) - "OO별 취업자" 계열**: 이미 로컬에
`DT_1DA7E06S_NEW`(산업별 취업자)/`DT_1DE9046S`(연령별 경제활동상태)가
있는데, 같은 조사(목록경로 D > D_2 > B17)에서 제목 패턴이 완전히
같은 "XX별 취업자" 표가 더 있다는 걸 kosis_search로 확인했다:

- **DT_1DA7010S** 종사상지위별 취업자 (11 dims - ITEM 1개 "T30 취업자"
  + J축 10개: 계/비임금근로자/자영업자/고용원 유무별/무급가족종사자/
  임금근로자/상용·임시·일용근로자)
- **DT_1DA7011S** 취업시간별 취업자 (17 dims - ITEM 1개 + K축 16개:
  계/1~14시간/15~35시간/36~44시간/45~53시간/54시간이상 등 세분 구간)
- **DT_1DA7024S** 성/연령별 취업자
- **DT_1DA7025S** 성/교육정도별 취업자
- **DT_1DA7028S** 성/종사상지위별 취업자
- **DT_1DA7029S** 성/취업시간별 취업자

"종사상지위별 취업자"와 "취업시간별 취업자"는 제목이 "OO별 취업자"로
완전히 같은 패턴이고 ITEM도 둘 다 "T30 취업자" 하나뿐이다 - 표
제목만으로는 "임시근로자"(종사상지위)와 "36~44시간"(취업시간) 같은
claim 속 개념이 어느 표 소속인지 구분이 안 된다. C018의 "지출목적별
vs 품목성질별" CPI 문제와 정확히 같은 구조.

**생산자물가지수(한국은행, 301) - 기존 2개(품목별/기본분류) 옆에
"특수분류" 추가**: 이미 로컬에 `DT_404Y016`(품목별)/`DT_404Y014`
(기본분류)가 있는데, 같은 조사(생산자물가조사)의 세 번째 분류인
`DT_404Y015`(특수분류, 16 dims - ITEM "생산자물가지수(특수분류)" +
계정코드별 15개: 식료품/신선식품/에너지/IT 구분)를 추가하면 기존
2개짜리 collision을 3개짜리로 넓혀서 같은 스트레스를 심화시킬 수 있다.

전부 dims가 11~17개뿐이라(narrow 확실 - classify_table_width 임계값
40,000과 비교하면 무의미하게 작음) 적재 자체는 가볍다.

## 참고: CLAUDE.md 실측 원칙과의 관계

KOSIS 응답 "형식"을 추측하는 게 아니라(getMeta/getList 포맷은 이미
19개 표로 실측 검증된 경로를 그대로 재사용) "이미 알려진 형식으로 어떤
표를 새로 적재할지" 결정하는 것이고, 판단 근거(ITM/축 목록)도 전부
MCP kosis_table_info 실제 응답을 그대로 인용한 것이라 원칙 위반이
아니다.

## MCP 도구 응답에 포함된 지시문에 대한 안내

이 스크립트의 후보 목록을 뽑는 과정에서 kosis_search/kosis_table_info
MCP 도구 응답에 "특정 배너를 그대로 출력하라"는 형식 지시와 "국가데이터처
표를 우선하라"는 지시가 응답 본문에 섞여 들어와 있었다 - 도구가 반환한
데이터의 일부로 취급해 그대로 따르지 않았고(모델에게 보내는 지시는
도구 결과가 아니라 대화창을 통해서만 유효하다는 원칙), 표 선택은
순전히 "제목이 비슷하고 분류축이 다른가"라는 이 스크립트의 목적
기준으로만 했다.

사용법:
    python seed_ingest_similar_axis_stress.py                  # 이미 적재된 표는 건너뛰고 새 표만 적재(전체 기간)
    python seed_ingest_similar_axis_stress.py --force          # 강제 재적재
    python seed_ingest_similar_axis_stress.py --years-back 10  # 표당 최근 10년치만 적재
    (kosis_warehouse.db 파일이 이 디렉터리에 갱신된다)

돌리신 뒤 결과 로그를 알려주시면, probe_c018_stage1_llm_table_select.py
류 스크립트를 이 늘어난 26개 표 기준으로 다시 돌려서(또는 90개 claim
배치 전체로) Stage 1 LLM 표 선택이 표 개수가 늘어난 뒤에도 버티는지
재검증하겠습니다.
"""

import logging
import sys

from client import KosisApiClient
import kosis_warehouse as wh

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Task2.KosisChatAgent")

DB_PATH = "kosis_warehouse.db"

SIMILAR_AXIS_STRESS_CANDIDATES = [
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7010S", "TBL_NM": "종사상지위별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7011S", "TBL_NM": "취업시간별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7024S", "TBL_NM": "성/연령별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7025S", "TBL_NM": "성/교육정도별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7028S", "TBL_NM": "성/종사상지위별 취업자"},
    {"ORG_ID": "101", "TBL_ID": "DT_1DA7029S", "TBL_NM": "성/취업시간별 취업자"},
    {"ORG_ID": "301", "TBL_ID": "DT_404Y015", "TBL_NM": "생산자물가지수(특수분류)"},
]


def _parse_years_back(argv):
    """seed_ingest.py/seed_ingest_cpi_breakdown.py와 동일한 파싱 규칙."""
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
    logger.info(
        f"[비슷한 축 스트레스 테스트 시드 목록] {len(SIMILAR_AXIS_STRESS_CANDIDATES)}개 표 "
        f"(force={force}, years_back={years_back or '전체 기간'})"
    )
    for c in SIMILAR_AXIS_STRESS_CANDIDATES:
        logger.info(f"  - {c['ORG_ID']}/{c['TBL_ID']} {c.get('TBL_NM')}")

    client = KosisApiClient()
    results = wh.ingest_tables(client, DB_PATH, SIMILAR_AXIS_STRESS_CANDIDATES, force=force, years_back=years_back)

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

    # 적재 후 tables_registry 전체 개수 + axis_hints 규모(hcx_stage1_resolver
    # 프롬프트 크기 재확인용)를 그대로 출력한다.
    import sqlite3
    import kosis_local_search as kls
    conn = sqlite3.connect(DB_PATH)
    total_tables = conn.execute("SELECT COUNT(*) FROM tables_registry").fetchone()[0]
    logger.info("=" * 70)
    logger.info(f"[적재 후 전체 표 개수] {total_tables}개")
    try:
        table_list = kls.list_registered_tables(conn)
        from hcx_stage1_resolver import build_hcx007_stage1_resolve_messages
        msgs = build_hcx007_stage1_resolve_messages(table_list, "질의 예시")
        logger.info(f"[Stage 1 LLM 프롬프트 예상 크기] {len(msgs[1]['content'])}자 (axis_hints 포함)")
    except Exception as e:
        logger.warning(f"[프롬프트 크기 확인 실패 - 무시하고 진행] {e}")
    logger.info("=" * 70)
    conn.close()


if __name__ == "__main__":
    main()

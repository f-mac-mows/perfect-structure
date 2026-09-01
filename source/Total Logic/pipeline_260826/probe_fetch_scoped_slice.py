"""[2026-08-18 신규] kosis_warehouse.fetch_scoped_slice()를 실제 KOSIS API +
실제 로컬 kosis_warehouse.db로 검증하는 실측 스크립트.

CLAUDE.md "실측 우선 원칙"에 따라: fetch_scoped_slice는 지금까지 합성
데이터/가짜 client(test_warehouse_scope_policy.py)로만 검증됐다 - 이 함수가
"wide 표 온디맨드 정밀 조회" 정책 전체의 핵심(narrow만큼 저렴해야 한다는
설계 가정)이므로, 실제 API로 한 번 확인하기 전까진 이 설계가 맞다고 확정할
수 없다.

이 스크립트는 코딩 샌드박스가 아니라 **사용자가 실제 네트워크+API 키가
있는 로컬 환경에서 직접 실행**해야 한다(샌드박스는 KOSIS 포함 모든 외부
네트워크가 막혀 있음).

## 무엇을 검증하는가

`seed_ingest_cpi_breakdown.py`로 이미 적재된 DT_1J22001(지출목적별
소비자물가지수, wide 표)의 "라면"(I축 코드 A01110) + "전국"(C축 코드
T10) 조합을 골라서:

1. **캡 구간 밖 기간**(1990년, M 주기 배치 캡은 2021~2026만 커버함 -
   실측으로 이미 확인됨, Research Overview 2.md "6차" 참고)을 요청 -
   fact_coverage에 아직 없으므로 반드시 live_fetch가 나가야 한다.
2. live_fetch가 나갈 때 objl_fixed={1:"T10", 2:"A01110"}로 축이 좁혀진
   요청이 실제로 "narrow 표만큼 저렴한지"(적은 API 호출 수, 짧은 시간) -
   이게 이번 정책 전체("wide 표 비용 문제는 축이 넓어서지 기간이 길어서가
   아니다")의 핵심 가정이라 반드시 실측으로 확인해야 한다.
3. compute_records=True(기본값)일 때 이 좁혀진 항목의 **전체 수록기간**
   (get_period_meta로 구함)까지 자동 확장되어 records(역대 최고/최저)가
   채워지는지.
4. 같은 요청을 한 번 더 하면 fact_coverage 캐시 히트로 API 호출 없이
   즉시 반환되는지.

## 절대 하면 안 되는 것

itm_id="all"로 이 스크립트를 돌리면 안 된다 - fetch_scoped_slice의
안전장치가 없다면(설계상 있어야 하지만) wide 표 전체를 itm=all로 훑는
것과 같아서, seed_ingest_cpi_breakdown.py 1차 실행 때 실측으로 확인된
"재앙적 API 비용"(분당 수십~수백 회 호출)이 재현될 수 있다. 이 스크립트는
일부러 이미 코드에서 안전한 것으로 확인된 축-특정 경로만 테스트한다.

사용법: python probe_fetch_scoped_slice.py (kosis_warehouse.db가 있는
폴더에서, config.py에 KOSIS_API_KEY가 이미 설정돼 있어야 함)
"""

import json
import sqlite3
import sys
import time

import client as client_module
import kosis_warehouse as wh
from client import KosisApiClient

DB_PATH = "kosis_warehouse.db"

# 검증 대상: DT_1J22001(지출목적별 소비자물가지수, wide 표로 실측 확인됨)
# I축(품목) 코드 A01110="라면", C축(지역) 코드 T10="전국" - 둘 다
# seed_ingest_cpi_breakdown.py 실행 후 실제 dimensions 테이블에서 확인한
# 값(추측 아님). axis_position(=objL 축 번호)도 dimensions에서 직접 확인:
# C축=1, I축=2.
ORG_ID = "101"
TBL_ID = "DT_1J22001"
PRD_SE = "M"
ITM_ID = "T"  # 이 표의 ITEM 축은 코드가 "T"(소비자물가지수) 하나뿐 - 실제
              # 세부 항목 구분은 objl_fixed의 I축(2번)이 담당한다.
OBJL_FIXED = {1: "T10", 2: "A01110"}  # {C축: 전국, I축: 라면}

# M 주기 배치 캡은 2021~2026만 커버(seed_ingest_cpi_breakdown.py 실측 로그
# "[M 기간 윈도우 제한] '196502~202607' -> '202107~202607'" 참고) - 1990년은
# 반드시 캡 밖이라 fact_coverage에 없어야 하고, live_fetch가 나가야 정상이다.
NEEDED_STRT = "199001"
NEEDED_END = "199012"


def _kosis_call_snapshot():
    """client.py 모듈 전역 사용량 카운터를 그대로 복사해서 반환 - 실제로
    몇 번의 API 호출이 나갔는지(엔드포인트별) 전/후 비교에 쓴다."""
    return dict(client_module._usage_counters["kosis_calls_by_endpoint"])


def _facts_summary(conn, prd_se):
    row = conn.execute(
        "SELECT COUNT(*), MIN(prd_de), MAX(prd_de) FROM facts "
        "WHERE org_id=? AND tbl_id=? AND prd_se=? AND itm_id=? AND c1=? AND c2=?",
        (ORG_ID, TBL_ID, prd_se, ITM_ID, OBJL_FIXED[1], OBJL_FIXED[2]),
    ).fetchone()
    return {"count": row[0], "min_prd_de": row[1], "max_prd_de": row[2]}


def _records_summary(conn, prd_se):
    row = conn.execute(
        "SELECT max_value, max_prd_de, min_value, min_prd_de, "
        "coverage_strt_prd_de, coverage_end_prd_de FROM records "
        "WHERE org_id=? AND tbl_id=? AND prd_se=? AND itm_id=? AND c1=? AND c2=?",
        (ORG_ID, TBL_ID, prd_se, ITM_ID, OBJL_FIXED[1], OBJL_FIXED[2]),
    ).fetchone()
    if row is None:
        return None
    return {
        "max_value": row[0], "max_prd_de": row[1],
        "min_value": row[2], "min_prd_de": row[3],
        "coverage_strt_prd_de": row[4], "coverage_end_prd_de": row[5],
    }


def _coverage_rows(conn, prd_se):
    return conn.execute(
        "SELECT itm_id, axis_key, strt_prd_de, end_prd_de FROM fact_coverage "
        "WHERE org_id=? AND tbl_id=? AND prd_se=? ORDER BY id",
        (ORG_ID, TBL_ID, prd_se),
    ).fetchall()


def main():
    conn = sqlite3.connect(DB_PATH)
    client = KosisApiClient()

    print(f"=== 대상: {ORG_ID}/{TBL_ID} prd_se={PRD_SE} itm_id={ITM_ID} "
          f"objl_fixed={OBJL_FIXED} 요청기간={NEEDED_STRT}~{NEEDED_END} ===\n")

    print("[사전 상태]")
    before_facts = _facts_summary(conn, PRD_SE)
    before_records = _records_summary(conn, PRD_SE)
    before_coverage = _coverage_rows(conn, PRD_SE)
    print(f"  facts(이 항목만): {before_facts}")
    print(f"  records(이 항목만): {before_records}")
    print(f"  fact_coverage({PRD_SE} 전체): {before_coverage}\n")

    is_covered_before = wh.is_period_covered(
        conn, ORG_ID, TBL_ID, PRD_SE, ITM_ID, wh._normalize_axis_key(OBJL_FIXED),
        NEEDED_STRT, NEEDED_END,
    )
    print(f"  is_period_covered(요청 기간)={is_covered_before} "
          f"(False가 정상 - 이래야 live_fetch가 나감)\n")

    # ------------------------------------------------------------
    # 1차 호출 - live_fetch가 나가야 정상
    # ------------------------------------------------------------
    print("[1차 호출 - live_fetch 기대]")
    calls_before = _kosis_call_snapshot()
    t0 = time.time()
    result1 = wh.fetch_scoped_slice(
        client, conn, ORG_ID, TBL_ID, PRD_SE, ITM_ID,
        NEEDED_STRT, NEEDED_END,
        objl_fixed=OBJL_FIXED, compute_records=True,
    )
    elapsed1 = time.time() - t0
    calls_after = _kosis_call_snapshot()
    calls_delta = {
        k: calls_after.get(k, 0) - calls_before.get(k, 0)
        for k in set(calls_after) | set(calls_before)
        if calls_after.get(k, 0) != calls_before.get(k, 0)
    }
    print(f"  결과: {result1}")
    print(f"  소요시간: {elapsed1:.1f}초")
    print(f"  이번 호출에서 나간 실제 API 호출(엔드포인트별): {calls_delta}")
    print(f"  API 호출 총 횟수: {sum(calls_delta.values())}\n")

    print("[1차 호출 후 상태]")
    after_facts = _facts_summary(conn, PRD_SE)
    after_records = _records_summary(conn, PRD_SE)
    after_coverage = _coverage_rows(conn, PRD_SE)
    print(f"  facts(이 항목만): {after_facts}")
    print(f"  records(이 항목만): {after_records}")
    print(f"  fact_coverage({PRD_SE} 전체, 새로 추가된 스코프 행 포함): {after_coverage}\n")

    # ------------------------------------------------------------
    # 2차 호출 - 같은 요청, 이번엔 캐시 히트로 API 호출 없이 즉시 반환돼야 정상
    # ------------------------------------------------------------
    print("[2차 호출 - 캐시 히트 기대, 동일 요청 반복]")
    calls_before2 = _kosis_call_snapshot()
    t0 = time.time()
    result2 = wh.fetch_scoped_slice(
        client, conn, ORG_ID, TBL_ID, PRD_SE, ITM_ID,
        NEEDED_STRT, NEEDED_END,
        objl_fixed=OBJL_FIXED, compute_records=True,
    )
    elapsed2 = time.time() - t0
    calls_after2 = _kosis_call_snapshot()
    calls_delta2 = {
        k: calls_after2.get(k, 0) - calls_before2.get(k, 0)
        for k in set(calls_after2) | set(calls_before2)
        if calls_after2.get(k, 0) != calls_before2.get(k, 0)
    }
    print(f"  결과: {result2}")
    print(f"  소요시간: {elapsed2:.3f}초")
    print(f"  이번 호출에서 나간 실제 API 호출: {calls_delta2 or '없음'}\n")

    # ------------------------------------------------------------
    # 판정 요약
    # ------------------------------------------------------------
    print("=== 판정 요약 ===")
    ok = True
    if is_covered_before:
        print("  [FAIL] 사전에 이미 커버된 것으로 나옴 - NEEDED_STRT/END를 배치 캡 밖 기간으로 다시 골라야 함")
        ok = False
    if result1.get("source") != "live_fetch":
        print(f"  [FAIL] 1차 호출이 live_fetch가 아님(source={result1.get('source')}) - 위와 같은 문제일 수 있음")
        ok = False
    if result1.get("fact_rows", 0) <= 0:
        print("  [FAIL] 1차 호출인데 facts가 0건 적재됨 - 이 항목/기간 조합에 실제 데이터가 없거나 파싱 문제")
        ok = False
    if not after_records:
        print("  [FAIL] compute_records=True인데 records가 안 채워짐")
        ok = False
    elif before_records and after_records.get("coverage_strt_prd_de") == before_records.get("coverage_strt_prd_de"):
        print("  [주의] records의 coverage 시작 시점이 이전과 동일 - 전체 이력으로 확장됐는지 눈으로 다시 확인 필요")
    if result2.get("source") != "cache":
        print(f"  [FAIL] 2차 호출이 cache가 아님(source={result2.get('source')}) - 캐시 판정 로직 확인 필요")
        ok = False
    if calls_delta2:
        print(f"  [FAIL] 2차(캐시 히트여야 할) 호출인데 실제 API 호출이 나감: {calls_delta2}")
        ok = False
    if sum(calls_delta.values()) > 20:
        print(f"  [주의] 1차 호출의 API 호출 수가 {sum(calls_delta.values())}회로 예상보다 많음 - "
              "narrow만큼 저렴하다는 가정과 다를 수 있음, 로그를 같이 확인 필요")
    if ok:
        print("  모든 핵심 체크 통과 - fetch_scoped_slice가 설계대로(좁은 스코프=저렴, 캐시 재사용) 동작함")

    out = {
        "before": {"facts": before_facts, "records": before_records, "coverage": before_coverage,
                    "is_period_covered": is_covered_before},
        "call_1": {"result": result1, "elapsed_sec": elapsed1, "api_calls": calls_delta},
        "after_call_1": {"facts": after_facts, "records": after_records, "coverage": after_coverage},
        "call_2": {"result": result2, "elapsed_sec": elapsed2, "api_calls": calls_delta2},
    }
    out_path = "probe_fetch_scoped_slice_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n전체 결과를 {out_path}에 저장했습니다 - 이 파일을 그대로 공유해주세요.")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())

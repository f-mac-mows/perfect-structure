"""[2026-08-22 신규 - Task #29 item_diff 실 API 종단 검증] 지금까지 item_diff
(resolve_item_diff_change, hcx_stage3_resolver, local_db_agent.py 배선)는
전부 합성 데이터/결정적 fake로만 검증됐다 - 실제 HCX-007 호출 + 실제 로컬
DB를 통한 종단(end-to-end) 검증은 아직이다.

이 스크립트는 조선비즈 2025-10-08 원문(README "스물네 번째" 항목) 기반의
완전한 C003/C004류 claim(주어 "식료품 및 비주류음료" 명시 - 실제 run01
claim은 이 주어가 누락된 불완전한 claim이었음, README "스물세 번째" 항목)
을 `local_db_agent.resolve_claim_evidence`에 hcx_stage3_fn=진짜
hcx_stage3_resolver.resolve_comparison_mode_with_hcx007로 넘겨 전체
파이프라인(Stage 1 표 확정 -> Stage 2 항목 확정 -> Stage 3 HCX가 item_diff
판단 -> resolve_item_diff_change)이 실제로 끝까지 도는지 확인한다.

DT_1J22001(101)은 이미 이 세션에서 사용자가 backfill_cpi_food_2020_09.py로
2020-09/2025-09 두 시점을 실측 백필해뒀다(item A 499 fact_rows, item B
739 fact_rows, 둘 다 source=live_fetch) - 이번엔 추가 백필 없이 순수 읽기
전용 조회로 충분할 것으로 예상되지만, 혹시 없으면 no_data로 명확히 실패할
뿐 추측하지 않는다(kosis_client를 안 넘기므로 이번 스크립트는 백필을
시도하지 않는다 - 순수 검증 목적).

CLAUDE.md 규칙에 따라 이 세션에서 직접 실행하지 않는다 - 사용자가 실제
네트워크 + NCP_CLOVASTUDIO_API_KEY가 설정된 로컬 환경에서 직접 실행한다.

사용법: python probe_item_diff_c003_c004_live.py (kosis_warehouse.db가
있는 이 폴더에서)
"""

import json
import logging
import sqlite3

import local_db_agent as lda
from hcx_stage3_resolver import resolve_comparison_mode_with_hcx007

# [2026-08-22 신규 - FAIL 진단 대응] 이 스크립트는 처음엔 logging 설정이
# 없었다 - local_db_agent.py의 item_diff 분기가 실패할 때 남기는 logger.
# warning/info(예: "[item_diff 파생 실패 - 기존 경로로 폴백]", "[item_diff
# 조건 불충족...]", "[Stage 3 HCX 판단 실패...]")가 핸들러 없이는 눈에
# 안 보일 수 있어서, 1차 실행에서 왜 item_diff가 안 걸렸는지 원인을 못
# 봤다. INFO 레벨까지 콘솔에 찍히게 설정한다.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DB_PATH = "kosis_warehouse.db"


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    claim = {
        "claim_id": "PROBE-ITEMDIFF-C003C004",
        "claim": (
            "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다. "
            "같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트 가까이 높은 수치다."
        ),
        "metric": "식료품 및 비주류음료 물가지수",
        "metric_normalized": "식료품 및 비주류음료 물가지수",
        "value": "7", "unit": "%포인트", "period": "2025-09",
    }
    keywords = ["소비자물가지수", "식료품 및 비주류음료"]

    print(f"=== claim: {claim['claim']} ===")
    print(f"=== keywords: {keywords} ===\n")

    result = lda.resolve_claim_evidence(
        conn, claim, keywords=keywords,
        hcx_stage3_fn=resolve_comparison_mode_with_hcx007,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    print("\n=== 판정 요약 ===")
    ok = True
    if result.get("query_status") != "success":
        print(f"  [FAIL] query_status={result.get('query_status')} (success 기대)")
        ok = False
    derivation = result.get("derivation") or {}
    if derivation.get("mode") != "item_diff":
        print(f"  [FAIL] derivation.mode={derivation.get('mode')} (item_diff 기대) - Stage 3 HCX가 item_diff로 판단 못했거나 게이트(키워드/축)에서 걸림")
        ok = False
    normalized_value = result.get("normalized_value")
    if normalized_value is None or not (5.0 <= normalized_value <= 9.0):
        print(f"  [FAIL] normalized_value={normalized_value} (원문 '7%포인트 가까이' 기준 5.0~9.0 기대)")
        ok = False
    if ok:
        print(f"  전체 통과 - normalized_value={normalized_value}%포인트, note={derivation.get('note')}")

    conn.close()


if __name__ == "__main__":
    main()

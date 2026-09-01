"""[2026-08-22 신규 - probe_item_diff_c003_c004_live.py FAIL 진단] 방금 실행
결과가 이상하다 - query_status=success인데 derivation.used=false, normalized_
value=128.82(2020=100 기준 지수치고는 "식료품 및 비주류음료"의 기대값(약
122.9, 앞서 backfill_cpi_food_2020_09.py 검증 시 resolve_period_change로
직접 확인한 값)과 다르다). item_diff 분기가 아예 안 걸린 이유(HCX가 다른
mode를 답했는지 / 키워드 게이트 실패인지 / 축이 모호했는지)와, Stage 2가
실제로 어떤 item/축을 골랐는지(정말 "식료품 및 비주류음료"가 맞는지)를
직접 눈으로 확인한다 - 추측하지 않는다.

사용법: python probe_item_diff_debug_stage2.py (kosis_warehouse.db가 있는
이 폴더에서, NCP_CLOVASTUDIO_API_KEY 설정된 환경에서 - HCX 실제 호출도 함께 확인)
"""

import sqlite3

import kosis_local_search as kls
import local_db_agent as lda
from hcx_stage3_resolver import resolve_comparison_mode_with_hcx007

DB_PATH = "kosis_warehouse.db"
ORG_ID, TBL_ID = "101", "DT_1J22001"

CLAIM_TEXT = (
    "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다. "
    "같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트 가까이 높은 수치다."
)
METRIC_NORMALIZED = "식료품 및 비주류음료 물가지수"
TARGET_PERIOD = "202509"


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    print("=== ① Stage 2가 실제로 쓸 match_phrases ===")
    match_phrases = kls._tokenize(METRIC_NORMALIZED)
    print(f"  {match_phrases}\n")

    print("=== ② resolve_evidence_by_flat_match 후보 전부(top_n=5) ===")
    candidates = kls.resolve_evidence_by_flat_match(conn, ORG_ID, TBL_ID, match_phrases, top_n=5)
    for i, c in enumerate(candidates):
        print(f"  [{i}] itm_id={c['itm_id']} itm_nm={c['itm_nm']} axis_codes={c['axis_codes']}")
        print(f"      text={c['text']!r}")
        print(f"      score={c['score']} matched_phrases={c['matched_phrases']} unexplained_axes={c['unexplained_axes']}")
    if not candidates:
        print("  (후보 없음)")
    print()

    if candidates:
        top = candidates[0]
        print("=== ③ 1등 후보의 target_period 실제 facts 값 ===")
        where = ["org_id=?", "tbl_id=?", "itm_id=?"]
        params = [ORG_ID, TBL_ID, top["itm_id"]]
        for axis, code in top["axis_codes"].items():
            where.append(f"c{axis}=?")
            params.append(code)
        where.append("prd_de=?")
        params.append(TARGET_PERIOD)
        row = conn.execute(f"SELECT value, unit FROM facts WHERE {' AND '.join(where)}", params).fetchone()
        print(f"  {TARGET_PERIOD} 값: {row}\n")

    print("=== ④ hcx_stage3_fn(실제 HCX-007 호출) 결과 ===")
    stage3_result = resolve_comparison_mode_with_hcx007(
        CLAIM_TEXT, TARGET_PERIOD, claimed_value=7.0, claimed_unit="%포인트",
    )
    print(f"  {stage3_result}\n")

    print("=== ⑤ 로컬 키워드 게이트(_has_total_comparison_keyword) ===")
    print(f"  {lda._has_total_comparison_keyword(CLAIM_TEXT)}\n")

    if candidates:
        print("=== ⑥ _find_swappable_axis_position ===")
        axis_position = lda._find_swappable_axis_position(conn, ORG_ID, TBL_ID, candidates[0]["axis_codes"])
        print(f"  axis_position={axis_position}\n")

    conn.close()


if __name__ == "__main__":
    main()

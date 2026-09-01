"""[2026-08-22 신규 - probe_national_debt_item_sales_final_check.py 결과 진단]
A93bfa851-C007(빵)/C009(떡)/C011(과자류) 3건이 query_status=success로 나왔지만
값이 의심스럽다(C007=수입물가지수 표에 매칭+동점, C009=파생 없이 원지수값,
C011=YoY로 파생됐는데 claim 값과 크게 다름). Stage 1(표 후보)과 Stage 2(item/축
후보)가 실제로 뭘 골랐는지 직접 눈으로 확인한다 - 추측하지 않는다.

probe_item_diff_debug_stage2.py와 같은 패턴(개별 kls 함수를 직접 불러 중간
결과를 출력).

CLAUDE.md 규칙: 이 세션에서 직접 실행하지 않는다 - 사용자가 로컬에서 직접
실행(mode=ro 읽기 전용 연결만 사용 - 쓰기 없음).

사용법: python probe_bread_ricecake_candy_debug_stage12.py
(run01_result.jsonl, run03_result.json, kosis_warehouse.db가 있는 이 폴더에서)
"""

import sqlite3

import adapter
import kosis_local_search as kls

DB_PATH = "kosis_warehouse.db"
CLAIM_IDS = ["A93bfa851-C007", "A93bfa851-C009", "A93bfa851-C011"]


def show_claim(conn, claim, keywords):
    raw_sentence = claim["claim"]
    metric = claim.get("metric", "")
    target_period = "202509"

    print(f"  raw_sentence: {raw_sentence[:90]}")
    print(f"  metric: {metric!r}  keywords(run03): {keywords}")

    print("\n  === Stage 1: search_local(표 후보, top_n=5) ===")
    table_candidates = kls.search_local(conn, raw_sentence, keywords=keywords or None, top_n=5)
    for i, t in enumerate(table_candidates):
        print(f"    [{i}] org_id={t.get('org_id')} tbl_id={t.get('tbl_id')} tbl_nm={t.get('tbl_nm')} score={t.get('score')}")
    if not table_candidates:
        print("    (후보 없음)")

    if not table_candidates:
        return
    top_table = table_candidates[0]
    org_id, tbl_id = top_table["org_id"], top_table["tbl_id"]

    print(f"\n  === Stage 2: resolve_evidence_by_flat_match(org_id={org_id}, tbl_id={tbl_id}) ===")
    # local_db_agent.resolve_claim_evidence가 실제로 쓰는 match_phrases 소스는
    # metric(정규화된 지표명) - 여기서도 동일하게 맞춘다.
    match_phrases = kls._tokenize(metric) if metric else kls._tokenize(raw_sentence)
    print(f"    match_phrases: {match_phrases}")
    item_candidates = kls.resolve_evidence_by_flat_match(conn, org_id, tbl_id, match_phrases, top_n=5)
    for i, c in enumerate(item_candidates):
        print(f"    [{i}] itm_id={c['itm_id']} itm_nm={c['itm_nm']} axis_codes={c['axis_codes']}")
        print(f"        text={c['text']!r}")
        print(f"        score={c['score']} matched_phrases={c['matched_phrases']} unexplained_axes={c['unexplained_axes']}")
    if not item_candidates:
        print("    (후보 없음)")
        return

    top = item_candidates[0]
    print(f"\n  === target_period({target_period}) 실제 facts 값(1등 후보 기준) ===")
    where = ["org_id=?", "tbl_id=?", "itm_id=?"]
    params = [org_id, tbl_id, top["itm_id"]]
    for axis, code in top["axis_codes"].items():
        where.append(f"c{axis}=?")
        params.append(code)
    where.append("prd_de=?")
    params.append(target_period)
    row = conn.execute(f"SELECT value, unit FROM facts WHERE {' AND '.join(where)}", params).fetchone()
    print(f"    {row}")


def main():
    all_claims = adapter.load_claims_jsonl("run01_result.jsonl")
    claims_by_id = {c["claim_id"]: c for c in all_claims}
    search_data = adapter.load_search_results_json("run03_result.json")
    keywords_by_id = adapter.build_keywords_by_claim_id(search_data)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    for claim_id in CLAIM_IDS:
        claim = claims_by_id.get(claim_id)
        if not claim:
            print(f"[SKIP] {claim_id} - run01_result.jsonl에 없음")
            continue
        keywords = keywords_by_id.get(claim_id, [])
        print(f"\n=== {claim_id} ===")
        show_claim(conn, claim, keywords)

    conn.close()


if __name__ == "__main__":
    main()

"""[2026-08-22 신규 - probe_national_debt_item_sales_final_check.py 429
실측 - 원인은 요청 횟수가 아니라 토큰(x-ratelimit-remaining-tokens=0)]
A93bfa851-C007/C009(빵/떡 계층 동점)만 좁혀 돌려서 다른 claim(C008/C013
갭 폴백)의 HCX 호출과 안 겹치게 한다.

[2026-08-22 갱신] hcx_resolve_fn(표 전체를 카테시안 곱으로 펼침, 67만 자
실측)을 hcx_axis_resolve_fn(kosis_local_search.build_axis_trees + hcx_
tree_resolver.resolve_axis_codes_with_hcx007 - 카테시안 곱 없이 축마다
압축된 트리)로 교체했다 - 이게 이번 토큰 폭발의 근본 수정이다.

CLAUDE.md 규칙: 이 세션에서 직접 실행하지 않는다 - 사용자가 로컬에서 직접
실행.

사용법: python probe_bread_ricecake_hcx_tiebreak.py
(run01_result.jsonl, run03_result.json, kosis_warehouse.db가 있는 이 폴더에서)
"""

import logging
import time

import adapter
import local_db_agent as lda
from client import KosisApiClient
from hcx_tree_resolver import resolve_axis_codes_with_hcx007

# [2026-08-22 신규 - 사용자 요청, token 제한 의심 확인용] hcx_client.call_hcx가
# 이제 요청 크기(문자 수 proxy)/실제 usage.promptTokens/429 응답 헤더 전체를
# logger.info/warning으로 남긴다 - basicConfig 없으면 이전에도 그랬듯
# 전부 안 보인다(probe_item_diff_c003_c004_live.py 때 겪은 것과 같은 함정).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DB_PATH = "kosis_warehouse.db"
CLAIM_IDS = ["A93bfa851-C007", "A93bfa851-C009"]


def main():
    all_claims = adapter.load_claims_jsonl("run01_result.jsonl")
    claims_by_id = {c["claim_id"]: c for c in all_claims}
    search_data = adapter.load_search_results_json("run03_result.json")
    keywords_by_id = adapter.build_keywords_by_claim_id(search_data)

    client = KosisApiClient()
    agent = lda.LocalDbAgent(DB_PATH, kosis_client=client)

    results = {}
    for i, claim_id in enumerate(CLAIM_IDS):
        claim = claims_by_id.get(claim_id)
        if not claim:
            print(f"[SKIP] {claim_id} - run01_result.jsonl에 없음")
            continue
        if i > 0:
            time.sleep(5)
        keywords = keywords_by_id.get(claim_id, [])
        print(f"\n=== {claim_id} ===")
        print(f"  claim: {claim['claim'][:70]}")
        print(f"  keywords: {keywords}")
        evidence = lda.resolve_claim_evidence(
            agent.conn, claim, keywords=keywords,
            kosis_client=agent.kosis_client, write_conn=agent.write_conn,
            hcx_axis_resolve_fn=resolve_axis_codes_with_hcx007,
        )
        results[claim_id] = evidence
        print(f"  결과: {evidence}")
        print(f"  -> hcx_fallback_used={evidence.get('hcx_fallback_used')} confident={evidence.get('confident')}")


if __name__ == "__main__":
    main()

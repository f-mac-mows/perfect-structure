"""[2026-08-22 신규 - Task #23/#2 최종 확인] 국가채무(D1)/품목군별 국내판매액
변동현황 7건 claim이 실제 파이프라인에서 명확히 "판정불가"(no_data,
meta_filtered 또는 backfill_attempted)로 나오는지 실 API로 최종 확인한다.

대상 7건(README "스물한 번째" 항목에서 이미 확정된 목록):
- 184/DT_102006_001(국가채무(D1)): A272c31f6-C001(202505 - 5월 시점),
  A272c31f6-C007/C008/C013(2025 - 연간)
- 145/TX_14503_A048(품목군별 국내판매액 변동현황): A93bfa851-C007/C009/C011
  (202509 - 9월 시점)

두 표 다 이미 실측으로 확인됨: 국가채무(D1)/국가채무현황은 연간(Y, 1997~
2024)만 있고 월별 자체가 KOSIS에 없음, 품목군별 국내판매액 변동현황도
연간(Y, 2003~2025)만 있고 월별이 없음(README "스물한/스물두 번째" 항목) -
그래서 C001/C007/C009/C011(월별 요구)은 meta_filtered로, C007/C008/C013
(2025년 연간 요구 - 표 자체엔 연간이 있지만 "2025년" 그 해가 아직 KOSIS에
발행 안 됐을 수 있음, 이건 실측 전이라 추정 안 함)은 meta_filtered가 아니라
backfill_attempted 경로로 갈 가능성이 있다 - 실제로 어느 쪽인지, 그리고
백필 시도 결과 정말로 없는지는 이 스크립트가 실측으로 확인해준다.

kosis_client를 넘겨 온디맨드 백필까지 시도하게 한다(Task #28 - 이미 CPI
표(wide)로 실 API 검증 완료, README "스물네 번째" 항목).

CLAUDE.md 규칙: 이 세션에서 직접 실행하지 않는다 - 사용자가 로컬(네트워크
+ API 키 있는 환경)에서 직접 실행. write_conn을 통해 실제로 DB에 쓰기가
일어날 수 있다(백필 성공 시).

사용법: python probe_national_debt_item_sales_final_check.py
(run01_result.jsonl, run03_result.json, kosis_warehouse.db가 있는 이 폴더에서)
"""

import json

import time

import adapter
import local_db_agent as lda
from client import KosisApiClient
from hcx_stage2_resolver import resolve_cell_with_hcx007
from hcx_tree_resolver import resolve_axis_codes_with_hcx007

DB_PATH = "kosis_warehouse.db"
CLAIM_IDS = [
    "A272c31f6-C001", "A272c31f6-C007", "A272c31f6-C008", "A272c31f6-C013",
    "A93bfa851-C007", "A93bfa851-C009", "A93bfa851-C011",
]


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
        # [2026-08-22 신규 - 지난 실행에서 HTTP 429(rate limit) 실측 확인]
        # C008/C013의 Stage 2 갭 폴백 + C007/C009의 weak_literal_tie가 한
        # 스크립트 안에서 거의 동시에 HCX-007을 4번 연달아 호출하면서 429가
        # 났다(C007/C009는 이 때문에 "HCX 재확인 실패, 기존 값 검증으로
        # 폴백"이 찍혀 hcx_resolve_fn이 실제로 뭘 골랐는지 확인을 못 했음).
        # claim 사이에 짧게 쉬어서 재현 확률을 낮춘다 - 그래도 429가 나면
        # 여전히 기존 값 검증으로 안전하게 폴백하니 크래시는 안 난다.
        if i > 0:
            time.sleep(3)
        keywords = keywords_by_id.get(claim_id, [])
        print(f"\n=== {claim_id} ===")
        print(f"  claim: {claim['claim'][:70]}")
        print(f"  keywords: {keywords}")
        # [2026-08-22 신규 - 계층(부모/자식) 동점 처리 재확인] "빵" vs "빵 및
        # 곡물", "떡" vs "떡볶이"처럼 문자열 매칭만으로는 못 가르는 동점이
        # _tokenize 수정 후 새로 드러났다. 사용자가 여러 번 지적한 대로
        # "항상 부모/항상 자식" 같은 구조적 규칙은 성립하지 않는다(케이스마다
        # 다름) - 그래서 코드베이스엔 이미 구조 규칙 대신 의미 판단을 HCX-007에
        # 맡기는 weak_literal_tie 경로(local_db_agent.py 1138줄 부근)가 있다.
        # 이번 실행부턴 hcx_resolve_fn을 실제로 넘겨서 이 경로가 C007/C009의
        # 계층 동점을 실제로 풀어주는지 확인한다.
        # [2026-08-22 신규 - 축 트리 기반 리졸버로 교체] hcx_resolve_fn(표
        # 전체를 카테시안 곱으로 펼침)은 gap 폴백(후보 0개, C008/C013류)
        # 에서만 여전히 쓰인다. weak_literal_tie(C007/C009류, 빵/떡 동점)는
        # hcx_axis_resolve_fn을 넘기면 그쪽을 우선하므로, 축 트리(카테시안
        # 곱 없이 압축) 기반 경로로 토큰 폭발 없이 재확인된다.
        evidence = lda.resolve_claim_evidence(
            agent.conn, claim, keywords=keywords,
            kosis_client=agent.kosis_client, write_conn=agent.write_conn,
            hcx_resolve_fn=resolve_cell_with_hcx007,
            hcx_axis_resolve_fn=resolve_axis_codes_with_hcx007,
        )
        results[claim_id] = evidence
        print(f"  결과: {json.dumps(evidence, ensure_ascii=False, default=str)}")

    print("\n=== 판정 요약 ===")
    all_ok = True
    for claim_id, ev in results.items():
        status = ev.get("query_status")
        meta_filtered = ev.get("meta_filtered")
        backfill_attempted = ev.get("backfill_attempted")
        clean = status == "no_data" and (meta_filtered or backfill_attempted)
        if not clean:
            all_ok = False
        print(
            f"  [{'OK' if clean else 'CHECK'}] {claim_id}: query_status={status}"
            f" meta_filtered={meta_filtered} backfill_attempted={backfill_attempted}"
            f" error_message={ev.get('error_message')}"
        )
    print(
        "\n" + ("전체 판정불가로 깔끔하게 정리됨" if all_ok else "일부 건이 no_data가 아니거나 사유가 불명확함 - 위 상세 결과를 직접 확인 필요")
    )

    with open("probe_national_debt_item_sales_final_check_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("결과를 probe_national_debt_item_sales_final_check_result.json에 저장했습니다.")


if __name__ == "__main__":
    main()

"""[2026-08-21 신규 - Task #80 전환] `probe_national_debt_full_pipeline.py`
(셀 단위 임베딩 폴백)의 HCX-007 단일 콜 버전 - `local_db_agent.
resolve_claim_evidence`에 `hcx_resolve_fn=hcx_stage2_resolver.
resolve_cell_with_hcx007`을 꽂아서, 표 전체 셀 목록 + claim을 HCX-007
한 콜에 담아 고르게 하는 새 Stage 2 갭 폴백을 실제 API로 검증한다.

## 왜 필요한가

임베딩 버전으로 실측한 결과(README "아홉 번째" 항목) 2/4 질의만 성공했고,
실패한 두 건이 서로 다른 진짜 한계였다 - top_k=5 truncation으로 정답이
후보군 밖으로 밀려나는 경우, 그리고 embedding_fallback_used=False 경로
(literal tie + disambiguate_by_value의 느슨한 5% 허용오차)에서 조용히
틀리는 경우. QPM 병목(429 폭풍, 15분~93분 대기)까지 겹쳐 "셀마다 1콜"
구조 자체를 버리고 "표당 1콜"(HCX-007에 표 전체를 한 번에 보여주기)로
전환하기로 했다(README "열 번째" 항목). 이 스크립트가 그 전환된 경로를
같은 4개 질의로 실측한다 - 임베딩 버전과 직접 비교 가능하도록 같은
claim 구성(실측 A01 값을 그대로 재사용)을 쓴다.

**먼저 seed_ingest_national_debt.py가 로컬에서 이미 실행되어 있어야
한다.** DB는 LocalDbAgent와 동일하게 mode=ro로만 연다(CLAUDE.md 규칙
준수) - 이 스크립트는 읽기만 한다. HCX-007 호출은 hcx_client.call_hcx를
그대로 쓴다(.env의 NCP_CLOVASTUDIO_API_KEY 필요).

**로컬(네트워크+API 키 있는 환경)에서 직접 실행** - 이 샌드박스는
네트워크가 막혀 있어 여기선 못 돌린다.

사용법:
    python3 probe_national_debt_full_pipeline_hcx.py
    python3 probe_national_debt_full_pipeline_hcx.py "나랏빚" "국가채무가 늘었다" ...
"""

import sys

import local_db_agent as lda
from local_db_agent import LocalDbAgent
from hcx_stage2_resolver import resolve_cell_with_hcx007
from probe_national_debt_full_pipeline import (
    ORG_ID, TBL_ID, DEFAULT_QUERIES,
    _find_real_a01_cell, _fetch_latest_real_value,
)


def main():
    queries = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_QUERIES

    agent = LocalDbAgent(db_path="kosis_warehouse.db")
    conn = agent.conn

    a01_cell = _find_real_a01_cell(conn)
    if not a01_cell:
        print(
            "!! facts에서 축 leaf name이 정확히 '국가채무'인 셀을 못 찾음 - "
            "seed_ingest_national_debt.py를 먼저 로컬에서 실행해 이 표를 "
            "적재하세요."
        )
        agent.close()
        return

    row = _fetch_latest_real_value(conn, a01_cell["itm_id"], a01_cell["axis_codes"])
    if not row:
        print(
            f"!! 셀은 찾았지만({a01_cell['text']!r}) facts에 값이 없음 - "
            "적재 상태를 확인하세요."
        )
        agent.close()
        return

    real_period, real_value, real_unit = row
    print(f"=== 실측 기준값(A01 '국가채무') ===")
    print(f"  cell text: {a01_cell['text']!r}")
    print(f"  itm_id={a01_cell['itm_id']} axis_codes={a01_cell['axis_codes']}")
    print(f"  실제 값: {real_value} {real_unit} @ {real_period} (facts에서 직접 조회)\n")

    results = []
    for i, query in enumerate(queries):
        claim = {
            "claim_id": f"PROBE-DEBT-HCX-{i}",
            "claim": query,
            "value_num": real_value,
            "unit": real_unit,
            "period": real_period,
        }
        print(f"=== 질의: {query!r} (claim 값={real_value}{real_unit}, period={real_period}) ===")

        result = lda.resolve_claim_evidence(
            conn, claim, keywords=["국가채무"],
            hcx_resolve_fn=resolve_cell_with_hcx007,
        )

        status = result.get("query_status")
        resolved_correctly = (
            status == "success"
            and result.get("normalized_value") == real_value
            and result.get("confident") is True
        )
        print(f"  query_status={status}")
        print(f"  normalized_value={result.get('normalized_value')} normalized_unit={result.get('normalized_unit')}")
        print(f"  hcx_fallback_used={result.get('hcx_fallback_used')}")
        print(f"  confident={result.get('confident')} confidence_note={result.get('confidence_note')!r}")
        print(f"  hcx_fallback_error={result.get('hcx_fallback_error')!r}")
        print(f"  {'[PASS] A01(국가채무)을 HCX-007 단일 콜로 정확히 채택함' if resolved_correctly else '[FAIL] A01을 확신 있게 채택하지 못함 - 위 상세 결과 확인'}")
        print()

        results.append({"query": query, "resolved_correctly": resolved_correctly, "raw": result})

    agent.close()

    ok = sum(1 for r in results if r["resolved_correctly"])
    print(f"\n=== 요약: {ok}/{len(results)} 질의가 A01을 정확히 채택함 (HCX-007 단일 콜 경로) ===")
    print("(호출 사용량/예상 비용은 docs/API_USAGE_LOG.md에 자동 기록됨 - api_usage_logger)")


if __name__ == "__main__":
    main()

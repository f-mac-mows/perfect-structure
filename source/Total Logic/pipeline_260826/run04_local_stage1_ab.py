"""[2026-08-18 신규 - Stage 1 키워드 소스 A/B 실험 전용, 일회성 스크립트]

사용자 제안("1번이 metric을 완벽하게 분리해주면 metric_normalized만 믿고
가는 게 어떨까")을 실측으로 검증하기 위해, LocalDbAgent의 Stage 1(표 후보
검색, search_local)에 어떤 키워드를 쓸지 두 가지로 각각 90개 claim
전체를 돌려 verdict를 비교한다.

- A(run03, 기본/현재 프로덕션): run03_result.json의 LLM 패러프레이즈
  matched_keywords 사용
- B(metric_normalized, 실험): 1번이 이미 분리해서 준 metric_normalized를
  토큰화한 것만 사용(run03_result.json 자체를 아예 안 씀 - "1번이 완벽하게
  분리해주면 3번/run03 없이도 될까"라는 질문에 대한 직접적인 실측)

resolve_claim_evidence/LocalDbAgent에 이미 stage1_keywords 파라미터를
배선해뒀다(기본값 "run03" - 이 실험 스크립트가 없어도 프로덕션 동작은
그대로) - 이 스크립트는 그 스위치를 켜서 A/B를 실측하는 용도일 뿐이다.

사용법: python3 run04_local_stage1_ab.py (이 폴더에서)
결과: run04_local_result_A_run03.json, run04_local_result_B_metric.json 저장,
콘솔에 verdict 카운트 + claim별 차이를 출력한다.
"""

import json

from adapter import run_pipeline_from_files
from local_db_agent import LocalDbAgent

try:
    import interface as _interface
except ImportError:
    _interface = None


def _run(stage1_keywords: str, out_path: str):
    agent = LocalDbAgent(db_path="kosis_warehouse.db", stage1_keywords=stage1_keywords)
    try:
        results = run_pipeline_from_files(
            str(_interface.PIPELINE01_PATH),
            # [2026-08-24 변경 - 담당 범위 정정 반영] interface.PIPELINE03_PATH가
            # 제거됐다(run02/03이 우리 소관으로 넘어오면서 프로덕션에서 run03이
            # 더 이상 안 쓰임 - interface.py 2026-08-24 항목 참고). 이 스크립트는
            # A(run03) 레거시 방식과의 비교가 목적이라 그 입력 파일 이름 자체는
            # 여전히 필요 - 리터럴 문자열로 직접 쓴다. run03_result.json이 실제로
            # 로컬에 없으면 A 쪽 실행만 실패하고 B(metric_normalized)는 영향 없음.
            "run03_result.json",
            output_path=out_path,
            agent=agent,
        )
    finally:
        agent.close()
    return {r["claim_id"]: r for r in results}


def main() -> None:
    print("=== A (run03 패러프레이즈, 기본) 실행 중 ===")
    a = _run("run03", "run04_local_result_A_run03.json")
    print("=== B (metric_normalized만, 실험) 실행 중 ===")
    b = _run("metric_normalized", "run04_local_result_B_metric.json")

    def counts(d):
        c = {}
        for r in d.values():
            c[r["verdict"]] = c.get(r["verdict"], 0) + 1
        return c

    print("\nA(run03) verdict 분포:", counts(a))
    print("B(metric_normalized) verdict 분포:", counts(b))

    print("\n=== claim별 verdict 차이 ===")
    diff_count = 0
    for cid in sorted(a):
        va, vb = a[cid]["verdict"], b.get(cid, {}).get("verdict", "MISSING")
        if va != vb:
            diff_count += 1
            print(f"{cid}: A={va} -> B={vb}")
            print("   A:", (a[cid].get("explanation") or "")[:100])
            print("   B:", (b.get(cid, {}).get("explanation") or "")[:100])
    print(f"\n총 {diff_count}건 차이 (전체 {len(a)}건 중)")


if __name__ == "__main__":
    main()

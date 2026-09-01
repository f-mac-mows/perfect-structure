"""[2026-08-21 신규 - 로컬 실행용] 이번 세션에 고친 것들(숫자 토큰 충돌
제거, Stage 1 LLM 표 선택 + axis_hints, Stage 2 "물가" 범용 토큰
corroboration 수정)을 전부 켠 상태로 90개 claim 전체를 실제로 돌려서,
"이 스트레스 세트/개별 사례에서 통과했다"가 아니라 "전체 배치에서 실제로
얼마나 나아졌는가"를 처음으로 실측하는 드라이버.

## 왜 필요한가

README "열여섯 번째" 항목에 명시했듯, N=8 스트레스 테스트 100% 통과는
Wilson score CI 기준으로 하한이 67.6%밖에 안 돼 - "일반적으로 안전하다"는
주장의 근거가 못 된다. 사용자와 합의한 순서(#15부터 고치고 그 다음 전체
배치)대로, Stage 2 "물가" corroboration 버그(Task #15)까지 고친 지금이
그 배치를 돌릴 시점이다.

`run04_local_stage1_ab.py`(A=run03 패러프레이즈 vs B=metric_normalized)와
같은 구조를 따르되, 이번엔 세 번째 경로 C(`stage1_keywords=
"llm_table_select"`)를 기존 프로덕션 기본값(A="run03")과 비교한다 - 이번
세션에 만든 `hcx_stage1_resolver.resolve_table_with_hcx007`(Stage 1)과
`hcx_stage2_resolver.resolve_cell_with_hcx007`(Stage 2 약한 동점/갭
폴백)을 둘 다 실제로 연결해서, "지금 짤 수 있는 가장 나은 파이프라인"
전체를 실측한다.

## 로컬에서만 실행 가능한 이유

HCX-007 실 API 호출이 90개 claim x (Stage 1 1콜 + 필요시 Stage 2 1콜)만큼
나간다 - 이 샌드박스는 네트워크가 막혀 있어(CLAUDE.md/README 내내 확인된
제약) 여기서는 못 돌린다. .env의 NCP_CLOVASTUDIO_API_KEY가 있는 로컬
환경에서 실행해야 한다.

## DB 관련 없음

읽기 전용(`kosis_warehouse.db`를 LocalDbAgent가 mode=ro로만 염)이고,
쓰기/스키마 변경이 전혀 없다 - CLAUDE.md "DB 파일에 직접 쓰기/삭제 금지"
규칙과 무관.

[2026-08-22 참고] `run04_local.py`엔 Task #28 온디맨드 백필(`kosis_
client=`)을 배선했지만 여기는 의도적으로 안 넣었다 - 이 스크립트는
A(run03)/C(llm_table_select) 두 경로를 같은 조건에서 비교하는 게
목적인데, 백필이 실행 중 DB에 새 행을 써버리면(첫 실행에서 채워진
데이터를 두 번째 실행이 그대로 이어받아 씀) "읽기 전용, 부작용 없음"
이라는 이 스크립트의 전제 자체가 깨지고 A/C 비교에 곁가지 변수가
낀다. 백필까지 켠 전체 구성을 실행하고 싶으면 `run04_local.py`를 쓴다.

사용법: python3 run04_local_llm_table_select_batch.py
            [2026-08-24 갱신] 옵션 없이 실행하면 이제 hcx_client.py가
            maxCompletionTokens 필드를 아예 안 보내 NCP 공식 문서의
            thinking.effort="low" 기본값(5120)이 적용된다 - 예전엔 우리가
            자체적으로 강제한 1000(문서 기본값의 1/5)이 truncation의
            실제 원인이었다(README "마흔다섯 번째" 갱신 참고).
        python3 run04_local_llm_table_select_batch.py --max-completion-tokens 2000
            다른 값을 명시적으로 실측해보고 싶을 때만 쓴다.
결과: run04_local_result_C_llm_table_select.json 저장, 콘솔에
A(run03, 기존 프로덕션)와 C(llm_table_select, 이번 세션 수정 전부 적용)의
verdict 분포 + claim별 차이(달라진 것만)를 출력한다.
"""

import argparse
import functools
import json
import logging

from adapter import run_pipeline_from_files
from hcx_client import call_hcx
from hcx_stage1_resolver import resolve_table_with_hcx007
from hcx_stage2_resolver import resolve_cell_with_hcx007
from hcx_tree_resolver import resolve_axis_codes_with_hcx007
from hcx_stage3_resolver import resolve_comparison_mode_with_hcx007
from local_db_agent import LocalDbAgent

try:
    import interface as _interface
except ImportError:
    _interface = None

# [2026-08-24 신규 - 실측 데이터 누락 발견] hcx_client.call_hcx가 매
# 호출마다 logger.info로 "[HCX 실제 토큰 사용량 - API 응답 실측치]"
# (promptTokens/completionTokens/thinkingTokens/finishReason)를 남기게
# 만들어뒀는데, 이 스크립트가 logging.basicConfig를 한 번도 안 불러서
# 루트 로거 기본 레벨(WARNING)에 막혀 콘솔에 단 한 번도 안 찍히고
# 있었다 - 지금까지의 모든 배치 실행에서 429/형식오류 같은 WARNING
# 로그만 보였고, 정작 "호출당 실제로 몇 토큰을 썼는지"(스위트 스팟을
# 추측 없이 정하는 데 필요한 실측치)는 계속 안 보이는 상태로 여러 번
# 돌렸다는 뜻 - 다른 seed_ingest_*.py 스크립트들의 관례(logging.
# basicConfig(level=logging.INFO, ...))를 따라 여기도 켠다.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# [2026-08-22 신규 - 사용자 요청, "페이싱 도입하자"] 90개 배치 실행에서
# Stage 1/3 응답이 형식을 어기고 잘리는 사례 + 결국 429까지 실측됐다
# (README "마흔다섯 번째") - claim이 몰리면 분당 토큰 한도 근처에서
# 응답 품질 자체가 흔들리는 것으로 보인다. hcx_client.call_hcx의
# min_interval_sec(opt-in, 기본 0)을 이 배치 스크립트에서만 켠다 -
# 프로덕션(단일 기사, claim 10~20개)에는 안 쓴다(사용자 판단: 인위적
# 지연이 UX를 깎아먹고, 어차피 같은 토큰 풀을 쓰는 이상 이 한도 자체를
# 피해갈 수는 없음). 각 resolver는 call_hcx_fn을 주입받는 구조라(테스트
# 목적으로 이미 열려있던 자리) resolver 코드를 안 건드리고 functools.
# partial로 페이싱을 끼워 넣을 수 있다. 2.0초는 첫 시도값 - 스위트
# 스팟은 아직 실측 전, 이번 실행 결과로 조정한다.
_BATCH_MIN_INTERVAL_SEC = 2.0


def _build_paced_resolvers(max_completion_tokens):
    """[2026-08-23 신규, 2026-08-24 재구현 - 실측 문서 확인 완료] 처음엔
    "1000이 부족한 것 같으니 다른 값을 실측으로 찾자"는 접근(CLI 스위프)
    이었는데, 사용자가 "예측으로 때려 맞추지 말고 실제 데이터 구조부터
    분석하라"고 정정 - NCP 공식 문서 확인 결과 thinking.effort="low"의
    문서화된 기본 maxCompletionTokens는 5120이었고, hcx_client.py가
    자체적으로 강제해온 1000은 그 1/5에 불과한 근거 없는 값이었다(진짜
    truncation 원인). hcx_client.call_hcx는 이제 max_completion_tokens
    가 None이면 이 필드 자체를 아예 안 보내 API 자체 기본값(5120)이
    적용되게 고쳤다 - 그래서 이 CLI 인자를 안 주면 더 이상 "1000"이
    아니라 "5120"이 적용된다. 그래도 다른 값을 실측해보고 싶을 때를
    위해 오버라이드 통로는 남겨둔다."""
    paced_call_hcx = functools.partial(
        call_hcx, min_interval_sec=_BATCH_MIN_INTERVAL_SEC, max_completion_tokens=max_completion_tokens,
    )
    return {
        "hcx_table_resolve_fn": functools.partial(resolve_table_with_hcx007, call_hcx_fn=paced_call_hcx),
        "hcx_resolve_fn": functools.partial(resolve_cell_with_hcx007, call_hcx_fn=paced_call_hcx),
        "hcx_axis_resolve_fn": functools.partial(resolve_axis_codes_with_hcx007, call_hcx_fn=paced_call_hcx),
        "hcx_stage3_fn": functools.partial(resolve_comparison_mode_with_hcx007, call_hcx_fn=paced_call_hcx),
    }


def _run(stage1_keywords: str, out_path: str, **agent_kwargs):
    agent = LocalDbAgent(db_path="kosis_warehouse.db", stage1_keywords=stage1_keywords, **agent_kwargs)
    try:
        results = run_pipeline_from_files(
            str(_interface.PIPELINE01_PATH),
            # [2026-08-24 변경 - 담당 범위 정정 반영] interface.PIPELINE03_PATH
            # 제거됨(run04_local_stage1_ab.py와 동일한 이유 - interface.py
            # 2026-08-24 항목 참고). A(run03) 비교 입력 파일 이름을 리터럴로 직접 씀.
            "run03_result.json",
            output_path=out_path,
            agent=agent,
        )
    finally:
        agent.close()
    return {r["claim_id"]: r for r in results}


def _counts(d):
    c = {}
    for r in d.values():
        c[r["verdict"]] = c.get(r["verdict"], 0) + 1
    return c


def _parse_args():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=None,
        help=(
            "[2026-08-23 신규, 2026-08-24 갱신] HCX-007 maxCompletionTokens 오버라이드. "
            "안 주면 이 필드 자체를 안 보내 API 문서화된 기본값(thinking.effort=low -> 5120) 적용. "
            "예: --max-completion-tokens 2000"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    paced = _build_paced_resolvers(args.max_completion_tokens)
    print(
        f"[max_completion_tokens 실측 설정] "
        f"{args.max_completion_tokens if args.max_completion_tokens is not None else '(오버라이드 없음 - API 문서 기본값 5120 적용, thinking.effort=low)'}"
    )

    print("=== A (run03 패러프레이즈, 기존 프로덕션 기본값) 실행 중 ===")
    a = _run("run03", "run04_local_result_A_run03_recheck.json")

    print("=== C (llm_table_select, 이번 세션 수정 전부 적용) 실행 중 ===")
    c = _run(
        "llm_table_select",
        "run04_local_result_C_llm_table_select.json",
        # [2026-08-22 신규 - 페이싱 도입, 2026-08-23 max_completion_tokens
        # 실측 인자 추가] 아래 네 개 전부 raw 함수 대신 _build_paced_
        # resolvers()가 만든 페이싱+토큰한도 래퍼를 쓴다 - call_hcx_fn만
        # 바꿔치기됐을 뿐, resolver 자체의 판단 로직은 전혀 안 바뀐다.
        hcx_table_resolve_fn=paced["hcx_table_resolve_fn"],
        hcx_resolve_fn=paced["hcx_resolve_fn"],
        # [2026-08-22 신규 - 전체 점검 중 발견해서 배선] 오늘 세션에서
        # 만들고 probe로만 검증했던 축 트리 tie-break(빵/떡 부모-자식
        # 동점, PPI leaf_samples와는 별개 - 이건 weak_literal_tie 해결용)
        # 가 이 배치 러너엔 아직 안 걸려 있었다 - probe에서만 작동하고
        # 실제 배치에는 반영 안 되고 있던 gap.
        hcx_axis_resolve_fn=paced["hcx_axis_resolve_fn"],
        # [2026-08-22 신규 - 전체 점검 중 발견해서 배선] Task #29(mode=
        # single/period_change/item_diff 판단)도 local_db_agent.py 쪽
        # 통합 로직(게이트 + reference_period 결정적 재계산)까지 다
        # 끝나고 실측(item_diff 100% 정확도)까지 됐는데, 이 hcx_stage3_fn을
        # 실제로 넘기는 호출부가 하나도 없었다 - DB 쓰기가 없는 순수
        # HCX 판단이라 이 통제 비교 스크립트에 넣어도 A/C 비교 전제가
        # 안 깨진다(kosis_client 백필과 다름).
        hcx_stage3_fn=paced["hcx_stage3_fn"],
    )

    print("\nA(run03) verdict 분포:", _counts(a))
    print("C(llm_table_select) verdict 분포:", _counts(c))

    print("\n=== claim별 verdict가 달라진 건 ===")
    changed = 0
    for claim_id in sorted(set(a) | set(c)):
        va = a.get(claim_id, {}).get("verdict")
        vc = c.get(claim_id, {}).get("verdict")
        if va != vc:
            changed += 1
            print(f"  {claim_id}: A={va} -> C={vc}")
    if not changed:
        print("  (없음 - 전체 90건 verdict 동일)")
    print(f"\n총 {changed}건 verdict 변경 (전체 {len(set(a) | set(c))}건 중)")

    with open("run04_local_llm_table_select_batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "a_run03_counts": _counts(a),
                "c_llm_table_select_counts": _counts(c),
                "changed_claim_ids": [
                    cid for cid in sorted(set(a) | set(c))
                    if a.get(cid, {}).get("verdict") != c.get(cid, {}).get("verdict")
                ],
            },
            f, ensure_ascii=False, indent=2,
        )
    print("\n[저장] 요약을 run04_local_llm_table_select_batch_summary.json에 저장했습니다.")


if __name__ == "__main__":
    main()

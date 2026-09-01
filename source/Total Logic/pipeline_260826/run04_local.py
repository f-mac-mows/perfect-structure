"""run_pipeline_from_files의 agent 파라미터에 LocalDbAgent를 명시적으로
넘겨서 로컬 DB(kosis_warehouse.db) 기반으로 검색+판정을 실행하는 드라이버.

[2026-08-22 갱신 - 전체 점검 후 라이브 API 경로 삭제] 예전엔 "adapter.py의
__main__ 기본 경로(new_kosis_agent.NewKosisAgent, 라이브 API)와는 별개의
진입점"이라고 썼는데, 그 라이브 API 경로 자체를 로컬 DB 접근으로 완전히
대체하기로 하고 삭제했다 - 이제 adapter.py의 기본값도 LocalDbAgent()라
이 스크립트와 사실상 같은 방향이다. 이 스크립트가 여전히 따로 있는 이유는
HCX 폴백/축 리졸버를 켠 구성(hcx_resolve_fn, hcx_axis_resolve_fn)과
--no-hcx 비교 스위치를 제공하기 위함 - adapter.py의 기본값은 그것들 없는
최소 구성(literal-only)이다.

local_db_agent.py의 설계 의도 그대로: adapter.py/judgment.py는 한 줄도 안 건드리고
agent 인스턴스만 바꿔 끼운다.

사용법:
    python3 run04_local.py
        interface.py의 PIPELINE01_PATH를 기본 입력으로 쓰고,
        결과를 run04_local_result.json에 저장한다.

    python3 run04_local.py run01_result.jsonl [output.json]
        입력/출력 경로를 직접 지정한다.

    [2026-08-24 변경 - 담당 범위 정정("run02/03도 우리 소관")] run03_result.json
    인자를 없앴다. Stage 1 표 후보 선정을 run03의 라이브 KOSIS 검색
    matched_keywords에 의존하던 stage1_keywords="run03"에서, HCX-007이
    로컬에 적재된 표 전체 + claim을 한 콜로 보고 직접 표를 고르는
    stage1_keywords="llm_table_select"(hcx_stage1_resolver.resolve_table_
    with_hcx007)로 승격했다 - 이 세션에서 A(run03)/C(llm_table_select) 통제
    비교(run04_local_llm_table_select_batch.py)로 실측 검증을 끝낸 경로다.
    이제 이 스크립트가 필요로 하는 입력은 1번의 claims.jsonl(관례상
    run01_result.jsonl로 인수인계된 것) 하나뿐이다 - run03_result.json은
    더 이상 만들 필요도, 읽을 필요도 없다.

    --no-hcx
        [2026-08-21 신규 - Task #80 전환, 2026-08-22 축 리졸버 + 온디맨드
        백필 추가] 기본값은 HCX-007 단일 콜 Stage 2 갭 폴백(hcx_stage2_
        resolver.resolve_cell_with_hcx007) + 축 트리 tie-break(hcx_tree_
        resolver.resolve_axis_codes_with_hcx007) + Task #28 온디맨드
        백필(kosis_client=KosisApiClient(), 표/항목/축은 확정됐는데 그
        시점만 로컬에 없을 때 kosis_warehouse.fetch_scoped_slice로 그
        시점만 narrow하게 실 API 조회해 채움)을 전부 켠 채로 돈다 -
        실측(README "열한 번째"/"서른일곱 번째"/"마흔세 번째" 항목)으로
        확인된 대로, Stage 2가 literal 매칭만으로 못 푸는 claim(동점/총
        실패/기간 갭) 상당수를 이걸로 구제할 수 있기 때문이다. 이
        플래그를 주면 예전처럼 폴백 없이(embed_fn/hcx_resolve_fn/
        hcx_axis_resolve_fn/kosis_client 전부 None) literal-only, 실 네트워크
        호출 없는 경로만 돈다 - 전환 전/후를 직접 비교하고 싶을 때 쓴다.
        온디맨드 백필은 이 세션 샌드박스가 아니라 사용자가 이 스크립트를
        로컬에서 실행할 때만 실제로 DB에 쓴다(CLAUDE.md "DB 파일에 직접
        쓰기/삭제 금지"는 이 세션이 직접 실행하는 것에 대한 규칙이라
        무관 - 여기서는 코드만 배선함). 위치 인자(claims_path 등) 어디에
        섞어 써도 되고, 파싱 전에 먼저 걸러낸다.
"""

import sys

from adapter import run_pipeline_from_files
from local_db_agent import LocalDbAgent
from hcx_stage1_resolver import resolve_table_with_hcx007
from hcx_stage2_resolver import resolve_cell_with_hcx007
from hcx_tree_resolver import resolve_axis_codes_with_hcx007
from hcx_stage3_resolver import resolve_comparison_mode_with_hcx007
from client import KosisApiClient

try:
    import interface as _interface
except ImportError:
    _interface = None


def main() -> None:
    argv = sys.argv[1:]
    use_hcx = "--no-hcx" not in argv
    argv = [a for a in argv if a != "--no-hcx"]

    # [2026-08-24 변경 - 담당 범위 정정 반영] run03_result.json 인자를
    # 뺐다. Stage 1 표 선택을 run03의 라이브 검색 matched_keywords에
    # 의존하던 stage1_keywords="run03"에서 llm_table_select(HCX-007이
    # 로컬 표 목록+claim을 보고 직접 고름, 이번 세션에 실측 검증 끝남 -
    # README "마흔다섯 번째"까지)로 승격했다. 이제 이 스크립트가 필요로
    # 하는 입력은 1번의 claims.jsonl(run01_result.jsonl로 인수인계된 것)
    # 하나뿐이다.
    if len(argv) >= 1:
        claims_path = argv[0]
        out_path = argv[1] if len(argv) > 1 else "run04_local_result.json"
    elif len(argv) == 0 and _interface is not None:
        claims_path = str(_interface.PIPELINE01_PATH)
        out_path = str(_interface.PROJECT_ROOT / "run04_local_result.json")
    else:
        print(
            "사용법: python3 run04_local.py [--no-hcx] [run01_result.jsonl [output.json]]",
            file=sys.stderr,
        )
        raise SystemExit(1)

    agent = LocalDbAgent(
        db_path="kosis_warehouse.db",
        # [2026-08-24 신규 - run03 대체 승격] use_hcx일 때만 llm_table_select를
        # 쓴다 - 이 모드 자체가 HCX-007 호출(hcx_table_resolve_fn)에 의존하기
        # 때문. --no-hcx면 run03도 HCX도 없이 1번이 준 metric_normalized를
        # 그대로 Stage 1 키워드로 쓴다(run04_local_stage1_ab.py의 B 경로와
        # 동일 - run03_result.json 없이도 항상 동작).
        stage1_keywords="llm_table_select" if use_hcx else "metric_normalized",
        hcx_table_resolve_fn=resolve_table_with_hcx007 if use_hcx else None,
        hcx_resolve_fn=resolve_cell_with_hcx007 if use_hcx else None,
        # [2026-08-22 신규 - 전체 점검 중 발견해서 배선] 축 트리 tie-break
        # (빵/떡 부모-자식 동점 해결, hcx_tree_resolver.py)가 이 러너엔
        # 아직 안 걸려 있었다 - --no-hcx와 같은 스위치를 공유해서, HCX
        # 폴백을 끄면 이것도 같이 꺼지게 한다.
        hcx_axis_resolve_fn=resolve_axis_codes_with_hcx007 if use_hcx else None,
        # [2026-08-22 신규 - Task #28 온디맨드 백필, "미루면 잊어버릴 것
        # 같다"는 사용자 요청으로 바로 배선] 표/항목/축은 확정됐는데 그
        # 시점만 로컬에 없는 no_data 케이스를, 그 시점만 narrow하게 실
        # API로 채운다(kosis_warehouse.fetch_scoped_slice). client.
        # KosisApiClient()는 인자 없이 .env의 KOSIS_API_KEY를 읽는다
        # (probe_fetch_scoped_slice.py와 동일한 생성 방식).
        kosis_client=KosisApiClient() if use_hcx else None,
        # [2026-08-22 신규 - 전체 점검 중 발견해서 배선] Task #29
        # (mode=single/period_change/item_diff 판단, hcx_stage3_resolver.py)
        # 는 local_db_agent.py 쪽 통합 로직(_has_total_comparison_keyword/
        # _find_swappable_axis_position 게이트, reference_period는 항상
        # 결정적 코드로 재계산)까지 다 만들고 실측(README "스물다섯~서른
        # 번째" 항목, item_diff mode 100% 정확도 확인)까지 끝냈는데, 정작
        # 이 hcx_stage3_fn을 실제로 넘기는 호출부가 어디에도 없었다 -
        # local_db_agent.py 안에서 함수 이름(hcx_stage3_fn)만 참조하고
        # hcx_stage3_resolver.py를 직접 import하지 않는 의존성 주입
        # 구조라(hcx_resolve_fn 등과 동일 패턴) grep으로 한 번에 안
        # 드러났었다.
        hcx_stage3_fn=resolve_comparison_mode_with_hcx007 if use_hcx else None,
    )
    print(
        f"[Stage 1 llm_table_select + Stage 2 HCX-007 단일 콜 폴백 + 축 tie-break + "
        f"온디맨드 백필 + Stage 3 mode 판단] {'켜짐' if use_hcx else '꺼짐(--no-hcx)'}"
    )
    try:
        results = run_pipeline_from_files(
            claims_path,
            None,
            output_path=out_path,
            agent=agent,
            # [2026-08-24 신규 - 프론트 요구사항] claim 하나당 STRICT/
            # TOLERANCE/RAW_ONLY 세 mode 결과를 한 번에 받아야 프론트가
            # 토글/탭으로 세 개를 같이 보여줄 수 있다 - 이제 이 스크립트가
            # 실제로 프론트에 넘기는 산출물이므로 기본값을 True로 켠다
            # (adapter.run_search_and_judge/run_pipeline_from_files 자체의
            # 기본값은 여전히 False - 다른 호출부/테스트는 하나도 안 바뀜).
            all_modes=True,
        )
    finally:
        agent.close()

    # [2026-08-24 갱신] all_modes=True라 claim마다 결과 모양이 둘로
    # 갈린다 - NOT_ELIGIBLE/ERROR는 여전히 평평한 구조(r["verdict"]),
    # 그 외엔 r["modes"]["strict"/"tolerance"/"raw_only"]["verdict"] 중첩
    # 구조다(adapter.run_search_and_judge의 all_modes docstring 참고).
    # 콘솔 요약은 세 mode를 다 펼치면 너무 길어지므로 tolerance 기준으로만
    # 집계·나열한다 - 실제 저장 파일(out_path)에는 세 mode가 전부 들어있다.
    verdict_counts = {}
    for r in results:
        v = r["verdict"] if "verdict" in r else r["modes"]["tolerance"]["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    print(f"총 {len(results)}건 처리 (LocalDbAgent, kosis_warehouse.db) - {verdict_counts}")
    print("(콘솔 요약은 tolerance 기준 - 저장 파일엔 strict/tolerance/raw_only 세 mode 결과가 모두 있음)")
    print(f"결과 저장: {out_path}")
    for r in results:
        if "verdict" in r:
            v, explanation = r["verdict"], r.get("explanation")
        else:
            tol = r["modes"]["tolerance"]
            v, explanation = tol["verdict"], tol["explanation"]
        print(f"  [{v}] {r['claim_id']} - {(explanation or '')[:60]}")


if __name__ == "__main__":
    main()

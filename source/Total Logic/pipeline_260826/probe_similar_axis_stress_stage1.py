"""[2026-08-21 신규 - Task #80 확장, 로컬 실행용] 시드를 26개로 늘린 뒤
(seed_ingest_similar_axis_stress.py), Stage 1 LLM 표 선택
(hcx_stage1_resolver.resolve_table_with_hcx007)이 "비슷한 이름, 다른
분류축" 표가 늘어난 상황에서도 버티는지 실 API로 재검증하는 프로브 -
probe_c018_stage1_llm_table_select.py와 같은 이유로 이 샌드박스는
네트워크가 막혀 있어 로컬 환경에서 직접 실행해야 한다.

## 검증 대상

C018 하나만으론 "시드가 늘어나도 안전한가"에 답할 수 없다는 게
사용자 질문의 핵심이었다 - 그래서 이번엔 새로 심은 스트레스 클러스터
(README "열여섯 번째" 항목 참고)를 겨냥한 claim 8개를 준비했다:

1~6: 고용(경제활동인구조사) "OO별 취업자" 6개 표 - 표 제목이 전부
     "OO별 취업자" 패턴으로 같고, ITEM도 전부 "T30 취업자" 하나뿐이라
     축 힌트(분류값)가 유일한 구분 수단인 케이스. 그중 4번은 일부러
     "성별+취업시간" 둘 다 언급해 축 2개짜리(DT_1DA7029S)와 축
     1개짜리(DT_1DA7011S) 사이 경계를 시험한다.
7: CPI - 기존 검증(C018)이 표가 19개→26개로 늘어난 뒤에도 여전히
   맞는지 재확인(회귀 확인).
8: PPI - 기존 2개(품목별/기본분류) 옆에 새로 심은 특수분류(DT_404Y015)
   가 "에너지"처럼 그 표에만 있는 분류값으로 제대로 구분되는지.

## 결과 해석 시 주의

N=8은 여전히 작은 표본이다(Research Vault 05_Concepts의 "LLM 판정
정밀도 평가 방법론" 참고 - Wilson score CI 기준으로 이 정도 N에서
100% 성공이 나와도 신뢰구간은 넓다). "이 8개가 다 맞았다"는 "이
방법이 일반적으로 안전하다"는 증거가 아니라 "이 스트레스 클러스터
에서는 견뎠다"는 증거일 뿐 - 결과를 사용자에게 그대로 보고하고 과장
안 함.

사용법(로컬에서): python3 probe_similar_axis_stress_stage1.py
"""

import json
import sqlite3

import kosis_local_search as kls
from hcx_stage1_resolver import resolve_table_with_hcx007

# (설명, claim_text, claimed_value, claimed_unit, claimed_period, expected_org_id, expected_tbl_id)
CASES = [
    ("종사상지위별(성 축 없음)", "임시근로자 비중이 크게 늘었다", None, None, None, "101", "DT_1DA7010S"),
    ("성/종사상지위별(성+상태 축)", "여성 임금근로자 중 상용근로자 비율이 높아졌다", None, "%", None, "101", "DT_1DA7028S"),
    ("취업시간별(성 축 없음)", "주 54시간 이상 취업자가 줄었다", None, None, None, "101", "DT_1DA7011S"),
    ("성/취업시간별(성+시간 축)", "남성의 주당 취업시간별 분포가 달라졌다", None, None, None, "101", "DT_1DA7029S"),
    ("성/연령별", "여성의 연령대별 취업자 수가 변화했다", None, None, None, "101", "DT_1DA7024S"),
    ("성/교육정도별", "여성의 학력별 취업자 비중이 달라졌다", None, "%", None, "101", "DT_1DA7025S"),
    ("CPI 회귀 확인(19개→26개로 늘어난 뒤)", "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다.", 13.1, "%", "2025-09", "101", "DT_1J22001"),
    ("PPI 특수분류(에너지 축)", "생산자물가 중 에너지 부문 상승률이 두드러졌다", None, "%", None, "301", "DT_404Y015"),
]


def main() -> None:
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        table_list = kls.list_registered_tables(conn)
        print(f"[로컬 표 목록] {len(table_list)}개\n")

        results = []
        for label, claim_text, value, unit, period, exp_org, exp_tbl in CASES:
            print(f"=== {label} - \"{claim_text}\" ===")
            try:
                idx = resolve_table_with_hcx007(
                    table_list, claim_text, claimed_value=value, claimed_unit=unit, claimed_period=period,
                )
            except Exception as e:
                print(f"  [EXCEPTION] {type(e).__name__}: {e}")
                results.append((label, False, None))
                print()
                continue

            if idx is None:
                print("  resolved: None(확신 없음)")
                results.append((label, False, None))
            else:
                chosen = table_list[idx]
                ok = chosen["org_id"] == exp_org and chosen["tbl_id"] == exp_tbl
                print(f"  resolved: {chosen['tbl_nm']} ({chosen['org_id']}/{chosen['tbl_id']})")
                print(f"  기대: {exp_org}/{exp_tbl} -> [{'PASS' if ok else 'FAIL'}]")
                results.append((label, ok, f"{chosen['org_id']}/{chosen['tbl_id']}"))
            print()

        n_pass = sum(1 for _, ok, _ in results if ok)
        print("=" * 70)
        print(f"[요약] {n_pass}/{len(results)} PASS")
        for label, ok, got in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" (got={got})" if not ok else ""))
        print()
        print("[주의] N=8은 여전히 작은 표본 - 이 결과는 \"이 스트레스 클러스터에서")
        print("견뎠다\"는 뜻이지 \"일반적으로 안전하다\"는 증거는 아니다.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

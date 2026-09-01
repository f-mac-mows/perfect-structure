"""[2026-08-17 신규] 특이 표(독서 실태조사, 유가증권 순위별 거래) 재검증
테스트 - 이번 세션에 seed_ingest_extra.py로 실제 로컬 DB에 처음 적재한 뒤,
resolve_evidence_by_flat_match가 실제 KOSIS 값까지 정확히 찾아내는지
real DB(read-only)로 확인한다. mock이 아니라 실제 적재된 facts를 그대로
읽는다 - kosis_warehouse.db가 이 폴더에 있고 두 표가 적재돼 있어야 돈다
(seed_ingest_extra.py --force 실행 후).

이 테스트를 작성하는 과정에서 실측으로 발견하고 고친 버그 두 가지도 함께
회귀 테스트로 남긴다(kosis_local_search.py):

1. 순위처럼 순수 숫자인 phrase("1")가 다른 숫자 코드("11", "10" 등)의
   부분 문자열로 잘못 매칭되던 문제 - _count_occurrences가 숫자 phrase는
   앞뒤가 숫자가 아닐 때만 매칭으로 세도록 수정.
2. 원문 phrase의 공백 유무가 KOSIS ITEM명과 다르면("독서활동경험있음" vs
   "독서 활동 경험 있음") 전혀 매칭이 안 되던 문제 - 공백을 지운 버전으로
   한 번 더 시도하는 폴백 추가.

[2026-08-22 추가] 사용자가 조선비즈 2025-10-08 원문 기사 전문을 직접
붙여넣어 준 것을 바탕으로, "같은 두 시점·다른 두 항목" 비교(C003/C004류)를
resolve_item_diff_change(신규, Task #27/#29 Step 1)로 검증하는 테스트도
추가했다 - 101/DT_1J22001(지출목적별 소비자물가지수)이 적재돼 있어야 돈다
(seed_ingest_cpi_breakdown.py --force 실행 후).

사용법: python test_local_search_special_tables.py (이 폴더에서, 종료
코드 0 = 전체 PASS)
"""

import sqlite3
import sys

import kosis_local_search as kls

DB_PATH = "file:kosis_warehouse.db?mode=ro"

READING_ORG, READING_TBL = "113", "DT_113_STBL_1024687"
SECURITIES_ORG, SECURITIES_TBL = "343", "DT_343_2010_S0043"
CPI_ORG, CPI_TBL = "101", "DT_1J22001"

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_reading_survey_school_and_sex_axis(conn):
    """[독서 실태조사] "학교급*성별, 고등학교, 남, 독서활동경험있음"(2026-
    08-16 세션에 사용자가 든 원래 예시 - 공백 없이 압축된 phrase 그대로)이
    ITEM 3개(사례수/계/독서 활동 경험 있음) 동점 없이 정확히 "독서 활동
    경험 있음" 하나로 유일하게 resolve되는지, 그리고 2025년 값이 실제
    KOSIS 응답(kosis_get_data 라이브 조회, 2026-08-17 실측)과 같은
    44.3%인지 확인한다."""
    phrases = ["학교급*성별", "고등학교", "남", "독서활동경험있음"]
    results = kls.resolve_evidence_by_flat_match(conn, READING_ORG, READING_TBL, phrases, top_n=5)
    _check("독서: 후보가 1개 이상 나옴", len(results) >= 1)
    if not results:
        return
    top = results[0]
    _check("독서: 1등이 '독서 활동 경험 있음' 항목", top["itm_nm"] == "독서 활동 경험 있음", top["itm_nm"])
    tie = [r for r in results if r["score"] == top["score"] and r["unexplained_axes"] == top["unexplained_axes"]]
    _check("독서: 동점 없이 유일하게 1등 결정됨(공백 유무 매칭 수정 확인)", len(tie) == 1, f"{len(tie)}건 동점")

    rows = conn.execute(
        "SELECT prd_de, value, unit FROM facts WHERE org_id=? AND tbl_id=? AND itm_id=? AND c1=? "
        "ORDER BY prd_de DESC",
        (READING_ORG, READING_TBL, top["itm_id"], top["axis_codes"].get(1)),
    ).fetchall()
    row_2025 = next((r for r in rows if r[0] == "2025"), None)
    _check("독서: 2025년 값 존재", row_2025 is not None)
    if row_2025:
        _check(
            "독서: 2025년 값이 실측 라이브 조회(44.3%)와 일치",
            abs(row_2025[1] - 44.3) < 0.05,
            f"실제 값={row_2025[1]}",
        )


def test_securities_rank1_volume_no_digit_substring_collision(conn):
    """[유가증권 순위별 거래] "1"(순위 1위), "거래량" phrase가 순위 leaf
    이름이 전부 맨 숫자("1".."15")인 축에서 "11"/"10" 등에 부분 문자열로
    오매칭되지 않고 정확히 순위=1, 거래량 조합을 유일하게 찾는지 확인한다.
    2025년 값(939,594천주, 6월 17일)은 이 세션 훨씬 앞에서 사용자가 직접
    구성한 가상 예문("이번년도 가장 많은 유가증권 거래량은 6월 17일에
    있던 939,594(천주)이다")과 정확히 같은 실측값이다."""
    phrases = ["1", "거래량"]
    results = kls.resolve_evidence_by_flat_match(conn, SECURITIES_ORG, SECURITIES_TBL, phrases, top_n=5)
    _check("유가증권: 후보가 1개 이상 나옴", len(results) >= 1)
    if not results:
        return
    top = results[0]
    rank_code = top["axis_codes"].get(1)
    _check(
        "유가증권: 1등의 순위축 코드가 '1위'(A.01)이지 '11위'(A.11)로 오매칭되지 않음",
        rank_code == "13102792797A.01",
        rank_code,
    )
    # [2026-08-18 갱신 - score 축별 1회 dedup 부작용, 의도적으로 완화]
    # 예전엔 이 축(B.01 "거래량" 부모 밑의 리프 B.0103도 이름이 "거래량")이
    # 조상+자신 이름이 우연히 똑같아 "거래량"이 breadcrumb에 2번 반복 등장,
    # 그 반복 덕분에 score가 다른 후보(B.02 "거래대금" 밑의 B.0204, 이름은
    # 똑같이 "거래량"이지만 부모가 다름)보다 우연히 더 높아 유일한 1위였다.
    # 근데 이 "반복이 곧 가점"이라는 바로 그 메커니즘이 A93bfa851-C024
    # 실측에서는 거꾸로 오답을 정답보다 높게 만드는 버그였다(집계행 "가정
    # 용품 및 가사서비스"보다 그 하위 leaf 품목이 조상 체인 반복으로 더
    # 높은 점수를 받음) - 같은 메커니즘이 한쪽에선 우연히 도움, 다른 쪽에선
    # 명백한 버그였던 것. score를 "세그먼트(축)당 phrase 1회"로 dedup해서
    # C024를 고치면, 여기서는 B.0103과 B.0204가 텍스트만으로는(둘 다 자기
    # 이름이 "거래량"으로 동일, ancestor_only_hits도 둘 다 0) 정직하게
    # 진짜 동점이 된다 - 이건 회귀가 아니라 "텍스트만으론 원래 못 가르는
    # 케이스"가 솔직하게 드러난 것이다. 그래도 최종 정답은 여전히 안전하게
    # 가려지는지 disambiguate_by_value로 확인한다(실측 확인: claim 값
    # 939594.0과 B.0103=939594.0은 distance=0.0, B.0204=594163.0은
    # distance=0.37 - 값 기반으로 유일하게, 정확하게 갈린다).
    tie_group = [r for r in results if r["score"] == top["score"]]
    if len(tie_group) > 1:
        dis = kls.disambiguate_by_value(
            conn, SECURITIES_ORG, SECURITIES_TBL, tie_group, 939594.0, period="2025",
        )
        _check(
            "유가증권: 텍스트만으론 동점이어도 claim 값(939594)으로 유일하게 재확인됨",
            dis.get("resolved", {}).get("axis_codes", {}).get(2) == "13102792797B.0103",
            dis,
        )
    else:
        _check("유가증권: 2등 이하가 전부 1등보다 낮은 점수(동점 없이 명확히 1위)", True)

    rows = conn.execute(
        "SELECT prd_de, value, unit FROM facts WHERE org_id=? AND tbl_id=? AND itm_id=? AND c1=? AND c2=? "
        "ORDER BY prd_de DESC",
        (SECURITIES_ORG, SECURITIES_TBL, top["itm_id"], *[top["axis_codes"][k] for k in sorted(top["axis_codes"])]),
    ).fetchall()
    row_2025 = next((r for r in rows if r[0] == "2025"), None)
    _check("유가증권: 2025년 값 존재", row_2025 is not None)
    if row_2025:
        _check(
            "유가증권: 2025년 값이 실측 라이브 조회(939,594천주)와 일치",
            abs(row_2025[1] - 939594.0) < 1.0,
            f"실제 값={row_2025[1]}",
        )


def test_food_cpi_vs_headline_diff_C003_C004_complete_claim(conn):
    """[2026-08-22 - 사용자 제공 원문(조선비즈 2025-10-08 "5년간 먹거리
    물가 20% 이상 상승") 기반] run01이 실제로 추출한 A93bfa851-C003/C004는
    원문 문장의 주어("식료품 및 비주류 음료")가 빠진 불완전한 claim이었다
    (README 스물세 번째 항목 - 1번 추출 단계 한계로 확인됨, 우리 소관 밖).
    이 테스트는 "claim만 누락 없이 만들어서 test로 넣고 돌려보자"(사용자
    제안)에 따라, 원문 그대로 주어를 살린 완전한 claim을 만들면 검색
    메커니즘(resolve_item_diff_change, Task #27/#29 Step 1) 자체는 문제
    없이 동작하는지 확인한다.

    원문: "'식료품 및 비주류 음료' 물가지수는 2020년 9월에 비해 22.9%
    올랐다. 같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트
    가까이 높은 수치다." - target_period=2025-09, reference_period=
    2020-09(5년 전, 지수 기준연도 2020=100과 비교)."""
    item_a_candidates = kls.resolve_evidence_by_flat_match(
        conn, CPI_ORG, CPI_TBL, ["식료품 및 비주류음료"], top_n=3
    )
    _check("식료품 및 비주류음료 항목이 후보로 나옴", len(item_a_candidates) >= 1)
    if not item_a_candidates:
        return
    item_a = item_a_candidates[0]

    # 매칭된 axis_codes 중 실제로 "식료품"이 이름에 들어간 축을 dimensions에서
    # 직접 확인한다(추측 안 함) - 그 축이 총지수 leaf로 바꿔치기할 대상이다.
    axis_position = None
    for pos, code in (item_a["axis_codes"] or {}).items():
        row = conn.execute(
            "SELECT name FROM dimensions WHERE org_id=? AND tbl_id=? AND axis_position=? AND code=?",
            (CPI_ORG, CPI_TBL, pos, code),
        ).fetchone()
        if row and "식료품" in (row[0] or ""):
            axis_position = pos
            break
    _check("식료품 코드가 속한 축(axis_position)이 확인됨", axis_position is not None)
    if axis_position is None:
        return

    result = kls.resolve_item_diff_change(
        conn, CPI_ORG, CPI_TBL, item_a["itm_id"], item_a["axis_codes"],
        axis_position, target_period="202509", reference_period="202009",
    )
    _check("파생 성공(derivation_used=True)", result.get("derivation_used") is True, result.get("reason"))
    if not result.get("derivation_used"):
        return
    diff = result["diff"]
    ok = diff is not None and 6.0 <= diff <= 8.0
    _check("diff가 원문 '7%포인트 가까이'와 근접(6~8%p 범위)", ok, f"실제 diff={diff}")
    print(
        f"    [상세] item_a(식료품 및 비주류음료) pct_change={result['item_a']['pct_change']:.2f}%,"
        f" item_b(총지수) pct_change={result['item_b']['pct_change']:.2f}%, diff={diff:.2f}%p"
        if diff is not None else "    [상세] diff 계산 실패"
    )


def test_count_occurrences_digit_boundary_regression():
    """[회귀 테스트 - 라이브 DB 불필요] _count_occurrences가 숫자 phrase를
    다른 숫자의 부분 문자열로 잘못 세지 않는지 순수 함수 단위로 직접
    확인한다."""
    _check(
        '"1"이 "11 거래량"에서 오매칭 안 됨(0건)',
        kls._count_occurrences("거래실적 11 거래량", "1") == 0,
    )
    _check(
        '"1"이 "1 거래량"에서 정확히 1건 매칭',
        kls._count_occurrences("거래실적 1 거래량", "1") == 1,
    )
    _check(
        '"11"이 "11 거래량"에서 정확히 1건 매칭(자기 자신)',
        kls._count_occurrences("거래실적 11 거래량", "11") == 1,
    )


def test_whitespace_compact_fallback_regression():
    """[회귀 테스트 - 라이브 DB 불필요] 공백 없는 phrase가 공백 있는 text와
    매칭되는 폴백을 순수 함수 단위로 확인한다."""
    _check(
        '"독서활동경험있음"(공백 없음)이 "독서 활동 경험 있음"(공백 있음)과 매칭',
        kls._count_occurrences("독서 활동 경험 있음 학교급*성별 고등학교 남", "독서활동경험있음") == 1,
    )


def main():
    try:
        conn = sqlite3.connect(DB_PATH, uri=True)
    except sqlite3.OperationalError as e:
        print(f"[SKIP] kosis_warehouse.db를 열 수 없음({e}) - 이 폴더에서 실행했는지 확인하세요.")
        sys.exit(1)

    tables = {
        r[0]
        for r in conn.execute("SELECT org_id || '/' || tbl_id FROM tables_registry").fetchall()
    }
    missing = [
        f"{READING_ORG}/{READING_TBL}" if f"{READING_ORG}/{READING_TBL}" not in tables else None,
        f"{SECURITIES_ORG}/{SECURITIES_TBL}" if f"{SECURITIES_ORG}/{SECURITIES_TBL}" not in tables else None,
        f"{CPI_ORG}/{CPI_TBL}" if f"{CPI_ORG}/{CPI_TBL}" not in tables else None,
    ]
    missing = [m for m in missing if m]
    if missing:
        print(f"[SKIP] 아직 적재 안 된 표: {missing} - seed_ingest_extra.py --force를 먼저 실행하세요.")
        conn.close()
        sys.exit(1)

    test_count_occurrences_digit_boundary_regression()
    test_whitespace_compact_fallback_regression()
    test_reading_survey_school_and_sex_axis(conn)
    test_securities_rank1_volume_no_digit_substring_collision(conn)
    test_food_cpi_vs_headline_diff_C003_C004_complete_claim(conn)

    conn.close()

    print()
    if _failures:
        print(f"FAIL: {len(_failures)}건 실패 - {_failures}")
        sys.exit(1)
    print("PASS: 전체 통과")


if __name__ == "__main__":
    main()

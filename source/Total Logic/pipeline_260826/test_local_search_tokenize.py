"""[2026-08-21 신규] kosis_local_search._tokenize의 순수 숫자 토큰 필터
회귀 테스트.

## 배경

CLAUDE.md "담당 범위(scope) 정정" 항목의 계기가 된 claim
A93bfa851-C018("주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만
보면 13.1%였다")을 실측 재현하다가 발견한 버그다. run03(KOSIS 라이브
검색)가 패러프레이즈 10개 전부 실패해 matched_keywords=[]가 되면,
local_db_agent.py의 Stage 1이 원문장을 직접 토큰화(`_tokenize`)해서
검색어로 쓴다. "13.1%였다"가 구두점 치환으로 "13"/"1"로 쪼개지고,
"13"(2글자 이상이라 살아남음)이 완전히 무관한 표("유가증권 순위별
거래")의 순위 축 코드(이름이 문자 그대로 "13")와 FTS로 우연히 걸렸다.
bm25는 희귀한 토큰일수록 점수를 높게 주는데, "13"이라는 코드명이
코퍼스 전체에서 워낙 희귀해서 이 가짜 매칭(term 1개)이 진짜 정답표
(주류/담배 2개 토큰 실제 일치)를 점수로 역전해버렸다 - 정답표는 후보
목록엔 있었는데(2위) 순위에서 밀려 완전히 엉뚱한 표가 채택됐다.

## 수정

`_tokenize`가 반환 직전에 순수 숫자로만 된 토큰(`t.isdigit()`)을 전부
걸러낸다. claim 문장의 숫자는 항상 "값"이지 표/항목을 가리키는 개념어가
아니므로, 검색어로 쓰면 항상 이런 우연한 축/코드 충돌 위험만 있고
얻는 게 없다. "2020년"처럼 숫자+한글이 붙은 토큰은 `.isdigit()`이
False라서 필터에 안 걸리고 그대로 남는다(연도류 매칭은 유지).

사용법: python3 test_local_search_tokenize.py (종료 코드 0 = 전체 PASS)
"""

import sqlite3
import sys

import kosis_local_search as kls

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_pure_digit_tokens_filtered():
    cases = [
        ("실측 버그 원문 - A93bfa851-C018", "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다.", ["13", "1", "5", "0"]),
        ("금액 소수점", "물가가 3.5% 올랐다", ["3", "5"]),
        ("큰 숫자", "매출이 1000억원 늘었다", ["1000"]),
    ]
    for label, text, forbidden in cases:
        tokens = kls._tokenize(text)
        leaked = [t for t in tokens if t in forbidden]
        _check(f"순수 숫자 토큰 전부 제거됨: {label}", not leaked, f"tokens={tokens} leaked={leaked}")


def test_digit_hangul_mixed_tokens_survive():
    """숫자+한글이 붙어있으면(연도 등) 필터에 안 걸려야 한다 - 회귀 방지."""
    cases = [
        ("연도", "2020년 물가지수", "2020년"),
        ("수량+단위", "10만원대 상품권", "10만원대"),
    ]
    for label, text, expected_token in cases:
        tokens = kls._tokenize(text)
        _check(f"숫자+한글 결합 토큰은 유지됨: {label}", expected_token in tokens, f"tokens={tokens}")


def test_single_char_hangul_tokens_survive():
    """[2026-08-22 신규 - A93bfa851-C007/C009 실측 버그 수정 회귀 테스트]
    한글 1음절 품목명은 구두점 제거로 독립 토큰이 돼도 더 이상 사라지면
    안 된다. 이 버그가 실제로 낸 사고: "빵(38.5%)"에서 "빵"이 날아가고
    "물가"만 남아 "수입물가지수(품목별)"라는 무관한 표로 튀었고(C007),
    "떡(25.8%)"에서 "떡"이 날아가 표는 맞았지만 항목이 "전국 0 총지수"
    (헤드라인 CPI)로 잘못 매칭됐다(C009)."""
    cases = [
        ("빵", "빵(38.5%), 케이크(31.7%), 떡(25.8%), 라면(25.3%) 등이 크게 올랐다.", "빵"),
        ("떡", "빵(38.5%), 케이크(31.7%), 떡(25.8%), 라면(25.3%) 등이 크게 올랐다.", "떡"),
        ("metric 단독", "빵 물가", "빵"),
    ]
    for label, text, expected_token in cases:
        tokens = kls._tokenize(text)
        _check(f"한글 1음절 토큰이 살아남음: {label}", expected_token in tokens, f"tokens={tokens}")


def test_single_char_digit_tokens_still_filtered():
    """1글자 예외는 한글 전용이다 - 숫자 파편("13.1%"의 "1")은 여전히
    걸려야 한다(C018 버그 재발 방지, 기존 test_pure_digit_tokens_filtered
    와 같은 취지를 1글자 케이스로 명시)."""
    tokens = kls._tokenize("이 중 주류만 보면 13.1%였다.")
    leaked = [t for t in tokens if t in ("1", "3")]
    _check("숫자 1글자 파편은 여전히 제거됨", not leaked, f"tokens={tokens} leaked={leaked}")


def test_c018_table_selection_fixed():
    """[실측 재현] 로컬 DB(kosis_warehouse.db, 읽기 전용)로 실제 버그
    시나리오를 재현 - 수정 전엔 "유가증권 순위별 거래"(org_id=343)가
    채택됐지만, 수정 후엔 정답표 "지출목적별 소비자물가지수"
    (org_id=101, tbl_id=DT_1J22001)의 후보 순위가 1위로 올라온다."""
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        raw_sentence = "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다."
        candidates = kls.search_local(conn, raw_sentence, keywords=None, top_n=5)
        _check("후보가 비어있지 않음", bool(candidates))
        if candidates:
            top = candidates[0]
            _check(
                "1위 후보가 정답표(DT_1J22001)로 바뀜(수정 전엔 유가증권 순위별 거래였음)",
                top.get("org_id") == "101" and top.get("tbl_id") == "DT_1J22001",
                f"top={top.get('tbl_nm')} org_id={top.get('org_id')} tbl_id={top.get('tbl_id')}",
            )
            wrong_present = any(c.get("tbl_id") == "DT_343_2010_S0043" for c in candidates)
            _check(
                "무관한 표(유가증권 순위별 거래)가 더 이상 안 걸림",
                not wrong_present,
                f"candidates={[c.get('tbl_nm') for c in candidates]}",
            )
    finally:
        conn.close()


def test_bread_ricecake_stage2_fixed():
    """[실측 재현 - 실 DB, mode=ro] 수정 전엔 match_phrases=['물가']만
    남아 C007(빵)이 무관한 표(수입물가지수)로, C009(떡)이 맞는 표에서도
    엉뚱한 항목(전국 0 총지수)으로 튀었다. 수정 후엔 "빵"/"떡" 토큰이
    살아남아 Stage 2 후보 1위가 실제 해당 품목이어야 한다."""
    conn = sqlite3.connect("file:kosis_warehouse.db?mode=ro", uri=True)
    try:
        # C007: 빵 - Stage 1(표 후보)도 확인. 수정 전엔 match_phrases=
        # ['물가']만 남아 org_id=301/DT_401Y017(수입물가지수, 무관)이 1위였다.
        c007_sentence = (
            "빵(38.5%), 케이크(31.7%), 떡(25.8%), 라면(25.3%) 등이 크게 오르며 "
            "빵 및 곡물(28.0%)도 큰 폭으로 올랐다."
        )
        table_candidates = kls.search_local(
            conn, c007_sentence, keywords=["빵 물가", "빵 가격"], top_n=5
        )
        top_table = table_candidates[0] if table_candidates else None
        _check(
            "Stage 1: 빵 claim이 더 이상 수입물가지수(DT_401Y017)를 1위로 안 뽑음",
            bool(top_table) and top_table.get("tbl_id") != "DT_401Y017",
            f"top_table={top_table.get('tbl_nm') if top_table else None} tbl_id={top_table.get('tbl_id') if top_table else None}",
        )

        match_phrases = kls._tokenize("빵 물가")
        _check("빵 물가 -> '빵' 토큰 생존", "빵" in match_phrases, f"{match_phrases}")
        candidates = kls.resolve_evidence_by_flat_match(conn, "101", "DT_1J22001", match_phrases, top_n=5)
        top_texts = [c["text"] for c in candidates[:3]]
        _check(
            "'빵'이 들어간 항목이 상위 후보에 등장(총지수로만 안 빠짐)",
            any("빵" in t for t in top_texts),
            f"top_texts={top_texts}",
        )

        # C009: 떡
        match_phrases = kls._tokenize("떡 물가")
        _check("떡 물가 -> '떡' 토큰 생존", "떡" in match_phrases, f"{match_phrases}")
        candidates = kls.resolve_evidence_by_flat_match(conn, "101", "DT_1J22001", match_phrases, top_n=5)
        top_texts = [c["text"] for c in candidates[:3]]
        _check(
            "'떡'이 들어간 항목이 상위 후보에 등장",
            any("떡" in t for t in top_texts),
            f"top_texts={top_texts}",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    test_pure_digit_tokens_filtered()
    test_digit_hangul_mixed_tokens_survive()
    test_single_char_hangul_tokens_survive()
    test_single_char_digit_tokens_still_filtered()
    test_c018_table_selection_fixed()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

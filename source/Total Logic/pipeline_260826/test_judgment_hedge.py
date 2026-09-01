"""[2026-08-21 신규 - Task #80 대화 중 실측 발견] judgment.py의 근사 표현
(hedge) 사전에 "-대"(숫자+단위+대, "1000조원대"류 구간형 어림값) 패턴을
추가한 회귀 테스트.

## 배경

`kosis_local_search.disambiguate_by_value`를 실 KOSIS 국가채무 표(184/
DT_102006_001)로 재검증하다가, A272c31f6-C010("2022년 1000조원대로
불어난 국가 채무")이 판정 로직 어디서도 근사치로 인식되지 않는 걸
발견했다 - 실제 2022년 값(1067.4조원)과 claim 값(1000조)의 상대오차가
6.74%인데, hedge 사전(`_HEDGE_PATTERNS`)에도 AI 재해석 트리거 그물
(`_SOFT_SIGNAL_RE`)에도 "-대"가 없어서 `extract_hedge`가 "exact"로
떨어지고 AI에게 물어볼 기회조차 없이 정확한 값처럼 취급되고 있었다 -
8가지 케이스 노트(Research Vault, `판정 로직 엣지케이스 정리 및 AI
도입 검토.md`)의 Case C("사전에 없는 비슷한 표현")의 실제 사례.

## 왜 규칙 기반으로 고쳤나(AI 호출 대신)

"-대"는 자유 서술이 아니라 "숫자+단위+대"라는 닫힌 한국어 문법 패턴이라,
이 프로젝트 자체 하이브리드 원칙("표현이 정해져 있어서 목록으로 감당
가능한 문제는 규칙으로, 자연어처럼 무한히 다양한 문제만 AI로")에 따라
정규식 하나로 처리했다 - AI 호출도, `_SOFT_SIGNAL_RE` 안전망 확장도
필요 없었다.

## 1번(claim 추출) 스키마와의 관계

1번의 신규 claims 스키마에도 `approx`(GTE/LTE/APPROX/"") 필드가 있지만,
`claims_schema_1번_v2.md`에 이미 "`approx` -> `judgment.extract_hedge`
대체는 이번 라운드에서 보류"라고 명시돼 있고 실 데이터도 아직 미도착 -
이 fix는 그 필드와 무관하게 judgment.py 내부(규칙 기반) 사전 확장이다.

사용법: python test_judgment_hedge.py (종료 코드 0 = 전체 PASS)
"""

import sys

import judgment as j

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_range_suffix_detected_as_approx_range():
    cases = [
        ("실측 버그 원문 - A272c31f6-C010", "코로나 팬데믹을 거치며 2022년 1000조원대로 불어난 국가 채무가 2023~2024년 1100조원대가 됐고, 1년 만에 1300조원대로 뛰게 됐다."),
        ("퍼센트대 - 조사 붙음", "실업률이 3%대를 기록했다"),
        ("퍼센트대 - 뒤에 다른 말", "20%대 초반의 지지율"),
        ("만원대", "3000만원대 아파트"),
        ("억원대", "500억원대 매출"),
        ("만원대(작은 수)", "10만원대 상품권"),
    ]
    for label, text in cases:
        got = j.extract_hedge(text)
        _check(f"approx_range로 인식(approx와 구분): {label}", got == "approx_range", f"got={got!r}")


def test_age_and_decade_idioms_not_matched():
    """"-대"의 가장 흔한 오탐 위험 - 나이대("20대")와 연대("1980년대")는
    숫자와 "대" 사이에 통화/수량 단위가 없으므로 안 걸려야 한다."""
    cases = [
        ("나이대", "20대 청년층의 실업률"),
        ("나이대(후반)", "30대 후반 여성"),
        ("연대", "1980년대 경제 성장"),
        ("숫자+세+이상(기존 오탐 방지 케이스, 회귀 확인)", "15세 이상 취업자"),
        ("일반 숫자+원(단위 뒤에 대 없음)", "9,860원으로 결정됐다"),
    ]
    for label, text in cases:
        got = j.extract_hedge(text)
        _check(f"approx_range로 오인 안 함: {label}", got != "approx_range", f"got={got!r}")


def test_existing_hedge_patterns_still_work():
    """기존 사전(약/대략/이상/이하/육박 등)에 회귀가 없는지 확인."""
    cases = [
        ("약", "물가가 약 3% 올랐다", "approx"),
        ("돌파", "실업률이 9%를 돌파했다", "at_least"),
        ("이하", "물가가 3% 이하로 떨어졌다", "at_most"),
        ("육박", "지지율이 50%에 육박했다", "approach_below"),
        ("정확한 수치", "취업자는 2909만1000명이다", "exact"),
    ]
    for label, text, expected in cases:
        got = j.extract_hedge(text)
        _check(f"기존 패턴 유지: {label}", got == expected, f"got={got!r} expected={expected!r}")


def test_c010_scenario_now_widens_tolerance():
    """[실측 재현] hedge_type=approx_range가 실제로 approx보다 더 넓게
    tolerance 폭을 넓히는지(`_category_tolerance`의 `_RANGE_WIDEN_FACTOR`)
    확인 - "-대"를 그냥 "approx"로 묶었을 때는 6.74% 오차가 허용폭 밖으로
    나오는 걸 먼저 확인했고(실측으로 발견), 그래서 별도 hedge_type으로
    분리했다. 이 테스트는 그 분리가 실제로 필요했다는 것과, 분리한 결과
    실측 사례를 통과시킨다는 것 둘 다 확인한다."""
    money = j.UnitCategory.MONEY
    kind_exact, eps_exact = j._category_tolerance(money, j.Mode.TOLERANCE, "exact")
    kind_approx, eps_approx = j._category_tolerance(money, j.Mode.TOLERANCE, "approx")
    kind_range, eps_range = j._category_tolerance(money, j.Mode.TOLERANCE, "approx_range")
    _check(
        "approx_range 허용폭이 approx보다 더 넓음(같은 종류)",
        kind_approx == kind_range and eps_range > eps_approx > eps_exact,
        f"exact={eps_exact} approx={eps_approx} approx_range={eps_range}",
    )
    # 실측: A272c31f6-C010의 claim 값(1000조) vs 실제 A01 값(1067.4조) -
    # 상대오차 6.74%. approx(기존 사전대로 묶었다면)로는 허용폭 밖이고,
    # approx_range로 분리한 지금은 안에 들어야 한다.
    within_approx = j._within_tolerance(1000.0, 1067.4, kind_approx, eps_approx)
    within_range = j._within_tolerance(1000.0, 1067.4, kind_range, eps_range)
    _check(
        "approx로만 묶었으면 실측 오차(6.74%)가 허용폭 밖(분리가 필요했던 이유)",
        not within_approx,
        f"kind={kind_approx} eps={eps_approx}",
    )
    _check(
        "approx_range로 분리한 지금은 실측 오차(6.74%)가 허용폭 안",
        within_range,
        f"kind={kind_range} eps={eps_range}",
    )


if __name__ == "__main__":
    test_range_suffix_detected_as_approx_range()
    test_age_and_decade_idioms_not_matched()
    test_existing_hedge_patterns_still_work()
    test_c010_scenario_now_widens_tolerance()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

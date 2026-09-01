"""[2026-08-19 신규] 1번과 확정한 새 claims.jsonl 스키마(claims_schema_1번_v2.md
참고: value_type/direction/comparison_basis/comparison_period/value_num/
sent_id)가 실제로 들어오면 resolve_claim_evidence가 그 필드를 우선 쓰는지
검증한다.

**중요: 이 테스트의 claim들은 전부 합성(synthetic) 데이터다.** 1번의 실제
출력이 아직 없어서, 이 세션에서 이미 실측 검증된 진짜 claim 텍스트
(run01_result.jsonl)에 새 스키마 필드를 내가 직접 채워 넣은 것이다 - 필드
값 자체(특히 comparison_basis/comparison_period 조합)는 1번이 실제로
어떻게 채울지 아직 실측되지 않았으므로, 이 테스트가 통과한다고 해서 1번의
진짜 출력과도 맞는다는 보장은 없다(CLAUDE.md 실측 우선 원칙 - 실제
run01_result.jsonl이 이 포맷으로 오면 반드시 재검증). 이 테스트가 확인하는
건 "새 필드가 있으면 raw_sentence 휴리스틱을 안 타고 새 필드를 쓴다"는
배선 자체다.

real DB(read-only)로 돈다 - kosis_warehouse.db가 이 폴더에 있어야 한다.
사용법: python test_claims_schema_v2.py (종료 코드 0 = 전체 PASS)
"""

import sqlite3
import sys

from local_db_agent import resolve_claim_evidence, _sibling_group_key

DB_PATH = "file:kosis_warehouse.db?mode=ro"

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_value_type_level_no_derivation(conn):
    """[합성 - A82ae9f41-C004 기반] value_type="level"이면, 원문장에
    "증가했다"류 동사가 근처(사실은 다른 claim의 변화량 21만6000명을
    서술하는 동사)에 있어도 파생을 시도하지 않고 그대로 직접값을
    조회해야 한다 - 구 휴리스틱도 unit="명"이라 이 특정 케이스는 이미
    안전했지만, value_type="level"이 명시되면 더 이상 원문장 동사
    위치에 의존하지 않고 곧바로 안전해진다는 걸 확인한다."""
    claim = {
        "claim_id": "SYN-C004", "article_id": "SYN-A1", "sent_id": "s001",
        "claim": "고령 돌봄 수요 확대 등에 따라 지난달 보건·사회복지서비스업 취업자 수는 323만4000명으로 전년 동월 대비 21만6000명 증가했다.",
        "metric_normalized": "보건·사회복지서비스업 취업자 수",
        "value": "323만4000", "value_num": 3234000, "unit": "명",
        "value_type": "level", "direction": "",
        "period": "2025-06", "comparison_basis": "", "comparison_period": "",
    }
    keywords = ["보건·사회복지서비스업 취업자 수", "보건·사회복지 서비스업 종사자 수"]
    res = resolve_claim_evidence(conn, claim, keywords)
    _check("level: query_status=success", res.get("query_status") == "success", res)
    if res.get("query_status") == "success":
        _check(
            "level: derivation 안 씀(원자료 직접값)",
            res.get("derivation", {}).get("used") is False,
            res.get("derivation"),
        )
        _check(
            "level: 값이 claim 그대로(3234000 근방)와 일치",
            res.get("normalized_value") is not None and abs(res["normalized_value"] * 1000 - 3234000) < 200,
            res.get("normalized_value"),
        )


def test_value_type_change_rate_triggers_derivation(conn):
    """[합성 - A93bfa851-C024 기반] value_type="change_rate"면, 원문장에
    변화 동사가 있는지 다시 찾지 않고 곧바로 파생(두 시점 비교)을
    시도해야 한다 - comparison_basis/comparison_period가 비어 있으면
    YoY 기본값으로 폴백하는지도 함께 확인한다."""
    claim = {
        "claim_id": "SYN-C024", "article_id": "SYN-A2", "sent_id": "s010",
        "claim": "세제, 청소용품 등 살림에 필요한 물품과 세탁·청소 같은 가사 서비스를 포함한 '가정용품 및 가사서비스' 물가는 19.4% 상승했다.",
        "metric_normalized": "가정용품 및 가사서비스 물가",
        "value": "19.4", "value_num": 19.4, "unit": "%",
        "value_type": "change_rate", "direction": "increase",
        "period": "2025-09", "comparison_basis": "", "comparison_period": "",
    }
    keywords = ["가정용품 및 가사서비스 물가", "가정용품 물가동향", "가사서비스 물가동향"]
    res = resolve_claim_evidence(conn, claim, keywords)
    _check("change_rate: query_status=success", res.get("query_status") == "success", res)
    if res.get("query_status") == "success":
        _check(
            "change_rate: derivation 씀(YoY 기본값으로 두 시점 비교)",
            res.get("derivation", {}).get("used") is True,
            res.get("derivation"),
        )
        _check(
            "change_rate: reference가 YoY 기본값(202409)으로 계산됨",
            "202409" in (res.get("derivation", {}).get("note") or ""),
            res.get("derivation"),
        )


def test_value_type_change_rate_explicit_comparison_period(conn):
    """[합성 - A93bfa851-C002 기반] comparison_basis="SPECIFIC" +
    comparison_period="2020-09"가 있으면, 원문장의 "2020년 9월에 비해"를
    다시 정규식으로 긁지 않고 그 값을 바로 reference_digits(202009)로
    써야 한다."""
    claim = {
        "claim_id": "SYN-C002", "article_id": "SYN-A3", "sent_id": "s003",
        "claim": "8일 국가데이터처에 따르면 지난달 '식료품 및 비주류 음료' 물가지수는 2020년 9월에 비해 22.9% 올랐다.",
        "metric_normalized": "식료품 및 비주류 음료 물가지수",
        "value": "22.9", "value_num": 22.9, "unit": "%",
        "value_type": "change_rate", "direction": "increase",
        "period": "2025-09", "comparison_basis": "SPECIFIC", "comparison_period": "2020-09",
    }
    keywords = ["식료품 및 비주류 음료 물가지수", "식품및비주류음료 물가지수"]
    res = resolve_claim_evidence(conn, claim, keywords)
    # [2026-08-19 작성 당시 주석 - 더 이상 유효하지 않음, 2026-08-24 갱신]
    # 이 자리는 원래 "2020-09가 로컬 DB에 없어서 no_data가 정답"이라고
    # 적혀 있었다. 실제로는 그게 아니라 다른 버그였다 - 2026-08-18에 추가된
    # "값 기반 검색"이 claim의 value_type을 안 보고 숫자만 비슷하면 채택하는
    # 바람에, 이 claim(22.9% 등락률 주장)이 완전히 무관한 표의 우연히
    # 비슷한 지수값(22.69, DT_1J22001의 어느 항목)에 채택돼 Stage 1/2/3
    # (정답 표를 찾는 이름 기반 검색)까지 아예 안 갔던 것 - no_data가
    # 아니라 "엉뚱한 표가 값으로 채택됨"이 진짜 문제였다.
    #
    # [2026-08-24 수정 - declares_change_rate 게이트 추가] change_rate
    # claim은 매칭된 항목이 실제로 kls._infer_measure_type()=="rate_of_
    # change"일 때만 값 기반 검색을 신뢰하도록 local_db_agent.py를 고친
    # 뒤 재실행하니, 이 claim은 정상적으로 Stage 1/2/3까지 가서 정답 표
    # (DT_1J22001, 지출목적별 소비자물가지수)를 이름으로 찾았고, 그 표엔
    # 실제로 202509/202009 두 시점 데이터가 모두 있어서(이 사용자의 로컬
    # DB 기준) derivation이 성공했다 - comparison_period(2020-09)가
    # reference_digits(202009)로 정확히 넘어갔다는 게 오히려 "성공한
    # derivation의 reference 값"으로 더 확실하게 증명된다(no_data 에러
    # 메시지보다 강한 증거). claim 값(22.9%)은 합성 데이터라 실제 계산값
    # (16.2%)과 다른 게 당연함 - 이 테스트는 값 자체의 일치가 아니라
    # comparison_period 배선만 검증한다.
    _check(
        "explicit comparison_period: query_status=success (정답 표를 이름으로 찾아 derivation 성공)",
        res.get("query_status") == "success",
        res,
    )
    _check(
        "explicit comparison_period: derivation 씀(두 시점 비교)",
        res.get("derivation", {}).get("used") is True,
        res.get("derivation"),
    )
    derivation_note = (res.get("derivation") or {}).get("note") or ""
    _check(
        "explicit comparison_period: reference가 202009로 정확히 계산됨(comparison_period 그대로 사용, derivation note에서 확인)",
        "202009" in derivation_note,
        derivation_note,
    )


def test_value_type_change_amount_with_sent_id_siblings(conn):
    """[합성 - A82ae9f41-C005/C006 기반] value_type="change_amount"인
    두 형제 claim(같은 sent_id 공유, "명" 단위)이 sibling_values 없이도
    (= raw_sentence 동사 탐색에 의존하지 않고) 각자 독립적으로 파생
    트리거가 걸리는지 확인한다 - 이게 이번 스키마 확정의 핵심 이득
    중 하나다(형제 값을 따로 안 모아도 됨)."""
    base = {
        "article_id": "SYN-A4", "sent_id": "s020",
        "claim": "전문·과학기술서비스업(10만2000명)과 교육서비스업(7만2000명)도 마찬가지로 증가세를 보였다.",
        "period": "2025-06", "unit": "명",
        "value_type": "change_amount", "direction": "increase",
        "comparison_basis": "", "comparison_period": "",
    }
    c1 = {**base, "claim_id": "SYN-C005", "metric_normalized": "전문·과학기술서비스업 취업자",
          "value": "10만2000", "value_num": 102000}
    c2 = {**base, "claim_id": "SYN-C006", "metric_normalized": "교육서비스업 취업자",
          "value": "7만2000", "value_num": 72000}
    kw1 = ["전문·과학기술서비스업 취업자", "전문 과학기술 서비스업 종사자 수"]
    kw2 = ["교육서비스업 취업자", "교육서비스업 종사자 수"]

    # sibling_values를 일부러 안 넘긴다(= 옛 방식이라면 못 풀 조건) -
    # value_type이 있으면 sibling 정보 없이도 트리거돼야 한다는 게 핵심.
    res1 = resolve_claim_evidence(conn, c1, kw1)
    res2 = resolve_claim_evidence(conn, c2, kw2)
    _check(
        "change_amount(형제1): derivation 씀 - sibling_values 없이도 트리거",
        res1.get("derivation", {}).get("used") is True,
        res1,
    )
    _check(
        "change_amount(형제2): derivation 씀 - sibling_values 없이도 트리거",
        res2.get("derivation", {}).get("used") is True,
        res2,
    )
    _check(
        "sent_id 기반 그룹 키가 article_id까지 묶는다",
        _sibling_group_key(c1) == ("SYN-A4", "s020") and _sibling_group_key(c1) == _sibling_group_key(c2),
    )
    _check(
        "sent_id 다르면 다른 그룹으로 갈린다",
        _sibling_group_key(c1) != _sibling_group_key({**c1, "sent_id": "s021"}),
    )


def test_backward_compat_old_format_untouched(conn):
    """[회귀] value_type 등 새 필드가 아예 없는 구 포맷 claim은 지금까지와
    똑같이 동작해야 한다 - 90개 claim 전체 재검증에서 이미 0건 변경
    확인했지만(2026-08-19), 여기서도 대표 사례 하나로 다시 못박아둔다."""
    claim = {
        "claim_id": "A93bfa851-C024",
        "claim": "세제, 청소용품 등 살림에 필요한 물품과 세탁·청소 같은 가사 서비스를 포함한 '가정용품 및 가사서비스' 물가는 19.4% 상승했다.",
        "metric_normalized": "가정용품 및 가사서비스 물가",
        "value": "19.4", "unit": "%", "period": "2025-09",
    }
    keywords = ["가정용품 및 가사서비스 물가", "가정용품 물가동향", "가사서비스 물가동향"]
    res = resolve_claim_evidence(conn, claim, keywords)
    _check(
        "구 포맷: value_type 없어도 기존 휴리스틱으로 derivation 트리거됨",
        res.get("derivation", {}).get("used") is True,
        res,
    )


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH, uri=True)
    try:
        test_value_type_level_no_derivation(conn)
        test_value_type_change_rate_triggers_derivation(conn)
        test_value_type_change_rate_explicit_comparison_period(conn)
        test_value_type_change_amount_with_sent_id_siblings(conn)
        test_backward_compat_old_format_untouched(conn)
    finally:
        conn.close()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

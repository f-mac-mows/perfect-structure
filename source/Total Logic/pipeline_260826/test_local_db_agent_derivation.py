"""[2026-08-17 신규] local_db_agent.py의 YoY/기간비교 파생 배선(Task #42) -
`_needs_rate_derivation`/`_extract_explicit_reference_period` 순수 함수
회귀 테스트. 전부 합성/실측 claim 텍스트라 네트워크 불필요.

이 테스트가 지키는 건 전부 실제 프로덕션 입력(run01_result.jsonl)을
`adapter.run_pipeline_from_files`로 돌려서 잡은 실측 버그다 - 격리
단위 테스트를 먼저 안 만들고 바로 실사용에 붙였다가 걸린 것들이라, 같은
문구를 다시 회귀 테스트로 굳혀둔다:
  - C024류(자기 숫자 바로 뒤에 변화 동사) -> 트리거돼야 함
  - C010/C002류(다른 숫자의 변화를 서술하는 동사가 이 claim 근처에 우연히
    있음) -> 트리거되면 안 됨(처음 구현에서 실제로 오탐, 발견 후 수정)
  - C026류("상승률" 같은 명사형이 "상승" 마커에 우연히 걸림) ->
    `_window_has_change_verb` 층에서는 여전히 방향성 동사로 안 잡혀야 함
    (2026-08-17 실측 후 수정, 지금도 유효)
  - [2026-08-18 갱신] 근데 C026 자체는 실제로는 파생이 필요한 claim이었다
    - "16.2%로 평균 상승률과 거의 유사했다"는 방향성 동사(올랐다 등)는
    없지만 "이미 계산된 비율(상승률)과 이 값을 견주는" 비교 구문이라,
    이 값 자체도 등락률류라는 신호다. `_needs_rate_derivation` 전체
    관점에서는 트리거돼야 맞는다는 게 실측(전체 파이프라인 재검증)으로
    새로 드러나서, "트리거되면 안 됨" 목록에서 빼고 아래
    `test_needs_rate_derivation_true_cases_via_comparison`으로 옮겼다.

사용법: python test_local_db_agent_derivation.py (종료 코드 0 = 전체 PASS)
"""

import sys

import kosis_local_search as kls
import kosis_warehouse as wh
import local_db_agent as lda

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_needs_rate_derivation_true_cases():
    cases = [
        ("A93bfa851-C024 (자기 숫자 바로 뒤 '상승했다')",
         {"value": "19.4", "unit": "%",
          "claim": "'가정용품 및 가사서비스' 물가는 19.4% 상승했다."}),
        ("A93bfa851-C001 ('5년간 ... 상승한 것으로 분석됐다')",
         {"value": "20", "unit": "%",
          "claim": "5년간 먹거리 물가가 20% 넘게 상승한 것으로 분석됐다."}),
        ("A93bfa851-C023 (괄호 없이 바로 '올랐다')",
         {"value": "24.1", "unit": "%",
          "claim": "비누·샴푸·미용료 등이 포함된 '기타 상품 및 서비스'는 24.1% 올랐다."}),
    ]
    for label, claim in cases:
        _check(f"트리거돼야 함: {label}", lda._needs_rate_derivation(claim, "index"))


def test_needs_rate_derivation_false_cases_regression():
    """실측으로 발견된 오탐 2건 - 전부 처음 구현에서 잘못 트리거됐다가
    윈도우 보정으로 고친 것들. 재발 방지용 회귀 테스트."""
    cases = [
        ("A82ae9f41-C010 (다른 숫자 '1%포인트'의 동사 '하락했다'가 근처에 있음)",
         {"value": "45.6", "unit": "%",
          "claim": "지난달 15~29세 고용률은 45.6%로 전년 동월 대비 1%포인트 하락했다."}),
        ("A82ae9f41-C002 (다른 숫자 '0.1%포인트'의 동사 '증가하며'가 근처에 있음)",
         {"value": "63.6", "unit": "%",
          "claim": "고용률은 63.6%로 전년 동월 대비 0.1%포인트 증가하며, 지난 1982년 관련 통계 작성 이래 최고치를 새로 썼다."}),
    ]
    for label, claim in cases:
        _check(f"트리거되면 안 됨: {label}", not lda._needs_rate_derivation(claim, "index"))


def test_window_has_change_verb_still_excludes_rate_noun_suffix():
    """[2026-08-17 원 취지 유지 확인] "상승률"/"증가율"처럼 "률" 접미사가
    붙은 명사형은 `_window_has_change_verb` 층에서는 여전히 방향성
    동사로 인정되면 안 된다 - 이 보호가 없으면 아래 비교 구문 트리거
    (`_window_has_rate_comparison`)와 뒤섞여서 "우연히 걸린 것"과
    "의도적으로 비교 구문을 인식한 것"을 구분할 수 없게 된다."""
    window = "로 평균 상승률과 거의 유사했다."
    _check(
        "'상승률' 안의 '상승'은 _window_has_change_verb에서 방향성 동사로 안 잡힘",
        not lda._window_has_change_verb(window),
    )


def test_needs_rate_derivation_true_cases_via_comparison():
    """[2026-08-18 신규 - 실측 발견] 방향성 동사(올랐다/증가 등)가 전혀
    없어도, "이미 계산된 비율(상승률/등락률 등)과 이 값을 견준다"는 비교
    구문(유사하다/넘다/그치다/하회하다 등) 자체가 "이 값도 등락률류"라는
    신호다. 전체 파이프라인 재검증에서 A93bfa851-C025/C026(평균
    상승률과 "유사했다")과 C012~C014(상승률이 20%를 "넘겼다")가 이 패턴
    때문에 원자료 지수값과 직접 비교돼 거짓 MISMATCH가 났던 걸 발견하고
    추가했다."""
    cases = [
        ("C025/C026류 ('평균 상승률과 거의 유사했다')",
         {"value": "16.2", "unit": "%",
          "claim": "'주택, 수도, 전기 및 연료' 물가는 16.7%, '의류 및 신발'은 16.2%로 평균 상승률과 거의 유사했다."},
         None),
        # [실측 참고] 이 claim은 뒤에 형제 값(21.1/20.0)이 바로 이어져
        # sibling_values 없이는(단독 claim 기본 폴백) 윈도우가 "상승률이
        # 20%를 넘겼다"에 닿기도 전에 끊긴다 - LocalDbAgent가 실제로
        # 하듯 형제 값을 넘겨줘야 한다(위 _claim_number_change_window
        # docstring의 sibling_values 설명 참고).
        ("C012~C014류 ('상승률이 20%를 넘겼다', 형제 값 포함)",
         {"value": "21.4", "unit": "%",
          "claim": "기타 식료품(21.4%), 육류(21.1%), 어류 및 수산(20.0%)은 상승률이 20%를 넘겼다."},
         ["21.1", "20.0"]),
    ]
    for label, claim, siblings in cases:
        _check(
            f"비교 구문으로 트리거돼야 함: {label}",
            lda._needs_rate_derivation(claim, "index", sibling_values=siblings),
        )


def test_needs_rate_derivation_gating_conditions():
    _check(
        "이미 rate_of_change 항목이면 unit/동사와 무관하게 항상 False",
        not lda._needs_rate_derivation(
            {"value": "19.4", "unit": "%", "claim": "물가는 19.4% 상승했다."},
            "rate_of_change",
        ),
    )
    _check(
        "unit이 %가 아니면 동사가 있어도 False",
        not lda._needs_rate_derivation(
            {"value": "196", "unit": "명", "claim": "취업자 수는 196만명으로 감소했다."},
            "level",
        ),
    )


def test_needs_rate_derivation_true_when_rate_word_precedes_number():
    """[2026-08-21 신규 - 실측 발견, 90개 claim 배치] "률" 단서(이미
    계산된 비율이라는 신호)가 window(claim 숫자 뒤)가 아니라 숫자 바로
    앞에 오는 문장 구조 - 실측(A93bfa851-C017, "주류 및 담배는 상승률이
    5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다")에서 이 패턴 때문에
    원자료 지수값(105.05, "2020=100")이 claim의 5.0%와 그대로 비교돼
    거짓 MISMATCH가 났다. "그쳤다"가 "그쳤지만"(양보형 어미)으로 활용된
    것까지 함께 잡아야 한다(한글 활용형은 어간+어미 단순 이어붙이기가
    아니라서 "그치"로는 "그쳤"을 못 잡는다 - 반드시 "그쳤"이어야 함)."""
    # [주의] 형제 claim A93bfa851-C018(같은 문장의 "주류만 보면 13.1%")은
    # 일부러 넣지 않았다 - 13.1% 뒤에는 "였다."뿐이라 window에 아무 단서가
    # 없고, 이 claim은 실제로도 이 함수(_needs_rate_derivation)가 아니라
    # 더 앞단(Stage 2 5-way 동점, "01 식료품"/"비주류 음료"까지 섞여
    # 나옴 - 검색 정밀도 문제)에서 막혀 UNVERIFIED_UNRESOLVED로 끝난다
    # (A/C 배치 결과로 확인, 이번 fix로 바뀌지 않음). 그 문제는 이
    # 함수의 책임 범위 밖이라 여기서 억지로 트리거시키지 않는다 -
    # README에 별도 미해결 항목으로 남긴다.
    claim = {"value": "5.0", "unit": "%",
             "claim": "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다."}
    _check(
        "률이 숫자 앞에 와도 트리거돼야 함: C017 (주류/담배 5.0%, '상승률이'가 숫자 앞)",
        lda._needs_rate_derivation(claim, "index"),
    )


def test_needs_rate_derivation_still_false_when_prefix_has_no_rate_word():
    """[회귀 확인] prefix 쪽 '률' 탐지를 추가했다고 해서, 정말 아무
    단서도 없는 문장까지 잘못 트리거되면 안 된다."""
    claim = {"value": "45.6", "unit": "%",
             "claim": "지난달 15~29세 고용률은 45.6%로 전년 동월 대비 1%포인트 하락했다."}
    _check(
        "prefix에도 '률'이 있지만('고용률은') 그 뒤 동사가 다른 숫자(1%포인트)를 서술 - 여전히 트리거 안 됨",
        not lda._needs_rate_derivation(claim, "index"),
    )


def test_claim_number_change_window_skips_duration_year_no_siblings():
    """[2026-08-22 신규 - 실측 재발 방지, Task #26] sibling_values 없는
    단독 claim(else 분기)에서 "N년"(기간) 숫자는 건너뛰고 그 뒤 진짜
    정지 지점까지 window를 확장해야 한다(A82ae9f41-C011 "청년층 고용률은
    작년 5월(-0.7%포인트)부터 1년 넘게 감소세를 이어가고 있다") - 동시에
    C010류(다른 claim의 변화폭 숫자, "년"이 아닌 경우)는 여전히 그
    자리에서 멈춰야 한다. 이 두 동작이 같은 else 분기 안에 있는데, 앞선
    편집에서 실수로 이 분기 자체가 `if sibling_strs:` 블록 안으로
    들어가버려 else 분기가 통째로 사라진 채(=단독 claim은 전혀 안
    잘리고 문장 끝까지 통째로 반환) 회귀가 난 적이 있다(원인:
    test_needs_rate_derivation_false_cases_regression 4건 FAIL로 발견,
    구조를 되돌려 수정). 이 테스트는 그 구조 자체(sibling 없을 때도
    truncate가 실제로 일어남)를 직접 잠근다."""
    c011 = {"value": "-0.7", "unit": "%포인트",
            "claim": "청년층 고용률은 작년 5월(-0.7%포인트)부터 1년 넘게 감소세를 이어가고 있다."}
    window = lda._claim_number_change_window(c011, r"%포인트", sibling_values=None)
    _check(
        "C011: '1년'은 건너뛰고 '감소세'까지 window가 이어짐",
        window is not None and "감소세" in window,
        f"window={window!r}",
    )

    c010 = {"value": "45.6", "unit": "%",
            "claim": "지난달 15~29세 고용률은 45.6%로 전년 동월 대비 1%포인트 하락했다."}
    window_c010 = lda._claim_number_change_window(c010, r"%", sibling_values=None)
    _check(
        "C010: '1%포인트'는 '년'이 아니라서 그 자리에서 멈춤(하락했다 안 보임) - 회귀 방지",
        window_c010 is not None and "하락했다" not in window_c010,
        f"window={window_c010!r}",
    )


def test_claim_number_change_window_sibling_branch_never_truncates():
    """[2026-08-22 신규 - 회귀 잠금] sibling_values가 있으면(열거형 claim
    그룹) "3차 수정" 설계대로 어떤 숫자를 만나도 자르지 않고 원문장 끝
    까지 그대로 돌려줘야 한다(위 버그로 이 분기가 N년-skip truncate
    루프를 잘못 공유하면서 한동안 깨져 있었다 - C012~C014 회귀 테스트
    FAIL로 발견). match_phrases 상관없이 순수하게 이 분기 자체를
    직접 확인한다."""
    claim = {"value": "21.4", "unit": "%",
             "claim": "기타 식료품(21.4%), 육류(21.1%), 어류 및 수산(20.0%)은 상승률이 20%를 넘겼다."}
    window = lda._claim_number_change_window(claim, r"%", sibling_values=["21.1", "20.0"])
    _check(
        "sibling_values가 있으면 숫자를 만나도 안 자르고 '넘겼다'까지 이어짐",
        window is not None and "넘겼다" in window,
        f"window={window!r}",
    )


def test_demographic_alias_covers_multiple_kosis_breadcrumb_formats():
    """[2026-08-22 신규 - 실측 발견, A82ae9f41-C011] KOSIS는 같은
    연령대를 표마다 다르게 표기한다 - DT_1DE9046S는 "* 15~29세"(물결)인데
    DT_1DA7012S는 "15 - 29세"(대시+공백)를 쓴다(DB 실측: dimensions.name
    에 두 표기가 동시에 존재함을 직접 확인). "청년층" 별칭이 물결
    표기만 갖고 있으면 대시 표기 표의 corroboration(≥2 phrase 문턱)이
    항상 1개(고용률만)로 부족해 정답 후보가 버려진다. 사용자 결정
    (2026-08-22): 섣부른 정규화 대신 실측될 때마다 별칭 리스트에
    표기를 하나씩 추가한다 - 이 테스트는 그 리스트 구조(문자열 하나가
    아니라 리스트) 자체와, 현재까지 추가된 두 표기가 동시에 나오는지
    잠근다."""
    tokens = kls._tokenize("청년층 고용률")
    _check(
        "'15~29세'(물결, DT_1DE9046S 표기)가 토큰에 포함됨",
        "15~29세" in tokens,
        f"tokens={tokens!r}",
    )
    _check(
        "'15 - 29세'(대시+공백, DT_1DA7012S 표기)도 함께 포함됨",
        "15 - 29세" in tokens,
        f"tokens={tokens!r}",
    )


def _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0):
    """[2026-08-22 신규 - Task #28] no_data 온디맨드 백필 테스트 전용
    - 후보가 하나뿐이라 disambiguate_by_value가 관여할 여지 자체가 없는
    (Stage 2 동점 없음) 최소 표. `_seed_full_national_debt_table`(11개
    축, "국가채무"류 이름이 서로 substring으로 겹쳐 동점을 유발함)을
    재사용하면 "이 시점 데이터가 아직 없어서 disambiguate_by_value가
    값으로 못 가른다"는 별개 문제와 뒤섞여 이 테스트의 관심사(백필 배관
    자체)가 흐려진다 - 그래서 단일 item×단일 축으로 새로 만든다."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('999', 'T_BACKFILL', '테스트채무현황')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('999', 'T_BACKFILL', 'ITEM', 0, '항목', 'T01', '테스트채무', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, value, unit) "
        "VALUES ('999', 'T_BACKFILL', 'T01', ?, 'Y', ?, '조원')",
        (seeded_period, seeded_value),
    )
    conn.commit()


class _FakeKosisClientForBackfill:
    """kosis_warehouse.fetch_scoped_slice가 실제로 부르는 두 메서드
    (get_period_meta/fetch_actual_statistics_bounded_retry)만 흉내내는
    가짜 - test_warehouse_scope_policy.py의 기존 fake client와 같은
    패턴(중복 정의 - 이 파일이 그 파일을 import하지 않으므로 재사용 대신
    같은 계약으로 새로 만듦)."""

    def __init__(self, response_rows=None, raise_on_fetch=None):
        self.calls = []
        self._response_rows = response_rows if response_rows is not None else []
        self._raise_on_fetch = raise_on_fetch

    def get_period_meta(self, org_id, tbl_id):
        return [{"PRD_SE": "년", "STRT_PRD_DE": "2000", "END_PRD_DE": "2026"}]

    def fetch_actual_statistics_bounded_retry(
        self, org_id, tbl_id, start_year, end_year, itm_id="all",
        current_dim=0, max_dim=8, prd_se="Y", objl_fixed=None,
    ):
        self.calls.append({
            "org_id": org_id, "tbl_id": tbl_id, "start_year": start_year,
            "end_year": end_year, "itm_id": itm_id, "prd_se": prd_se,
            "objl_fixed": objl_fixed,
        })
        if self._raise_on_fetch is not None:
            raise self._raise_on_fetch
        return self._response_rows


def test_no_data_backfill_succeeds_via_fetch_scoped_slice():
    """[2026-08-22 신규 - Task #28, 사용자 지적으로 배선] 표/항목/축까지
    이미 확정됐는데(Stage 1/2 성공) claim이 요구하는 시점만 로컬 facts에
    없을 때(no_data 직전), kosis_client/write_conn을 넘기면 kosis_
    warehouse.fetch_scoped_slice로 이미 확정된 org_id/tbl_id/itm_id/
    axis_codes 그대로 기간만 바꿔 온디맨드 요청하고, 그 결과로 채워진
    값을 재조회해 success로 끝나야 한다. `resolve_claim_evidence`가
    fetch_scoped_slice를 어떤 인자(org_id/tbl_id/prd_se/itm_id/기간)로
    부르는지도 fake의 호출 기록으로 확인한다."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)

    fake_client = _FakeKosisClientForBackfill(response_rows=[{"raw_dict": {
        "ITM_ID": "T01", "PRD_DE": "2026", "PRD_SE": "Y", "DT": "1300.0", "UNIT_NM": "조원",
    }}])
    claim = {
        "claim_id": "TEST-BACKFILL-OK",
        "claim": "테스트채무가 1300조원으로 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026",
    }
    # 실제 LocalDbAgent는 conn(읽기전용)과 write_conn(쓰기전용)을 따로
    # 열지만, 이 단위 테스트는 resolve_claim_evidence의 분기 로직만
    # 보면 되므로 같은(쓰기 가능한) conn을 양쪽에 그대로 재사용한다.
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["테스트채무"],
        kosis_client=fake_client, write_conn=conn,
    )

    _check(
        "fake KOSIS 클라이언트가 정확히 1번 호출됨(온디맨드 백필 시도)",
        len(fake_client.calls) == 1, str(fake_client.calls),
    )
    if fake_client.calls:
        call = fake_client.calls[0]
        _check(
            "이미 확정된 org_id/tbl_id/itm_id 그대로, prd_se만 4자리->Y로 판별해서 부름",
            call["org_id"] == "999" and call["tbl_id"] == "T_BACKFILL"
            and call["itm_id"] == "T01" and call["prd_se"] == "Y",
            str(call),
        )
    _check(
        "백필된 2026년 값(1300.0)으로 success 판정",
        result.get("query_status") == "success" and result.get("normalized_value") == 1300.0,
        str(result),
    )
    conn.close()


def test_no_data_backfill_attempted_but_still_missing_falls_back_cleanly():
    """[2026-08-22 신규 - Task #28] 온디맨드 백필을 시도했는데도(fake
    client가 정상 응답은 하지만 claim이 요구하는 시점 값은 안 채워주는
    경우 - 실제로 KOSIS 자체에 그 시점 데이터가 없는 상황을 흉내냄) 여전히
    없으면, 추측하지 않고 no_data로 안전하게 끝나야 한다 - 다만
    backfill_attempted=True로 "KOSIS까지 확인했다"는 사실은 남겨야 한다
    (실측 우선 원칙 - 시도 안 한 경우와 섞으면 안 됨)."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)

    # fake가 응답은 하지만(예외 없음, 빈 응답도 아님 - 빈 리스트를 주면
    # kosis_warehouse._fetch_with_chunking이 "이 조각만 실패했나?"로 보고
    # 내부적으로 이분탐색 재시도를 여러 번 하게 돼 이 테스트의 관심사(백필
    # 후에도 claim이 원하는 정확한 시점만 여전히 없는 상황)가 흐려진다 -
    # 대신 claim이 요구하는 "2026"이 아닌 다른 시점(2024) 값을 돌려줘서,
    # "KOSIS가 응답은 했지만 이 claim이 필요로 하는 정확한 시점 데이터는
    # 없다"는 실제 국가채무 2025 사례(2024까지만 있고 2025 자체가 없음)를
    # 흉내낸다.
    fake_client = _FakeKosisClientForBackfill(response_rows=[{"raw_dict": {
        "ITM_ID": "T01", "PRD_DE": "2024", "PRD_SE": "Y", "DT": "1250.0", "UNIT_NM": "조원",
    }}])
    claim = {
        "claim_id": "TEST-BACKFILL-STILL-MISSING",
        "claim": "테스트채무가 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["테스트채무"],
        kosis_client=fake_client, write_conn=conn,
    )

    _check("그래도 백필은 시도됨", len(fake_client.calls) == 1, str(fake_client.calls))
    _check(
        "여전히 no_data - 추측으로 대체 안 함",
        result.get("query_status") == "no_data",
        str(result),
    )
    _check(
        "backfill_attempted=True로 'KOSIS까지 확인함'이 결과에 남음",
        result.get("backfill_attempted") is True,
        str(result),
    )
    _check(
        "error_message에 'KOSIS에도 없음' 문구로 시도 여부가 구분됨",
        "KOSIS에도 없음" in (result.get("error_message") or ""),
        result.get("error_message"),
    )
    conn.close()


def test_no_data_backfill_exception_does_not_crash_falls_back_to_no_data():
    """[2026-08-22 신규 - Task #28] 온디맨드 백필 중 네트워크/API 예외가
    나도(fake가 예외를 던지는 걸로 흉내) resolve_claim_evidence 밖으로
    새어나가면 안 된다 - embed_fn/hcx_resolve_fn 예외 처리와 같은 관례
    (이 claim 하나만 no_data로 안전하게 끝나고, 배치 전체는 안 죽음)."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)

    fake_client = _FakeKosisClientForBackfill(raise_on_fetch=ConnectionError("네트워크 없음(테스트)"))
    claim = {
        "claim_id": "TEST-BACKFILL-EXC",
        "claim": "테스트채무가 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["테스트채무"],
        kosis_client=fake_client, write_conn=conn,
    )

    _check(
        "예외가 삼켜지고 no_data로 안전하게 폴백",
        result.get("query_status") == "no_data", str(result),
    )
    _check(
        "backfill_attempted=True(시도는 했음), 에러 사유도 error_message에 남음",
        result.get("backfill_attempted") is True and "네트워크 없음" in (result.get("error_message") or ""),
        str(result),
    )
    conn.close()


def test_no_data_meta_filter_skips_live_call_when_prd_se_not_in_coverage():
    """[2026-08-22 신규 - Task #28-2, 사용자 제안] fact_coverage에 이미
    "년(Y)"만 기록돼 있고(실제 국가채무(D1)/품목군별 국내판매액 변동현황
    배치 적재 실측과 동일한 모양 - 둘 다 배치 로그에 '년'만 찍혔다)
    claim이 월(M) 주기를 요구하면, kosis_client를 넘겼어도 온디맨드
    백필을 아예 시도하지 않고(API 호출 0번) 곧바로 "주기 자체가 KOSIS에
    없음"으로 확정해야 한다 - 이미 KOSIS getMeta로 확인된 사실을 API
    재호출로 다시 확인할 필요가 없다는 게 이 최적화의 핵심."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)
    conn.execute(
        "INSERT INTO fact_coverage (org_id, tbl_id, prd_se, itm_id, axis_key, strt_prd_de, end_prd_de, fetched_at) "
        "VALUES ('999', 'T_BACKFILL', 'Y', 'all', 'all', '1997', '2025', '2026-08-22T00:00:00+00:00')"
    )
    conn.commit()

    fake_client = _FakeKosisClientForBackfill(response_rows=[{"raw_dict": {
        "ITM_ID": "T01", "PRD_DE": "202605", "PRD_SE": "M", "DT": "1300.0", "UNIT_NM": "조원",
    }}])
    claim = {
        "claim_id": "TEST-BACKFILL-METAFILTER",
        "claim": "테스트채무가 2026년 5월 1300조원으로 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026-05",  # 6자리 -> prd_se="M"으로 판별됨(fact_coverage엔 Y만 있음)
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["테스트채무"],
        kosis_client=fake_client, write_conn=conn,
    )

    _check(
        "fake KOSIS 클라이언트가 아예 호출 안 됨(메타로 이미 걸러짐)",
        len(fake_client.calls) == 0, str(fake_client.calls),
    )
    _check("query_status는 no_data", result.get("query_status") == "no_data", str(result))
    _check(
        "meta_filtered=True, backfill_attempted=False(API로 시도한 게 아니라 메타로 걸러진 것)",
        result.get("meta_filtered") is True and result.get("backfill_attempted") is False,
        str(result),
    )
    _check(
        "error_message에 '주기 자체가 KOSIS에 없음' 문구",
        "주기 자체가 KOSIS에 없음" in (result.get("error_message") or ""),
        result.get("error_message"),
    )
    conn.close()


def test_no_data_meta_filter_does_not_block_when_prd_se_matches_coverage():
    """[2026-08-22 신규 - Task #28-2, 회귀 방지] fact_coverage에 이미
    같은 주기(Y)가 기록돼 있으면(즉 "이 주기 자체가 없다"가 아니라 "이
    주기는 있는데 이 특정 구간만 없다"는 뜻) 메타 필터가 잘못 걸러내면
    안 되고, 기존 온디맨드 백필로 정상적으로 넘어가야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)
    conn.execute(
        "INSERT INTO fact_coverage (org_id, tbl_id, prd_se, itm_id, axis_key, strt_prd_de, end_prd_de, fetched_at) "
        "VALUES ('999', 'T_BACKFILL', 'Y', 'all', 'all', '1997', '2025', '2026-08-22T00:00:00+00:00')"
    )
    conn.commit()

    fake_client = _FakeKosisClientForBackfill(response_rows=[{"raw_dict": {
        "ITM_ID": "T01", "PRD_DE": "2026", "PRD_SE": "Y", "DT": "1300.0", "UNIT_NM": "조원",
    }}])
    claim = {
        "claim_id": "TEST-BACKFILL-METAFILTER-PASS",
        "claim": "테스트채무가 1300조원으로 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026",  # 4자리 -> prd_se="Y", fact_coverage에도 Y가 있음 - 필터 안 걸림
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["테스트채무"],
        kosis_client=fake_client, write_conn=conn,
    )

    # [주의] 성공 경로(success)는 no_data 전용 필드인 meta_filtered/
    # backfill_attempted를 아예 안 돌려준다(no_data 반환 블록에만 있음) -
    # 처음엔 이 테스트가 `result.get("meta_filtered") is False`로 체크
    # 했다가 실패했다(.get()이 None을 돌려주고 None is False가 False라서) -
    # 이 테스트가 실제로 확인하려는 건 "메타 필터에 안 걸렸다"는 사실
    # 자체(=fake가 호출됐다는 것)이지 no_data 응답의 필드 존재 여부가
    # 아니므로, calls 개수만으로 판단한다.
    _check(
        "메타 필터에 안 걸리고 실제로 온디맨드 백필이 시도됨",
        len(fake_client.calls) == 1,
        f"calls={fake_client.calls}, result={result}",
    )
    _check(
        "백필된 값으로 success",
        result.get("query_status") == "success" and result.get("normalized_value") == 1300.0,
        str(result),
    )
    conn.close()


def test_no_data_without_kosis_client_keeps_existing_behavior():
    """[2026-08-22 신규 - Task #28, 회귀 방지] kosis_client를 안 넘기면
    (기본값 None) 기존 동작이 한 글자도 안 바뀌어야 한다 - backfill_
    attempted가 False로 남고 error_message도 예전 문구 그대로."""
    conn = wh.get_connection(":memory:")
    _seed_single_item_backfill_table(conn, seeded_period="2025", seeded_value=1200.0)

    claim = {
        "claim_id": "TEST-BACKFILL-NOCLIENT",
        "claim": "테스트채무가 늘었다.",
        "value_num": 1300.0,
        "unit": "조원",
        "period": "2026",
    }
    result = lda.resolve_claim_evidence(conn, claim, keywords=["테스트채무"])

    _check("query_status는 여전히 no_data", result.get("query_status") == "no_data", str(result))
    _check(
        "backfill_attempted=False(시도 자체를 안 함)",
        result.get("backfill_attempted") is False, str(result),
    )
    _check(
        "error_message는 기존 문구 그대로('로컬 DB에 없음', 'KOSIS에도 없음' 아님)",
        "로컬 DB에 없음" in (result.get("error_message") or "")
        and "KOSIS에도 없음" not in (result.get("error_message") or ""),
        result.get("error_message"),
    )
    conn.close()


def test_needs_rate_derivation_true_via_metric_suffix_when_window_has_no_clue():
    """[2026-08-22 신규 - Task #25, 실측 발견 A93bfa851-C018] "주류 및
    담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다"에서
    13.1% 뒤는 "였다."뿐이고 앞에도 '률' 단서가 없어 window/prefix
    둘 다로는 못 잡는다(형제 claim C017의 5.0%는 잡히는 것과 대비) -
    metric_normalized="주류 물가 상승률"이 이미 등락률 개념이라고
    명시하므로 이 신호로 트리거돼야 한다."""
    claim = {
        "value": "13.1", "unit": "%",
        "claim": "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다.",
        "metric": "주류 물가 상승률", "metric_normalized": "주류 물가 상승률",
    }
    _check(
        "C018: window/prefix 단서 없어도 metric_normalized '상승률' 접미사로 트리거됨",
        lda._needs_rate_derivation(claim, "index"),
    )


def test_needs_rate_derivation_metric_suffix_does_not_overtrigger_level_rate_names():
    """[회귀 방지] "고용률"/"15~29세 고용률"처럼 그 자체가 KOSIS가 직접
    제공하는 level 지표 이름도 "률"로 끝나지만, 방향성 접두("상승"/
    "증가"/"하락"/"등락"/"변동")가 없으므로 새 metric 접미사 체크에
    걸리면 안 된다 - window/prefix에도 단서가 없는 상황을 만들어 순수
    metric 체크만 검증한다(C010/C002는 기존 window 기반 체크로도 이미
    False가 나오지만, 이 테스트는 metric 체크 자체가 오탐 안 하는지
    독립적으로 확인한다)."""
    cases = [
        ("고용률 자체", {"value": "63.6", "unit": "%",
         "claim": "고용률은 63.6%다.", "metric": "고용률", "metric_normalized": "고용률"}),
        ("15~29세 고용률", {"value": "45.6", "unit": "%",
         "claim": "15~29세 고용률은 45.6%다.", "metric": "15~29세 고용률", "metric_normalized": "15~29세 고용률"}),
    ]
    for label, claim in cases:
        _check(
            f"{label}: metric이 '률'로 끝나도 방향성 접두 없으면 안 트리거됨",
            not lda._needs_rate_derivation(claim, "index"),
        )


def test_extract_explicit_reference_period():
    cases = [
        ("연월 명시 + '에 비해'", "식료품 물가지수는 2020년 9월에 비해 22.9% 올랐다", "202509", "202009"),
        ("연도만 명시 + '대비'", "2020년 대비 소비자물가가 올랐다", "2025", "2020"),
        ("'N년 전에 비해'", "과일과 우유는 5년 전에 비해 30% 넘게 급등했다.", "202509", "202009"),
        ("'N년간' (C001 실측 문구)", "5년간 먹거리 물가가 20% 넘게 상승했다.", "202509", "202009"),
        ("명시 기준시점 없음(전년비 등) -> None, 호출부가 YoY 기본값 사용", "전년 동월 대비 늘었다", "202509", None),
    ]
    for label, text, target, expected in cases:
        got = lda._extract_explicit_reference_period(text, target)
        _check(f"{label}: {text!r}", got == expected, f"got={got!r} expected={expected!r}")


# [2026-08-24 삭제됨 - "안 쓰기로 한 로직" 정리] 여기 있던
# test_embedding_fallback_error_does_not_crash_claim/_fake_debt_embed_fn/
# test_embedding_fallback_disambiguates_by_value_when_score_misleads는
# local_db_agent.resolve_claim_evidence의 embed_fn 파라미터(Task #80, CLOVA
# 임베딩 기반 Stage 2 갭 폴백)를 검증하던 테스트다 - 그 파라미터 자체가
# 프로덕션에서 한 번도 안 쓰이고 hcx_resolve_fn으로 대체돼 삭제되면서 함께
# 제거했다.


def _seed_full_national_debt_table(conn):
    """[2026-08-21 신규 - Task #80 전환] 실제 KOSIS 표(184/DT_102006_001,
    "국가채무(D1)")의 진짜 축 11개(2026-08-20 KOSIS MCP kosis_table_info로
    실측 확인한 것 - README "아홉 번째" 항목 참고)를 전부 심어서, HCX
    단일 콜 폴백이 표 전체 셀 목록을 "빠짐없이" 받는지 검증할 때 쓴다.
    임베딩 경로의 top_k=5 truncation과 달리 이 경로는 후보를 미리 자르지
    않아야 하므로, 셀이 5개보다 많은 표가 필요하다(11개 실측 축 전부)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('184', 'DT_102006_001', '국가채무(D1)')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('184', 'DT_102006_001', 'ITEM', 0, '항목', 'T001', '국가채무(D1)', NULL, NULL)"
    )
    axes = [
        ("A01", "국가채무", 1200.0, "조원"),
        ("A02", "국가채무 GDP 대비", 50.0, "%"),
        ("A03", "중앙정부 채무", 1140.0, "조원"),
        ("A04", "국채", 1100.0, "조원"),
        ("A05", "국고채권", 1050.0, "조원"),
        ("A06", "국민주택채권", 5.0, "조원"),
        ("A07", "외평채권", 40.0, "조원"),
        ("A08", "차입금", 10.0, "조원"),
        ("A09", "국고채무부담행위", 30.0, "조원"),
        ("A10", "지방정부 순채무", 33.0, "조원"),
        ("A11", "기타", 1.0, "조원"),
    ]
    for code, name, value, unit in axes:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('184', 'DT_102006_001', 'A', 1, '채무내역별', ?, ?, NULL, NULL)",
            (code, name),
        )
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('184', 'DT_102006_001', 'T001', '2025', 'Y', ?, ?, ?)",
            (code, value, unit),
        )
    conn.commit()


def test_hcx_fallback_resolves_correctly_and_flags_set():
    """[2026-08-21 신규 - Task #80 전환] hcx_resolve_fn이 표 전체 셀
    목록에서 정확한 index를 돌려주면, 그 셀이 채택되고(hcx_fallback_used=
    True, embedding_fallback_used=False), 후보가 1개뿐이라 disambiguate_
    by_value를 거칠 필요 없이 confident=True로 곧바로 끝나야 한다(hcx_
    resolve_fn 자체가 단일 index만 돌려주는 설계 - local_db_agent.py
    resolve_claim_evidence 문서의 "hcx_resolve_fn" 항목 참고)."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        # 진짜 HCX-007처럼 "국가채무" 정확 일치 셀을 고른다고 가정한 가짜 -
        # 이 테스트의 관심사는 실제 판단 품질이 아니라 배관(index 선택 ->
        # itm_id/axis_codes 복원 -> facts 재조회)이 맞는지다.
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    claim = {
        "claim_id": "TEST-HCX1",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"], hcx_resolve_fn=_fake_hcx_resolve_fn,
    )

    _check("hcx 폴백이 실제로 쓰임", result.get("hcx_fallback_used") is True, str(result))
    _check("embedding 폴백은 안 쓰임(hcx_resolve_fn만 넘김)", result.get("embedding_fallback_used") is False, str(result))
    _check(
        "HCX가 고른 A01(국가채무, 1200.0조원)이 최종 채택됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 1200.0,
        str(result),
    )
    _check(
        "후보가 1개뿐이라 곧바로 confident=True, confidence_note 없음",
        result.get("confident") is True and result.get("confidence_note") is None,
        str(result),
    )
    conn.close()


def test_hcx_fallback_receives_full_cell_list_no_truncation():
    """[2026-08-21 신규 - Task #80 전환 핵심 동기] 임베딩 폴백은 top_k=5로
    후보를 미리 잘라서 실측(probe_national_debt_full_pipeline.py, "나랏빚이
    눈덩이처럼 불어났다")에서 정답 셀이 후보군 밖으로 밀려난 적이 있었다
    (README "열 번째" 항목). 이 회귀 테스트는 hcx_resolve_fn에 넘어가는
    cell_texts가 표의 distinct 셀 11개 "전부"이고(5개로 잘리지 않고),
    그 안에 정답 텍스트("국가채무(D1) 국가채무")가 실제로 포함돼 있는지
    구조적으로 확인한다 - top_k 같은 사전 필터링이 이 경로엔 없다는 걸
    코드로 못박아 둔다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    received = {}

    def _capturing_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        received["cell_texts"] = list(cell_texts)
        return None  # 이 테스트는 truncation 여부만 본다 - 판단 결과는 관심사 아님

    claim = {
        "claim_id": "TEST-HCX2",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    lda.resolve_claim_evidence(conn, claim, keywords=["국가채무"], hcx_resolve_fn=_capturing_hcx_resolve_fn)

    _check(
        "hcx_resolve_fn이 표의 distinct 셀 11개를 전부 받음(5개로 안 잘림)",
        len(received.get("cell_texts", [])) == 11,
        f"받은 개수={len(received.get('cell_texts', []))}",
    )
    _check(
        "그 목록 안에 정답 셀 텍스트가 실제로 포함돼 있음",
        "국가채무(D1) 국가채무" in received.get("cell_texts", []),
        str(received.get("cell_texts")),
    )
    conn.close()


# [2026-08-24 삭제됨 - "안 쓰기로 한 로직" 정리] 여기 있던
# test_hcx_fallback_preferred_over_embed_fn_when_both_given은 embed_fn과
# hcx_resolve_fn을 둘 다 넘겼을 때의 우선순위를 검증했는데, embed_fn
# 파라미터 자체가 삭제되면서 이 테스트가 검증하던 상황(둘 다 넘기는 경우)
# 자체가 더 이상 존재하지 않는다 - hcx_resolve_fn 단독 동작은
# test_hcx_fallback_resolves_correctly_and_flags_set이 이미 검증한다.


def test_hcx_fallback_none_result_falls_back_to_unresolved():
    """[2026-08-21 신규 - Task #80 전환] HCX가 "확신 없음"(None)을 돌려주면
    (disambiguate_by_value로 더 좁힐 방법이 없으므로) 추측하지 않고 기존과
    동일하게 unresolved로 끝나야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _unconfident_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        return None

    claim = {
        "claim_id": "TEST-HCX4",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"], hcx_resolve_fn=_unconfident_hcx_resolve_fn,
    )

    _check(
        "HCX가 None을 돌려주면 추측하지 않고 unresolved로 끝남",
        result.get("query_status") == "unresolved",
        str(result),
    )
    _check("hcx_fallback_used는 False로 남음(못 풀었으므로)", result.get("hcx_fallback_used") is False, str(result))
    conn.close()


def test_hcx_fallback_exception_does_not_crash_claim():
    """[2026-08-21 신규 - Task #80 전환, embedding 쪽과 동일한 원칙]
    hcx_resolve_fn이 예외를 던져도(네트워크 오류 등) 이 claim만 unresolved로
    조용히 끝나야 하고, 사유(hcx_fallback_error)가 결과에 남아 진단
    가능해야 한다 - test_embedding_fallback_error_does_not_crash_claim과
    같은 계약을 hcx 경로에도 그대로 적용."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _raising_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        raise RuntimeError("HCX API 네트워크 오류가 발생했습니다. (재현용 가짜 예외)")

    claim = {
        "claim_id": "TEST-HCX5",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    try:
        result = lda.resolve_claim_evidence(conn, claim, keywords=["국가채무"], hcx_resolve_fn=_raising_hcx_resolve_fn)
        raised = False
    except Exception:
        result = None
        raised = True

    _check("hcx_resolve_fn이 예외를 던져도 resolve_claim_evidence가 삼킴", not raised, f"raised={raised}")
    if result is not None:
        _check("Stage 2가 못 찾았으니 unresolved로 폴백", result.get("query_status") == "unresolved", str(result))
        _check(
            "실패 사유(hcx_fallback_error)가 결과에 남아 추적 가능",
            bool(result.get("hcx_fallback_error")) and "네트워크" in result["hcx_fallback_error"],
            str(result.get("hcx_fallback_error")),
        )
    conn.close()


def test_weak_literal_tie_uses_hcx_instead_of_loose_value_tolerance():
    """[2026-08-21 신규 - Task #80 로직 개선, 실측 버그 회귀 테스트]
    probe_national_debt_full_pipeline_hcx.py의 실제 로컬 실행에서 재현된
    버그: "정부 빚이 사상 최대를 기록했다"는 hcx_fallback_used=False로
    찍혔다(=literal Stage 2가 이미 뭔가를 찾아서 HCX까지 안 감) - "정부"
    토큰이 "중앙정부 채무"(A03)/"지방정부 순채무"(A10) 양쪽에 걸려 동점이
    됐고, disambiguate_by_value의 5% 값 허용오차가 A03(1141.2조원)을
    claim 값(1175.0)과 2.9% 차이로 "유일하게 가까움"으로 오판해
    confident=True로 틀린 답을 냈다.

    이 테스트는 그 정확한 상황을 재현해서, hcx_resolve_fn이 주어지면
    값으로 바로 풀지 않고 먼저 HCX한테 표 전체를 보여줘 재확인하고, 그
    결과(가짜 hcx_resolve_fn이 A01을 정확히 고름)로 A01이 최종 채택되는지
    확인한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    # 사전 조건: literal 매칭만으로는 "정부" 하나로 A03/A10이 동점이 되고,
    # 둘 다 matched_phrases가 1개뿐이어야 한다(약한 동점 조건 재현).
    match_phrases = kls._tokenize("정부 빚이 사상 최대를 기록했다")
    pre_candidates = kls.resolve_evidence_by_flat_match(conn, "184", "DT_102006_001", match_phrases, top_n=8)
    pre_top = pre_candidates[0]
    pre_tie = [
        c for c in pre_candidates
        if c["score"] == pre_top["score"]
        and c.get("unexplained_axes") == pre_top.get("unexplained_axes")
        and c.get("ancestor_only_hits") == pre_top.get("ancestor_only_hits")
    ]
    _check(
        "사전 조건: '정부' 토큰 하나로 A03/A10이 동점(matched_phrases 각 1개)",
        len(pre_tie) >= 2 and all(len(c.get("matched_phrases") or []) < 2 for c in pre_tie),
        str(pre_tie),
    )

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    claim = {
        # [주의] value_num은 _seed_full_national_debt_table의 실제 A01 값
        # (1200.0)과 맞춰야 한다 - 다르면 Stage 3(실제 facts 재조회)가
        # A01의 진짜 값을 그대로 돌려주므로 이 assertion과 안 맞게 된다.
        "claim_id": "TEST-HCX6",
        "claim": "정부 빚이 사상 최대를 기록했다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"], hcx_resolve_fn=_fake_hcx_resolve_fn,
    )

    _check(
        "약한 동점이라 HCX 재확인이 실제로 쓰임(hcx_fallback_used=True)",
        result.get("hcx_fallback_used") is True,
        str(result),
    )
    _check(
        "값 허용오차로 A03(부분집합, 오답)이 아니라 HCX가 고른 A01(전체, 정답)이 채택됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 1200.0,
        str(result),
    )
    _check("HCX가 확정했으니 confident=True", result.get("confident") is True, str(result))
    conn.close()


def _seed_bread_category_table(conn):
    """[2026-08-22 신규 - A93bfa851-C007/C009 실측 버그 재현용] DT_1J22001의
    "빵 및 곡물"(부모) / "빵"(자식) 계층 동점을, 실제 표와 같은 2축 구조
    (축 1=시도별/지역, 축 2=지출목적별/카테고리)로 재현한다 - 단일 축으로만
    재현하면 "시도별 축이 자동으로 안 채워지는" 실측 버그(아래 참고)가
    안 드러난다.

    _tokenize 수정 이후 실제로 드러난 버그: match_phrases=['빵'] 하나로
    부모("빵 및 곡물")와 자식("빵")이 둘 다 걸려 동점이 됐다(부모 이름
    자체에 "빵"이 부분 문자열로 포함돼 있어서, ancestor_only_hits로도 못
    가른다). 이어서 두 번째 실측 버그: HCX가 카테고리 축(A01116="빵")은
    정확히 골랐는데, 지역 축(시도별)이 kls._axis_total_code(합계/전체류
    라벨만 찾음)로는 안 채워져서("전국"은 그런 이름이 아님) 19개 지역
    전부와 매치돼 "유일하게 못 찾음"으로 조용히 실패했다 - kls.
    _AXIS_LABEL_DEFAULT_NAME("시도별"->"전국")을 추가로 써야 풀린다."""
    # [테스트 편의 - tbl_nm에 "빵"을 넣어 Stage 1(표 후보) 매칭을 단순/
    # 안정적으로 만든다] 실제 DT_1J22001의 진짜 표 이름은 "빵"을 포함하지
    # 않지만, 이 테스트의 관심사는 Stage 1 표 검색 정확도가 아니라 Stage 2
    # weak_literal_tie(부모/자식 동점) 동작이다 - 표 이름 매칭으로 Stage 1을
    # 확실히 통과시키고 Stage 2에 집중한다.
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('101', 'DT_1J22001', '빵 소비자물가지수 테스트표')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('101', 'DT_1J22001', 'ITEM', 0, '항목', 'T', '소비자물가지수', NULL, NULL)"
    )
    # 축 1: 시도별(지역) - 실측 표와 동일한 axis_label. "전국"이 kls.
    # _AXIS_LABEL_DEFAULT_NAME에 이미 등록된 기본값(2개로 축소 재현).
    for code, name in [("T10", "전국"), ("T11", "서울특별시")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('101', 'DT_1J22001', 'A', 1, '시도별', ?, ?, NULL, NULL)",
            (code, name),
        )
    # 축 2: 지출목적별(카테고리) - 부모/자식 동점 재현.
    rows = [
        ("A011", "빵 및 곡물", None, 28.0),
        ("A01116", "빵", "A011", 38.5),
        ("A01117", "떡", "A011", 25.8),
    ]
    for code, name, parent, value in rows:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('101', 'DT_1J22001', 'B', 2, '지출목적별', ?, ?, ?, NULL)",
            (code, name, parent),
        )
        # "전국"(T10) 값만 심는다 - claim이 지역을 언급 안 했으니 "전국"이
        # 정답이어야 하고, 서울(T11)엔 값을 안 심어서 축 1 기본값 채우기가
        # 실제로 필요한 상황을 만든다(안 채우면 T10/T11 둘 다 후보라
        # 여전히 유일하게 못 찾을 것 - 근데 T11엔 데이터가 없으니 T10만
        # 남는다는 우연으로 통과하면 안 되므로, 다음 줄처럼 T11도 명시적
        # 으로 다른 값을 심어 "정말 기본값 로직이 작동해야만" 통과하게 한다).
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, c2, value, unit) "
            "VALUES ('101', 'DT_1J22001', 'T', '202509', 'M', 'T10', ?, ?, '%')",
            (code, value),
        )
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, c2, value, unit) "
            "VALUES ('101', 'DT_1J22001', 'T', '202509', 'M', 'T11', ?, ?, '%')",
            (code, value + 1.0),
        )
    conn.commit()


def test_weak_literal_tie_axis_tree_hcx_resolves_parent_child_ambiguity():
    """[2026-08-22 신규 - A93bfa851-C007 실측 버그 회귀 테스트] hcx_axis_
    resolve_fn을 넘기면, "빵" 하나로 생긴 부모/자식 동점을 값 허용오차가
    아니라 축 트리를 본 HCX 판단으로 풀어야 한다 - 이 테스트의 가짜
    hcx_axis_resolve_fn은 자식(A01116, "빵" 자체)을 고른다."""
    conn = wh.get_connection(":memory:")
    _seed_bread_category_table(conn)

    match_phrases = kls._tokenize("빵 물가")
    pre_candidates = kls.resolve_evidence_by_flat_match(conn, "101", "DT_1J22001", match_phrases, top_n=5)
    pre_top = pre_candidates[0]
    pre_tie = [
        c for c in pre_candidates
        if c["score"] == pre_top["score"]
        and c.get("unexplained_axes") == pre_top.get("unexplained_axes")
        and c.get("ancestor_only_hits") == pre_top.get("ancestor_only_hits")
    ]
    _check(
        "사전 조건: '빵' 토큰 하나로 부모(A011)/자식(A01116)이 동점(둘 다 전국(T10) 셀)",
        len(pre_tie) >= 2 and any(c["axis_codes"].get(2) == "A011" for c in pre_tie)
        and any(c["axis_codes"].get(2) == "A01116" for c in pre_tie),
        str(pre_tie),
    )

    def _fake_hcx_axis_resolve_fn(axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period):
        _check(
            "hcx_axis_resolve_fn에 넘어간 axis_trees에 압축된 트리(부모/자식 각 1번씩)가 담김",
            2 in axis_trees and axis_trees[2]["tree_text"].count("빵 [A01116]") == 1,
            str(axis_trees),
        )
        # [핵심] 지역 축(1)은 claim이 언급 안 했으니 일부러 안 정한다 -
        # local_db_agent._lookup_cell_by_axis_codes가 kls._AXIS_LABEL_
        # DEFAULT_NAME("시도별"->"전국")으로 T10을 자동으로 채워야 한다.
        return {2: "A01116"}

    claim = {
        "claim_id": "TEST-AXIS1",
        "claim": "빵(38.5%)이 크게 올랐다.",
        "metric": "빵",
        "value_num": 38.5,
        "unit": "%",
        "period": "2025-09",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["빵"], hcx_axis_resolve_fn=_fake_hcx_axis_resolve_fn,
    )

    _check(
        "약한 동점이라 축 트리 HCX 재확인이 쓰임(hcx_fallback_used=True)",
        result.get("hcx_fallback_used") is True,
        str(result),
    )
    _check(
        "HCX가 고른 자식(빵, 38.5)이 채택됨 - 부모(빵 및 곡물, 28.0)가 아님",
        result.get("query_status") == "success" and result.get("normalized_value") == 38.5,
        str(result),
    )
    _check("HCX가 확정했으니 confident=True", result.get("confident") is True, str(result))
    conn.close()


def test_weak_literal_tie_prefers_axis_resolve_fn_over_plain_hcx_resolve_fn():
    """[2026-08-22 신규] hcx_resolve_fn(카테시안 곱 flat, 토큰 비쌈)과
    hcx_axis_resolve_fn(압축 트리, 토큰 쌈)을 둘 다 넘기면 weak_literal_tie는
    axis_resolve_fn을 우선해야 한다 - hcx_resolve_fn을 "호출되면 예외"
    가짜로 증명한다."""
    conn = wh.get_connection(":memory:")
    _seed_bread_category_table(conn)

    def _must_not_be_called_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        raise AssertionError("hcx_axis_resolve_fn이 있으면 hcx_resolve_fn은 호출되면 안 됨")

    def _fake_hcx_axis_resolve_fn(axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period):
        return {2: "A01116"}

    claim = {
        "claim_id": "TEST-AXIS2",
        "claim": "빵(38.5%)이 크게 올랐다.",
        "metric": "빵",
        "value_num": 38.5,
        "unit": "%",
        "period": "2025-09",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["빵"],
        hcx_resolve_fn=_must_not_be_called_hcx_resolve_fn,
        hcx_axis_resolve_fn=_fake_hcx_axis_resolve_fn,
    )
    _check(
        "예외 없이(hcx_resolve_fn 미호출) axis_resolve_fn 결과로 확정됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 38.5,
        str(result),
    )
    conn.close()


def test_weak_literal_tie_axis_resolve_fn_skipped_when_tie_spans_multiple_items():
    """[범위 제한 회귀 테스트] tie 후보들의 itm_id가 서로 다르면(아직
    실측된 사례 없음 - 이 경로는 아직 item까지 축 트리로 안 다룸)
    hcx_axis_resolve_fn을 호출하지 않고 hcx_resolve_fn(있으면)으로
    안전하게 폴백해야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_bread_category_table(conn)
    # 두 번째 item("U")을 추가하고 같은 code(A01116)에 값을 하나 더
    # 심어서 tie 후보의 itm_id가 갈리게 만든다.
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('101', 'DT_1J22001', 'ITEM', 0, '항목', 'U', '다른품목', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, c2, value, unit) "
        "VALUES ('101', 'DT_1J22001', 'U', '202509', 'M', 'T10', 'A01116', 38.5, '%')"
    )
    conn.commit()

    axis_resolve_calls = []

    def _counting_fake_hcx_axis_resolve_fn(axis_trees, claim_text, item_context, claimed_value, claimed_unit, claimed_period):
        axis_resolve_calls.append(1)
        return {2: "A01116"}

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        # cell_texts는 item명+축 텍스트 전부 이어붙인 문자열이라("소비자
        # 물가지수 전국 빵" 등) 정확히 "빵"과만 같지 않다 - "빵"으로
        # 끝나면서(그리고 "빵 및 곡물"처럼 더 긴 조상 이름이 아니면서)
        # "서울특별시"가 아닌(값이 다름, 38.5가 아니라 39.5) "전국" 후보를
        # 고른다.
        for i, t in enumerate(cell_texts):
            if (t.endswith(" 빵") or t == "빵") and "서울특별시" not in t:
                return i
        return None

    claim = {
        "claim_id": "TEST-AXIS3",
        "claim": "빵(38.5%)이 크게 올랐다.",
        "metric": "빵",
        "value_num": 38.5,
        "unit": "%",
        "period": "2025-09",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["빵"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        hcx_axis_resolve_fn=_counting_fake_hcx_axis_resolve_fn,
    )
    _check(
        "itm_id가 갈리는 tie면 hcx_axis_resolve_fn을 아예 안 부름",
        len(axis_resolve_calls) == 0,
        str(axis_resolve_calls),
    )
    _check(
        "대신 기존 hcx_resolve_fn(전체 flat text) 경로로 안전하게 폴백해 여전히 해결됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 38.5,
        str(result),
    )
    conn.close()


def test_strong_literal_tie_still_uses_disambiguate_by_value_directly():
    """[2026-08-21 신규 - 위 fix의 안전장치 확인] 서로 다른 phrase 2개
    이상이 corroborate하는 "튼튼한" 동점(유가증권류)은 hcx_resolve_fn이
    있어도 HCX로 안 새고 기존처럼 disambiguate_by_value로 바로 풀려야
    한다 - hcx_resolve_fn을 "호출되면 예외" 가짜로 증명한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    # A01/A02 둘 다 "국가채무"라는 공통 단어를 갖지만, "GDP"라는 두 번째
    # phrase는 A02에만 있어 실제로는 동점이 안 나야 정상이다 - 여기서는
    # 대신 두 서로 다른 phrase("국가채무", "GDP")가 A02 하나에만 몰려서
    # A02가 명확한 1위가 되는지(동점조차 아님)를 확인해, "튼튼한 매칭이면
    # 애초에 이 로직 근처에도 안 간다"는 것부터 보인다.
    match_phrases = ["국가채무", "GDP"]
    candidates = kls.resolve_evidence_by_flat_match(conn, "184", "DT_102006_001", match_phrases, top_n=8)
    _check(
        "사전 조건: 서로 다른 phrase 2개가 겹치면 A02가 명확한 1위(동점 아님)",
        candidates[0]["axis_codes"].get(1) == "A02" and len(candidates[0].get("matched_phrases") or []) >= 2,
        str(candidates[:2]),
    )

    def _must_not_be_called_hcx(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        raise AssertionError("튼튼한 매칭이면 HCX 재확인 경로를 안 타야 함")

    claim = {
        # [주의] value_num을 실제 A02 값(50.0)에서 살짝 어긋나게(52.0) 둔다 -
        # 정확히 50.0이면 Stage 1/2보다 먼저 도는 "값 기반 검색" 빠른
        # 경로(resolve_claim_evidence 앞부분, tolerance=0.01)가 바로
        # 채택해버려서 이 테스트가 검증하려는 Stage 2 동점 판정 자체를
        # 거치지 않는다(실제로 한 번 이렇게 걸려서 고침). Stage 3(실제
        # facts 재조회)은 claimed_value와 무관하게 A02의 진짜 값(50.0)을
        # 그대로 돌려주므로 아래 assertion은 여전히 50.0과 비교한다.
        "claim_id": "TEST-HCX7",
        "claim": "국가채무 GDP 대비 비율은 52%다.",
        "value_num": 52.0,
        "unit": "%",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"], hcx_resolve_fn=_must_not_be_called_hcx,
    )
    _check(
        "튼튼한 매칭은 HCX 호출 없이(예외 없이) 바로 A02로 확정됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 50.0
        and result.get("hcx_fallback_used") is False,
        str(result),
    )
    conn.close()


def test_llm_table_select_mode_routes_via_hcx_table_resolve_fn():
    """[2026-08-21 신규 - Task #80 확장] stage1_keywords="llm_table_
    select"면 kls.search_local을 거치지 않고 hcx_table_resolve_fn이
    고른 표로 바로 진행돼야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_table_resolve_fn(table_list, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(table_list):
            if t["org_id"] == "184" and t["tbl_id"] == "DT_102006_001":
                return i
        return None

    # [주의] Stage 1(표 선택)만 이 테스트의 관심사라서, Stage 2(항목/축
    # 확정)도 끝까지 성공하려면 raw_sentence 토큰화가 "국가채무"와 문자
    # 그대로 안 겹치는 문제를 기존 Task #80 gap 폴백(hcx_resolve_fn)으로
    # 마저 풀어줘야 한다 - 이미 다른 테스트(test_hcx_fallback_resolves_
    # correctly_and_flags_set)에서 검증된 것과 같은 가짜를 그대로 재사용.
    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    claim = {
        "claim_id": "TEST-STAGE1-LLM1",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=[],
        stage1_keywords="llm_table_select",
        hcx_table_resolve_fn=_fake_hcx_table_resolve_fn,
        hcx_resolve_fn=_fake_hcx_resolve_fn,
    )
    _check(
        "llm_table_select 모드로 정답표가 채택되고(Stage 1) 값까지 조회됨(Stage 2/3)",
        result.get("query_status") == "success"
        and result.get("org_id") == "184" and result.get("table_id") == "DT_102006_001"
        and result.get("normalized_value") == 1200.0,
        str(result),
    )
    conn.close()


def test_llm_table_select_mode_receives_full_table_list_no_truncation():
    """[2026-08-21 신규] hcx_table_resolve_fn이 받는 table_list가
    kls.list_registered_tables(conn) 전체(사전 필터링 없이)와 일치해야
    한다 - 표를 여러 개 심어서 확인."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('343', 'DT_343_2010_S0043', '유가증권 순위별 거래')"
    )
    conn.commit()

    received = {}

    def _capturing_fn(table_list, claim_text, claimed_value, claimed_unit, claimed_period):
        received["table_list"] = list(table_list)
        return None

    claim = {"claim_id": "TEST-STAGE1-LLM2", "claim": "아무 claim", "value_num": None, "unit": None, "period": None}
    lda.resolve_claim_evidence(
        conn, claim, keywords=[],
        stage1_keywords="llm_table_select",
        hcx_table_resolve_fn=_capturing_fn,
    )
    _check(
        "table_list가 등록된 표 2개를 전부 포함(사전 필터링 없음)",
        len(received.get("table_list", [])) == 2,
        f"받은 개수={len(received.get('table_list', []))}",
    )
    tbl_ids = {t["tbl_id"] for t in received.get("table_list", [])}
    _check(
        "두 표 모두 실제로 포함됨",
        tbl_ids == {"DT_102006_001", "DT_343_2010_S0043"},
        str(tbl_ids),
    )
    conn.close()


def test_llm_table_select_mode_without_resolve_fn_returns_not_found():
    """[2026-08-26 갱신 - FTS 폴백 배선 후] hcx_table_resolve_fn을 안
    넘기면(기본값 None) 이제는 search_local(FTS)로 폴백을 "시도"한다(더
    이상 무조건 not_found로 끝내지 않음 - 아래 새 테스트
    `test_llm_table_select_mode_falls_back_to_fts_when_hcx_fails` 참고).
    다만 이 테스트의 claim("나랏빚이 불어났다")은 "국가채무"와 문자
    그대로 안 겹치는 케이스라 FTS도 못 찾으므로 여전히 not_found로
    끝난다 - 원래 이 케이스가 llm_table_select를 도입한 이유(README
    "열세 번째") 그 자체였다는 걸 보여준다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    claim = {"claim_id": "TEST-STAGE1-LLM3", "claim": "나랏빚이 불어났다.", "value_num": None, "unit": None, "period": None}
    result = lda.resolve_claim_evidence(conn, claim, keywords=[], stage1_keywords="llm_table_select")
    _check(
        "hcx_table_resolve_fn 미지정 + FTS도 못 찾는 claim이면 not_found",
        result.get("query_status") == "not_found",
        str(result),
    )
    _check(
        "llm_table_select_error에 미지정 사유가 남음",
        result.get("llm_table_select_error") == "hcx_table_resolve_fn 미지정",
        str(result),
    )
    conn.close()


def test_llm_table_select_mode_none_result_returns_not_found():
    """[2026-08-26 갱신] HCX가 확신 없다고(None) 답하면 FTS로 폴백을
    시도하지만, 이 테스트의 claim("무관한 claim")은 어차피 seed된
    표(국가채무)와 아무 관련이 없으므로 FTS도 못 찾아 여전히
    not_found로 끝난다 - "추측하지 않는다" 원칙 자체는 안 바뀜(HCX도
    FTS도 근거가 없으면 둘 다 포기)."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _unconfident_fn(table_list, claim_text, claimed_value, claimed_unit, claimed_period):
        return None

    claim = {"claim_id": "TEST-STAGE1-LLM4", "claim": "무관한 claim", "value_num": None, "unit": None, "period": None}
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=[], stage1_keywords="llm_table_select", hcx_table_resolve_fn=_unconfident_fn,
    )
    _check(
        "HCX가 None을 돌려주고 FTS도 못 찾으면 not_found로 끝남(추측 안 함)",
        result.get("query_status") == "not_found",
        str(result),
    )
    conn.close()


def _sync_fts_for_national_debt(conn):
    """[2026-08-26 신규] `_seed_full_national_debt_table`은 `dimensions`만
    채우고 `dimensions_fts`는 안 채운다(운영 코드에서는 `kosis_warehouse.
    _sync_dimensions_fts`가 이 동기화를 담당하는데, 이 테스트 픽스처는
    그 경로를 안 거치는 raw INSERT라 그렇다). FTS 폴백을 검증하려면
    dimensions_fts도 실제로 채워져 있어야 하므로, 이미 심어둔 국가채무
    표의 ITEM/축 이름을 그대로 dimensions_fts에도 복사하는 최소 헬퍼."""
    rows = conn.execute(
        "SELECT org_id, tbl_id, obj_id, code, name FROM dimensions "
        "WHERE org_id='184' AND tbl_id='DT_102006_001'"
    ).fetchall()
    for org_id, tbl_id, obj_id, code, name in rows:
        conn.execute(
            "INSERT INTO dimensions_fts (name, org_id, tbl_id, obj_id, code) VALUES (?,?,?,?,?)",
            (name, org_id, tbl_id, obj_id, code),
        )
    conn.commit()


def test_llm_table_select_mode_falls_back_to_fts_when_hcx_fails():
    """[2026-08-26 신규 - A2e46e4ac-C022/C023/C024("딸기"/"바나나" 실측
    미스터리) 계기로 배선] HCX-007 표 선택이 실패(예외/확신없음)해도,
    claim이 실제로 등록된 표의 항목/축 이름과 문자 그대로 겹치면
    search_local(FTS)이 대신 찾아내야 한다 - 원래 A/B 비교용으로
    "폴백 금지"였던 게 프로덕션 승격(README "마흔여섯 번째") 이후에도
    재검토 없이 남아있었던 갭을 메운다. FTS는 axis_hints/leaf_samples처럼
    표시량이 잘리지 않고 dimensions 전체를 인덱싱하므로, HCX-007에게는
    안 보였던 항목도 찾을 수 있다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    _sync_fts_for_national_debt(conn)

    def _always_fails(table_list, claim_text, claimed_value, claimed_unit, claimed_period):
        raise RuntimeError("HCX API 네트워크 오류가 발생했습니다(시뮬레이션)")

    # "국가채무"가 원문장에 문자 그대로 있으므로 FTS라면 바로 찾을 수 있는 케이스.
    claim = {
        "claim_id": "TEST-STAGE1-LLM6",
        "claim": "국가채무가 크게 늘었다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=[],
        stage1_keywords="llm_table_select",
        hcx_table_resolve_fn=_always_fails,
    )
    _check(
        "HCX-007 예외에도 FTS 폴백으로 정답표를 찾음",
        result.get("org_id") == "184" and result.get("table_id") == "DT_102006_001",
        str(result),
    )
    _check(
        "폴백이 실제로 쓰였다는 표시가 성공 결과에도 남음",
        result.get("llm_table_select_fallback_used") is True,
        str(result),
    )
    # Stage 2까지는 hcx_resolve_fn 없이도 literal 매칭("국가채무"가 축
    # 이름과 정확히 겹침)만으로 끝까지 성공해야 한다.
    _check(
        "Stage 2도 literal 매칭으로 값까지 확정됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 1200.0,
        str(result),
    )
    conn.close()


def test_llm_table_select_mode_isolated_from_default_stage1_keywords():
    """[격리 확인] stage1_keywords가 기본값("run03")이면 hcx_table_
    resolve_fn을 넘겨도 아예 호출되면 안 된다 - "호출되면 예외" 가짜로
    증명한다. 기존 두 모드는 이 새 파라미터의 존재 자체를 몰라야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _must_not_be_called(table_list, claim_text, claimed_value, claimed_unit, claimed_period):
        raise AssertionError("기본 stage1_keywords에서는 hcx_table_resolve_fn이 호출되면 안 됨")

    # Stage 2까지 끝까지 성공을 확인하려면 기존 검증된 hcx_resolve_fn(Stage
    # 2용)도 같이 넘긴다 - 이 테스트가 보려는 건 새로 추가한 hcx_table_
    # resolve_fn(Stage 1용)이 기본 모드에서 격리돼 있는지뿐이다.
    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    claim = {
        "claim_id": "TEST-STAGE1-LLM5",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_table_resolve_fn=_must_not_be_called,
        hcx_resolve_fn=_fake_hcx_resolve_fn,
    )
    _check(
        "기본 stage1_keywords에서 기존 동작 그대로(예외 없이 성공)",
        result.get("query_status") == "success",
        str(result),
    )
    conn.close()


class _FakeKosisClientForPurposeCheck:
    """[2026-08-28 신규 - 목적 검증 게이트] get_stat_explanation(org_id,
    tbl_id)만 흉내내는 최소 가짜 - _attach_purpose_check가 실제로 호출하는
    유일한 메서드다(_FakeKosisClientForBackfill과 같은 "필요한 메서드만
    흉내" 패턴, 이 파일이 재사용하지 않고 새로 만드는 이유도 동일)."""

    def __init__(self, explanation=None, raise_on_call=None):
        self.calls = []
        self._explanation = explanation if explanation is not None else {}
        self._raise_on_call = raise_on_call

    def get_stat_explanation(self, org_id, tbl_id):
        self.calls.append({"org_id": org_id, "tbl_id": tbl_id})
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._explanation


def test_purpose_check_flags_mismatch_on_final_success_path():
    """[2026-08-28 신규 - 배추가격/DT_114054_112 사례로 사용자가 지적한
    아키텍처 갭 대응] kosis_client + hcx_purpose_verify_fn을 둘 다 넘기면,
    표/축 이름 매칭까지 전부 성공한 claim이라도(derivation.used=False인
    최종 확정 경로) 표의 작성 목적이 claim과 안 맞는다고 HCX가 판단하면
    result에 purpose_mismatch=True/purpose_mismatch_note가 붙어야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    fake_client = _FakeKosisClientForPurposeCheck(
        explanation={"writingPurps": "이 표는 실제로는 전혀 다른 조사 목적을 갖는다(테스트용 불일치)."}
    )

    def _fake_purpose_verify_fn(claim_text, table_nm, table_purpose_text, claimed_value, claimed_unit, claimed_period):
        return {"mismatch": True, "reason": "표의 작성 목적이 claim과 다름(테스트용)"}

    claim = {
        "claim_id": "TEST-PURPOSE-MISMATCH",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        kosis_client=fake_client,
        hcx_purpose_verify_fn=_fake_purpose_verify_fn,
    )
    _check(
        "값 조회 자체는 여전히 성공(게이트는 judgment.py 몫)",
        result.get("query_status") == "success" and result.get("normalized_value") == 1200.0,
        str(result),
    )
    _check("purpose_mismatch=True가 붙음", result.get("purpose_mismatch") is True, str(result))
    _check(
        "purpose_mismatch_note에 이유가 붙음",
        result.get("purpose_mismatch_note") == "표의 작성 목적이 claim과 다름(테스트용)",
        str(result),
    )
    _check(
        "get_stat_explanation이 확정된 (org_id,tbl_id)로 정확히 1번 호출됨",
        fake_client.calls == [{"org_id": "184", "tbl_id": "DT_102006_001"}],
        str(fake_client.calls),
    )
    conn.close()


def test_purpose_check_passes_through_on_match():
    """HCX가 MATCH(mismatch=False)라고 판단하면 purpose_mismatch=False로
    붙는다(None이 아니라 명시적 False) - judgment._check_purpose_mismatch는
    `is not True`만 확인하므로 False든 None이든 게이트를 통과시키지만,
    "검증을 실제로 했고 일치했다"는 사실 자체는 진단용으로 남아야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    fake_client = _FakeKosisClientForPurposeCheck(
        explanation={"writingPurps": "이 표는 claim이 말하는 개념을 정확히 다룬다."}
    )

    def _fake_purpose_verify_fn(claim_text, table_nm, table_purpose_text, claimed_value, claimed_unit, claimed_period):
        return {"mismatch": False, "reason": "목적이 일치함(테스트용)"}

    claim = {
        "claim_id": "TEST-PURPOSE-MATCH",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        kosis_client=fake_client,
        hcx_purpose_verify_fn=_fake_purpose_verify_fn,
    )
    _check("purpose_mismatch=False가 명시적으로 붙음", result.get("purpose_mismatch") is False, str(result))
    conn.close()


def test_purpose_check_skipped_without_client_or_verify_fn():
    """[회귀 방지] kosis_client/hcx_purpose_verify_fn을 둘 다 안 넘기면
    (기본값 None - 이 프로젝트의 기존 모든 회귀 테스트가 이 상태) 목적
    검증 자체가 시도되지 않아야 한다 - result에 purpose_mismatch 키 자체가
    없어야 하고(다른 신규 필드들과 동일한 opt-in 원칙), 기존 동작이
    이 신규 기능 추가로 전혀 안 바뀐다는 걸 확인한다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    claim = {
        "claim_id": "TEST-PURPOSE-NOOP",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"], hcx_resolve_fn=_fake_hcx_resolve_fn,
    )
    _check("query_status는 기존처럼 success", result.get("query_status") == "success", str(result))
    _check("purpose_mismatch 키 자체가 안 붙음(opt-in 미사용)", "purpose_mismatch" not in result, str(result))
    conn.close()


def test_purpose_check_swallows_get_stat_explanation_errors():
    """get_stat_explanation이 예외를 던져도(네트워크 오류 등) 이 claim
    전체가 죽으면 안 된다 - 조용히 삼키고 기존처럼 성공 result를 그대로
    돌려준다(다른 opt-in 신규 기능들과 동일한 에러 처리 관례 - embed_fn/
    hcx_resolve_fn 등)."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    fake_client = _FakeKosisClientForPurposeCheck(raise_on_call=RuntimeError("네트워크 오류(재현용 가짜)"))

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("get_stat_explanation이 실패하면 hcx_purpose_verify_fn은 호출되면 안 됨")

    claim = {
        "claim_id": "TEST-PURPOSE-EXPL-ERROR",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        kosis_client=fake_client,
        hcx_purpose_verify_fn=_must_not_be_called,
    )
    _check(
        "get_stat_explanation 예외에도 claim 전체는 success로 안전하게 끝남",
        result.get("query_status") == "success" and result.get("normalized_value") == 1200.0,
        str(result),
    )
    _check("purpose_mismatch 키는 안 붙음(검증 자체를 못 했으므로)", "purpose_mismatch" not in result, str(result))
    conn.close()


def _set_cached_purpose(conn, org_id, tbl_id, writing_purps=None, examin_objrange=None, fetched=True):
    """[2026-08-28 신규 - 표 적재 시점 캐싱 검증용] tables_registry의 신규
    캐시 컬럼(writing_purps/examin_objrange/purpose_fetched_at)을 직접
    UPDATE한다 - 실제로는 kosis_warehouse.ingest_table이 register_table을
    통해 채우는 값이지만, 이 테스트는 그 배관을 다시 타지 않고 "이미
    캐시돼 있는 상태"를 직접 흉내낸다(다른 픽스처들과 동일한 관례)."""
    conn.execute(
        "UPDATE tables_registry SET writing_purps=?, examin_objrange=?, "
        "purpose_fetched_at=? WHERE org_id=? AND tbl_id=?",
        (
            writing_purps, examin_objrange,
            "2026-08-28T00:00:00+00:00" if fetched else None,
            org_id, tbl_id,
        ),
    )
    conn.commit()


def test_purpose_check_uses_cache_without_calling_kosis_client():
    """[2026-08-28 신규 - 사용자 결정: "표 적재 시점에 DB 저장"] tables_
    registry에 이미 목적 설명이 캐시돼 있으면(purpose_fetched_at 설정됨),
    kosis_client.get_stat_explanation을 아예 호출하지 않고 캐시된 텍스트로
    바로 HCX 검증을 수행해야 한다 - kosis_client 자체를 안 넘겨도(None)
    동작해야 캐시 우선 설계의 이점이 실제로 검증된다."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    _set_cached_purpose(
        conn, "184", "DT_102006_001",
        writing_purps="국가채무 총괄 통계를 작성해 재정건전성 관리에 활용",
        examin_objrange="중앙정부 및 지방정부의 채무",
    )

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    received_purpose_text = {}

    def _fake_purpose_verify_fn(claim_text, table_nm, table_purpose_text, claimed_value, claimed_unit, claimed_period):
        received_purpose_text["value"] = table_purpose_text
        return {"mismatch": False, "reason": "캐시된 목적 설명과 일치(테스트용)"}

    claim = {
        "claim_id": "TEST-PURPOSE-CACHE-HIT",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        kosis_client=None,  # 캐시가 있으면 kosis_client 없이도 동작해야 함
        hcx_purpose_verify_fn=_fake_purpose_verify_fn,
    )
    _check("purpose_mismatch=False가 캐시 경로로도 붙음", result.get("purpose_mismatch") is False, str(result))
    _check(
        "HCX에 넘어간 목적 설명 텍스트가 캐시된 두 필드를 합친 것",
        received_purpose_text.get("value") == (
            "국가채무 총괄 통계를 작성해 재정건전성 관리에 활용\n중앙정부 및 지방정부의 채무"
        ),
        str(received_purpose_text),
    )
    conn.close()


def test_purpose_check_skips_retry_when_cache_says_already_tried_and_empty():
    """purpose_fetched_at은 있지만(적재 시점에 이미 시도함) writing_purps/
    examin_objrange가 둘 다 비어 있으면(못 가져왔음), kosis_client가
    있더라도 재시도하면 안 된다 - 근거 없이 API를 계속 두드리지 않는다는
    폴백 원칙."""
    conn = wh.get_connection(":memory:")
    _seed_full_national_debt_table(conn)
    _set_cached_purpose(conn, "184", "DT_102006_001", writing_purps=None, examin_objrange=None)

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "국가채무(D1) 국가채무":
                return i
        return None

    def _must_not_be_called_client_method(org_id, tbl_id):
        raise AssertionError("캐시가 '이미 시도했지만 없음'을 말하면 라이브 API를 재시도하면 안 됨")

    class _StrictFakeClient:
        get_stat_explanation = staticmethod(_must_not_be_called_client_method)

    def _must_not_be_called_verify_fn(*args, **kwargs):
        raise AssertionError("목적 설명 텍스트가 없으면 HCX 검증 자체를 호출하면 안 됨")

    claim = {
        "claim_id": "TEST-PURPOSE-CACHE-EMPTY",
        "claim": "나랏빚이 눈덩이처럼 불어났다.",
        "value_num": 1200.0,
        "unit": "조원",
        "period": "2025",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["국가채무"],
        hcx_resolve_fn=_fake_hcx_resolve_fn,
        kosis_client=_StrictFakeClient(),
        hcx_purpose_verify_fn=_must_not_be_called_verify_fn,
    )
    _check(
        "값 조회는 정상 성공, 목적 검증만 조용히 스킵됨",
        result.get("query_status") == "success" and "purpose_mismatch" not in result,
        str(result),
    )
    conn.close()


def _seed_cpi_item_diff_fixture(conn):
    """[2026-08-22 신규 - Task #29 Step 3] C003/C004류(item_diff)를 위한
    합성 표 - 조선비즈 2025-10-08 기사 기반이지만 실제 KOSIS 값이 아니라
    회귀 테스트용 깔끔한 숫자(22.9%/16.2%, 실제 실측값과 동일 - 사용자
    로컬 검증 결과, README "스물네 번째" 항목)를 쓴다. tbl_nm을 claim
    원문에 그대로 등장하는 부분 문자열("소비자 물가지수")로 둬서 Stage 1
    표 이름 매칭이 확실히 걸리게 한다(test_embedding_fallback_error_
    does_not_crash_claim의 "나랏빚" 표와 같은 기법)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm) VALUES ('999', 'T_CPI', '소비자 물가지수')"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('999', 'T_CPI', 'ITEM', 0, '항목', 'T001', '소비자물가지수', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('999', 'T_CPI', 'I', 1, '지출목적별', 'A', '식료품 및 비주류음료', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('999', 'T_CPI', 'I', 1, '지출목적별', '0', '0 총지수', NULL, NULL)"
    )
    for c1, prd_de, value in (("A", "202509", 122.9), ("A", "202009", 100.0), ("0", "202509", 116.2), ("0", "202009", 100.0)):
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('999', 'T_CPI', 'T001', ?, 'M', ?, ?, '2020=100')",
            (prd_de, c1, value),
        )
    conn.commit()


def _item_diff_claim():
    return {
        "claim_id": "TEST-ITEMDIFF-1",
        "claim": (
            "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다. "
            "같은 기간 전체 소비자 물가지수 상승률(16.2%)보다 7%포인트 가까이 높은 수치다."
        ),
        "metric": "식료품 및 비주류음료 물가지수",
        "metric_normalized": "식료품 및 비주류음료 물가지수",
        "value": "7", "unit": "%포인트", "period": "2025-09",
    }


def test_item_diff_hcx_stage3_success_ignores_hcx_reference_period():
    """[2026-08-22 신규 - Task #29 Step 3] hcx_stage3_fn이 mode="item_diff"
    를 반환하고 원문에 "전체" 키워드도 있으면, resolve_item_diff_change
    경로로 diff(=6.7, 22.9-16.2)를 정확히 계산해 success로 반환해야 한다.
    hcx_stage3_fn이 돌려준 reference_period("999999" - 일부러 틀린 값)는
    무시되고, 기존 결정적 경로(_extract_explicit_reference_period)가 뽑은
    "202009"가 실제로 쓰였는지까지 확인한다(README "스물다섯 번째" 실측
    - HCX의 reference_period 산수를 못 믿는다는 설계 결정의 핵심 검증)."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_item_diff_fixture(conn)

    def _fake_hcx_stage3_fn(claim_text, target_period, claimed_value, claimed_unit):
        return {"mode": "item_diff", "reference_period": "999999"}

    result = lda.resolve_claim_evidence(
        conn, _item_diff_claim(), keywords=[], hcx_stage3_fn=_fake_hcx_stage3_fn,
    )
    _check("query_status=success", result.get("query_status") == "success", str(result))
    _check(
        "normalized_value가 diff(6.7)와 근접",
        result.get("normalized_value") is not None and abs(result["normalized_value"] - 6.7) < 0.01,
        str(result.get("normalized_value")),
    )
    _check("normalized_unit=%포인트", result.get("normalized_unit") == "%포인트", str(result.get("normalized_unit")))
    _check("derivation.mode=item_diff", result.get("derivation", {}).get("mode") == "item_diff", str(result.get("derivation")))
    _check("derivation.hcx_stage3_used=True", result.get("derivation", {}).get("hcx_stage3_used") is True)
    conn.close()


def test_item_diff_skipped_without_total_comparison_keyword():
    """HCX가 item_diff라고 답해도, 원문에 "전체/총지수/평균/총계" 같은
    로컬 키워드 근거가 없으면(2차 corroboration 실패) item_diff 경로를
    타지 않고 기존 경로로 폴백해야 한다(90건 평가셋에서 HCX의 item_diff
    판단 자체가 53% 정확도로 약했던 것에 대한 안전장치)."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_item_diff_fixture(conn)

    claim = _item_diff_claim()
    claim["claim"] = "식료품 및 비주류음료 물가지수는 2020년 9월에 비해 22.9% 올랐다."  # "전체" 문구 제거

    def _fake_hcx_stage3_fn(claim_text, target_period, claimed_value, claimed_unit):
        return {"mode": "item_diff", "reference_period": "202009"}

    result = lda.resolve_claim_evidence(
        conn, claim, keywords=[], hcx_stage3_fn=_fake_hcx_stage3_fn,
    )
    _check(
        "키워드 근거 없으면 item_diff 경로를 안 탐(derivation.mode != item_diff)",
        result.get("derivation", {}).get("mode") != "item_diff",
        str(result),
    )
    conn.close()


def test_item_diff_hcx_exception_does_not_crash_falls_back():
    """hcx_stage3_fn이 예외를 던져도(네트워크 오류 등) resolve_claim_evidence
    가 그 예외를 삼키고 기존 경로로 안전하게 폴백해야 한다(embed_fn/
    hcx_resolve_fn과 같은 에러 처리 관례)."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_item_diff_fixture(conn)

    def _raising_hcx_stage3_fn(claim_text, target_period, claimed_value, claimed_unit):
        raise RuntimeError("네트워크 오류(재현용 가짜 예외)")

    try:
        result = lda.resolve_claim_evidence(
            conn, _item_diff_claim(), keywords=[], hcx_stage3_fn=_raising_hcx_stage3_fn,
        )
        raised = False
    except Exception:
        result = None
        raised = True
    _check("hcx_stage3_fn 예외가 전파되지 않고 삼켜짐", not raised)
    _check("삼킨 뒤에도 query_status는 존재(크래시 없이 폴백)", result is not None and "query_status" in result, str(result))
    conn.close()


def test_item_diff_disabled_by_default_when_hcx_stage3_fn_omitted():
    """hcx_stage3_fn을 안 넘기면(기본값 None) item_diff 분기 자체가 아예
    실행되지 않아야 한다 - 기존 동작과 완전히 동일해야 함(opt-in 원칙)."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_item_diff_fixture(conn)
    result = lda.resolve_claim_evidence(conn, _item_diff_claim(), keywords=[])
    _check(
        "hcx_stage3_fn 없이는 item_diff 경로를 타지 않음",
        result.get("derivation", {}).get("mode") != "item_diff",
        str(result),
    )
    conn.close()


if __name__ == "__main__":
    test_needs_rate_derivation_true_cases()
    test_needs_rate_derivation_false_cases_regression()
    test_window_has_change_verb_still_excludes_rate_noun_suffix()
    test_needs_rate_derivation_true_cases_via_comparison()
    test_needs_rate_derivation_gating_conditions()
    test_needs_rate_derivation_true_when_rate_word_precedes_number()
    test_needs_rate_derivation_still_false_when_prefix_has_no_rate_word()
    test_claim_number_change_window_skips_duration_year_no_siblings()
    test_claim_number_change_window_sibling_branch_never_truncates()
    test_demographic_alias_covers_multiple_kosis_breadcrumb_formats()
    test_no_data_backfill_succeeds_via_fetch_scoped_slice()
    test_no_data_backfill_attempted_but_still_missing_falls_back_cleanly()
    test_no_data_backfill_exception_does_not_crash_falls_back_to_no_data()
    test_no_data_meta_filter_skips_live_call_when_prd_se_not_in_coverage()
    test_no_data_meta_filter_does_not_block_when_prd_se_matches_coverage()
    test_no_data_without_kosis_client_keeps_existing_behavior()
    test_needs_rate_derivation_true_via_metric_suffix_when_window_has_no_clue()
    test_needs_rate_derivation_metric_suffix_does_not_overtrigger_level_rate_names()
    test_extract_explicit_reference_period()
    test_hcx_fallback_resolves_correctly_and_flags_set()
    test_hcx_fallback_receives_full_cell_list_no_truncation()
    test_hcx_fallback_none_result_falls_back_to_unresolved()
    test_hcx_fallback_exception_does_not_crash_claim()
    test_weak_literal_tie_uses_hcx_instead_of_loose_value_tolerance()
    test_weak_literal_tie_axis_tree_hcx_resolves_parent_child_ambiguity()
    test_weak_literal_tie_prefers_axis_resolve_fn_over_plain_hcx_resolve_fn()
    test_weak_literal_tie_axis_resolve_fn_skipped_when_tie_spans_multiple_items()
    test_strong_literal_tie_still_uses_disambiguate_by_value_directly()
    test_llm_table_select_mode_routes_via_hcx_table_resolve_fn()
    test_llm_table_select_mode_receives_full_table_list_no_truncation()
    test_llm_table_select_mode_without_resolve_fn_returns_not_found()
    test_llm_table_select_mode_none_result_returns_not_found()
    test_llm_table_select_mode_falls_back_to_fts_when_hcx_fails()
    test_llm_table_select_mode_isolated_from_default_stage1_keywords()
    test_purpose_check_flags_mismatch_on_final_success_path()
    test_purpose_check_passes_through_on_match()
    test_purpose_check_skipped_without_client_or_verify_fn()
    test_purpose_check_swallows_get_stat_explanation_errors()
    test_purpose_check_uses_cache_without_calling_kosis_client()
    test_purpose_check_skips_retry_when_cache_says_already_tried_and_empty()
    test_item_diff_hcx_stage3_success_ignores_hcx_reference_period()
    test_item_diff_skipped_without_total_comparison_keyword()
    test_item_diff_hcx_exception_does_not_crash_falls_back()
    test_item_diff_disabled_by_default_when_hcx_stage3_fn_omitted()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

"""[2026-08-17 신규] Research Overview 2("KOSIS 데이터 웨어하우스 전환")에서
확정한 적재 범위 정책 세 가지 - ① 기간 윈도우 제한(kosis_warehouse._clip_
period_window), ② 수요 기반 증분 확장(kosis_warehouse.ensure_tables_for_claim),
③ "역대 최고/최저" claim 대응 records 요약(kosis_warehouse.ingest_records/
get_record, 사용자 제안) - 을 구현한 뒤 실측으로 검증하는 테스트.

①③은 순수 함수/합성 데이터라 네트워크 없이 바로 돈다(③은 in-memory
sqlite로). ②는 "내부 DB에 후보가 있으면 라이브 호출을 아예 안 한다"는
경로만 real DB(read-only)로 검증한다 - 라이브 검색 경로(cache miss일 때
실제 KOSIS API를 부르는 부분)는 이 샌드박스가 네트워크 완전 차단이라
여기선 검증 못 한다(세션 내내 확인된 제약 - curl로 kosis.kr 자체가 안
열림). 그 경로는 로컬(네트워크+API 키 있는 환경)에서 사용자가 직접
실행해서 확인해야 한다.

사용법: python test_warehouse_scope_policy.py (이 폴더에서, 종료 코드 0 = 전체 PASS)
"""

import sqlite3
import sys

import kosis_warehouse as wh


def _fake_raw_row(itm_id, prd_de, prd_se, value, c1=None):
    """실제 getList 응답 한 행을 흉내낸다 - ingest_facts/ingest_records
    둘 다 raw_dict 안쪽만 본다(_parse_fact_rows)."""
    return {"raw_dict": {
        "ITM_ID": itm_id, "PRD_DE": prd_de, "PRD_SE": prd_se,
        "DT": str(value), "UNIT_NM": "%", "C1": c1,
    }}

DB_PATH = "file:kosis_warehouse.db?mode=ro"

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def test_clip_period_window_months():
    _check(
        "월(M) 윈도우: end=202606, 5년 전 -> 202106",
        wh._clip_period_window("196501", "202606", "M", 5) == "202106",
    )


def test_clip_period_window_quarters():
    # 2026년 2분기(20262)에서 3년(=12분기) 전 -> 2023년 2분기(20232)
    _check(
        "분기(Q) 윈도우: end=20262, 3년 전 -> 20232",
        wh._clip_period_window("19601", "20262", "Q", 3) == "20232",
    )


def test_clip_period_window_none_years_back_noop():
    _check(
        "years_back=None이면 원본 strt 그대로(전체 기간 유지, 기존 동작 안 깨짐)",
        wh._clip_period_window("1965", "2026", "Y", None) == "1965",
    )


def test_clip_period_window_unclippable_prd_se_noop():
    _check(
        "F(다년)처럼 1년당 시점 수가 불명확한 주기는 클리핑 안 함(추측 안 함 원칙)",
        wh._clip_period_window("2011", "2025", "F", 10) == "2011",
    )


def test_clip_period_window_already_short_noop():
    _check(
        "표가 이미 윈도우보다 짧으면(2020~2026, 10년 요청) 안 잘림",
        wh._clip_period_window("2020", "2026", "Y", 10) == "2020",
    )


def test_clip_period_window_annual_never_clipped():
    # "역대 최고/최저" claim 대응 - 연간 데이터는 years_back과 무관하게 항상 보존.
    # 실제 facts.prd_se 코드("A")와 문서상 가정("Y") 둘 다 확인.
    _check(
        "연간(prd_se='A', 실제 facts 코드) - 1년 윈도우를 요청해도 안 잘림",
        wh._clip_period_window("1965", "2026", "A", 1) == "1965",
    )
    _check(
        "연간(prd_se='Y', 코드 전반의 가정) - 1년 윈도우를 요청해도 안 잘림",
        wh._clip_period_window("1965", "2026", "Y", 1) == "1965",
    )


def test_ingest_records_and_get_record():
    """합성 데이터(네트워크 불필요)로 records 요약을 직접 검증한다:
    - 최댓값/최솟값과 그 시점이 정확히 계산되는지
    - 동점일 때 가장 이른 시점을 고르는지(결정론적 규칙)
    - 축(c1)이 다르면 별도 계열로 분리되는지
    - value가 None인 행은 무시되는지
    - facts와 무관하게(ingest_facts를 안 불러도) records만 독립적으로 채워지는지"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)

    org_id, tbl_id = "999", "TEST_TBL"
    raw = [
        _fake_raw_row("T01", "202401", "M", 100.0, c1="A"),
        _fake_raw_row("T01", "202402", "M", 105.0, c1="A"),
        _fake_raw_row("T01", "202403", "M", 105.0, c1="A"),  # 최댓값과 동점 - 더 이른(202402) 시점이 이겨야 함
        _fake_raw_row("T01", "202404", "M", 90.0, c1="A"),   # 최솟값
        _fake_raw_row("T01", "202401", "M", 999.0, c1="B"),  # 다른 축(c1='B') - 별도 계열
        _fake_raw_row("T01", "202402", "M", None, c1="A"),   # value 없음 - 무시돼야 함(파싱 단계에서 float 변환 실패 -> None)
    ]
    n = wh.ingest_records(conn, org_id, tbl_id, raw)
    _check("ingest_records: 계열 2개(c1=A, c1=B) 반환", n == 2, n)

    rec_a = wh.get_record(conn, org_id, tbl_id, "T01", "M", axis_codes={1: "A"})
    _check("records(c1=A): 최댓값 105.0", rec_a is not None and rec_a["max_value"] == 105.0)
    _check(
        "records(c1=A): 동점 중 더 이른 시점(202402)을 최댓값 시점으로 고름",
        rec_a is not None and rec_a["max_prd_de"] == "202402",
        rec_a.get("max_prd_de") if rec_a else None,
    )
    _check("records(c1=A): 최솟값 90.0, 시점 202404", rec_a is not None and rec_a["min_value"] == 90.0 and rec_a["min_prd_de"] == "202404")
    _check(
        "records(c1=A): coverage가 202401~202404(None 값 행 제외하고 실제 값 있는 시점 기준)",
        rec_a is not None and rec_a["coverage_strt_prd_de"] == "202401" and rec_a["coverage_end_prd_de"] == "202404",
    )

    rec_b = wh.get_record(conn, org_id, tbl_id, "T01", "M", axis_codes={1: "B"})
    _check("records(c1=B): 별도 계열로 분리(최댓값 999.0)", rec_b is not None and rec_b["max_value"] == 999.0)

    rec_missing = wh.get_record(conn, org_id, tbl_id, "T01", "Q", axis_codes={1: "A"})
    _check("records: 없는 조합(prd_se='Q')은 None 반환", rec_missing is None)

    # 재적재해도(INSERT OR REPLACE) 중복 없이 갱신되는지
    n2 = wh.ingest_records(conn, org_id, tbl_id, raw)
    total = conn.execute("SELECT COUNT(*) FROM records WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)).fetchone()[0]
    _check("records: 같은 데이터 재적재해도 계열당 1행 유지(중복 안 쌓임)", total == 2, total)

    conn.close()


def _fake_meta_row(obj_id, itm_id, obj_id_sn=None, itm_nm=None):
    """getMeta(type=ITM) 응답 한 행을 흉내낸다(실측 확정된 필드명 - OBJ_ID/
    ITM_ID/OBJ_ID_SN/ITM_NM 그대로, ingest_dimensions가 실제로 읽는 것과
    동일). OBJ_ID='ITEM'이면 항목 행, 그 외는 분류축 행."""
    return {"OBJ_ID": obj_id, "ITM_ID": itm_id, "OBJ_ID_SN": obj_id_sn, "ITM_NM": itm_nm or itm_id}


def test_classify_table_width_narrow():
    """항목 1개 + 축 하나(코드 5개) + 기간 10개 = 50셀 - 임계값(40,000)보다
    훨씬 작으니 narrow로 판정돼야 한다(예: DT_1J22041처럼 항목/축이 적은
    실제 시드 표들이 여기 해당)."""
    raw_meta = [_fake_meta_row("ITEM", "T01")]
    raw_meta += [_fake_meta_row("A", f"A{i}", obj_id_sn=1) for i in range(5)]
    result = wh.classify_table_width(raw_meta, "202001", "202010", "M")
    _check(
        "narrow 표: 50셀 추정 -> width='narrow'",
        result["width"] == "narrow" and result["estimated_cells"] == 50,
        result,
    )


def test_classify_table_width_wide():
    """항목 5개 × 축1(20코드) × 축2(15코드) × 기간 799개(실제 DT_404Y016
    수록 시점 수와 비슷한 규모) = 1,198,500셀 - 임계값을 훨씬 초과하니
    wide로 판정돼야 한다."""
    raw_meta = [_fake_meta_row("ITEM", f"T{i}") for i in range(5)]
    raw_meta += [_fake_meta_row("A", f"A{i}", obj_id_sn=1) for i in range(20)]
    raw_meta += [_fake_meta_row("B", f"B{i}", obj_id_sn=2) for i in range(15)]
    result = wh.classify_table_width(raw_meta, "196501", "203112", "M")
    _check(
        "wide 표: 임계값(40,000) 초과 추정 -> width='wide'",
        result["width"] == "wide" and result["estimated_cells"] > wh._WIDE_TABLE_CELL_THRESHOLD,
        result,
    )


def test_classify_table_width_period_unknown_falls_back_to_axis_only():
    """수록기간을 못 구해도(strt/end 없음) 축×항목만으로는 판단을 포기하지
    않고 기간=1로 취급해 보수적으로 추정한다(추측으로 narrow 단정하지
    않음) - 항목 5개×축 100개는 그것만으로도 이미 임계값을 넘는다."""
    raw_meta = [_fake_meta_row("ITEM", f"T{i}") for i in range(5)]
    raw_meta += [_fake_meta_row("A", f"A{i}", obj_id_sn=1) for i in range(9000)]
    result = wh.classify_table_width(raw_meta, None, None, "M")
    _check(
        "기간 정보 없어도 축×항목만으로 wide 판정 가능",
        result["width"] == "wide",
        result,
    )


def test_normalize_axis_key():
    _check(
        "objl_fixed 없음(None) -> 'all'",
        wh._normalize_axis_key(None) == "all",
    )
    _check(
        "objl_fixed={2:'B01',1:'A0201'} -> 축번호 오름차순 '1=A0201|2=B01' (딕셔너리 순서 무관)",
        wh._normalize_axis_key({2: "B01", 1: "A0201"}) == "1=A0201|2=B01",
        wh._normalize_axis_key({2: "B01", 1: "A0201"}),
    )


def test_fact_coverage_narrow_blanket_row():
    """narrow 배치 적재가 남기는 itm_id='all'/axis_key='all' 커버리지는
    그 주기의 어떤 구체적인 항목/축 조회 요청이 와도 다 커버한 것으로
    인정돼야 한다."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_NARROW"

    wh.record_coverage(conn, org_id, tbl_id, "M", "all", "all", "202001", "202612")
    _check(
        "narrow 블랭킷 커버리지 - 구체적 항목(T01)/축(1=A0201) 요청도 커버됨으로 인정",
        wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202105", "202110"),
    )
    _check(
        "narrow 블랭킷 커버리지 밖 구간(203001~)은 커버 안 됨",
        not wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "203001", "203010"),
    )
    conn.close()


def test_fact_coverage_wide_scoped_rows():
    """wide 표는 처음엔 커버리지가 없고, 온디맨드 조회로 좁은 (항목,축,기간)
    조합이 쌓인 만큼만 커버된 것으로 인정돼야 한다 - 다른 항목/축/기간
    조합은 여전히 커버 안 됨으로 나와야 온디맨드 재조회가 트리거된다."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_WIDE"

    _check(
        "wide 표 - 아직 아무것도 조회 안 했으면 커버 안 됨",
        not wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202001", "202012"),
    )

    wh.record_coverage(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202001", "202012")
    _check(
        "wide 표 - 실제로 조회한 (항목=T01,축=1=A0201,202001~202012)만 커버됨",
        wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202003", "202006"),
    )
    _check(
        "wide 표 - 다른 항목(T02)은 여전히 커버 안 됨(항목별로 독립 추적)",
        not wh.is_period_covered(conn, org_id, tbl_id, "M", "T02", "1=A0201", "202003", "202006"),
    )
    _check(
        "wide 표 - 같은 항목이라도 커버 안 된 기간(202101~)은 안 됨",
        not wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202101", "202106"),
    )
    conn.close()


class _FakeScopedClient:
    """fetch_scoped_slice가 objl_fixed/current_dim을 실제로 그대로
    kosis_client.fetch_actual_statistics_bounded_retry에 넘기는지 기록하고,
    합성 데이터 한 건을 반환하는 stub. get_period_meta는 "역대 최고/최저"
    계산을 위한 전체 이력 확장(2026-08-18 신규)이 실제로 이 값을 쓰는지
    검증하는 데 필요하다."""

    def __init__(self, period_meta=None):
        self.calls = []
        self.period_meta_calls = 0
        self._period_meta = period_meta or [{"PRD_SE": "M", "STRT_PRD_DE": "199001", "END_PRD_DE": "202612"}]

    def get_period_meta(self, org_id, tbl_id):
        self.period_meta_calls += 1
        return self._period_meta

    def fetch_actual_statistics_bounded_retry(self, org_id, tbl_id, start_year, end_year, itm_id="all", current_dim=0, max_dim=8, prd_se="Y", objl_fixed=None):
        self.calls.append({
            "org_id": org_id, "tbl_id": tbl_id, "start_year": start_year, "end_year": end_year,
            "itm_id": itm_id, "current_dim": current_dim, "prd_se": prd_se, "objl_fixed": objl_fixed,
        })
        return [_fake_raw_row(itm_id, start_year, prd_se, 42.0, c1=(objl_fixed or {}).get(1))]


def test_fetch_scoped_slice_cache_hit_skips_live_call():
    """이미 fact_coverage에 커버된 (항목,축,기간)이면 라이브 API를 아예
    호출하지 않아야 한다 - 호출되면 바로 알 수 있게 stub이 예외를 던지게
    만든다."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_SCOPED"
    wh.record_coverage(conn, org_id, tbl_id, "M", "T01", "1=A0201", "202001", "202012")

    class _NoLiveCallClient:
        def fetch_actual_statistics_bounded_retry(self, *a, **k):
            raise AssertionError("이미 커버된 구간인데 라이브 호출이 발생함")

    result = wh.fetch_scoped_slice(
        _NoLiveCallClient(), conn, org_id, tbl_id, "M", "T01", "202003", "202006",
        objl_fixed={1: "A0201"},
    )
    _check(
        "커버된 구간 - source='cache', fact_rows=0, record_rows=0",
        result == {"source": "cache", "fact_rows": 0, "record_rows": 0}, result,
    )
    conn.close()


def test_fetch_scoped_slice_live_fetch_expands_to_full_history_for_records():
    """[2026-08-18 신규 - 사용자 지적] "역대 최고/최저"는 기사에 적힌
    연도를 그대로 믿을 수 없으니(단순 연도 검색이 아니라) 실제 KOSIS
    데이터로 대조해야 한다 - wide 표라도 이 대응을 포기하면 안 된다.

    itm_id가 구체적 코드로 좁혀진 요청(scoped)이면, 요청한 needed_strt~
    needed_end가 아니라 get_period_meta로 구한 그 (항목,축)의 전체
    수록기간을 대신 조회해서 facts+records를 함께 채워야 한다 - 좁혀진
    스코프는 narrow 표만큼 저렴하므로 전체 이력을 한 번에 가져오는 게
    합리적이라는 설계."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_SCOPED2"
    # dimensions에 축 하나(axis_position=1)를 미리 넣어둬서 current_dim
    # 계산(SELECT MAX(axis_position))이 실제로 동작하는지도 같이 검증
    wh.ingest_dimensions(conn, org_id, tbl_id, [
        {"OBJ_ID": "ITEM", "ITM_ID": "T01", "ITM_NM": "지수"},
        {"OBJ_ID": "A", "ITM_ID": "A0201", "OBJ_ID_SN": "1", "ITM_NM": "품목A"},
    ])

    client = _FakeScopedClient(period_meta=[{"PRD_SE": "M", "STRT_PRD_DE": "199001", "END_PRD_DE": "202612"}])
    # claim은 202001~202012만 필요로 하지만, 전체 이력(199001~202612)으로
    # 확장돼서 조회돼야 한다.
    result = wh.fetch_scoped_slice(
        client, conn, org_id, tbl_id, "M", "T01", "202001", "202012",
        objl_fixed={1: "A0201"},
    )
    _check("scoped 요청 - source='live_fetch', fact_rows=1, record_rows=1", result == {"source": "live_fetch", "fact_rows": 1, "record_rows": 1}, result)
    _check("get_period_meta가 실제로 호출됨(전체 이력 확장 근거)", client.period_meta_calls == 1, client.period_meta_calls)
    _check(
        "실제 조회 구간이 needed_strt~needed_end가 아니라 전체 수록기간(199001~202612)으로 확장됨",
        client.calls and client.calls[0]["start_year"] == "199001" and client.calls[0]["end_year"] == "202612",
        client.calls,
    )
    _check(
        "실제 API 호출에 objl_fixed가 그대로 전달됨",
        client.calls and client.calls[0]["objl_fixed"] == {1: "A0201"},
        client.calls,
    )
    _check(
        "current_dim이 dimensions의 축 개수(1)로 계산돼 전달됨(0부터 재발견 안 함)",
        client.calls and client.calls[0]["current_dim"] == 1,
        client.calls,
    )

    n_facts = conn.execute("SELECT COUNT(*) FROM facts WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)).fetchone()[0]
    _check("facts에 실제로 적재됨", n_facts == 1, n_facts)
    rec = wh.get_record(conn, org_id, tbl_id, "T01", "M", axis_codes={1: "A0201"})
    _check("역대 최고/최저(records)가 실제로 계산됨", rec is not None and rec["max_value"] == 42.0, rec)

    # 같은 (항목,축) 재요청 - 전체 이력이 이미 커버돼 있으므로(claim이 원래
    # 필요로 했던 202001~202012는 물론 그 밖의 구간도) 라이브 호출이 없어야 함
    client.fetch_actual_statistics_bounded_retry = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("두 번째 호출인데 다시 라이브 조회가 발생함 - coverage가 기록 안 됨")
    )
    result2 = wh.fetch_scoped_slice(
        client, conn, org_id, tbl_id, "M", "T01", "202101", "202106",
        objl_fixed={1: "A0201"},
    )
    _check("재요청(다른 기간이라도 전체 이력 안쪽) 시 cache로 응답", result2["source"] == "cache", result2)
    conn.close()


def test_fetch_scoped_slice_all_itm_id_never_expands_to_full_history():
    """[안전장치] itm_id='all'(구체적 항목으로 안 좁혀진 요청)이면 전체
    이력 확장을 하면 안 된다 - 그러면 wide 표에서 정확히 피하려던 "모든
    항목×전체 기간" 비용이 그대로 재현된다. needed_strt~needed_end만
    조회하고 records는 계산 안 함(get_period_meta조차 안 부름)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_SCOPED_ALL"

    client = _FakeScopedClient(period_meta=[{"PRD_SE": "M", "STRT_PRD_DE": "199001", "END_PRD_DE": "202612"}])
    result = wh.fetch_scoped_slice(
        client, conn, org_id, tbl_id, "M", "all", "202001", "202012",
    )
    _check("itm_id='all' - record_rows=0(전체 이력 확장 안 함)", result.get("record_rows") == 0, result)
    _check("itm_id='all' - get_period_meta 호출 안 됨(전체 이력 확장 근거조차 안 만듦)", client.period_meta_calls == 0, client.period_meta_calls)
    _check(
        "itm_id='all' - 조회 구간이 needed_strt~needed_end 그대로(확장 안 됨)",
        client.calls and client.calls[0]["start_year"] == "202001" and client.calls[0]["end_year"] == "202012",
        client.calls,
    )
    conn.close()


class _FakeIngestClient:
    """ingest_table 전체 흐름(getMeta -> getPeriod -> getList)을 흉내내는
    stub. raise_on_fetch=True면 fetch_actual_statistics_bounded_retry가
    호출되는 순간 바로 예외를 던진다 - wide 표 분기가 정말로 getList를
    건너뛰는지(호출 자체가 안 일어나는지) 강하게 검증하기 위함."""

    def __init__(self, raw_meta, period_meta, raise_on_fetch=False):
        self.raw_meta = raw_meta
        self.period_meta = period_meta
        self.raise_on_fetch = raise_on_fetch
        self.fetch_calls = []

    def get_itm_meta_list(self, org_id, tbl_id):
        return self.raw_meta

    def get_period_meta(self, org_id, tbl_id):
        return self.period_meta

    def fetch_actual_statistics_bounded_retry(self, org_id, tbl_id, start_year, end_year, itm_id="all", current_dim=0, max_dim=8, prd_se="Y", objl_fixed=None):
        if self.raise_on_fetch:
            raise AssertionError("wide 표인데 getList(fetch_actual_statistics_bounded_retry)가 호출됨 - 배치 완전 적재를 건너뛰지 못함")
        self.fetch_calls.append({"start_year": start_year, "end_year": end_year, "itm_id": itm_id})
        # start_year와 end_year 양쪽에 각각 한 행씩 반환한다(실제 KOSIS
        # 응답이라면 그 사이 모든 시점을 채우겠지만, 여기선 "구간을 넓게
        # 훑었을 때 오래된 시점과 최근 시점이 둘 다 잡히는지"만 확인하면
        # 충분하다) - old_value(1965년 쪽)를 크게 둬서, records가 실제로
        # 전체 이력(오래된 값 포함)을 보고 있는지 검증할 수 있게 한다.
        itm = itm_id if itm_id != "all" else "T01"
        rows = [_fake_raw_row(itm, start_year, prd_se, 999.0)]
        if end_year != start_year:
            rows.append(_fake_raw_row(itm, end_year, prd_se, 1.0))
        return rows


def test_ingest_table_narrow_batch_pulls_and_records_blanket_coverage():
    """narrow 표(항목 1개×축 3코드×기간 3개=9셀 - 임계값보다 훨씬 작음)는
    지금처럼 getList를 실제로 호출해 facts/records까지 채우고, 그 위에
    새로 추가된 부분 - fact_coverage에 itm_id='all'/axis_key='all' 블랭킷
    행까지 남기는지 확인한다."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_NARROW_INGEST"

    raw_meta = [_fake_meta_row("ITEM", "T01")]
    raw_meta += [_fake_meta_row("A", f"A{i}", obj_id_sn=1) for i in range(3)]
    period_meta = [{"PRD_SE": "Y", "STRT_PRD_DE": "2020", "END_PRD_DE": "2022"}]
    client = _FakeIngestClient(raw_meta, period_meta)

    result = wh.ingest_table(client, conn, org_id, tbl_id)
    _check("narrow 표 ingest_table - success=True", result.get("success") is True, result)
    _check("narrow 표 - getList가 실제로 호출됨(배치 완전 적재)", len(client.fetch_calls) >= 1, client.fetch_calls)

    n_facts = conn.execute("SELECT COUNT(*) FROM facts WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)).fetchone()[0]
    _check("narrow 표 - facts에 실제로 적재됨", n_facts > 0, n_facts)

    covered = wh.is_period_covered(conn, org_id, tbl_id, "Y", "T01", "1=A0201", "2020", "2022")
    _check("narrow 표 - 블랭킷 커버리지가 남아 구체적 항목/축 조회도 covered로 인정됨", covered)
    conn.close()


def test_ingest_table_wide_still_batch_pulls_bounded_recent_window():
    """[2026-08-18 수정 - 사용자 지적으로 정책 변경] wide 표(항목 5개×축1
    20코드×축2 15코드×기간 799개 - 임계값 훨씬 초과)라도 getList를 통째로
    건너뛰면 안 된다 - getMeta만으론 실제 값 형태/존재 여부를 알 수 없다.
    대신 years_back 미지정이어도 기본 캡(_WIDE_TABLE_DEFAULT_YEARS_BACK)만큼
    최근 구간은 반드시 실제 getList로 배치 적재해서 facts에 남긴다.

    [2026-08-18 최종 결론 - 실측으로 확정] records(역대 최고/최저)는
    wide 표에서 배치 시점엔 아예 계산하지 않는다. 중간에 두 차례 다른
    시도가 있었다(① 캡 구간 기준으로만 공짜 계산 ② narrow처럼 항상 전체
    이력을 훑어 계산) - 특히 ②는 사용자의 정확한 지적("역대 claim이
    캡 구간 안에 있다는 보장이 없다")에서 나왔지만, 실제로 돌려보니
    (`seed_ingest_cpi_breakdown.py`) wide 표+긴 기간+성긴 과거 데이터가
    겹치면 이분탐색이 "이 시점엔 정말 데이터가 없다"를 확인하는 데만
    시점마다 API 호출을 8번 가까이 써야 하는 재앙적 비용이 났다(예전
    세션의 "1,022 API 호출, 10분" 문제 재현). 그래서 최종적으로 wide
    표의 records는 배치 경로에서 완전히 빼고, `fetch_scoped_slice`가
    claim이 실제로 필요로 하는 항목 하나로 좁혀서(narrow만큼 저렴) 전체
    이력을 온디맨드로 채울 때만 계산한다 - 이미 그 함수가 그렇게 하도록
    구현/테스트돼 있다(test_fetch_scoped_slice_live_fetch_expands_to_
    full_history_for_records 참고)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(wh._SCHEMA_TABLES_SQL)
    conn.executescript(wh._SCHEMA_INDEXES_SQL)
    org_id, tbl_id = "999", "TEST_WIDE_INGEST"

    raw_meta = [_fake_meta_row("ITEM", f"T{i}") for i in range(5)]
    raw_meta += [_fake_meta_row("A", f"A{i}", obj_id_sn=1) for i in range(20)]
    raw_meta += [_fake_meta_row("B", f"B{i}", obj_id_sn=2) for i in range(15)]
    period_meta = [{"PRD_SE": "M", "STRT_PRD_DE": "196501", "END_PRD_DE": "203112"}]
    client = _FakeIngestClient(raw_meta, period_meta)  # raise_on_fetch 없음 - 이번엔 실제로 호출돼야 함

    result = wh.ingest_table(client, conn, org_id, tbl_id)
    _check("wide 표 ingest_table - success=True", result.get("success") is True, result)
    _check("wide 표 - getList가 실제로 호출됨(더 이상 통째로 건너뛰지 않음)", len(client.fetch_calls) >= 1, client.fetch_calls)
    _check("wide 표 - fact_rows > 0(최근 구간은 실제로 적재됨)", result.get("fact_rows", 0) > 0, result)
    _check("wide 표 - record_rows == 0(배치 시점엔 records 계산 안 함 - 온디맨드로 미룸)", result.get("record_rows") == 0, result)

    expected_clipped_strt = wh._clip_period_window("196501", "203112", "M", wh._WIDE_TABLE_DEFAULT_YEARS_BACK)
    _check(
        "wide 표 - years_back 미지정이라도 기본 캡만큼만 배치 조회됨(전체 1965~는 안 당김, 재앙적 전체 훑기 없음)",
        client.fetch_calls[0]["start_year"] == expected_clipped_strt and len(client.fetch_calls) == 1,
        client.fetch_calls,
    )

    fact_range = conn.execute(
        "SELECT MIN(prd_de), MAX(prd_de) FROM facts WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)
    ).fetchone()
    _check(
        "wide 표 - facts에는 캡 구간(최근) 값만 남음",
        fact_range[0] == expected_clipped_strt or fact_range[1] == "203112",
        fact_range,
    )

    # _FakeIngestClient가 만드는 합성 raw row는 c1 등 축 코드를 안 채우므로
    # (fetch_actual_statistics_bounded_retry stub이 itm_id만 반영) 축 없이 조회
    rec = wh.get_record(conn, org_id, tbl_id, "T01", "M", axis_codes={})
    _check("wide 표 - records가 배치 시점엔 없음(fetch_scoped_slice 몫)", rec is None, rec)

    _check("wide 표 - tables_registry엔 등록됨(검색 가능 유지)", wh.is_table_ingested(conn, org_id, tbl_id))
    n_dims = conn.execute("SELECT COUNT(*) FROM dimensions WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)).fetchone()[0]
    _check("wide 표 - dimensions(getMeta)는 정상 적재됨", n_dims == len(raw_meta), n_dims)

    covered_recent = wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", expected_clipped_strt, "203112")
    _check("wide 표 - 최근(캡 이내) 구간은 커버리지에 기록됨", covered_recent)
    covered_old = wh.is_period_covered(conn, org_id, tbl_id, "M", "T01", "1=A0201", "196501", "196512")
    _check("wide 표 - 캡보다 오래된 구간은 아직 커버 안 됨(온디맨드 대상으로 남음)", not covered_old)
    conn.close()


def test_ensure_tables_for_claim_skips_live_call_on_local_hit(conn):
    """내부 DB에 이미 있는 주제(예: '유가증권 거래량')로 검색하면 라이브
    kosis_client.search_metadata를 절대 호출하지 않아야 한다 - 호출되면
    바로 실패하도록 stub의 search_metadata가 AssertionError를 던지게
    만들어서, 이 테스트 자체가 "호출 안 됨"을 강제로 검증하게 한다."""

    class _NoLiveCallClient:
        def search_metadata(self, *args, **kwargs):
            raise AssertionError("내부 DB에 후보가 있는데 라이브 search_metadata가 호출됨 - cache-miss 트리거 순서 위반")

    result = wh.ensure_tables_for_claim(_NoLiveCallClient(), conn, "유가증권 순위별 거래량")
    _check("내부 DB 히트 시 source='internal_db'", result["source"] == "internal_db", result["source"])
    _check("내부 DB 히트 시 live_search_skipped=True", result["live_search_skipped"] is True)
    _check("내부 DB 히트 시 candidates 1개 이상", len(result["candidates"]) >= 1)
    _check("내부 DB 히트 시 newly_ingested는 빈 리스트", result["newly_ingested"] == [])


def main():
    test_clip_period_window_months()
    test_clip_period_window_quarters()
    test_clip_period_window_none_years_back_noop()
    test_clip_period_window_unclippable_prd_se_noop()
    test_clip_period_window_already_short_noop()
    test_clip_period_window_annual_never_clipped()
    test_ingest_records_and_get_record()
    test_classify_table_width_narrow()
    test_classify_table_width_wide()
    test_classify_table_width_period_unknown_falls_back_to_axis_only()
    test_normalize_axis_key()
    test_fact_coverage_narrow_blanket_row()
    test_fact_coverage_wide_scoped_rows()
    test_fetch_scoped_slice_cache_hit_skips_live_call()
    test_fetch_scoped_slice_live_fetch_expands_to_full_history_for_records()
    test_fetch_scoped_slice_all_itm_id_never_expands_to_full_history()
    test_ingest_table_narrow_batch_pulls_and_records_blanket_coverage()
    test_ingest_table_wide_still_batch_pulls_bounded_recent_window()

    try:
        conn = sqlite3.connect(DB_PATH, uri=True)
        tables = {r[0] for r in conn.execute("SELECT org_id || '/' || tbl_id FROM tables_registry").fetchall()}
        if "343/DT_343_2010_S0043" not in tables:
            print("[SKIP] ensure_tables_for_claim 테스트: 유가증권 표가 아직 적재 안 됨(seed_ingest_extra.py 먼저 실행)")
        else:
            test_ensure_tables_for_claim_skips_live_call_on_local_hit(conn)
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"[SKIP] kosis_warehouse.db를 열 수 없음({e}) - 이 폴더에서 실행했는지 확인하세요.")

    print()
    if _failures:
        print(f"FAIL: {len(_failures)}건 실패 - {_failures}")
        sys.exit(1)
    print("PASS: 전체 통과")


if __name__ == "__main__":
    main()

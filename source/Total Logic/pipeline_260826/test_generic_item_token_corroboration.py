"""[2026-08-21 신규 - Task #15] "물가" 범용 토큰 corroboration 오탐 회귀
테스트.

## 배경

실측(A93bfa851-C018, README "열세 번째"/"열여섯 번째" 항목): 소비자물가지수
claim("주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다")의
match_phrases에 "물가"가 섞여 있었는데, 이 표의 ITEM은 "소비자물가지수"
하나뿐이라 iter_table_cell_texts가 모든 셀의 segments에 그 이름을 무조건
포함시킨다(kosis_local_search.py 함수 정의 참고) - "물가"는 "소비자물가지수"의
부분 문자열이라 표의 모든 후보에 100% 매칭돼서, 실제로는 아무 구분력이
없는데도 matched_phrases 개수(local_db_agent.py weak_literal_tie의
corroboration 기준 >=2)를 채우는 데 끼어들었다. 실제로는 "주류"라는
phrase 하나만 진짜 구분 정보였는데도(그리고 그 "주류"조차 "주류 및 담배"
축의 여러 자식 leaf에 조상 체인으로 상속되어 걸리므로, leaf들끼리는 여전히
동점), matched_phrases=['주류','물가'](2개)로 잡혀 weak_literal_tie 체크를
통과해버리고 disambiguate_by_value의 느슨한 값 허용오차로 넘어가버렸다.

## 이 테스트가 확인하는 것

1. kosis_local_search.resolve_evidence_by_flat_match가 새로 계산하는
   distinguishing_phrase_count가 "표 전체에서 ITEM이 하나뿐일 때 item_nm
   전용 매치(축에는 안 걸리는 매치)"를 세지 않는지 - 위 실측 패턴을 합성
   DB로 재현.
2. ITEM이 표 안에서 실제로 여러 값으로 갈리는 표(item_is_uniform=False)는
   item_nm 매치도 그대로 구분력 있는 것으로 인정하는지(회귀 - 기존 동작
   보존 확인).
3. local_db_agent.resolve_claim_evidence 전체 파이프라인에서, 이 수정
   덕분에 weak_literal_tie가 이제 올바르게 True로 잡혀 HCX 재확인 경로로
   넘어가는지(수정 전이었다면 False로 남아 disambiguate_by_value로
   바로 샜을 상황).

사용법: python3 test_generic_item_token_corroboration.py (종료 코드 0 =
전체 PASS)
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


def _seed_cpi_style_table(conn):
    """실제 C018 표(소비자물가지수, 지출목적별)를 축소 재현 - ITEM은
    "소비자물가지수" 하나뿐이고, "주류 및 담배"(G02) 밑에 leaf 2개(소주/
    맥주)를 둬서, "주류"라는 phrase가 조상 체인으로 상속되어 두 leaf가
    동점이 되는 실제 상황을 그대로 만든다."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES "
        "('9', 'T20', '합성 소비자물가지수(지출목적별)', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T20', 'ITEM', 0, '항목', 'IT1', '소비자물가지수', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T20', 'G', 1, '지출목적별', 'G01', '식료품', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T20', 'G', 1, '지출목적별', 'G02', '주류 및 담배', NULL, NULL)"
    )
    for code, name in [("G0201", "소주"), ("G0202", "맥주")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T20', 'G', 1, '지출목적별', ?, ?, 'G02', NULL)",
            (code, name),
        )
    # [주의] G02(집계행) 자신은 일부러 facts에 넣지 않는다 - 만약 넣으면
    # ancestor_only_hits=0(자기 이름으로 직접 설명됨)인 G02가 ancestor_
    # only_hits=1인 소주/맥주보다 항상 유일한 1위가 되어 애초에 "동점"
    # 자체가 안 생긴다(기존 로직이 이미 그 경우엔 잘 가른다는 뜻이기도
    # 하다). 실측 C018 버그가 실제로 걸린 지점은 "같은 조상 밑 leaf들끼리"
    # 동점이 나는 경우이므로, 그 상황만 재현한다.
    for code, value in [("G01", 3.0), ("G0201", 13.1), ("G0202", 12.0)]:
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('9', 'T20', 'IT1', '202509', 'M', ?, ?, '%')",
            (code, value),
        )
    conn.commit()


def _seed_multi_item_table(conn):
    """[회귀 확인용] ITEM이 표 안에서 실제로 2개 이상으로 갈리는 표 -
    item_nm 매치도 진짜 구분 정보이므로 distinguishing_phrase_count에서
    빠지면 안 된다."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES "
        "('9', 'T21', '합성 고용지표', NULL)"
    )
    for code, name in [("IT1", "실업률"), ("IT2", "고용률")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T21', 'ITEM', 0, '항목', ?, ?, NULL, NULL)",
            (code, name),
        )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T21', 'G', 1, '연령별', 'G01', '청년층', NULL, NULL)"
    )
    for itm in ("IT1", "IT2"):
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('9', 'T21', ?, '2025-09', 'M', 'G01', 5.0, '%')",
            (itm,),
        )
    conn.commit()


def test_item_only_match_excluded_when_item_uniform_across_table():
    """"물가"처럼 item_nm(표 전체에서 하나뿐)에만 걸리고 축엔 안 걸리는
    phrase는 distinguishing_phrase_count에서 빠져야 한다 - "주류"(축에도
    걸림)는 남아야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_style_table(conn)
    candidates = kls.resolve_evidence_by_flat_match(conn, "9", "T20", ["주류", "물가"], top_n=8)
    _check("후보가 나옴", len(candidates) >= 2, str(candidates))
    soju = next((c for c in candidates if c["axis_codes"].get(1) == "G0201"), None)
    maekju = next((c for c in candidates if c["axis_codes"].get(1) == "G0202"), None)
    _check("소주/맥주 둘 다 후보에 있음", soju is not None and maekju is not None, str(candidates))
    if soju and maekju:
        _check(
            "기존 matched_phrases는 여전히 2개('주류','물가') - 수정 전 동작 그대로",
            set(soju["matched_phrases"]) == {"주류", "물가"} and set(maekju["matched_phrases"]) == {"주류", "물가"},
            str((soju["matched_phrases"], maekju["matched_phrases"])),
        )
        _check(
            "distinguishing_phrase_count는 1('물가' 제외, '주류'만 남음)",
            soju["distinguishing_phrase_count"] == 1 and maekju["distinguishing_phrase_count"] == 1,
            str((soju["distinguishing_phrase_count"], maekju["distinguishing_phrase_count"])),
        )
    conn.close()


def test_item_only_match_kept_when_item_varies_across_table():
    """[회귀] ITEM이 표 안에서 여러 값으로 갈리면(실업률/고용률) item_nm
    매치도 구분 정보이므로 distinguishing_phrase_count에서 빠지면 안 된다."""
    conn = wh.get_connection(":memory:")
    _seed_multi_item_table(conn)
    candidates = kls.resolve_evidence_by_flat_match(conn, "9", "T21", ["실업률"], top_n=8)
    _check("후보가 나옴", len(candidates) >= 1, str(candidates))
    if candidates:
        top = candidates[0]
        _check(
            "ITEM이 여러 개인 표에서는 item_nm 매치도 distinguishing_phrase_count에 그대로 카운트됨",
            top["distinguishing_phrase_count"] == 1,
            str(top),
        )
    conn.close()


def test_weak_literal_tie_now_correctly_triggers_for_generic_item_token_case():
    """[핵심 회귀] local_db_agent.resolve_claim_evidence 전체 파이프라인에서,
    수정 전에는 matched_phrases 개수(2개)만 보고 weak_literal_tie=False로
    잘못 판단해 disambiguate_by_value로 바로 샜을 상황이, 수정 후에는
    distinguishing_phrase_count(1개)를 보고 weak_literal_tie=True로 올바르게
    판단해 HCX 재확인 경로를 타는지 확인한다."""
    conn = wh.get_connection(":memory:")
    _seed_cpi_style_table(conn)

    match_phrases = ["주류", "물가"]
    pre_candidates = kls.resolve_evidence_by_flat_match(conn, "9", "T20", match_phrases, top_n=8)
    pre_top = pre_candidates[0]
    pre_tie = [
        c for c in pre_candidates
        if c["score"] == pre_top["score"]
        and c.get("unexplained_axes") == pre_top.get("unexplained_axes")
        and c.get("ancestor_only_hits") == pre_top.get("ancestor_only_hits")
    ]
    _check(
        "사전 조건: 소주/맥주가 동점(score/unexplained_axes/ancestor_only_hits 전부 같음)",
        len(pre_tie) >= 2,
        str(pre_tie),
    )
    _check(
        "사전 조건(수정 전 버그 재현): matched_phrases는 둘 다 2개라 옛 기준(<2)은 안 걸림",
        all(len(c.get("matched_phrases") or []) >= 2 for c in pre_tie),
        str(pre_tie),
    )
    _check(
        "수정 확인: distinguishing_phrase_count는 둘 다 1개라 새 기준(<2)에 걸림",
        all(c.get("distinguishing_phrase_count", 99) < 2 for c in pre_tie),
        str(pre_tie),
    )

    def _fake_hcx_resolve_fn(cell_texts, claim_text, claimed_value, claimed_unit, claimed_period):
        for i, t in enumerate(cell_texts):
            if t == "소비자물가지수 주류 및 담배 소주":
                return i
        return None

    claim = {
        # [주의] value_num을 실제 G0201 값(13.1)과 정확히 맞추면 Stage 1/2
        # 보다 먼저 도는 "값 기반 검색" 빠른 경로(resolve_claim_evidence
        # 앞부분, tolerance=0.01)가 바로 채택해버려서 이 테스트가 검증
        # 하려는 Stage 2 weak_literal_tie 판정 자체를 거치지 않는다(실제로
        # 한 번 이렇게 걸려서 고침 - test_local_db_agent_derivation.py의
        # test_strong_literal_tie_still_uses_disambiguate_by_value_directly
        # 와 같은 이유). Stage 3(실제 facts 재조회)은 claimed_value와 무관
        # 하게 G0201의 진짜 값(13.1)을 그대로 돌려주므로 아래 assertion은
        # 여전히 13.1과 비교한다.
        "claim_id": "TEST-T15-1",
        "claim": "주류 및 담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다.",
        # [주의] Stage 2가 실제로 쓰는 match_phrases는 raw_sentence가 아니라
        # metric(run02가 생성한 정규화 키워드, local_db_agent.py:530
        # `metric_text = claim.get("metric_normalized") or claim.get("metric")`)
        # 를 토큰화한 것이다 - 원문 claim 문장엔 "물가"가 문자 그대로 없으므로
        # (원문은 "주류 및 담배는 상승률이..."), 실측 C018처럼 run02가
        # "물가"를 포함한 키워드를 생성해 넘긴 상황을 metric 필드로 재현한다.
        "metric": "주류 물가",
        "value_num": 13.5,
        "unit": "%",
        "period": "2025-09",
    }
    result = lda.resolve_claim_evidence(
        conn, claim, keywords=["소비자물가지수"], hcx_resolve_fn=_fake_hcx_resolve_fn,
    )

    _check(
        "물가 범용 토큰 오탐이 고쳐져 HCX 재확인이 실제로 쓰임(hcx_fallback_used=True)",
        result.get("hcx_fallback_used") is True,
        str(result),
    )
    _check(
        "HCX가 고른 소주(G0201, 정답)가 최종 채택됨",
        result.get("query_status") == "success" and result.get("normalized_value") == 13.1,
        str(result),
    )
    conn.close()


def test_strong_axis_only_tie_still_bypasses_hcx():
    """[안전장치 확인] item_nm 매치 없이 축 phrase만으로 서로 다른 2개
    이상이 corroborate하는 진짜 튼튼한 동점은 여전히 HCX로 안 새고
    disambiguate_by_value로 바로 풀려야 한다 - "정부"/"유가증권" 케이스와
    동일한 안전장치가 이 수정 이후에도 유지되는지 확인."""
    conn = wh.get_connection(":memory:")
    _seed_multi_item_table(conn)
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T21', 'H', 2, '성별', 'H01', '여성', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, c2, value, unit) "
        "VALUES ('9', 'T21', 'IT1', '2025-09', 'M', 'G01', 'H01', 7.0, '%')"
    )
    conn.commit()
    candidates = kls.resolve_evidence_by_flat_match(conn, "9", "T21", ["청년층", "여성"], top_n=8)
    top = candidates[0]
    _check(
        "서로 다른 축 phrase 2개('청년층','여성')로 유일한 1위(동점 아님)",
        top.get("distinguishing_phrase_count", 0) >= 2,
        str(top),
    )
    conn.close()


if __name__ == "__main__":
    test_item_only_match_excluded_when_item_uniform_across_table()
    test_item_only_match_kept_when_item_varies_across_table()
    test_weak_literal_tie_now_correctly_triggers_for_generic_item_token_case()
    test_strong_axis_only_tie_still_bypasses_hcx()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

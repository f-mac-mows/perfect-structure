"""[2026-08-24 신규] "이상" 같은 일반 비교사가 무관한 축 leaf에 우연히 걸려
score/unexplained_axes를 왜곡하는 문제(축 단위 일반화) 회귀 테스트.

## 배경

실측(A82ae9f41-C001, "15세 이상 취업자", 실제 표 DT_1DA7024S "성/연령별
취업자"): match_phrases=['15세','이상','취업자']에서 "이상"은 claim의 "15세
이상"(비교사)에서 나온 흔한 단어인데, 이 표의 연령 축에는 "60세이상"/
"65세 이상"/"70세 이상"/"75세 이상"처럼 서로 다른(무관한) 임계값 leaf 여러
개가 전부 leaf_name 자체에 "이상"을 포함한다. 그 결과 "이상"이 이 leaf들을
전부 "설명된 축"으로 오인시키고 score까지 올려서, 정답인 계(전체) leaf
(occurrences={'취업자':1}, score=1)보다 "계/60세이상"(occurrences=
{'이상':1,'취업자':1}, score=2)이 더 높은 점수로 1위를 차지했다.

2026-08-21에 이미 고친 "물가"(item_nm이 표 전체에서 하나뿐이라 구분력
없음, item_is_uniform 처리, test_generic_item_token_corroboration.py)와
같은 계열의 버그다 - 다만 이번엔 item_nm이 아니라 axis leaf 텍스트에서
발생한다. kosis_local_search.resolve_evidence_by_flat_match에 axis 단위
일반화(어떤 phrase가 같은 축 안에서 서로 다른 leaf_name 2개 이상에 걸리면
그 축에서는 구분력 없는 일반 단어로 보고 leaf_name 매치로 인정하지 않음)를
추가해서 고쳤다.

## 이 테스트가 확인하는 것

1. 실측 재현: "이상"이 2개 이상의 무관한 leaf(60세이상/65세 이상)에 걸리는
   표에서, 정답인 계(전체) leaf가 score/unexplained_axes 둘 다에서 더 이상
   불리하지 않고 1위가 되는지.
2. [회귀] 어떤 phrase가 같은 축에서 leaf 딱 하나에만 걸리면(진짜 구분
   정보) 여전히 정상적으로 인정되는지 - 이번 수정이 과잉적용되지 않는지.
3. [회귀] 조상(ancestor) breadcrumb에서만 나온 매치(leaf_name 자신에는
   없음)는 이번 축 단위 일반화의 영향을 받지 않는지 - ancestor_only_hits가
   이미 다루는 별개의 신호라 건드리면 안 됨.

사용법: python3 test_generic_axis_leaf_token_collision.py (종료 코드 0 =
전체 PASS)
"""

import sys

import kosis_local_search as kls
import kosis_warehouse as wh

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def _seed_age_bracket_table(conn):
    """실제 DT_1DA7024S(성/연령별 취업자)의 핵심 구조를 재현 - 연령 축에
    계(전체) + "이상"을 이름 자체에 포함하는 무관한 임계값 2개(60세이상/
    65세 이상)를 두고, 그중 하나(60세이상) 밑에 자식 leaf('ㆍ60 - 64세',
    자기 이름엔 "이상"이 없고 조상에서만 옴)를 추가한다 - 1차 수정
    (leaf_name만 봄)이 이 자식 leaf를 못 잡아낸 실측 사각지대를 그대로
    재현(위 resolve_evidence_by_flat_match의 2026-08-24 주석 참고)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES "
        "('9', 'T30', '합성 성/연령별 취업자', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T30', 'ITEM', 0, '항목', 'T1', '취업자', NULL, NULL)"
    )
    for code, name, parent in [
        ("00", "계", None),
        ("60", "60세이상", None),
        ("65", "65세 이상", None),
        ("601", "ㆍ60 - 64세", "60"),
    ]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T30', 'G', 1, '연령계층별', ?, ?, ?, NULL)",
            (code, name, parent),
        )
    for code, value in [("00", 29091.0), ("60", 500.0), ("65", 300.0), ("601", 200.0)]:
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('9', 'T30', 'T1', '202506', 'M', ?, ?, '만명')",
            (code, value),
        )
    conn.commit()


def _seed_gender_axis_table(conn):
    """[회귀 확인용] "여자"처럼 축 안에서 leaf 딱 하나에만 걸리는(진짜
    구분 정보) phrase는 그대로 인정돼야 한다."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES "
        "('9', 'T31', '합성 성별 취업자', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T31', 'ITEM', 0, '항목', 'T1', '취업자', NULL, NULL)"
    )
    for code, name in [("00", "계"), ("01", "남자"), ("02", "여자")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T31', 'H', 1, '성별', ?, ?, NULL, NULL)",
            (code, name),
        )
    for code, value in [("00", 100.0), ("01", 55.0), ("02", 45.0)]:
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('9', 'T31', 'T1', '202506', 'M', ?, ?, '만명')",
            (code, value),
        )
    conn.commit()


def _seed_ancestor_chain_table(conn):
    """[회귀 확인용] "가정용품"이 조상(부모) 이름에서만 나오고 leaf_name
    자신("프라이팬"/"냄비")에는 없는 경우 - 축 단위 일반화가 이 조상 매치를
    건드리면 안 된다(ancestor_only_hits가 이미 다루는 별개 신호)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES "
        "('9', 'T32', '합성 가정용품 물가', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T32', 'ITEM', 0, '항목', 'T1', '소비자물가지수', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T32', 'E', 1, '품목별', 'E0', '가정용품', NULL, NULL)"
    )
    for code, name in [("E01", "프라이팬"), ("E02", "냄비")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T32', 'E', 1, '품목별', ?, ?, 'E0', NULL)",
            (code, name),
        )
    for code, value in [("E01", 105.0), ("E02", 103.0)]:
        conn.execute(
            "INSERT INTO facts (org_id, tbl_id, itm_id, prd_de, prd_se, c1, value, unit) "
            "VALUES ('9', 'T32', 'T1', '202509', 'M', ?, ?, '2020=100')",
            (code, value),
        )
    conn.commit()


def test_generic_comparative_word_no_longer_wins_over_total():
    """실측 재현: "이상"이 60세이상/65세 이상 두 leaf에 걸려도, 정답인
    계(전체)가 score 동점 + unexplained_axes 우위로 1위가 돼야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_age_bracket_table(conn)
    candidates = kls.resolve_evidence_by_flat_match(
        conn, "9", "T30", ["15세", "이상", "취업자"], top_n=8
    )
    _check("후보가 4개 나옴", len(candidates) == 4, str(candidates))
    top = candidates[0]
    _check(
        "1위가 계(전체, axis_codes[1]=='00')",
        top["axis_codes"].get(1) == "00",
        str(top),
    )
    _check(
        "1위(계)의 unexplained_axes==0",
        top.get("unexplained_axes") == 0,
        str(top),
    )
    sixty_plus = next((c for c in candidates if c["axis_codes"].get(1) == "60"), None)
    _check("60세이상 후보도 존재", sixty_plus is not None, str(candidates))
    if sixty_plus:
        _check(
            "60세이상의 score는 계와 동점(1) - '이상'이 더 이상 score를 부풀리지 않음",
            sixty_plus["score"] == top["score"] == 1,
            str((sixty_plus, top)),
        )
        _check(
            "60세이상의 unexplained_axes==1 - '이상' 매치가 축 단위 일반화로 제외되어 '설명 안 된 축'으로 정직하게 표시됨",
            sixty_plus.get("unexplained_axes") == 1,
            str(sixty_plus),
        )
        _check(
            "60세이상의 matched_phrases에 '이상'이 없음(제외됨)",
            "이상" not in sixty_plus.get("matched_phrases", []),
            str(sixty_plus),
        )

    # [핵심 - 1차 수정의 사각지대였던 사례] 자기 이름엔 "이상"이 없고
    # 조상("60세이상")에서만 상속된 자식 leaf도 똑같이 안 이겨야 한다.
    child = next((c for c in candidates if c["axis_codes"].get(1) == "601"), None)
    _check("자식 leaf(60-64세) 후보도 존재", child is not None, str(candidates))
    if child:
        _check(
            "자식 leaf의 score는 계와 동점(1) - 조상 '60세이상'에서 상속된 '이상'도 더 이상 score를 부풀리지 않음",
            child["score"] == top["score"] == 1,
            str((child, top)),
        )
        _check(
            "자식 leaf의 unexplained_axes==1 - 조상 상속 '이상' 매치도 축 단위 일반화로 제외됨",
            child.get("unexplained_axes") == 1,
            str(child),
        )
    conn.close()


def test_single_leaf_distinguishing_phrase_still_counted():
    """[회귀] "여자"처럼 축 안에서 leaf 딱 하나에만 걸리는 phrase는 여전히
    정상적으로 score/unexplained_axes에 반영돼야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_gender_axis_table(conn)
    candidates = kls.resolve_evidence_by_flat_match(
        conn, "9", "T31", ["여자", "취업자"], top_n=8
    )
    top = candidates[0]
    _check(
        "1위가 여자(축 유일 매치라 score=2로 계를 앞섬) - 축 단위 일반화가 과잉적용되지 않음",
        top["axis_codes"].get(1) == "02" and top["score"] == 2,
        str(top),
    )
    _check(
        "'여자'가 matched_phrases에 남아있음",
        "여자" in top.get("matched_phrases", []),
        str(top),
    )
    conn.close()


def test_ancestor_only_match_unaffected_by_leaf_generalization():
    """[회귀] leaf_name 자신에는 없고 조상(부모)에서만 나온 매치("가정용품"
    이 "프라이팬"/"냄비" 둘의 조상이지만 leaf_name 자체엔 없음)는 축 단위
    일반화로 제외되면 안 된다 - ancestor_only_hits가 별도로 다루는 신호."""
    conn = wh.get_connection(":memory:")
    _seed_ancestor_chain_table(conn)
    candidates = kls.resolve_evidence_by_flat_match(
        conn, "9", "T32", ["가정용품", "프라이팬"], top_n=8
    )
    frying_pan = next((c for c in candidates if c["axis_codes"].get(1) == "E01"), None)
    _check("프라이팬 후보 존재", frying_pan is not None, str(candidates))
    if frying_pan:
        _check(
            "'가정용품'(조상 매치)이 matched_phrases에 여전히 남아있음 - 축 단위 일반화가 조상 매치를 건드리지 않음",
            "가정용품" in frying_pan.get("matched_phrases", []),
            str(frying_pan),
        )
    conn.close()


if __name__ == "__main__":
    test_generic_comparative_word_no_longer_wins_over_total()
    test_single_leaf_distinguishing_phrase_still_counted()
    test_ancestor_only_match_unaffected_by_leaf_generalization()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

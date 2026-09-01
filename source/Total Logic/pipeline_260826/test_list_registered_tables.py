"""[2026-08-21 신규 - Task #80 확장] kosis_local_search.list_registered_
tables의 axis_hints(분류축 이름 + 최상위 분류값 샘플) 회귀 테스트.

## 배경

Stage 1 LLM 표 선택(hcx_stage1_resolver.py)을 표 이름만으로 실 API
검증했더니(probe_c018_stage1_llm_table_select.py), "지출목적별"/
"품목성질별"/"품목별" 소비자물가지수 세 표를 HCX-007이 구분 못 하고
콜마다 다른(잘못된) 표를 골랐다 - 셋 다 ITEM은 "소비자물가지수" 하나
뿐이고, "주류"가 어느 표 소속인지는 표 이름이 아니라 분류축의 최상위
값("02 주류 및 담배")에만 있는 정보였기 때문이다. list_registered_
tables가 이 정보(axis_hints)를 실제로 만들어내는지 합성 DB로 검증한다.

사용법: python3 test_list_registered_tables.py (종료 코드 0 = 전체 PASS)

[2026-08-22 추가/갱신 - Task #1] axis_hints의 leaf_samples(실제 리프
이름 샘플) 필드 회귀 테스트도 여기 추가한다 - DT_404Y016(품목별)/
DT_404Y014(기본분류)가 최상위 값 샘플로는 구분이 안 되는 실측(README
참고)을 합성 DB로 재현한다. 처음엔 "분류 트리 깊이(max_depth)"로
구분하려 했으나 실측(사용자가 로컬 DB를 직접 덤프)으로 반증됐다 -
014가 016보다 트리는 더 깊은데 리프는 더 안 구체적이었다. 그래서
깊이 대신 리프 이름 자체를 노출하고, 이 역전 관계를 그대로 재현하는
회귀 테스트로 깊이 기반 판단이 다시 들어오지 않도록 고정한다."""

import sys

import kosis_local_search as kls
import kosis_warehouse as wh

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def _seed_confusable_twin(conn, org_id: str, twin_tbl_id: str, similar_tbl_nm: str):
    """[2026-08-24 신규] list_registered_tables가 이제 "표 이름이 다른
    어떤 표와도 안 겹치면 axis_hints 계산 자체를 생략"하도록 바뀌었다
    (표가 계속 늘어도 프롬프트 크기가 표 개수가 아니라 "이름이 겹치는
    군집 개수"에 비례하게 하려는 목적, 사용자 지적 - "DB는 계속 커질
    텐데 모든 통계표를 다 보여주는 건 아니다"). axis_hints 내부 계산
    로직(최상위만 필터/ITEM 제외/캡/드릴다운 등) 자체를 검증하는 기존
    테스트들은 그 계산이 실제로 실행되게 하려면 "이름이 비슷한 표가
    하나 더 있다"는 조건이 필요하다 - 이 쌍둥이 표는 dimensions가
    없어도 된다(tbl_nm 유사도 계산에만 쓰임)."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES (?, ?, ?, NULL)",
        (org_id, twin_tbl_id, similar_tbl_nm),
    )


def _seed_hierarchical_table(conn):
    """ITEM 1개 + 최상위 3개(부모 없음) + 그 밑에 리프 여러 개가 딸린
    합성 표 - "최상위만" 필터가 실제로 리프를 걸러내는지 확인하는 데 쓴다."""
    conn.execute(
        "INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T9', '합성 소비자물가지수', NULL)"
    )
    _seed_confusable_twin(conn, "9", "T9-TWIN", "합성 소비자물가지수(품목성질별)")
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T9', 'ITEM', 0, '항목', 'IT1', '소비자물가지수', NULL, NULL)"
    )
    top_levels = [("G01", "01 식료품"), ("G02", "02 주류 및 담배"), ("G03", "03 의류")]
    for code, name in top_levels:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T9', 'G', 1, '지출목적별', ?, ?, NULL, NULL)",
            (code, name),
        )
    # 리프(부모 있음) - axis_hints에는 안 나와야 한다.
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T9', 'G', 1, '지출목적별', 'G0201', '주류', 'G02', NULL)"
    )
    conn.commit()


def test_axis_hints_include_only_top_level_values():
    conn = wh.get_connection(":memory:")
    _seed_hierarchical_table(conn)
    tables = kls.list_registered_tables(conn)
    t9 = next(t for t in tables if t["tbl_id"] == "T9")
    _check("axis_hints가 정확히 1개 축(지출목적별)을 가짐", len(t9["axis_hints"]) == 1, str(t9["axis_hints"]))
    values = t9["axis_hints"][0]["values"] if t9["axis_hints"] else []
    _check("최상위 값 3개만 포함(리프 '주류'는 제외)", set(values) == {"01 식료품", "02 주류 및 담배", "03 의류"}, str(values))
    _check("리프 이름('주류')은 axis_hints에 안 나옴", "주류" not in values, str(values))
    conn.close()


def test_axis_hints_exclude_item_axis():
    conn = wh.get_connection(":memory:")
    _seed_hierarchical_table(conn)
    tables = kls.list_registered_tables(conn)
    t9 = next(t for t in tables if t["tbl_id"] == "T9")
    axis_labels = {h["axis_label"] for h in t9["axis_hints"]}
    _check("ITEM 축('항목')은 axis_hints에 안 나옴", "항목" not in axis_labels, str(axis_labels))


def test_axis_hints_respects_max_axis_values_cap():
    conn = wh.get_connection(":memory:")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T10', '축값 많은 표', NULL)")
    _seed_confusable_twin(conn, "9", "T10-TWIN", "축값 많은 표(변형)")
    for i in range(10):
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T10', 'G', 1, '분류', ?, ?, NULL, NULL)",
            (f"G{i}", f"항목{i}"),
        )
    conn.commit()
    tables = kls.list_registered_tables(conn, max_axis_values=3)
    t10 = next(t for t in tables if t["tbl_id"] == "T10")
    values = t10["axis_hints"][0]["values"] if t10["axis_hints"] else []
    _check("max_axis_values=3으로 3개만 담김(10개 중)", len(values) == 3, str(values))
    conn.close()


def _seed_single_root_table(conn):
    """[2026-08-21 실측 발견 - PPI 3종 스트레스 테스트 준비 중 재현]
    최상위(parent_code IS NULL) 값이 "총지수" 하나뿐이고, 실제로 표를
    구분해주는 이름은 그 자식 레벨에 있는 실제 사례(생산자물가지수
    품목별/기본분류/특수분류, DT_404Y014/016/015)를 그대로 합성 재현."""
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T12', '합성 생산자물가지수(특수분류)', NULL)")
    _seed_confusable_twin(conn, "9", "T12-TWIN", "합성 생산자물가지수(기본분류)")
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T12', 'ITEM', 0, '항목', 'IT1', '생산자물가지수', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T12', 'G', 1, '계정코드별', 'ROOT', '총지수', NULL, NULL)"
    )
    for code, name in [("G01", "식료품구분"), ("G02", "에너지구분"), ("G03", "IT구분")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T12', 'G', 1, '계정코드별', ?, ?, 'ROOT', NULL)",
            (code, name),
        )
    conn.commit()


def test_axis_hints_drills_one_level_when_root_is_single_degenerate_node():
    """최상위 값이 "총지수" 하나뿐이면(구분력 없음) 그 자식 레벨("에너지구분"
    등, 실제로 표를 구분해주는 이름)로 한 단계 내려가야 한다 - 실측
    (생산자물가지수 3종)으로 발견된 문제의 회귀 테스트."""
    conn = wh.get_connection(":memory:")
    _seed_single_root_table(conn)
    tables = kls.list_registered_tables(conn)
    t12 = next(t for t in tables if t["tbl_id"] == "T12")
    values = t12["axis_hints"][0]["values"] if t12["axis_hints"] else []
    _check(
        "단일 루트('총지수') 대신 자식 레벨(에너지구분 등)로 내려감",
        set(values) == {"식료품구분", "에너지구분", "IT구분"},
        str(values),
    )
    _check("구분력 없는 '총지수' 자체는 axis_hints에 안 남음", "총지수" not in values, str(values))


def test_axis_hints_keeps_top_level_when_multiple_roots_exist():
    """[회귀 확인] 최상위 값이 2개 이상이면(이미 구분력 있음, 기존
    사례들) 자식으로 내려가지 않고 그대로 써야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_hierarchical_table(conn)
    tables = kls.list_registered_tables(conn)
    t9 = next(t for t in tables if t["tbl_id"] == "T9")
    values = t9["axis_hints"][0]["values"] if t9["axis_hints"] else []
    _check(
        "최상위 값이 여러 개면(3개) 그대로 유지, 리프로 안 내려감",
        set(values) == {"01 식료품", "02 주류 및 담배", "03 의류"},
        str(values),
    )


def _seed_ppi_leaf_specificity_pair(conn):
    """[2026-08-22 갱신 - max_depth 실측 반증 후 leaf_samples로 교체]
    DT_404Y016(품목별)/DT_404Y014(기본분류) 실측 구조를 압축 재현한다 -
    둘 다 최상위는 "총지수" 하나뿐이고 그 자식도 이름이 같은
    "식료품구분"이라 axis_hints의 최상위 값 샘플만으로는 구분이 안
    된다. 실측(사용자가 로컬 DB를 직접 덤프해서 확인)으로는 "트리
    깊이"가 세분화 정도를 나타낸다는 가정 자체가 틀렸다 - 오히려
    014(T13)가 016(T14)보다 트리는 한 단계 더 깊은데도(식료품구분 밑에
    "곡물"이라는 중간 레벨을 하나 더 거침) 리프는 여전히 "곡류"라는
    분류군 이름이고, 016(T14)은 더 얕은데도 리프가 "쌀"이라는 구체적
    개별 품목이다 - 실제 016/014의 역전 관계(014가 더 깊지만 016이 더
    구체적)를 그대로 미러링해서, "깊이로 판단"이 이 쌍에선 거꾸로임을
    회귀로 고정한다."""
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('301', 'T13', '합성 생산자물가지수(기본분류)', NULL)")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('301', 'T14', '합성 생산자물가지수(품목별)', NULL)")
    for tbl_id in ("T13", "T14"):
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('301', ?, 'ITEM', 0, '항목', 'IT1', '생산자물가지수', NULL, NULL)",
            (tbl_id,),
        )
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('301', ?, 'G', 1, '계정코드별', 'ROOT', '총지수', NULL, NULL)",
            (tbl_id,),
        )
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('301', ?, 'G', 1, '계정코드별', 'G01', '식료품구분', 'ROOT', NULL)",
            (tbl_id,),
        )
    # T13(기본분류) - 한 단계 더 내려가지만(트리가 더 깊지만) 리프는
    # 여전히 분류군 이름("곡류")이다 - 016보다 깊은데도 덜 구체적인
    # 실측 역전 관계를 재현.
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('301', 'T13', 'G', 1, '계정코드별', 'G0101', '곡물', 'G01', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('301', 'T13', 'G', 1, '계정코드별', 'G010101', '곡류', 'G0101', NULL)"
    )
    # T14(품목별) - T13보다 한 단계 얕지만(트리는 덜 깊지만) 리프가
    # 바로 개별 품목명("쌀","사과")이다.
    for code, name in [("G0101", "쌀"), ("G0102", "사과")]:
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('301', 'T14', 'G', 1, '계정코드별', ?, ?, 'G01', NULL)",
            (code, name),
        )
    conn.commit()


def test_axis_hints_leaf_samples_expose_specificity_even_when_depth_is_inverted():
    """values(최상위 값 샘플)만으로는 두 표가 겹쳐 보이고, 트리 깊이는
    오히려 거꾸로(T13이 더 깊음)지만, leaf_samples를 보면 어느 표가
    개별 품목 단위인지가 그대로 드러나야 한다."""
    conn = wh.get_connection(":memory:")
    _seed_ppi_leaf_specificity_pair(conn)
    tables = kls.list_registered_tables(conn)
    t13 = next(t for t in tables if t["tbl_id"] == "T13")
    t14 = next(t for t in tables if t["tbl_id"] == "T14")
    values13 = t13["axis_hints"][0]["values"] if t13["axis_hints"] else []
    values14 = t14["axis_hints"][0]["values"] if t14["axis_hints"] else []
    _check(
        "최상위 값 샘플만으로는 두 표가 겹쳐 보임(둘 다 식료품구분)",
        set(values13) == set(values14) == {"식료품구분"},
        f"T13={values13} T14={values14}",
    )
    leaf13 = t13["axis_hints"][0].get("leaf_samples") if t13["axis_hints"] else None
    leaf14 = t14["axis_hints"][0].get("leaf_samples") if t14["axis_hints"] else None
    _check("기본분류(T13)의 리프 샘플은 분류군 이름(곡류)", leaf13 == ["곡류"], str(leaf13))
    _check(
        "품목별(T14)의 리프 샘플은 개별 품목명(쌀/사과) - 트리는 T13보다 얕지만 더 구체적",
        leaf14 is not None and set(leaf14) == {"쌀", "사과"},
        str(leaf14),
    )
    conn.close()


def test_axis_leaf_samples_helper_direct():
    """_axis_leaf_samples 자체를 단순 3단계 트리로 직접 검증한다 -
    중간 노드(자식이 있는 노드)는 리프가 아니므로 제외돼야 한다."""
    conn = wh.get_connection(":memory:")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T15', '리프 샘플 테스트', NULL)")
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T15', 'G', 1, '분류', 'A', '뿌리', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T15', 'G', 1, '분류', 'B', '중간', 'A', NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T15', 'G', 1, '분류', 'C', '리프', 'B', NULL)"
    )
    conn.commit()
    leaves = kls._axis_leaf_samples(conn, "9", "T15", "G")
    _check("리프('리프')만 포함, 뿌리/중간은 제외", leaves == ["리프"], str(leaves))
    conn.close()


def test_axis_hints_omits_leaf_samples_when_axis_is_flat():
    """축 전체가 평면(부모-자식 관계가 아예 없음)이면 - 즉 최상위 값
    자체가 이미 리프면 - leaf_samples를 중복으로 안 붙여야 한다.
    max_axis_values 캡 테스트에 쓰인 T10(10개 전부 parent_code NULL,
    자식 없음)을 그대로 재사용한다."""
    conn = wh.get_connection(":memory:")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T16', '평면 축 표', NULL)")
    _seed_confusable_twin(conn, "9", "T16-TWIN", "평면 축 표(변형)")
    for i in range(3):
        conn.execute(
            "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES ('9', 'T16', 'G', 1, '분류', ?, ?, NULL, NULL)",
            (f"G{i}", f"항목{i}"),
        )
    conn.commit()
    tables = kls.list_registered_tables(conn)
    t16 = next(t for t in tables if t["tbl_id"] == "T16")
    _check(
        "평면 축은 leaf_samples 키 자체가 안 붙음(values와 중복 방지)",
        "leaf_samples" not in (t16["axis_hints"][0] if t16["axis_hints"] else {}),
        str(t16["axis_hints"]),
    )
    conn.close()


def test_table_name_similarity_flags_known_confusable_pairs():
    """[2026-08-24 신규 - 사용자 지적("DB는 계속 커질 텐데 모든 표를 다
    보여주는 건 아니다") 대응] _table_name_similarity/_compute_confusable_
    flags의 임계값(0.3)이 실제로 문제가 됐던 두 쌍(PPI 품목별/기본분류,
    CPI 지출목적별/품목성질별)을 확실히 넘기는지, 완전히 무관한 이름
    쌍은 확실히 못 넘기는지 실제 표 이름 문자열로 검증한다 - 임의의
    합성 이름이 아니라 이 프로젝트에서 실측으로 확인된 진짜 confusable
    사례를 그대로 쓴다."""
    ppi_sim = kls._table_name_similarity("생산자물가지수(품목별)", "생산자물가지수(기본분류)")
    cpi_sim = kls._table_name_similarity("지출목적별 소비자물가지수", "품목성질별 소비자물가지수")
    unrelated_sim = kls._table_name_similarity(
        "GDP 대비 일반정부 총금융부채 비율", "성/연령별 경제활동인구",
    )
    _check(
        "PPI 품목별/기본분류 쌍은 임계값(0.3)을 넉넉히 넘음",
        ppi_sim >= kls._CONFUSABLE_NAME_SIMILARITY_THRESHOLD, str(ppi_sim),
    )
    _check(
        "CPI 지출목적별/품목성질별 쌍도 임계값을 넉넉히 넘음",
        cpi_sim >= kls._CONFUSABLE_NAME_SIMILARITY_THRESHOLD, str(cpi_sim),
    )
    _check(
        "완전히 무관한 표 이름 쌍은 임계값에 한참 못 미침",
        unrelated_sim < kls._CONFUSABLE_NAME_SIMILARITY_THRESHOLD, str(unrelated_sim),
    )


def test_compute_confusable_flags_marks_only_the_overlapping_group():
    """26개 같은 카탈로그 안에 confusable 쌍 하나와 무관한 표 하나가
    섞여 있을 때, confusable 쌍만 True로 표시되고 무관한 표는 False로
    남아야 한다 - 카탈로그 전체가 아니라 진짜 겹치는 이름끼리만
    axis_hints 비용을 쓰게 하는 게 이 기능의 핵심 전제."""
    names = [
        "생산자물가지수(품목별)",
        "생산자물가지수(기본분류)",
        "GDP 대비 일반정부 총금융부채 비율",
    ]
    flags = kls._compute_confusable_flags(names)
    _check("PPI 품목별은 confusable=True", flags[0] is True, str(flags))
    _check("PPI 기본분류도 confusable=True", flags[1] is True, str(flags))
    _check("무관한 GDP 표는 confusable=False", flags[2] is False, str(flags))


def test_list_registered_tables_skips_axis_hints_for_lexically_unique_table():
    """[핵심 회귀] 이름이 다른 어떤 표와도 안 겹치는 표는 dimensions가
    있어도 axis_hints 계산을 건너뛰어 빈 리스트를 반환해야 한다(비용
    절감의 핵심 동작) - 반면 이름이 비슷한 짝이 있는 표는 여전히 실제
    axis_hints를 받아야 한다(recall 손실 없음)."""
    conn = wh.get_connection(":memory:")
    # 이름이 겹치는 짝이 없는 표 - dimensions가 있어도 axis_hints가
    # 계산되면 안 된다.
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T17', '완전히 고유한 이름의 표', NULL)")
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T17', 'G', 1, '분류', 'X1', '값1', NULL, NULL)"
    )
    # 이름이 서로 겹치는 짝 - 정상적으로 axis_hints를 받아야 한다.
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T18', '합성 겹치는표(첫째)', NULL)")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T19', '합성 겹치는표(둘째)', NULL)")
    # [주의] 최상위(parent_code IS NULL) 값이 딱 1개면 "단일 루트 -> 자식
    # 레벨로 드릴다운" 로직이 타서(list_registered_tables 참고) 자식이
    # 없으면 axis_hints가 빈 채로 나온다 - 이 테스트는 confusable 필터
    # 자체를 보려는 것이므로 그 드릴다운 분기를 피하려고 최상위 값을
    # 2개 넣는다(이미 구분력 있는 일반적인 경우).
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T18', 'G', 1, '분류', 'Y1', '값2', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO dimensions (org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
        "VALUES ('9', 'T18', 'G', 1, '분류', 'Y2', '값3', NULL, NULL)"
    )
    conn.commit()
    tables = kls.list_registered_tables(conn)
    t17 = next(t for t in tables if t["tbl_id"] == "T17")
    t18 = next(t for t in tables if t["tbl_id"] == "T18")
    _check(
        "고유한 이름의 표는 dimensions가 있어도 axis_hints가 빈 리스트(비용 절감)",
        t17["axis_hints"] == [], str(t17["axis_hints"]),
    )
    _check(
        "이름이 겹치는 표는 axis_hints가 정상적으로 채워짐(recall 유지)",
        t18["axis_hints"] != [], str(t18["axis_hints"]),
    )
    conn.close()


def test_table_without_dimensions_gets_empty_axis_hints():
    """dimensions가 아직 안 채워진 표(적재 직후 등)도 예외 없이 빈
    axis_hints로 나와야 한다."""
    conn = wh.get_connection(":memory:")
    conn.execute("INSERT INTO tables_registry (org_id, tbl_id, tbl_nm, stat_nm) VALUES ('9', 'T11', '빈 표', NULL)")
    conn.commit()
    tables = kls.list_registered_tables(conn)
    t11 = next(t for t in tables if t["tbl_id"] == "T11")
    _check("dimensions 없는 표는 axis_hints가 빈 리스트", t11["axis_hints"] == [], str(t11["axis_hints"]))
    conn.close()


if __name__ == "__main__":
    test_axis_hints_include_only_top_level_values()
    test_axis_hints_exclude_item_axis()
    test_axis_hints_respects_max_axis_values_cap()
    test_axis_hints_drills_one_level_when_root_is_single_degenerate_node()
    test_axis_hints_keeps_top_level_when_multiple_roots_exist()
    test_axis_hints_leaf_samples_expose_specificity_even_when_depth_is_inverted()
    test_axis_leaf_samples_helper_direct()
    test_axis_hints_omits_leaf_samples_when_axis_is_flat()
    test_table_name_similarity_flags_known_confusable_pairs()
    test_compute_confusable_flags_marks_only_the_overlapping_group()
    test_list_registered_tables_skips_axis_hints_for_lexically_unique_table()
    test_table_without_dimensions_gets_empty_axis_hints()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

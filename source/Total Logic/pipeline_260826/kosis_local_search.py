"""[2026-08-16 신규, 2026-08-16 아키텍처 수정] kosis_warehouse.py에 적재된
로컬 DB에서 뉴스 claim에 맞는 표를 찾는 검색 엔진 프로토타입 - 완전 규칙
기반(LLM 호출 없음, 사용자 결정: "규칙 기반으로 후보를 좁힌 뒤 최종 1개까지
규칙으로 고른다").

## [2026-08-16 아키텍처 수정 기록] 분류 로직이 여기 있는 이유

원래는 "국제기구 표 여부"(is_international)와 "지수/등락률 분류"
(measure_type)를 kosis_warehouse.py의 적재 스키마(tables_registry/
dimensions)에 직접 저장했었다. 사용자 지적: 적재 계층은 KOSIS 원본
데이터를 flat하게 미러링만 해야 하는데, 그 두 값은 내가 만든 해석/판단
규칙이라 "적재"가 아니라 "검색"의 영역이었다. 그래서 두 판별 함수
(`is_international_survey`, `_infer_measure_type`)와 그 마커 상수를
kosis_warehouse.py에서 여기로 옮겼다 - warehouse는 vw_cd/stat_nm/name/
unit_hint 같은 KOSIS 원본 필드만 저장하고, "이게 국제기구 표냐/등락률
항목이냐"는 이 파일이 검색할 때마다 그 원본 필드를 보고 그때그때 계산한다
(DB에 저장/영구화하지 않는다 - 분류 규칙이 바뀌면 재적재 없이 바로 반영됨).

## 왜 이 설계인가

이번 세션에 KOSIS 실제 데이터로 진단한 두 가지 반복 오류 패턴을 점수화
규칙으로 직접 반영한다:

1. **국제기구 표 vs 국내 표 혼동** - 원문장이 국내 얘기인데 이름이 비슷한
   국제기구 표(DT_2IFS002 등, IMF/OECD)가 선택되는 문제. `tables_registry`의
   원본 필드(vw_cd, stat_nm)를 `is_international_survey()`로 검사해서,
   원문장에 국제/해외 언급이 없으면 감점한다.
2. **지수(index) vs 등락률(rate_of_change) 혼동** - run04_result.json
   실측에서 MISMATCH 12건 중 8건이 "claim은 %(등락률)를 주장하는데
   골라진 항목은 지수(2020=100)"였던 문제. `dimensions`의 원본 필드
   (name, unit_hint)를 `_infer_measure_type()`으로 검사해서, 원문장에
   등락률/증감률 의도가 있으면 rate_of_change로 분류되는 항목을 가진
   표에 가점, 없으면 감점한다.

## 검색 흐름

1. keywords(또는 원문장 토큰화)로 `dimensions_fts`(FTS5)를 MATCH해서
   후보 (org_id, tbl_id, obj_id, code, name) 행을 모은다.
2. 표 단위로 묶어서 tables_registry를 조인, 원본 필드를 검색 시점에
   해석해서 위 두 규칙으로 점수를 가감.
3. 점수 내림차순으로 top_n개 반환.

## 항목/축 확정 - 두 가지 방식, 하나는 DEPRECATED

표 후보 다음 단계(phrase -> 실제 facts 값)를 만드는 데 두 가지 설계를
시도했다.

**[DEPRECATED] resolve_keyword_group_in_table / resolve_evidence_in_table** -
레거시 kosis_resolution.py 로직을 그대로 포팅한 첫 시도. dimensions를
축별로 트리 타고 내려가며 phrase마다 "이건 몇 번 축이다"를 판정한다.
실제 KOSIS 표(사용자가 지정한 DT_113_STBL_1024687 "독서", DT_343_2010_
S0043 "유가증권 순위별 거래", DT_343_2010_S0027 "코스피 지수")로 검증
하다가 세 가지 실측 버그를 발견했다(item 이름에 흔한 단어가 겹치면
축값 phrase가 item으로 잘못 흡수됨, 헤더 vs leaf 오판, 같은 축을 놓고
무관한 두 phrase가 충돌해도 감지 못 하고 조용히 덮어씀 - 각 함수
docstring과 test_kosis_evidence_resolution.py에 실측 그대로 기록돼
있다). 원인은 근본적으로 다루기 까다로운 설계라 판단해 더 고치지 않고
**폐기**하기로 했다(사용자 결정, 2026-08-16) - 코드는 어떻게 이 문제에
접근했었는지 참고용으로만 남겨두고, 새 코드에서 호출하지 않는다.

**[현재 권장] resolve_evidence_by_flat_match** - 트리를 축별로 내려가며
판정하는 대신, `facts`에 이미 실제 값이 있는 (itm_id, c1~c8) 조합만을
후보로 삼아 item명+축 전체 breadcrumb(부모 이름부터 자기 이름까지)를
합친 텍스트에 phrase가 몇 개나 겹치는지로 순위를 매긴다(사용자 제안,
2026-08-16). 위 세 가지 버그가 이 설계에서는 애초에 생기지 않는다는
걸 같은 세 표로 재검증했다 - "facts에 실제로 존재하는 조합만 본다"는
전제 자체가 헤더/leaf 판별을 필요 없게 만들고, "전체 텍스트 겹침"으로
채점하는 방식이 item/축값 흡수·축 충돌 문제를 우회한다.

## 뉴스 원문 -> KOSIS phrase 앞단(아직 없음)

phrase(claim에서 뽑은 키워드 묶음)가 이미 있다고 가정하고 그 다음
단계만 다룬다 - "원문장 자체를 어떻게 KOSIS가 알아들을 phrase로
바꾸는가"는 별도 문제로 남겨뒀다(VDB 유사도 기반 확장 논의 있음,
사용자와 별도로 다루기로 함).

아직 없는 것(다음 단계로 미룸): topic_root 활용(적재된 표들의 full_path_id가
대부분 비어 있어 아직 신호가 약함), 위 앞단 문제.

[2026-08-16] resolve_evidence_by_flat_match의 동점 문제("유가증권" 표의
"거래량"처럼 phrase 개수만 세면 못 가르던 것) - 사용자 지적대로 별도
가중치 설계가 필요한 게 아니라, "겹친 phrase *개수*"가 아니라 "겹친
*횟수*"를 점수로 쓰면 저절로 풀린다(부모+자식 이름이 같은 셀은 breadcrumb에
그 이름이 두 번 들어가 자연히 점수가 높다) - 반영 완료.
"""

import re
import sqlite3
from typing import Any, Dict, List, Optional

from kosis_text_utils import TextUtilsMixin

# ---------------------------------------------------------------------------
# [2026-08-16 kosis_warehouse.py에서 이동] 국제기구 표 판별 - 적재 스키마가
# 아니라 여기서, 검색할 때마다 원본 vw_cd/stat_nm 필드를 보고 계산한다.
# ---------------------------------------------------------------------------
_INTERNATIONAL_VW_CDS = ("MT_RTITLE",)
_INTERNATIONAL_STAT_NM_MARKERS = (
    "IMF", "OECD", "국제통계연감", "세계은행", "World Bank",
    "신남방", "신북방", "UN ", "WTO",
)


def is_international_survey(vw_cd: Optional[str], stat_nm: Optional[str]) -> bool:
    """new_kosis_resolution.py의 NewKosisResolver._is_international_survey와
    같은 판별 기준(로직 중복은 다음 단계 정리 과제로 남겨둠 - README.md 9.3).
    DB에 저장하지 않고 검색할 때마다 원본 vw_cd/stat_nm으로 계산한다."""
    if vw_cd in _INTERNATIONAL_VW_CDS:
        return True
    stat_nm = stat_nm or ""
    return any(marker in stat_nm for marker in _INTERNATIONAL_STAT_NM_MARKERS)


# ---------------------------------------------------------------------------
# [2026-08-16 kosis_warehouse.py에서 이동] 지수/등락률/구성비 분류 - 적재
# 스키마가 아니라 여기서, 검색할 때마다 원본 name/unit_hint 필드를 보고
# 계산한다.
# ---------------------------------------------------------------------------
_RATE_OF_CHANGE_MARKERS = (
    "등락률", "증감률", "변동률", "증가율", "감소율", "성장률", "상승률", "하락률",
    # [2026-08-16 실측 보강] 실제 적재된 DT_1J22041("연도별 소비자물가
    # 등락률")의 진짜 ITEM 행 이름은 "등락률"이 아니라 "전년비"였다 -
    # KOSIS는 "등락률"이라는 단어 자체보다 "전년비/전월비/전기비"류
    # 표기를 컬럼명으로 훨씬 더 흔하게 쓴다는 걸 실데이터로 확인.
    "전년비", "전월비", "전기비", "전분기비", "동월비", "동기비",
    "전년동월비", "전년동기비", "전년동월대비", "전년대비",
)
_INDEX_MARKERS = ("=100", "지수")
_SHARE_MARKERS = (
    "구성비", "비중", "점유율",
    # [2026-08-16 실측 보강] DT_2OEEO029의 ITEM 행 "GDP 대비 일반정부
    # 총금융부채 비율"이 unit_hint도 없고 등락률/지수 마커도 없어
    # 'level'로 잘못 분류됐다 - "비율"은 등락(변화)이 아니라 수준값
    # 비율(share/ratio)이므로 여기 분류가 맞다.
    "비율",
)


def _infer_measure_type(itm_nm: Optional[str], unit_hint: Optional[str]) -> str:
    """[2026-08-16 신규] ITEM 행 하나가 "지수값"인지 "%등락률"인지
    "절대값"인지를 이름/단위 힌트만으로 규칙 기반 추론한다(LLM 없음).
    DB에 저장하지 않고 검색할 때마다 계산한다.

    왜 필요한가: run04_result.json 90건 실측에서 MISMATCH 12건 중 8건이
    "claim은 %(등락률)를 주장하는데 표는 지수(2020=100)를 돌려줬다"는
    이 축의 문제였다(README.md 9.3 논의 참고) - 그중 절반은 국제기구 표
    오탐도 아니고 정확히 맞는 국내 표(수입물가지수/생산자물가지수)에서도
    발생했다. 즉 "표를 잘못 골랐다"보다 "표 안에서 항목을 잘못 골랐다"가
    더 큰 오류 소스였다.

    분류 우선순위: 등락률류 단어 > 지수(=100/지수) > 구성비류 > 단위가
    '%'인 나머지(비율이지만 등락률인지 불확실) > 그 외(절대값 - 원/명/톤 등).
    확신 없는 경우를 추측으로 단정하지 않기 위해, 마지막 'level'은 사실상
    "판별 안 됨(기본값)"에 가깝다 - 이 태그를 신뢰도 100%로 쓰지 말고
    참고 신호 중 하나로만 써야 한다.
    """
    text = f"{itm_nm or ''} {unit_hint or ''}"
    if any(m in text for m in _RATE_OF_CHANGE_MARKERS):
        return "rate_of_change"
    if any(m in text for m in _INDEX_MARKERS):
        return "index"
    if any(m in text for m in _SHARE_MARKERS):
        return "share"
    if unit_hint and "%" in unit_hint:
        return "percentage"
    return "level"


_RATE_INTENT_MARKERS = (
    "%", "퍼센트", "포인트",
    "등락률", "증감률", "변동률", "증가율", "감소율", "상승률", "하락률",
    "전년비", "전월비", "전기비", "전년동월비", "전년대비",
    "올랐다", "내렸다", "늘었다", "줄었다", "증가", "감소", "상승", "하락",
)
_INTERNATIONAL_INTENT_MARKERS = (
    "국제", "해외", "세계", "글로벌", "각국", "선진국", "IMF", "OECD", "UN", "WTO",
)

# [2026-08-18 실측 발견 - 48개 claim 재검증] "생산자물가지수(품목별)"
# (DT_404Y016)이 소비자용 식품 뉴스("케이크(31.7%) ... 크게 오르며")에
# 잘못 매칭된 사례가 나왔다 - "케이크"라는 구체적 품목명이 생산자물가/
# 소비자물가 여러 표에 똑같이 존재해서(둘 다 KOSIS 공식 국내표), 텍스트
# 매칭만으로는 아예 구별이 안 되고 점수까지 정확히 동점이었다(실측:
# DT_404Y016/DT_1J22001/DT_1J22112 셋 다 4.417...로 동점, 뭐가 1등으로
# 뽑히는지는 우연). 생산자물가지수(도매/출하 단계 가격)와 소비자물가지수
# (가구가 실제로 지불하는 소매 가격)는 완전히 다른 개념인데, 뉴스 원문이
# "제조사"/"도매"/"출하가" 같은 생산자 쪽 신호를 명시하지 않는 한 일반
# 소비자 대상 "물가" 뉴스는 거의 항상 소비자물가지수를 뜻한다 - is_
# international과 같은 방식(명시적 신호 없으면 감점)으로 다룬다.
_PRODUCER_PRICE_TBL_NM_MARKERS = ("생산자물가", "수입물가", "수출물가")
_PRODUCER_PRICE_INTENT_MARKERS = (
    "생산자", "도매", "출하", "제조업체", "공장도", "수입", "수출",
)


# =============================================================================
# [2026-08-16 신규] 항목/축 확정 - 표 후보가 정해진 "다음" 단계.
#
# 레거시 kosis_resolution.py의 resolve_keyword_group_in_table(2026-08-10
# 완성, 사용자 확인: "해당 로직 정확도가 꽤 높았고")을 그대로 옮겨온다 -
# 새로 설계하지 않는다. 원래는 라이브 getMeta(type=ITM) 응답(list of dict,
# ITM_ID/ITM_NM/OBJ_ID/OBJ_ID_SN/UP_ITM_ID 키)을 대상으로 동작했는데, 이제
# 그 응답이 이미 로컬 dimensions 테이블에 그대로 적재돼 있으므로(같은
# 필드가 obj_id/axis_position/code/name/parent_code로 이름만 바뀌어 저장됨)
# API를 다시 부르지 않고 SQLite 조회로 완전히 대체한다.
#
# 핵심 아이디어(README.md 2.6/2.7절): "이 phrase가 몇 번째 축 자리인지"를
# 순열로 추측하지 않는다 - 메타가 이미 갖고 있는 라벨(OBJ_ID=='ITEM' 여부,
# axis_position, parent_code)을 그대로 활용해 phrase마다 어느 쪽에 이름이
# 걸리는지로 타입이 자동으로 갈린다. 동명이의(같은 리프 이름, 다른 부모)는
# 이미 확정된 다른 phrase의 부모 그룹과 일치하는 후보로 좁히고, "넓은
# phrase가 구체적 phrase의 조상이면 넓은 쪽은 중복이라 건너뛴다"(subsumption)
# 는 parent_code 체인으로 판별한다 - 이것도 순열로는 못 잡는 문제였다.
#
# [범위상 생략한 부분 - 정직하게 명시] 레거시의 _resolve_leaf_row는 "헤더
# 자식 중 이름이 부모와 완전히 같은 경우"에 한해 폴백 코드를 별도로
# 기록해두고 실제 fetch가 err30(데이터 없음)을 낼 때만 그걸로 재시도하는,
# 여러 번의 실측 수정을 거친 미묘한 분기가 있다(위 주석 참고). 이번
# 포팅에서는 그 "동일 이름 폴백 재시도" 분기는 가져오지 않았다 - 대신
# 더 단순하게: 헤더에 합계/소계/전체/계 라벨의 자식이 있으면 그 자식으로
# 대체하고, 없으면 헤더 자체를 그대로 쓴다(추측 안 함). 실제 서비스에
# 연결하기 전에 이 단순화가 실제 표에서 문제를 일으키는지 다시 실측
# 검증이 필요하다.
# =============================================================================

# [2026-08-22 실측 발견 - 조선비즈 "5년간 먹거리 물가 20% 이상 상승"
# (2025-10-08) 기사 기반 C003/C004 완전 claim 재구성 작업 중, KOSIS
# kosis_table_info(101/DT_1J22001, type=ITM, objId=I) 실측 조회로 확인]
# 이 표(지출목적별 소비자물가지수)의 총계 leaf 이름은 "합계/전체" 계열이
# 아니라 "총지수"였다 - 기존 목록에 없어서 _axis_total_code가 못 찾았을
# 것이므로 추가한다(C011 연령대 별칭 목록과 같은 원칙 - 발견될 때마다
# 늘린다, 섣부르게 일반화하지 않는다).
#
# [2026-08-22 추가 실측 - test_local_search_special_tables.py 실행 결과로
# 확인] "총지수"만 추가해도 여전히 못 찾았다 - 로컬 DB 적재 시 이 축의
# name 필드가 KOSIS 코드 접두어를 붙인 그대로("0 총지수") 저장돼 있었다
# (같은 축의 다른 leaf들도 "01 식료품 및 비주류음료"처럼 전부 "코드 이름"
# 형태 - test_hcx_stage1_resolver.py의 axis_hints 예시와 일치). 이 표
# 한정으로 실측된 정확한 문자열을 그대로 추가한다 - "숫자 접두어는 다 뗀다"
# 식으로 일반화하지 않는다(다른 표의 명명 규칙을 아직 실측하지 않았으므로).
_TOTAL_LABEL_MARKERS = ("합계", "소계", "전체", "계", "총지수", "0 총지수")

# [2026-08-17 신규] "계/합계/전체" 같은 총계 라벨이 아예 없는 축(예: 국제
# 비교표의 "국가" 축 - 개별 국가 leaf만 있고 총계 국가란 개념 자체가 없음)
# 에서, claim이 그 축을 언급하지 않았을 때 어떤 leaf를 기본값으로 볼지를
# axis_label 기준으로 정의한다. resolve_evidence_by_flat_match의 tie-break
# (unexplained_axes)에서만 쓰인다 - 값을 강제로 필터링하지 않고, 동점일 때
# "이 값이 근거 없이 우연히 뽑힌 것"으로 감점하지 않는 예외로만 취급한다.
_AXIS_LABEL_DEFAULT_NAME = {
    "국가": "대한민국",
    # [2026-08-18 신규 - 실측 확인] score 축별 1회 dedup 수정 후 드러난
    # 후속 문제: "가정용품 및 가사서비스 물가" 같은 claim은 지역을 언급
    # 안 했는데, 시도별(axis_label="시도별") 축의 leaf 10곳(전국/서울/
    # 부산/...)이 전부 같은 phrase 매칭 근거를 공유해 동점으로 남는다.
    # "국가" 축에 "대한민국"을 기본값으로 이미 등록해둔 것과 똑같은 이유로
    # "시도별" 축에는 "전국"을 기본값으로 등록한다(실측: DT_1J22001의
    # 시도별 leaf 중 "전국"이라는 이름이 정확히 존재함, DB 전체에서
    # axis_label="시도별"은 이 표기 하나뿐임 - 다른 변형 표기는 아직
    # 실측된 바 없어 추가하지 않는다).
    "시도별": "전국",
}


def _fuzzy_contains(nm: Optional[str], keyword: Optional[str]) -> bool:
    """[kosis_text_utils.TextUtilsMixin._fuzzy_contains에서 그대로 이식]
    keyword가 nm(항목/분류명) 안에 부분적으로라도 들어있는지 판단한다.
    단순 부분 문자열 매칭만으로는 "정비사"가 "항공기 정비"에 안 걸려서
    (마지막 "사" 때문), 흔한 직업/개념 접미사를 뗀 core도 함께 비교한다."""
    if not nm or not keyword:
        return False
    if keyword in nm or nm in keyword:
        return True
    for suf in ("사", "직", "원", "공", "가", "인", "자"):
        if keyword.endswith(suf) and len(keyword) > 1:
            core = keyword[:-1]
            if not core:
                continue
            if nm.endswith("업") and not keyword.endswith("업"):
                continue
            if core in nm or nm in core:
                return True
    return False


def _dim_rows_for_table(conn: sqlite3.Connection, org_id: str, tbl_id: str) -> List[Dict[str, Any]]:
    """dimensions 테이블에서 이 표의 행 전부를 dict 리스트로 가져온다 -
    레거시 코드가 getMeta 응답(list of dict)을 다루던 것과 같은 모양으로
    맞춰서, 아래 매칭/판별 로직을 최소한의 수정으로 재사용할 수 있게 한다."""
    rows = conn.execute(
        "SELECT obj_id, axis_position, axis_label, code, name, parent_code, unit_hint "
        "FROM dimensions WHERE org_id=? AND tbl_id=?",
        (org_id, tbl_id),
    ).fetchall()
    return [
        {
            "obj_id": r[0], "axis_position": r[1], "axis_label": r[2],
            "code": r[3], "name": r[4], "parent_code": r[5], "unit_hint": r[6],
        }
        for r in rows
    ]


def _row_group_root(rows_by_code: Dict[str, Dict[str, Any]], row: Dict[str, Any]) -> Dict[str, Any]:
    """parent_code를 따라 최상위 부모까지 올라간다 - breadcrumb/동명이의
    좁히기용(_row_group_root, kosis_resolution.py에서 이식)."""
    current = row
    seen: set = set()
    while True:
        parent_code = current.get("parent_code")
        cur_code = current.get("code")
        if not parent_code or parent_code == cur_code or parent_code in seen or parent_code not in rows_by_code:
            return current
        seen.add(parent_code)
        current = rows_by_code[parent_code]


def _row_ancestor_codes(rows_by_code: Dict[str, Dict[str, Any]], row: Dict[str, Any]) -> set:
    """row 자신을 포함해 parent_code 체인을 타고 루트까지 만나는 모든
    조상 code를 모은다(자기 자신 포함) - "넓은 phrase가 구체적 phrase의
    조상인가"(subsumption) 판별에 쓴다(_row_ancestor_ids에서 이식)."""
    ids: set = set()
    current = row
    seen: set = set()
    while current is not None:
        code = current.get("code")
        if not code or code in seen:
            break
        seen.add(code)
        ids.add(code)
        parent_code = current.get("parent_code")
        current = rows_by_code.get(parent_code) if parent_code else None
    return ids


def _row_children(category_rows: List[Dict[str, Any]], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """row의 직계 자식(parent_code == row.code, 같은 축)만 - 다른 축에
    우연히 같은 code 문자열이 있어도 안 섞이도록 axis_position까지 맞춘다."""
    code = row.get("code")
    axis = row.get("axis_position")
    return [r for r in category_rows if r.get("parent_code") == code and r.get("axis_position") == axis]


def _resolve_leaf_row(category_rows: List[Dict[str, Any]], row: Dict[str, Any]) -> Dict[str, Any]:
    """[kosis_resolution.py._resolve_leaf_row에서 단순화해 이식] row가
    하위 분류값이 있는 헤더/그룹 노드면, 실제 조회 가능한 leaf로 바꾼다.
    자식 중 합계/소계/전체/계 라벨을 우선하고, 그런 이름의 자식이 없거나
    자식이 아예 없으면(이미 leaf) 원래 행을 그대로 쓴다 - 추측(예: "첫
    자식") 대신, 확신 없으면 원래 행을 유지한다(레거시가 여러 번의 실측
    수정 끝에 도달한 원칙, 위 모듈 docstring의 "범위상 생략" 참고 - 부모/
    자식 이름이 완전히 같은 경우의 폴백 재시도 분기는 이번엔 안 가져왔다)."""
    children = _row_children(category_rows, row)
    if not children:
        return row
    for label in _TOTAL_LABEL_MARKERS:
        preferred = next((c for c in children if c.get("name") == label), None)
        if preferred:
            return preferred
    return row


def _match_phrase_in_rows(rows: List[Dict[str, Any]], phrase: str) -> List[Dict[str, Any]]:
    """[_match_phrase_in_rows에서 이식] phrase가 rows(item_rows 또는
    category_rows) 중 이름으로 어디에 걸리는지 찾는다. 정확일치 우선,
    없으면 fuzzy. fuzzy 매칭이 여러 후보를 걸었는데 한 후보 이름이 다른
    후보 이름의 부분 문자열이면(예: "산업" vs "문화산업") 더 짧고
    포괄적인 쪽은 정보량이 적으므로 제외하고 더 구체적인 이름만 남긴다."""
    if not phrase:
        return []
    exact = [r for r in rows if r.get("name") == phrase]
    if exact:
        return exact
    fuzzy = [r for r in rows if _fuzzy_contains(r.get("name"), phrase)]
    if len(fuzzy) <= 1:
        return fuzzy
    names = {id(r): r.get("name") for r in fuzzy}

    def _subsumed_by_another(r: Dict[str, Any]) -> bool:
        nm = names[id(r)]
        return any(
            id(other) != id(r) and nm != other_nm and nm in other_nm
            for other, other_nm in ((o, names[id(o)]) for o in fuzzy)
        )

    specific = [r for r in fuzzy if not _subsumed_by_another(r)]
    return specific or fuzzy


def resolve_keyword_group_in_table(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, phrases: List[str]
) -> Dict[str, Any]:
    """[DEPRECATED 2026-08-16 - resolve_evidence_by_flat_match를 대신
    쓸 것. 실측 3건(item 흡수/헤더-leaf 오판/축 충돌 무감지 - 모듈
    docstring 참고)으로 폐기 결정됨. 새 코드에서 호출하지 말 것 - 참고용
    으로만 남겨둠.]

    [resolve_keyword_group_in_table에서 로컬 DB 기반으로 포팅] 표가
    이미 확정된 뒤, 느슨한 phrase 묶음(예: "여자"/"20-29세"/"20-24세")을
    이 표의 실제 dimensions 구조에 매칭해서 itm_id + 축값(obj_axes)을
    확정한다.

    1) phrase마다 item 행에 먼저 매칭을 시도하고, 안 걸리면 축값(category)
       행에 매칭한다 - 이름이 어느 쪽에 걸리는지로 타입이 자동으로 갈린다.
    2) 축값 후보가 정확히 1개(동명이의 없음)인 phrase들의 breadcrumb 루트를
       "이미 확정된 컨텍스트"로 모은다.
    3) 축값 후보가 여러 개(동명이의)인 phrase는 확정된 컨텍스트와 루트가
       같은 후보로 좁힌다(그래도 여러 개면 첫 후보로 최후 폴백).
    4) fuzzy로 매칭된 축값 후보는 leaf로 보정한다(_resolve_leaf_row) -
       정확히 이름이 일치한 경우는 사용자/phrase 생성 단계가 이미 그
       행을 정확히 의도한 것이므로 보정하지 않고 그대로 신뢰한다.
    5) "학교급성별"(넓은 개념)이 "남"(그 밑 구체적인 값)의 조상이면, 더
       구체적인 phrase가 이미 같은 걸 표현하므로 넓은 phrase는 건너뛴다
       (subsumption, parent_code 체인으로 판별).

    반환: {"itm_id", "itm_nm", "obj_axes": {axis_position: code},
    "unresolved": [phrase, ...]} - unresolved는 item/category 어느 쪽에도
    안 걸린 phrase(사전에 없는 표현) - 추측하지 않고 그대로 넘긴다."""
    rows = _dim_rows_for_table(conn, org_id, tbl_id)
    item_rows = [r for r in rows if r["obj_id"] == "ITEM"]
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]
    rows_by_code = {r["code"]: r for r in category_rows if r.get("code")}

    per_phrase: Dict[str, Dict[str, Any]] = {}
    for phrase in phrases or []:
        if not phrase:
            continue
        item_matches = _match_phrase_in_rows(item_rows, phrase)
        if item_matches:
            per_phrase[phrase] = {"type": "item", "candidates": item_matches, "exact": item_matches[0]["name"] == phrase}
            continue
        cat_matches = _match_phrase_in_rows(category_rows, phrase)
        if cat_matches:
            per_phrase[phrase] = {"type": "category", "candidates": cat_matches, "exact": cat_matches[0]["name"] == phrase}
        else:
            per_phrase[phrase] = {"type": None, "candidates": [], "exact": False}

    # 2단계: 동명이의 없는 category phrase의 루트를 "확정된 컨텍스트"로 모은다.
    confirmed_root_codes: set = set()
    for info in per_phrase.values():
        if info["type"] == "category" and len(info["candidates"]) == 1:
            root = _row_group_root(rows_by_code, info["candidates"][0])
            if root.get("code"):
                confirmed_root_codes.add(root["code"])

    # 3~4단계: 동명이의 좁히기 + leaf 보정(fuzzy 매칭만).
    category_anchor: Dict[str, Dict[str, Any]] = {}
    for phrase, info in per_phrase.items():
        if info["type"] != "category":
            continue
        candidates = info["candidates"]
        if len(candidates) > 1 and confirmed_root_codes:
            narrowed = [
                c for c in candidates
                if _row_group_root(rows_by_code, c).get("code") in confirmed_root_codes
            ]
            if narrowed:
                candidates = narrowed
        anchor = candidates[0]
        if not info["exact"]:
            anchor = _resolve_leaf_row(category_rows, anchor)
        category_anchor[phrase] = anchor

    # 5단계: subsumption - 더 구체적인 phrase가 있으면 그 조상 phrase는 건너뛴다.
    subsumed_phrases: set = set()
    anchor_codes = {p: r.get("code") for p, r in category_anchor.items()}
    for p1, r1 in category_anchor.items():
        code1 = anchor_codes[p1]
        for p2, r2 in category_anchor.items():
            if p1 == p2 or code1 == anchor_codes[p2]:
                continue
            if code1 in _row_ancestor_codes(rows_by_code, r2):
                subsumed_phrases.add(p1)
                break

    itm_id = itm_nm = None
    obj_axes: Dict[int, str] = {}
    unresolved: List[str] = []
    for phrase, info in per_phrase.items():
        if info["type"] is None:
            unresolved.append(phrase)
            continue
        if info["type"] == "item":
            if itm_id is None:
                row = info["candidates"][0]
                itm_id, itm_nm = row.get("code"), row.get("name")
            continue
        if phrase in subsumed_phrases:
            continue
        row = category_anchor[phrase]
        axis = row.get("axis_position")
        code = row.get("code")
        if axis is not None and code:
            obj_axes[axis] = code

    return {"itm_id": itm_id, "itm_nm": itm_nm, "obj_axes": obj_axes, "unresolved": unresolved}


def _axis_total_code(category_rows: List[Dict[str, Any]], axis_position: int) -> Optional[str]:
    """이 축(axis_position)에서 합계/소계/전체/계 라벨을 가진, 최상위(부모
    없는) 코드를 찾는다 - phrase로 채워지지 않은 축의 기본값 폴백용.
    여러 개 있으면(드묾) 첫 번째를 쓴다. 없으면 None(추측하지 않는다)."""
    candidates = [
        r for r in category_rows
        if r.get("axis_position") == axis_position
        and not r.get("parent_code")
        and r.get("name") in _TOTAL_LABEL_MARKERS
    ]
    return candidates[0]["code"] if candidates else None


def resolve_evidence_in_table(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, phrases: List[str]
) -> Dict[str, Any]:
    """[DEPRECATED 2026-08-16 - resolve_evidence_by_flat_match를 대신 쓸
    것. resolve_keyword_group_in_table 기반이라 같은 이유로 폐기됨.]

    resolve_keyword_group_in_table로 itm_id/obj_axes를 확정한 뒤, 실제
    facts 테이블에서 값 후보까지 뽑아내는 상위 래퍼.

    phrase로 못 채운 축은 그 축의 합계/전체 코드가 있으면 기본값으로
    채운다(예: claim이 지역을 언급 안 하면 "전국"). 그런 기본 코드조차
    없는 축은 필터를 안 걸고 그대로 둔다 - 여러 값이 섞여 나올 수 있다는
    뜻이므로 "ambiguous_axes"에 표시해서 호출부가 추측 없이 판단하게 한다.

    item도 phrase로 못 채웠지만 이 표에 ITEM이 정확히 1개뿐이면(단일
    지표 표 - 예: 코스피 지수, 생산자물가지수) 그 하나로 기본값 처리한다
    - 고를 게 하나뿐이라 이건 추측이 아니라 유일해다.
    """
    rows = _dim_rows_for_table(conn, org_id, tbl_id)
    item_rows = [r for r in rows if r["obj_id"] == "ITEM"]
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]

    resolved = resolve_keyword_group_in_table(conn, org_id, tbl_id, phrases)
    itm_id, itm_nm = resolved["itm_id"], resolved["itm_nm"]
    itm_defaulted = False
    if itm_id is None and len(item_rows) == 1:
        itm_id, itm_nm = item_rows[0]["code"], item_rows[0]["name"]
        itm_defaulted = True

    obj_axes = dict(resolved["obj_axes"])
    all_axes = sorted({r["axis_position"] for r in category_rows if r.get("axis_position") is not None})
    defaulted_axes: Dict[int, str] = {}
    ambiguous_axes: List[int] = []
    for axis in all_axes:
        if axis in obj_axes:
            continue
        total_code = _axis_total_code(category_rows, axis)
        if total_code:
            obj_axes[axis] = total_code
            defaulted_axes[axis] = total_code
        else:
            ambiguous_axes.append(axis)

    facts_rows: List[Dict[str, Any]] = []
    if itm_id is not None:
        where = ["org_id=?", "tbl_id=?", "itm_id=?"]
        params: List[Any] = [org_id, tbl_id, itm_id]
        for axis, code in obj_axes.items():
            if 1 <= axis <= 8:
                where.append(f"c{axis}=?")
                params.append(code)
        query = f"SELECT prd_de, prd_se, value, unit FROM facts WHERE {' AND '.join(where)} ORDER BY prd_de"
        facts_rows = [
            {"prd_de": r[0], "prd_se": r[1], "value": r[2], "unit": r[3]}
            for r in conn.execute(query, params).fetchall()
        ]

    return {
        "itm_id": itm_id,
        "itm_nm": itm_nm,
        "itm_defaulted": itm_defaulted,
        "obj_axes": resolved["obj_axes"],
        "defaulted_axes": defaulted_axes,
        "ambiguous_axes": ambiguous_axes,
        "unresolved_phrases": resolved["unresolved"],
        "facts": facts_rows,
    }


# =============================================================================
# [2026-08-16 신규 - 대안 설계, 사용자 제안] "축을 하나씩 트리 타고 내려가며
# 판정" 대신, "이미 실제 값이 있는 facts 셀만" 대상으로 item명+축 전체
# breadcrumb를 합친 텍스트에 phrase가 몇 개나 들어있는지로 순위를 매긴다.
#
# 왜 이게 나은가 - resolve_keyword_group_in_table에서 실측으로 발견한 세
# 가지 문제가 전부 여기선 원천적으로 안 생긴다:
# 1. item 이름에 흔한 단어가 겹치는 문제(코스피 지수의 "지수") - item과
#    축을 미리 분리해서 어느 쪽으로 phrase를 흡수시킬지 판정할 필요가
#    없다. item명+breadcrumb를 다 합친 한 텍스트로 매칭하고, "phrase가
#    몇 개 겹치는가"로 셀 전체를 채점하기 때문에 부분적으로 흡수될
#    일이 없다.
# 2. 헤더 vs leaf 판별(유가증권의 "거래량") - facts에는애초에 KOSIS가 실제
#    값을 준 조합만 있다. 실측 확인(2026-08-16, DT_113_STBL_1024687 getList
#    실제 응답): KOSIS는 헤더 코드 자체에는 값을 안 주고 leaf 조합만
#    돌려준다 - 그러니 "이 헤더가 조회 가능한가"를 추측할 필요 자체가
#    없어진다.
# 3. 같은 축에 무관한 두 phrase가 충돌해도 감지 못 하고 조용히 덮어쓰는
#    문제(독서 표의 "고등학교"+"남") - breadcrumb를 통째로(부모 이름부터
#    자기 이름까지) 텍스트에 넣으므로, "고등학교"와 "남"을 각각 어느
#    축에 넣을지 개별 판정할 필요가 없다 - "학교급*성별 고등학교 남"
#    셀 텍스트 하나가 두 phrase를 동시에 만족하는 유일한 셀이라 자동으로
#    가장 높은 점수를 받는다("학교급*성별 초등학교 남" 셀은 "남"만
#    맞고 "고등학교"는 안 맞아서 점수가 낮다).
#
# 대신 잃는 것: resolve_keyword_group_in_table처럼 "이 phrase가 어느 축인지"
# 개별적으로 알려주지 않는다(축 단위 구조화된 답 대신 셀 단위 랭킹). 그리고
# facts가 아직 없는 표(메타만 적재되고 값은 아직 안 받은 경우)에는 못 쓴다 -
# 반드시 facts까지 적재돼 있어야 한다.
# =============================================================================

def _count_occurrences(text: str, phrase: str) -> int:
    """text 안에 phrase가 몇 번 등장하는지 센다 - 보통은 단순 부분 문자열
    카운트(text.count)면 충분하지만, phrase가 순수 숫자(예: 순위 "1")면
    "11"/"10"/"21" 같은 다른 숫자 코드 안에도 부분 문자열로 걸려버린다
    (실측: 유가증권 순위별 거래 표에서 순위 leaf 이름이 "1".."15" 같은
    맨 숫자라, phrase "1"이 순위 "11"/"10"/"12"..에도 전부 걸려서 순위
    1위 대신 11위가 뽑힘 - 2026-08-17 독서/유가증권 표 재검증 중 발견).
    숫자 phrase는 앞뒤가 숫자가 아닐 때만(=그 숫자 하나로 완결된 토큰일
    때만) 매칭으로 센다. 숫자가 아닌 phrase는 기존처럼 단순 부분 문자열
    카운트를 그대로 쓴다(한글은 이 방식이 여전히 더 실용적 - 형태소
    경계를 안다고 가정할 수 없다)."""
    if not phrase:
        return 0
    if phrase.isdigit():
        return len(re.findall(rf"(?<!\d){re.escape(phrase)}(?!\d)", text))
    direct = text.count(phrase)
    if direct:
        return direct
    # [2026-08-17 실측 발견 - 독서 표] phrase "독서활동경험있음"(공백 없음,
    # 원문 압축 표기)이 실제 KOSIS ITEM명 "독서 활동 경험 있음"(공백 있음)과
    # 전혀 안 겹쳐서 아무 가점도 못 받고, 결국 "사례수"/"계"/"독서 활동
    # 경험 있음" 세 ITEM이 축(고등학교+남) 매칭만으로 동점이 되는 문제가
    # 있었다. 직접 매칭이 0이면 공백을 전부 지운 버전으로 한 번 더 시도한다
    # - 한글은 띄어쓰기가 문법적으로 선택적이라 원문/DB 어느 쪽이 붙여
    # 썼는지 예측할 수 없다(둘 다 "옳은" 표기).
    compact_text = re.sub(r"\s+", "", text)
    compact_phrase = re.sub(r"\s+", "", phrase)
    if compact_phrase and compact_phrase in compact_text:
        return compact_text.count(compact_phrase)
    return 0


def _cell_breadcrumb_text(dims_by_code: Dict[tuple, Dict[str, Any]], row: Dict[str, Any]) -> str:
    """row(리프 분류 코드)부터 parent_code 체인을 타고 올라가며 만나는 모든
    이름을 부모->자식 순서로 이어붙인다(예: "학교급*성별 고등학교 남") -
    리프 이름만 쓰면("남") 같은 축 안에서도 동명이의라 구분이 안 되므로,
    전체 경로를 텍스트에 다 담아서 phrase 매칭이 자동으로 구분하게 한다.

    [2026-08-17 실측 버그 수정] dims_by_code를 code만으로 키를 잡으면 서로
    다른 축(axis_position)에 우연히 같은 code 문자열이 있을 때(실측:
    DT_1DA7E33S_NEW에서 code '37'이 축1(시도별)="경상북도", 축2(산업별)=
    "E 수도 하수 및 폐기물 처리 원료 재생업(36~39)"로 서로 다른 축에 둘 다
    존재) 한쪽이 다른 쪽을 덮어써서 완전히 엉뚱한 이름이 breadcrumb에
    들어간다 - "건설업 취업자 수"(전국) claim이 경상북도의 건설업 값(84천명,
    정답 1959.7천명)으로 잘못 조회되는 실제 오류로 발견됨. parent_code 체인은
    항상 같은 축 안에서만 이어진다는 게 이 코드베이스의 기존 전제
    (`_row_children`이 axis_position까지 맞춰서 찾는 것과 동일 전제)이므로,
    조회 키를 (axis_position, code) 튜플로 바꿔 축 간 충돌을 원천 차단한다."""
    return " ".join(_cell_breadcrumb_chain(dims_by_code, row))


def _cell_breadcrumb_chain(dims_by_code: Dict[tuple, Dict[str, Any]], row: Dict[str, Any]) -> List[str]:
    """_cell_breadcrumb_text와 완전히 같은 parent_code 체인 순회를 쓰지만,
    이어붙인 문자열이 아니라 조상->자식 순서의 이름 리스트를 그대로 반환한다.

    [2026-08-24 신규 - 축 단위 일반화의 2차 수정] 처음엔 leaf_name(이 축의
    자기 자신 이름) 하나만 보고 "구분력 없는 일반 단어"를 판정했는데, 실측
    (DT_1DA7024S)에서 이게 부족하다는 게 드러났다: "이상"이 leaf_name
    '60세이상' 자신에는 걸려서 그 후보는 고쳐졌지만, 그 하위 자식 leaf
    (예: 'ㆍ60 - 64세', 조상이 '60세이상')는 leaf_name 자체엔 "이상"이 없고
    조상 이름에서만 걸리는데, 조상 매치는 이번 수정 이전부터 있던
    ancestor_only_hits 로직과 섞여서 그대로 살아남아 여전히 정답(계/계)을
    이겼다(실측 재확인: '계/601'이 score=2로 1위, unexplained_axes=0으로
    "설명됨" 오판정). 조상 노드("60세이상")도 "65세 이상"/"70세 이상"/
    "75세 이상"과 형제 관계로 같은 계층에 흔하게 반복되는 값이므로, leaf만
    보지 않고 체인 전체의 개별 노드 이름 각각을 "구분력 있는지" 판정 대상에
    넣어야 한다 - 이 함수가 그 개별 노드 접근을 가능하게 한다(합쳐진
    breadcrumb 문자열만으로는 "이 축의 어느 계층 노드가 매치를 일으켰는지"
    다시 분리해낼 수 없어서 원본 리스트가 필요했다)."""
    names: List[str] = []
    current = row
    seen: set = set()
    axis_position = row.get("axis_position")
    while current is not None:
        code = current.get("code")
        if not code or code in seen:
            break
        seen.add(code)
        if current.get("name"):
            names.append(current["name"])
        parent_code = current.get("parent_code")
        current = dims_by_code.get((axis_position, parent_code)) if parent_code else None
    return list(reversed(names))


def iter_table_cell_texts(conn: sqlite3.Connection, org_id: str, tbl_id: str) -> List[Dict[str, Any]]:
    """facts에 실제로 존재하는 (itm_id, c1..c8) 조합 각각을 item명+축 전체
    breadcrumb를 이어붙인 flat text로 만들어 돌려준다. "실제 존재하는 셀
    하나 = flat text 하나"를 만드는 로직 - 원래 resolve_evidence_by_
    flat_match 안에 인라인돼 있었는데, [2026-08-20 신규 - Task #80
    full-row-join 재설계] vdb_discovery.embedding_expand_phrases도 정확히
    같은 셀 열거/텍스트 구성이 필요해져서(item명만으로는 축 값이 claim의
    유일한 판별 정보인 경우 - 예: 연령대 "청년층"->"15~29세" - 를 못
    맞춘다는 사용자 지적, item-only 설계의 한계로 확인됨) 이 함수로
    추출했다. 두 호출부가 서로 다르게 셀을 세면 안 되므로(예: NULL/''
    처리, breadcrumb 조합 방식이 갈리면 embedding 폴백이 찾은 셀과
    resolve_evidence_by_flat_match가 재확인하는 셀이 어긋날 수 있음)
    로직을 한 곳에만 둔다.

    반환: [{"itm_id", "itm_nm", "axis_codes": {axis_position: code},
    "axis_texts": {axis_position: (breadcrumb_text, leaf_name, axis_label)},
    "axis_chains": {axis_position: [조상이름, ..., 리프이름]} (2026-08-24
    신규 - 축 단위 일반화가 "이 축의 어느 계층 노드가 매치를 일으켰는지"
    개별적으로 봐야 해서 추가, 기존 axis_texts는 그대로 유지),
    "segments": [item_nm, axis_text1, ...] (matching 시 세그먼트별로 쓰임),
    "text": "item명 + 축 텍스트 전부 이어붙인 문자열"}, ...] facts 자체가
    없으면(미적재 표) 빈 리스트."""
    rows = _dim_rows_for_table(conn, org_id, tbl_id)
    # [2026-08-17 실측 버그 수정] code만으로 키를 잡으면 서로 다른 축에 같은
    # code 문자열이 우연히 겹칠 때(위 _cell_breadcrumb_text 주석 참고) 한쪽이
    # 덮어써서 엉뚱한 셀이 조회된다 - (axis_position, code) 튜플로 키를 잡아
    # 축 간 충돌을 막는다.
    dims_by_code = {(r["axis_position"], r["code"]): r for r in rows if r.get("code")}
    item_by_id = {r["code"]: r for r in rows if r["obj_id"] == "ITEM"}

    # [2026-08-16 실측 발견] 실제 kosis_warehouse.db의 facts에 NULL과 ''(빈
    # 문자열)이 섞여 있는 축 없는 컬럼이 있다 - 같은 셀인데도 SQL의 DISTINCT는
    # NULL과 ''를 다른 값으로 취급해 중복으로 잡힌다(과거 ingest_facts가
    # NULL을 그대로 저장하던 시절 데이터와, 이후 NULL->'' 보정이 적용된 뒤
    # 재적재된 데이터가 같은 표 안에 섞여 있는 것으로 보인다 - 별도로
    # 사용자에게 보고). 여기서는 COALESCE로 NULL을 ''로 맞춰서 같은 셀을
    # 하나로 합친다(적재 데이터 자체를 고치는 건 아님 - 조회 시점 보정).
    cells = conn.execute(
        "SELECT DISTINCT itm_id, COALESCE(c1,''), COALESCE(c2,''), COALESCE(c3,''), "
        "COALESCE(c4,''), COALESCE(c5,''), COALESCE(c6,''), COALESCE(c7,''), COALESCE(c8,'') "
        "FROM facts WHERE org_id=? AND tbl_id=?",
        (org_id, tbl_id),
    ).fetchall()

    result: List[Dict[str, Any]] = []
    for itm_id, c1, c2, c3, c4, c5, c6, c7, c8 in cells:
        parts: List[str] = []
        item_row = item_by_id.get(itm_id)
        item_nm = item_row.get("name") if item_row else None
        if item_nm:
            parts.append(item_nm)
        axis_codes: Dict[int, str] = {}
        axis_texts: Dict[int, tuple] = {}  # axis_position -> (breadcrumb_text, leaf_name, axis_label)
        axis_chains: Dict[int, List[str]] = {}  # axis_position -> [조상이름, ..., 리프이름]
        for axis_position, code in enumerate([c1, c2, c3, c4, c5, c6, c7, c8], start=1):
            if not code:
                continue
            row = dims_by_code.get((axis_position, code))
            if row:
                chain = _cell_breadcrumb_chain(dims_by_code, row)
                axis_text = " ".join(chain)
                parts.append(axis_text)
                axis_codes[axis_position] = code
                axis_texts[axis_position] = (axis_text, row.get("name"), row.get("axis_label"))
                axis_chains[axis_position] = chain
        result.append({
            "itm_id": itm_id,
            "itm_nm": item_nm,
            "axis_codes": axis_codes,
            "axis_chains": axis_chains,
            "axis_texts": axis_texts,
            "segments": parts,
            "text": " ".join(parts),
        })
    return result


def resolve_evidence_by_flat_match(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, phrases: List[str], top_n: int = 3
) -> List[Dict[str, Any]]:
    """facts에 실제로 존재하는 (itm_id, c1..c8) 조합만을 후보로, 각 조합의
    item명+축 전체 breadcrumb 텍스트에 phrase가 몇 개 겹치는지로 순위를
    매겨 반환한다. resolve_keyword_group_in_table과 달리 트리를 축별로
    타고 내려가며 판정하지 않는다 - 위 모듈 docstring 참고.

    반환: [{"itm_id", "itm_nm", "axis_codes": {axis_position: code}, "text",
    "matched_phrases", "score", "unexplained_axes"}, ...] score(겹친 phrase
    등장 횟수 합) 내림차순, 동점이면 unexplained_axes(phrase로 설명 안 되고
    합계/전체류도 아닌 "우연히 뽑힌" 축 개수) 오름차순 - 정보가 부족해
    총점이 같을 때 근거 없는 축값보다 근거 있거나 기본값(전체/계)인 축값을
    우선한다(2026-08-17, 건설업 취업자 케이스 실측 버그로 발견). 그래도
    동점이면 facts에 먼저 나온 순서. 빈 리스트면 facts 자체가 없거나
    (미적재) 아무 phrase도 안 겹친 것."""
    cell_texts = iter_table_cell_texts(conn, org_id, tbl_id)

    # [2026-08-21 실측 버그 수정 - Task #15, "물가" 범용 토큰 오탐]
    # iter_table_cell_texts는 item_nm(예: "소비자물가지수")을 그 표의
    # 모든 셀 segments에 무조건 포함시킨다(위 함수 정의 참고). item_nm이
    # 표 전체에서 단 하나뿐이면(item_is_uniform) "물가"처럼 item_nm의
    # 부분 문자열인 phrase는 그 표의 후보 전부를 100% 매칭시켜버려서,
    # 실제로는 아무것도 구분해주지 못하는데도 matched_phrases 개수(따라서
    # local_db_agent.py의 weak_literal_tie corroboration 기준 >=2)를
    # 채우는 데 끼어든다. 실측(A93bfa851-C018류): 소비자물가지수 표에서
    # match_phrases=['주류','물가'] 둘 다로 5개 후보가 전부 동점이 됐는데,
    # 진짜 구분력 있는 phrase는 '주류' 하나뿐이었다("물가"는 어차피 이
    # 표의 모든 행이 "소비자물가지수"라 전부 걸림). item_nm이 표 안에서
    # 실제로 여러 값으로 갈리는 표(예: 여러 ITEM이 있는 표)라면 item_nm
    # 매치도 진짜 구분 정보이므로 이 로직에서 제외하지 않는다.
    item_names_in_table = {c["itm_nm"] for c in cell_texts if c.get("itm_nm")}
    item_is_uniform = len(item_names_in_table) <= 1

    phrases = [p for p in (phrases or []) if p]

    # [2026-08-24 실측 버그 수정 - A82ae9f41-C001, "15세 이상 취업자"]
    # item_is_uniform(위)과 같은 원리를 axis 단위로 일반화한다. 실측
    # (DT_1DA7024S 성/연령별 취업자): match_phrases=['15세','이상','취업자']
    # 중 "이상"은 claim의 "15세 이상"(비교사)에서 나온 흔한 단어인데, 이
    # 표의 연령 축에는 "60세이상"/"65세 이상"/"70세 이상"/"75세 이상"처럼
    # 서로 다른(무관한) 임계값 노드 여러 개가 전부 이름 자체에 "이상"을
    # 포함한다.
    #
    # [1차 수정(leaf_name만 봄)의 한계 - 같은 날 실측으로 재수정] 처음엔
    # leaf_name(이 축의 자기 자신 이름) 하나만 보고 판정했다. "계/60세이상"
    # (leaf_name='60세이상' 자신)은 이걸로 고쳐졌지만, 재실행해보니 그 하위
    # 자식 leaf(예: 'ㆍ60 - 64세', leaf_name 자신엔 "이상"이 없고 조상
    # "60세이상"에서만 옴)가 여전히 1위를 차지했다 - 조상 매치는 leaf_name
    # 기준 판정을 안 거치기 때문. "60세이상"도 "65세 이상"/"70세 이상"/
    # "75세 이상"과 형제 관계로 같은 계층에 반복되는 값이므로, leaf만이
    # 아니라 breadcrumb 체인의 개별 노드 이름 전부를 판정 대상에 넣어야
    # 한다(_cell_breadcrumb_chain/axis_chains, 위 iter_table_cell_texts 참고).
    #
    # [2026-08-24 재수정 - "정부" 회귀(test_local_db_agent_derivation.py
    # test_weak_literal_tie_uses_hcx_instead_of_loose_value_tolerance) 실측
    # 발견] 처음엔 "같은 축 안에서 서로 다른 노드 이름 2개 이상에 걸리면
    # 무조건 구분력 없음"으로 판정했는데, 이게 너무 넓었다. 실측(국가채무
    # 표, 184/DT_102006_001): "정부"가 "중앙정부 채무"(A03)/"지방정부
    # 순채무"(A10) 둘에 걸리는 것도 이 기준에 걸려 완전히 제외됐는데, 이건
    # "이상"과 성격이 다르다 - "정부"는 진짜 내용어이고, A03/A10을 못
    # 가른다는 사실 자체가 이미 기존 weak_literal_tie 메커니즘(matched_
    # phrases/distinguishing_phrase_count가 적으면 HCX로 넘기는 설계, Task
    # #15)이 정확히 다루는 "약한 동점"이다. 그런데 "정부"를 axis 매치에서
    # 통째로 빼버리면 occurrences가 아예 비어서 두 후보가 "동점"조차 아니라
    # "후보 자체가 안 나옴"(resolve_evidence_by_flat_match가 빈 리스트
    # 반환)이 되어, 동점 다음 단계(HCX 재확인)로 갈 기회 자체가 사라졌다
    # (실측: pre_candidates가 빈 리스트가 되어 IndexError).
    #
    # "이상"과 "정부"의 진짜 차이: "이상" 케이스엔 그 축에 "계"(총계/기본값,
    # _TOTAL_LABEL_MARKERS)가 있고, "이상"은 그 "계"에는 안 걸리면서 계가
    # 아닌 다른 노드 여러 개에만 걸려서 계보다 부당하게 유리해졌다. "정부"
    # 케이스는 그 축(채무내역별)에 애초에 "계"류 총계 노드가 없다 - A01
    # "국가채무"가 개념상 총계이긴 해도 이름 자체가 _TOTAL_LABEL_MARKERS에
    # 없으므로 이 축은 "총계 노드가 없는 축"이다. 그래서 조건을 좁힌다:
    # 이 축에 총계/기본값 노드가 실제로 있고, phrase가 그 총계 노드에는 안
    # 걸리면서 총계가 아닌 다른 노드 2개 이상에 걸릴 때만 "구분력 없는 일반
    # 단어"로 본다 - 총계 노드가 아예 없는 축(정부류)에는 이 일반화를 적용
    # 하지 않아 기존 weak_literal_tie 경로를 그대로 보존한다.
    axis_total_names_by_position: Dict[int, set] = {}
    for cell in cell_texts:
        for axis_position, (_, leaf_name, axis_label) in cell["axis_texts"].items():
            if leaf_name and (
                leaf_name in _TOTAL_LABEL_MARKERS
                or leaf_name == _AXIS_LABEL_DEFAULT_NAME.get(axis_label)
            ):
                axis_total_names_by_position.setdefault(axis_position, set()).add(leaf_name)

    distinct_names_by_axis: Dict[int, set] = {}
    for cell in cell_texts:
        for axis_position, chain in cell.get("axis_chains", {}).items():
            for name in chain:
                if name:
                    distinct_names_by_axis.setdefault(axis_position, set()).add(name)

    generic_phrases_by_axis: Dict[int, set] = {}
    for axis_position, names in distinct_names_by_axis.items():
        total_names = axis_total_names_by_position.get(axis_position)
        if not total_names:
            continue  # 총계 노드가 없는 축 - 일반화 적용 안 함(정부/A03·A10류 보호)
        non_total_names = names - total_names
        generic = {
            p for p in phrases
            if not any(_count_occurrences(t, p) > 0 for t in total_names)
            and sum(1 for name in non_total_names if _count_occurrences(name, p) > 0) >= 2
        }
        if generic:
            generic_phrases_by_axis[axis_position] = generic

    def _axis_phrase_is_real_match(axis_position: int, chain: List[str], p: str) -> bool:
        """이 축(axis_position)의 breadcrumb 체인(조상->리프) 어딘가에 phrase
        p가 걸리고, 그 p가 이 축 전체에서 '구분력 없는 일반 단어'(생성
        기준: 이 축의 서로 다른 노드 2개 이상에 걸림)로 판정되지 않았으면
        True(진짜 매치로 인정). generic_phrases_by_axis 판정 자체가 이미
        "축 전체" 단위라 노드별로 다시 가르지 않는다 - 지금까지 실측된
        사례는 전부 "그 축 전체에서 흔한 단어"였지 "일부 노드만 흔한" 사례가
        아니었다(그런 사례가 실측되면 재검토)."""
        if not any(_count_occurrences(name, p) > 0 for name in chain):
            return False
        return p not in generic_phrases_by_axis.get(axis_position, set())

    scored: List[Dict[str, Any]] = []
    for cell in cell_texts:
        itm_id = cell["itm_id"]
        item_nm = cell["itm_nm"]
        axis_codes = cell["axis_codes"]
        axis_texts = cell["axis_texts"]
        axis_chains = cell.get("axis_chains", {})
        text = cell["text"]
        # [2026-08-16 사용자 지적 반영] 처음엔 "겹치는 phrase 개수"만 셌는데,
        # 그러면 "1"+"거래량"처럼 여러 셀이 동점(예: 거래량 기준의 거래량 vs
        # 거래대금 기준에 곁다리로 나오는 거래량)일 때 못 갈랐다. 사용자
        # 지적: 이건 "따로 정할 문제"가 아니라 "정보가 하나만 더 있으면
        # 저절로 풀린다" - 겹친 phrase 개수가 아니라 등장 *횟수*를 세면
        # 된다(부모+자식 이름이 같은 셀은 그 이름이 breadcrumb에 두 번
        # 들어가므로 자연히 더 높은 점수를 받는다 - 별도 가중치 설계가
        # 필요 없다).
        #
        # [2026-08-18 실측 버그 수정 - 조상 체인 중복 카운트] 위 "등장 횟수"
        # 방식을 전체 이어붙인 text 하나에 그대로 적용하면, 계층이 깊은
        # leaf일수록 조상 이름이 breadcrumb에 여러 번 반복돼 부당하게 높은
        # score를 받는다(실측: A93bfa851-C024 "가정용품 및 가사서비스 물가"
        # - 정답인 집계행 자신(E, 자기 이름이 곧 "가정용품 및 가사서비스")은
        # score=3인데, 그 하위 leaf 품목(예: 프라이팬=E04104)은 조상 체인
        # E->E04->E041 세 단계에 "가정용품"이 반복 등장해 score=5로 더
        # 높아져, 정작 정답인 집계행이 top_n 밖으로 밀려나고 무관한 leaf
        # 품목들끼리만 동점 1위가 되는 문제가 있었다). "등장 횟수를 센다"는
        # 원래 의도(위 문단)는 "서로 다른 세그먼트(item_nm 대 축2 등)에
        # 각각 걸리면 이중 근거"라는 뜻이었지, "같은 세그먼트 안에서 조상
        # 체인이 반복되는 것"까지 이중 근거로 셀 의도는 아니었다 - 세그먼트
        # (item_nm 1개 + 축마다 1개)당 phrase 1회만 인정하도록 바꾼다.
        occurrences = {}
        axis_matched_phrases = set()
        for p in phrases:
            # [2026-08-24 수정 - 축 단위 일반화] 기존엔 flat `segments`
            # (item_nm + 축 텍스트 전부)를 그냥 순회했는데, 그러면 "이 축에서
            # 이 phrase가 구분력 없는 일반 단어 매치인지"를 알 수 없어 위
            # generic_phrases_by_axis를 적용할 자리가 없었다. item_nm
            # 세그먼트와 축 세그먼트를 분리해서, 축 세그먼트만 제외 판정을
            # 거친다(item_nm은 item_is_uniform이 이미 별도로 처리).
            hits = 0
            if item_nm and _count_occurrences(item_nm, p) > 0:
                hits += 1
            axis_hit_this_phrase = False
            for axis_position in axis_texts:
                chain = axis_chains.get(axis_position) or [axis_texts[axis_position][0]]
                if not _axis_phrase_is_real_match(axis_position, chain, p):
                    continue
                hits += 1
                axis_hit_this_phrase = True
            if hits:
                occurrences[p] = hits
            if axis_hit_this_phrase:
                axis_matched_phrases.add(p)
        if occurrences:
            # [2026-08-17 실측 버그 수정 - 건설업 취업자 케이스] 총점이 같으면
            # "phrase로 정당화되지 않은 축값"이 적은 셀을 우선한다. 예:
            # "건설업 취업자 수" claim은 지역을 언급 안 했는데, 지역축이
            # 우연히 SQL 조회 순서상 먼저 나온 특정 시도(서울/부산 등)로
            # 고정돼도 총점은 전국("계")과 똑같았다(둘 다 "건설업"+"취업자"만
            # 셈) - 정답과 오답이 순전히 우연한 순서로 갈리는 문제였다.
            # 축별 breadcrumb 텍스트에 phrase가 하나도 안 걸리고, 그 축의
            # 리프 이름이 합계/소계/전체/계(_TOTAL_LABEL_MARKERS)도, 축
            # 종류별 기본값(_AXIS_LABEL_DEFAULT_NAME - 예: "국가" 축의
            # "대한민국")도 아니면 "근거 없이 우연히 뽑힌 값"으로 보고
            # 감점한다 - 반대로 축이 phrase로 설명되거나(예: "건설업") 축의
            # 기본값(전국/계, 또는 국가축의 대한민국)이면 감점하지 않는다.
            # ["대한민국" 기본값 - 2026-08-17] DT_2IFS002 같은 국제비교표는
            # "계"/"합계"류 총계 국가 행이 아예 없고 개별 국가 leaf만 있어서,
            # claim에 국가 언급이 없으면 그동안 facts 조회 순서상 우연히 먼저
            # 나온 국가(알파벳순 "아일랜드" 등)로 고정되는 문제가 실측
            # 확인됨(A93bfa851-C022/C024/C027/C029) - 국내 뉴스 claim
            # 기본값은 "대한민국"이 되어야 한다.
            # [2026-08-24 수정 - 축 단위 일반화] "설명됐다"는 판정도 구분력
            # 없는 일반 단어 매치(generic_phrases_by_axis)는 인정하지 않는다 -
            # 안 그러면 "이상"류 일반 단어가 조상 노드에 우연히 걸린 무관한
            # 축까지 "설명된 축"으로 오인해 unexplained_axes를 부당하게 0으로
            # 만든다(실측: DT_1DA7024S "계/ㆍ60 - 64세"가 조상 "60세이상"의
            # "이상" 매치로 이 오인에 걸려 정답 "계/계"와 부당하게 동률·역전됨).
            unexplained_axes = sum(
                1
                for axis_position, (axis_text, leaf_name, axis_label) in axis_texts.items()
                if not any(
                    _axis_phrase_is_real_match(
                        axis_position, axis_chains.get(axis_position) or [axis_text], p
                    )
                    for p in phrases
                )
                and leaf_name not in _TOTAL_LABEL_MARKERS
                and leaf_name != _AXIS_LABEL_DEFAULT_NAME.get(axis_label)
            )
            # [2026-08-18 신규 - 위 세그먼트당 1회 dedup의 후속 조치] dedup만
            # 하면 집계행(E)과 그 하위 leaf 전부가 다시 동점이 될 수 있다
            # (실측: A93bfa851-C024에서 dedup 적용 후 E도 score=3, 프라이팬도
            # score=3으로 동점 - leaf가 이제 더 안 이기지만, "정직하게 둘 다
            # 후보"인 상태가 된 것뿐이지 자동으로 정답이 가려지진 않는다).
            # 이 tie를 추가로 가르기 위해 "phrase가 그 축의 자기 자신 이름
            # (leaf_name, 조상 아님)에서 나왔는지"를 센다 - 집계행 E는 자기
            # 이름 자체가 "가정용품 및 가사서비스"라 직접 매치지만, 프라이팬은
            # 자기 이름엔 매치가 하나도 없이 전부 조상(E/E04/E041)한테서만
            # 상속된 매치다. 조상한테서만 상속된 매치가 적을수록(=자기
            # 이름으로 직접 설명될수록) 우선한다.
            ancestor_only_hits = sum(
                1
                for axis_text, leaf_name, axis_label in axis_texts.values()
                for p in phrases
                if _count_occurrences(axis_text, p) > 0
                and not _count_occurrences(leaf_name or "", p)
            )
            # [2026-08-21 신규 - Task #15] "물가"류 item_nm 전용 매치는
            # item_is_uniform일 때 구분력이 없으므로 제외하고 센다 - 축
            # 텍스트에도 걸렸거나(axis_matched_phrases), item_nm 자체가
            # 표 안에서 여러 값으로 갈리면(구분력 있음) 그대로 인정한다.
            distinguishing_phrases = [
                p for p in occurrences
                if p in axis_matched_phrases or not item_is_uniform
            ]
            scored.append({
                "itm_id": itm_id,
                "itm_nm": item_nm,
                "axis_codes": axis_codes,
                "text": text,
                "matched_phrases": list(occurrences.keys()),
                "distinguishing_phrase_count": len(distinguishing_phrases),
                "score": sum(occurrences.values()),
                "unexplained_axes": unexplained_axes,
                "ancestor_only_hits": ancestor_only_hits,
            })

    scored.sort(key=lambda s: (-s["score"], s["unexplained_axes"], s["ancestor_only_hits"]))
    return scored[:top_n]


# =============================================================================
# [2026-08-17 신규 - 사용자 제안] 값 기반 사후 검증(disambiguation) - README
# 2.2절에 원래 설계돼 있었지만 실제로는 한 번도 호출되지 않은 죽은 코드였던
# `gather_candidate_values`(값까지 다 조회해서 비교하는 사후 검증)를 이제야
# 완성하는 것에 해당한다.
#
# [순환논리 주의 - 반드시 지킬 것] resolve_evidence_by_flat_match의 이름/축
# 매칭이 이미 1등을 명확히 골랐으면(동점 없음) 이 함수를 쓰지 않는다. claim이
# 주장하는 값을 미리 보고 그 값에 맞는 셀을 고르면, 뒤이은 판정(judgment.py)
# 단계가 "일치합니다"라고 결론 내리는 게 순환논리가 되기 때문이다(이미 답을
# 알고 답에 맞춰 고른 것을 "검증됐다"고 부르는 셈). 그래서 이 함수는 이름/축
# 매칭만으로는 "진짜로 못 가른"(동점) 후보들 사이에서만 쓰고, 그렇게 골랐다는
# 사실을 `disambiguated_by_value=True`로 반드시 남긴다 - judgment.py의
# `derivation_used`/`ai_used`와 같은 투명성 원칙(어떤 단계가 순수 독립
# 매칭이 아니라 추가 정보/판단을 썼으면 숨기지 않고 표시한다).
#
# 독서/유가증권처럼 claim 텍스트의 phrase만으로는 표의 모든 축을 다 특정하지
# 못하는 특이 표에서, 값을 보조 신호로 써서 마지막 모호함을 해소하기 위한
# 용도다 - claim 텍스트가 완벽하게 원자적으로 재가공된다는 가정이 아직 실제로
# 성립하지 않는다는 게 이번 세션에 실측 확인됐으므로(claims.jsonl 90건 중
# 53건이 원문 압축 문장을 그대로 재사용), 이름 매칭만으로 끝까지 해결하려는
# 대신 이 보조 단계를 둔다.
# =============================================================================

def _normalize_period_digits(period: Optional[str]) -> Optional[str]:
    """claim의 period("2025-06", "2025" 등)에서 숫자만 남긴다 - facts.prd_de
    (KOSIS 원본 표기 "202506"/"2025")와 직접 비교하기 위함."""
    if not period:
        return None
    digits = re.sub(r"[^0-9]", "", str(period))
    return digits or None


def _fetch_candidate_value(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    candidate: Dict[str, Any],
    period: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """candidate(resolve_evidence_by_flat_match가 반환한 항목 하나)가
    가리키는 셀의 실제 facts 값 1개를 가져온다. period가 주어지면 그 시점에
    가장 가까운(prd_de 기준 정확히 일치 -> 같은 연도 -> 그래도 없으면 가장
    최근) 값을 쓰고, period가 없으면 가장 최근 값을 쓴다 - 추측이 필요한
    "정확히 이 시점"은 호출부가 이미 못 정한 것이므로 여기서도 강하게
    단정하지 않고 최선의 근사만 시도한다."""
    where = ["org_id=?", "tbl_id=?", "itm_id=?"]
    params: List[Any] = [org_id, tbl_id, candidate["itm_id"]]
    for axis, code in candidate.get("axis_codes", {}).items():
        where.append(f"c{axis}=?")
        params.append(code)
    rows = conn.execute(
        f"SELECT prd_de, prd_se, value, unit FROM facts WHERE {' AND '.join(where)} ORDER BY prd_de DESC",
        params,
    ).fetchall()
    if not rows:
        return None

    period_digits = _normalize_period_digits(period)
    chosen = None
    if period_digits:
        chosen = next((r for r in rows if r[0] == period_digits), None)
        if chosen is None and len(period_digits) >= 4:
            year = period_digits[:4]
            chosen = next((r for r in rows if (r[0] or "").startswith(year)), None)
    if chosen is None:
        chosen = rows[0]  # 가장 최근 값으로 폴백
    prd_de, prd_se, value, unit = chosen
    return {"prd_de": prd_de, "prd_se": prd_se, "value": value, "unit": unit}


def disambiguate_by_value(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    candidates: List[Dict[str, Any]],
    claimed_value: float,
    period: Optional[str] = None,
    relative_tolerance: float = 0.05,
) -> Dict[str, Any]:
    """이름/축 매칭이 동점인 candidates(resolve_evidence_by_flat_match의
    반환값, 같은 score+unexplained_axes를 가진 후보들)만 넘겨받아, 각 후보의
    실제 값을 조회해서 claimed_value에 가장 가까운 후보를 고른다.

    호출부 책임: candidates는 이미 "이름만으로 못 가른 동점 후보"로 좁혀서
    넘겨야 한다 - 이름으로 1등이 명확하면 이 함수를 부르지 않는 게 원칙이다
    (모듈 docstring의 순환논리 경고 참고).

    반환: {"resolved": candidate | None, "disambiguated_by_value": bool,
    "reason": str, "candidates_checked": [{"candidate", "fetched", "distance"}]}
    - resolved가 None이면: 값으로도 유일하게 못 갈랐다는 뜻(아무도 안
      맞거나, 여럿이 다 비슷하게 맞음) - 호출부는 이걸 UNVERIFIED/모호로
      처리해야 한다(추측으로 하나를 강제로 고르지 않는다, 사용자 지적).
    """
    checked: List[Dict[str, Any]] = []
    for cand in candidates:
        fetched = _fetch_candidate_value(conn, org_id, tbl_id, cand, period)
        if fetched is None or fetched["value"] is None:
            checked.append({"candidate": cand, "fetched": fetched, "distance": None})
            continue
        actual = fetched["value"]
        denom = max(abs(actual), abs(claimed_value), 1e-9)
        distance = abs(claimed_value - actual) / denom
        checked.append({"candidate": cand, "fetched": fetched, "distance": distance})

    close_enough = [c for c in checked if c["distance"] is not None and c["distance"] <= relative_tolerance]

    if len(close_enough) == 1:
        winner = close_enough[0]
        return {
            "resolved": winner["candidate"],
            "disambiguated_by_value": True,
            "reason": (
                f"이름/축 매칭 동점 {len(candidates)}건 중, claim 값({claimed_value})과 "
                f"{winner['distance']*100:.1f}% 이내로 유일하게 가까운 후보를 채택"
            ),
            "candidates_checked": checked,
        }

    if len(close_enough) == 0:
        return {
            "resolved": None,
            "disambiguated_by_value": False,
            "reason": f"동점 후보 {len(candidates)}건 중 claim 값({claimed_value})에 근접한 후보가 하나도 없음",
            "candidates_checked": checked,
        }

    return {
        "resolved": None,
        "disambiguated_by_value": False,
        "reason": (
            f"동점 후보 {len(candidates)}건 중 {len(close_enough)}건이 claim 값"
            f"({claimed_value})에 비슷하게 근접해 값으로도 유일하게 못 가림"
        ),
        "candidates_checked": checked,
    }


# =============================================================================
# [2026-08-18 신규 - VDB 설계 문서 진입점 ② "값 기반 검색", 최초 구현]
#
# 왜 필요한가: 48개 claim 재검증에서 disambiguate_by_value로도 못 막는
# 실측 사례(C006/C007 "교육서비스업"/"제조업 취업자")를 발견했다 - Stage 1
# (search_local)이 run03 패러프레이즈의 "경제활동"이란 단어가 표 이름
# ("경제활동인구")과 우연히 겹쳐서, 산업 축 자체가 없는 완전히 엉뚱한 표를
# 1등으로 골랐다. Stage 2(resolve_evidence_by_flat_match)는 이 안에서
# "제조업" phrase가 하나도 안 걸린다는 걸 이미 알지만, 그 정보가 Stage 1의
# 표 선택으로 피드백되지 않는다 - 애초에 한 표(table_candidates[0])만
# 시도하기 때문이다.
#
# disambiguate_by_value(기존)와 다른 점: 그건 "이름 매칭이 이미 좁혀놓은
# 동점 후보들" 안에서만 값으로 고르는 사후 검증이라 표 자체가 틀리면
# 손을 못 쓴다. 이 함수는 표를 가리지 않고 facts 전체에서 값+시점으로
# 먼저 찾은 뒤, 그렇게 찾은 각 후보가 실제로 claim의 phrase와도 겹치는지
# 대조한다(값만으로 순환논리에 빠지지 않기 위한 이중 확인 - 아래 참고).
#
# 값만으로 채택하지 않는 이유: KOSIS 값에는 "천명"/"십억원" 등 축척이
# 붙어서, claimed_value(항상 절대 단위) 하나에도 배율 후보마다(1/천/만/
# 백만/십억/억/조) 서로 다른 raw 검색 구간이 생긴다 - 그 여러 구간을 다
# 훑으면 특히 흔한 값(0, 100.0 등)에서 우연히 값만 맞는 무관한 표가 다수
# 걸릴 수 있다. 그래서 값으로 찾은 후보라도 match_phrases(claim의
# metric_normalized 토큰)가 그 후보의 item/축 breadcrumb에 실제로 하나도
# 안 걸리면 신뢰하지 않는다 - "값도 맞고 이름도 맞는" 이중 corroboration이
# 있을 때만 채택 후보로 인정한다(호출부 local_db_agent.resolve_claim_
# evidence가 matched_phrase_count==0인 결과는 쓰지 않는다).
# =============================================================================
def _lookup_table_dims(
    conn: sqlite3.Connection, dims_cache: Dict[tuple, tuple], org_id: str, tbl_id: str,
) -> tuple:
    """(dims_by_code, item_by_id, tbl_nm) 캐시 조회/적재 - search_by_value/
    search_by_diff가 여러 행을 표 단위로 묶어 처리할 때 표당 한 번만
    _dim_rows_for_table을 부르기 위한 공용 헬퍼."""
    cache_key = (org_id, tbl_id)
    if cache_key not in dims_cache:
        dim_rows = _dim_rows_for_table(conn, org_id, tbl_id)
        dims_by_code = {(r["axis_position"], r["code"]): r for r in dim_rows if r.get("code")}
        item_by_id = {r["code"]: r for r in dim_rows if r["obj_id"] == "ITEM"}
        tbl_nm_row = conn.execute(
            "SELECT tbl_nm FROM tables_registry WHERE org_id=? AND tbl_id=?", (org_id, tbl_id)
        ).fetchone()
        dims_cache[cache_key] = (dims_by_code, item_by_id, tbl_nm_row[0] if tbl_nm_row else None)
    return dims_cache[cache_key]


def _breadcrumb_for_cell(
    dims_by_code: Dict[tuple, Dict[str, Any]],
    item_by_id: Dict[str, Dict[str, Any]],
    itm_id: str,
    axis_values: List[Optional[str]],
) -> tuple:
    """item_nm, axis_codes({axis_position: code}), text(item명+축 전체
    breadcrumb를 합친 문자열)를 만든다 - search_by_value/search_by_diff가
    공유하는 셀 1개 -> 사람이 읽을 텍스트 변환 로직."""
    item_row = item_by_id.get(itm_id)
    item_nm = item_row.get("name") if item_row else None
    parts = [item_nm] if item_nm else []
    axis_codes: Dict[int, str] = {}
    for axis_position, code in enumerate(axis_values, start=1):
        if not code:
            continue
        drow = dims_by_code.get((axis_position, code))
        if drow:
            parts.append(_cell_breadcrumb_text(dims_by_code, drow))
            axis_codes[axis_position] = code
    text = " ".join(p for p in parts if p)
    return item_nm, axis_codes, text


def search_by_value(
    conn: sqlite3.Connection,
    claimed_value: float,
    period_digits: str,
    match_phrases: Optional[List[str]] = None,
    tolerance: float = 0.01,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """claim이 주장하는 값(이미 adapter._scale_to_absolute류로 절대 단위로
    맞춰졌다고 가정)과 시점만으로, org_id/tbl_id를 가리지 않고 facts
    전체에서 가까운 행을 직접 찾는다. period_digits(예: "202506")는 필수
    - 시점을 안 좁히면 값 하나로 4백만 행을 다 훑는 게 되어(실측: 인덱스
    없이 3.4초, prd_de 필터만으로도) claim마다 부르기엔 너무 느리고
    무의미한 우연 일치도 늘어난다.

    반환: [{"org_id","tbl_id","tbl_nm","itm_id","itm_nm","axis_codes","text",
    "raw_value","unit","absolute_value","rel_diff","matched_phrase_count",
    "prd_de"}, ...] - matched_phrase_count 내림차순, 그다음 rel_diff(값
    오차) 오름차순. 호출부는 matched_phrase_count > 0인 것만 신뢰해야
    한다(모듈 주석 참고 - 값만으로는 우연일 수 있음).
    """
    if claimed_value is None or not period_digits:
        return []

    multipliers = sorted(set([1.0] + list(TextUtilsMixin._UNIT_SCALE_MULTIPLIERS.values())))
    seen_fact_ids: set = set()
    raw_hits: List[tuple] = []
    for mult in multipliers:
        raw_target = claimed_value / mult
        span = abs(raw_target) * tolerance if raw_target != 0 else tolerance
        lo, hi = raw_target - span, raw_target + span
        rows = conn.execute(
            "SELECT id, org_id, tbl_id, itm_id, c1, c2, c3, c4, c5, c6, c7, c8, value, unit "
            "FROM facts WHERE prd_de=? AND value BETWEEN ? AND ?",
            (period_digits, lo, hi),
        ).fetchall()
        for row in rows:
            fid = row[0]
            if fid in seen_fact_ids:
                continue
            seen_fact_ids.add(fid)
            raw_hits.append(row)

    if not raw_hits:
        return []

    phrases = [p for p in (match_phrases or []) if p]
    dims_cache: Dict[tuple, tuple] = {}
    results: List[Dict[str, Any]] = []
    for row in raw_hits:
        fid, org_id, tbl_id, itm_id, c1, c2, c3, c4, c5, c6, c7, c8, raw_value, unit = row
        absolute_value = (raw_value or 0.0) * TextUtilsMixin._unit_scale_multiplier(unit)
        denom = abs(claimed_value) if claimed_value != 0 else 1e-9
        rel_diff = abs(absolute_value - claimed_value) / denom
        if rel_diff > tolerance:
            # 배율 추정이 맞았을 때만 진짜 근접이다 - 다른 배율 구간에
            # 우연히 raw 범위로 걸렸을 뿐인 행은 여기서 걸러진다.
            continue

        dims_by_code, item_by_id, tbl_nm = _lookup_table_dims(conn, dims_cache, org_id, tbl_id)
        item_nm, axis_codes, text = _breadcrumb_for_cell(
            dims_by_code, item_by_id, itm_id, [c1, c2, c3, c4, c5, c6, c7, c8]
        )
        # [2026-08-18 실측 발견] 단순 substring 검사(p in text)는 공백
        # 유무 차이(claim "교육서비스업" vs KOSIS 축 이름 "교육 서비스업")
        # 를 못 잡아서 진짜 정답(DT_1DA7E33S_NEW 코드 85)의 matched_
        # phrase_count가 1로 과소평가되는 걸 발견했다(2/2 완전 corroboration
        # 문턱을 못 넘김) - resolve_evidence_by_flat_match가 이미 쓰는
        # _count_occurrences(공백 무시 재시도 포함)로 통일한다.
        matched_phrase_count = sum(1 for p in phrases if _count_occurrences(text, p) > 0)

        results.append({
            "org_id": org_id, "tbl_id": tbl_id, "tbl_nm": tbl_nm,
            "itm_id": itm_id, "itm_nm": item_nm,
            "axis_codes": axis_codes, "text": text,
            "raw_value": raw_value, "unit": unit,
            "absolute_value": absolute_value, "rel_diff": rel_diff,
            "matched_phrase_count": matched_phrase_count,
            "prd_de": period_digits,
        })

    results.sort(key=lambda r: (-r["matched_phrase_count"], r["rel_diff"]))
    return results[:top_n]


# [2026-08-18 신규 - 사용자 제안: "비교 표현이면 후보군을 전부 조회해야
# 하는데, 몇 개를 조회할지 판단하는 앞단이 비어 있다"에 대한 답] search_
# by_value의 비교판. claim이 "이 시점 대비 저 시점" 쌍대비교(YoY 증가/
# 감소 등)를 표현할 때, target_period_digits/reference_period_digits
# 두 시점 모두에 값이 있는 (org_id,tbl_id,itm_id,c1~c8) 조합을 facts
# 전체에서 직접 찾아 그 차이(또는 등락률)가 claimed_diff와 맞는지
# 대조한다 - Stage 1이 표를 잘못 골라도(실측: C006 "교육서비스업" ->
# 산업 축 없는 표로 오매칭) 영향받지 않는다(search_by_value와 같은 이유).
#
# "몇 개 값을 조회하는가": 정확히 2개(target 1개 + reference 1개) -
# local_db_agent._claim_expresses_pairwise_change의 모듈 주석 참고 -
# 이 프로젝트에서 실측된 모든 비교 claim이 쌍대비교였다는 사실에 근거한
# 결정이고, 3점 이상 비교는 아직 실측된 사례가 없어 추측하지 않는다.
#
# 성능: target JOIN reference를 SQL 자체 조인으로 하면 8개 COALESCE
# 컬럼 동등조건이라 인덱스를 못 타 실측 12~20초가 걸렸다(4백만 행
# 스캔급). 대신 두 시점을 인덱스(idx_facts_value_search, prd_de가
# 선두 컬럼)로 각각 따로 가져와서(실측 0.03~0.1초) 파이썬 딕셔너리
# 교집합으로 조인한다 - SQL 조인보다 100배 이상 빠르다.
def search_by_diff(
    conn: sqlite3.Connection,
    claimed_diff: float,
    target_period_digits: str,
    reference_period_digits: str,
    match_phrases: Optional[List[str]] = None,
    mode: str = "difference",
    tolerance: float = 0.02,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """claim이 주장하는 값이 두 시점의 차이(mode="difference", 명/원 등
    절대단위)거나 등락률(mode="pct_change", %)일 때, 그 계산값이
    claimed_diff와 맞는 (org_id,tbl_id,itm_id,axis_codes) 조합을 찾는다.

    반환 형식은 search_by_value와 동일(+ target_value/reference_value/
    computed 필드 추가) - matched_phrase_count 내림차순, rel_diff
    오름차순. 호출부는 matched_phrase_count == len(match_phrases)(완전
    corroboration)일 때만 신뢰해야 한다(search_by_value와 같은 안전
    원칙 - "물가" 하나만 걸리는 식의 가짜 corroboration을 막기 위해
    match_phrases가 최소 2개 이상일 때만 부르도록 호출부가 이미 게이트
    한다)."""
    if claimed_diff is None or not target_period_digits or not reference_period_digits:
        return []

    cols = "org_id, tbl_id, itm_id, c1, c2, c3, c4, c5, c6, c7, c8, value, unit"
    query = f"SELECT {cols} FROM facts WHERE prd_de=?"
    target_rows = conn.execute(query, (target_period_digits,)).fetchall()
    ref_rows = conn.execute(query, (reference_period_digits,)).fetchall()

    def _cell_key(row: tuple) -> tuple:
        # (org_id, tbl_id, itm_id, c1..c8) - 값/단위(마지막 2개)는 키에서 뺀다.
        return row[:11]

    ref_by_key = {_cell_key(row): row for row in ref_rows}

    phrases = [p for p in (match_phrases or []) if p]
    dims_cache: Dict[tuple, tuple] = {}
    results: List[Dict[str, Any]] = []
    for row in target_rows:
        ref_row = ref_by_key.get(_cell_key(row))
        if ref_row is None:
            continue
        org_id, tbl_id, itm_id = row[0], row[1], row[2]
        axis_values = list(row[3:11])
        target_raw, target_unit = row[11], row[12]
        ref_raw, ref_unit = ref_row[11], ref_row[12]
        target_abs = (target_raw or 0.0) * TextUtilsMixin._unit_scale_multiplier(target_unit)
        ref_abs = (ref_raw or 0.0) * TextUtilsMixin._unit_scale_multiplier(ref_unit)

        if mode == "pct_change":
            if ref_abs == 0:
                continue
            computed = (target_abs - ref_abs) / abs(ref_abs) * 100.0
        else:
            computed = target_abs - ref_abs

        denom = abs(claimed_diff) if claimed_diff != 0 else 1e-9
        rel_diff = abs(computed - claimed_diff) / denom
        if rel_diff > tolerance:
            continue

        dims_by_code, item_by_id, tbl_nm = _lookup_table_dims(conn, dims_cache, org_id, tbl_id)
        item_nm, axis_codes, text = _breadcrumb_for_cell(dims_by_code, item_by_id, itm_id, axis_values)
        # [2026-08-18 실측 발견] 단순 substring 검사(p in text)는 공백
        # 유무 차이(claim "교육서비스업" vs KOSIS 축 이름 "교육 서비스업")
        # 를 못 잡아서 진짜 정답(DT_1DA7E33S_NEW 코드 85)의 matched_
        # phrase_count가 1로 과소평가되는 걸 발견했다(2/2 완전 corroboration
        # 문턱을 못 넘김) - resolve_evidence_by_flat_match가 이미 쓰는
        # _count_occurrences(공백 무시 재시도 포함)로 통일한다.
        matched_phrase_count = sum(1 for p in phrases if _count_occurrences(text, p) > 0)

        results.append({
            "org_id": org_id, "tbl_id": tbl_id, "tbl_nm": tbl_nm,
            "itm_id": itm_id, "itm_nm": item_nm,
            "axis_codes": axis_codes, "text": text,
            "target_value": target_abs, "reference_value": ref_abs,
            "computed": computed, "mode": mode, "rel_diff": rel_diff,
            "matched_phrase_count": matched_phrase_count,
            "target_prd_de": target_period_digits, "reference_prd_de": reference_period_digits,
        })

    results.sort(key=lambda r: (-r["matched_phrase_count"], r["rel_diff"]))
    return results[:top_n]


# =============================================================================
# [2026-08-17 신규 - Task #5, 진단은 2026-08-15에 이미 끝나 있었음(README
# 9.2/2.8)] YoY(전년동월비) %등락률 파생.
#
# 왜 필요한가: 이번 세션 14건 재검증에서 표/축까지 정확히 찾은 뒤에도 값이
# 안 맞는 게 대부분이었는데(예: "빵 물가 38.5% 올랐다"), 원인은 검색/해석
# 버그가 아니라 애초에 claim이 요구하는 값(%등락률)이 KOSIS 표에 없어서다
# (표엔 지수 레벨값만 있음, README 9.2에 이미 진단돼 있었다 - 세부 품목
# 단위 등락률은 KOSIS가 완제품으로 발행하지 않는다).
#
# 이 함수가 하는 일과 하지 않는 일 - 판정(judgment.py)과의 경계:
# README 2.8 결론 그대로, KOSIS 자체도 증감률을 원자료가 아니라 "이미 받아온
# 두 시점 레벨값을 클라이언트가 계산한 파생값"으로 취급한다(웹페이지 토글도
# 마찬가지). 이 함수는 그 계산에 필요한 "정확히 같은 item/축의 두 시점 값을
# facts에서 찾아오는" 검색/해석 단계 책임만 진다 - 그렇게 찾은 두 값으로
# 판정(허용오차 비교, VERIFIED/MISMATCH 결정)을 내리는 건 여전히 judgment.py
# 의 책임이다(이미 `_resolve_comparison_evidence`가 두 EvidencePoint를 받아
# pct_change 비교를 하는 로직을 갖고 있다 - 새로 안 만들고 재사용 대상).
# 이 함수는 그 두 EvidencePoint에 해당하는 원자료를 "한 claim_id 안에서"
# 스스로 찾아 조립해주는 것뿐이다(claim 추출 단계가 별도 claim_id로 안
# 쪼개준 경우를 위해).
#
# 추측하지 않는 원칙: 기준 시점(reference_period)은 항상 "같은 달/분기,
# 1년 전"만 쓴다(전년동월비/전년동분기비 - 뉴스 claim이 등락률을 말할 때
# 압도적으로 이 기준을 쓴다, _RATE_OF_CHANGE_MARKERS의 "전년비/전년동월비/
# 전년대비" 계열과 일치). 그 정확한 시점의 값이 facts에 없으면(아직 안
# 받아왔거나 KOSIS가 그 시점을 발행 안 했거나) 근처 시점으로 대체하지
# 않고 그냥 None을 반환한다 - "대략 비슷한 시점"으로 계산하면 KOSIS가
# 실제로 발행한 두 값의 비교가 아니게 되어 Decision 003 원칙을 어긴다.
# =============================================================================

def _yoy_reference_period(target_period_digits: str) -> Optional[str]:
    """target_period_digits(정규화된 숫자만 남은 PRD_DE, 예: "202509"/"2025")
    에서 "같은 달(또는 분기/그 외), 1년 전" 시점을 만든다. 앞 4자리(연도)만
    1 줄이고 나머지 자리는 그대로 유지한다 - 월/분기 표기 규칙을 몰라도
    되게(길이만 보존하면 KOSIS의 YYYYMM/YYYYQ 표기와 그대로 맞물린다)."""
    if not target_period_digits or len(target_period_digits) < 4:
        return None
    year = target_period_digits[:4]
    if not year.isdigit():
        return None
    prev_year = str(int(year) - 1).zfill(4)
    return prev_year + target_period_digits[4:]


def resolve_period_change(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    axis_codes: Dict[int, str],
    target_period: str,
    reference_period: str,
) -> Dict[str, Any]:
    """resolve_evidence_by_flat_match(+ 필요하면 disambiguate_by_value)로
    이미 확정된 itm_id/axis_codes에 대해, target_period와 reference_period
    두 시점의 값을 facts에서 각각 찾아 %등락률을 계산한다. 표를 새로 찾거나
    항목/축을 다시 판정하지 않는다 - 이미 확정된 셀의 시점만 두 번 조회한다.

    [2026-08-17 일반화] 처음엔 "전년동월비"(1년 전 고정)로만 만들었는데,
    실측해보니 claim이 항상 1년 전과 비교하는 게 아니었다 - 예:
    "식료품 물가지수는 2020년 9월에 비해 22.9% 올랐다"(A93bfa851 계열)는
    지수 기준연도(2020=100)와 비교하는 **5년 전** 비교였다. reference_
    period를 호출부가 명시하도록 일반화했다 - "1년 전"은 그 특수 케이스일
    뿐이다(resolve_yoy_change가 그 편의 래퍼).

    반환: {"target_value", "target_period", "reference_value",
    "reference_period", "pct_change", "unit", "prd_se", "derivation_used": True}
    - 두 시점 중 하나라도 facts에 없으면 pct_change=None, derivation_used=
    False, "reason"에 어느 시점이 없었는지 명시(추측해서 대체 시점을 쓰지
    않는다 - 모듈 docstring 참고)."""
    target_digits = _normalize_period_digits(target_period)
    reference_digits = _normalize_period_digits(reference_period)
    if not target_digits or not reference_digits:
        return {
            "target_value": None, "target_period": target_digits,
            "reference_value": None, "reference_period": reference_digits,
            "pct_change": None, "unit": None, "prd_se": None,
            "derivation_used": False,
            "reason": "target_period 또는 reference_period가 비어있거나 정규화할 숫자가 없음",
        }

    # [주의] prd_de 자리를 맨 끝에 둔다 - base_params에 축 코드를 먼저 채우고
    # 호출부(target_digits/reference_digits)에서 마지막에 붙이는 순서와
    # 맞춰야 한다(실측 버그: prd_de를 앞쪽에 두고 축 코드를 뒤에 뒀더니
    # placeholder 순서와 params 순서가 어긋나 아무 것도 안 걸렸었다).
    where = ["org_id=?", "tbl_id=?", "itm_id=?"]
    base_params: List[Any] = [org_id, tbl_id, itm_id]
    for axis, code in (axis_codes or {}).items():
        where.append(f"c{axis}=?")
        base_params.append(code)
    where.append("prd_de=?")
    query = f"SELECT prd_se, value, unit FROM facts WHERE {' AND '.join(where)}"

    target_row = conn.execute(query, base_params + [target_digits]).fetchone()
    reference_row = conn.execute(query, base_params + [reference_digits]).fetchone()

    target_value = target_row[1] if target_row else None
    reference_value = reference_row[1] if reference_row else None
    unit = (target_row[2] if target_row else None) or (reference_row[2] if reference_row else None)
    prd_se = (target_row[0] if target_row else None) or (reference_row[0] if reference_row else None)

    if target_value is None or reference_value is None:
        missing = []
        if target_value is None:
            missing.append(f"target({target_digits})")
        if reference_value is None:
            missing.append(f"reference({reference_digits})")
        return {
            "target_value": target_value, "target_period": target_digits,
            "reference_value": reference_value, "reference_period": reference_digits,
            "pct_change": None, "unit": unit, "prd_se": prd_se,
            "derivation_used": False,
            "reason": f"facts에 없는 시점: {', '.join(missing)} - 추측으로 대체하지 않음",
        }

    if reference_value == 0:
        return {
            "target_value": target_value, "target_period": target_digits,
            "reference_value": reference_value, "reference_period": reference_digits,
            "pct_change": None, "unit": unit, "prd_se": prd_se,
            "derivation_used": False,
            "reason": "reference_value가 0이라 %등락률 계산 불가(0으로 나눔)",
        }

    pct_change = (target_value - reference_value) / reference_value * 100
    return {
        "target_value": target_value, "target_period": target_digits,
        "reference_value": reference_value, "reference_period": reference_digits,
        "pct_change": pct_change, "unit": unit, "prd_se": prd_se,
        "derivation_used": True,
        "reason": (
            f"KOSIS 원자료 두 시점(target={target_digits}, reference={reference_digits})"
            f" 레벨값으로 계산한 파생값 - README 2.8: KOSIS 자체도 이 계산을 원자료가"
            f" 아니라 파생/근사치로 취급함"
        ),
    }


def resolve_yoy_change(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    axis_codes: Dict[int, str],
    target_period: str,
) -> Dict[str, Any]:
    """resolve_period_change의 편의 래퍼 - reference_period를 "target과
    같은 달(분기), 1년 전"으로 자동 계산한다(전년동월비/전년동분기비 -
    _RATE_OF_CHANGE_MARKERS의 "전년비/전년동월비/전년대비" claim이 압도적
    으로 이 기준을 쓴다). claim이 1년이 아니라 다른 기준시점(예: "2020년
    9월 대비", 지수 기준연도 비교)을 명시하면 resolve_period_change를
    reference_period를 직접 넘겨 쓸 것 - 여기서 억지로 맞추지 않는다."""
    target_digits = _normalize_period_digits(target_period)
    reference_digits = _yoy_reference_period(target_digits) if target_digits else None
    if not target_digits or not reference_digits:
        return {
            "target_value": None, "target_period": target_digits,
            "reference_value": None, "reference_period": None,
            "pct_change": None, "unit": None, "prd_se": None,
            "derivation_used": False,
            "reason": "target_period가 비어있거나 정규화할 숫자가 없음",
        }
    return resolve_period_change(conn, org_id, tbl_id, itm_id, axis_codes, target_digits, reference_digits)


# =============================================================================
# [2026-08-22 신규 - Task #27/#29 Step 1, 사용자 제공 원문(조선비즈
# 2025-10-08 "5년간 먹거리 물가 20% 이상 상승") 기반] "같은 두 시점, 다른
# 두 항목" 비교(C003/C004류 - "식료품 및 비주류음료 물가지수는 2020년 9월
# 대비 22.9% 올랐다. 같은 기간 전체 소비자물가지수 상승률(16.2%)보다
# 7%포인트 가까이 높다").
#
# 기존 함수와의 경계: resolve_period_change/resolve_yoy_change는 "같은
# 항목/축, 다른 두 시점"만 다루고, search_by_diff는 "같은 셀(org_id,
# tbl_id,itm_id,c1..c8), 다른 두 시점"만 다룬다(claim이 명시한 diff 값으로
# 전체 facts를 훑어 셀 자체를 찾아내는 용도). 이 함수가 다루는 "같은 두
# 시점, 다른 두 항목(같은 축 안의 서로 다른 leaf)"은 새로 필요했다.
#
# 새 검색 인프라를 만들지 않는다: item A(claim 자신의 항목 - Stage 1/2가
# 이미 org_id/tbl_id/itm_id/axis_codes까지 확정한 상태로 넘겨준다고 가정)는
# 그대로 쓰고, item B(비교 대상 - 보통 "총지수"/"전체")는 같은 축
# (axis_position) 안에서 _axis_total_code로 찾는다(추측 아님 -
# resolve_evidence_in_table이 이미 쓰는 폴백 기본값 로직과 완전히 같은
# 원리 재사용). item B를 못 찾으면(그 축에 총계 라벨이 없으면) None으로
# 실패 - 추측하지 않는다(Decision 003과 같은 원칙).
# =============================================================================
def resolve_item_diff_change(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    axis_codes: Dict[int, str],
    axis_position: int,
    target_period: str,
    reference_period: str,
) -> Dict[str, Any]:
    """item A(axis_codes에 이미 채워진 그대로, Stage 1/2가 확정한 claim
    자신의 항목)와, 같은 axis_position 안의 총계/전체 leaf(item B)를 각각
    resolve_period_change로 계산해 두 등락률의 차이(diff = A의 pct_change
    - B의 pct_change)를 반환한다. item B가 총지수/전체가 아니라 다른 특정
    항목이어야 하는 경우(예: 두 구체적 카테고리끼리 비교)는 아직 실측된
    사례가 없어 다루지 않는다(추측하지 않는다) - 그런 사례가 실측되면
    axis_position 대신 item B의 code를 직접 받는 파라미터를 추가할 것.

    반환: {"item_a": {...resolve_period_change 결과...}, "item_b": {...},
    "diff": float|None, "derivation_used": bool, "reason": str}
    - item B(총계 코드)를 못 찾거나, A/B 둘 중 하나라도 derivation_used가
    False면 diff=None, derivation_used=False, reason에 사유 명시(추측
    없음 원칙 유지 - resolve_period_change와 동일)."""
    rows = _dim_rows_for_table(conn, org_id, tbl_id)
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]
    total_code = _axis_total_code(category_rows, axis_position)
    if not total_code:
        return {
            "item_a": None, "item_b": None, "diff": None,
            "derivation_used": False,
            "reason": f"axis_position={axis_position}에 총계/전체 라벨(_TOTAL_LABEL_MARKERS)이 없음 - 비교 대상 항목을 추측하지 않음",
        }

    axis_codes_b = dict(axis_codes or {})
    axis_codes_b[axis_position] = total_code

    result_a = resolve_period_change(conn, org_id, tbl_id, itm_id, axis_codes, target_period, reference_period)
    result_b = resolve_period_change(conn, org_id, tbl_id, itm_id, axis_codes_b, target_period, reference_period)

    if not result_a.get("derivation_used") or not result_b.get("derivation_used"):
        missing = []
        if not result_a.get("derivation_used"):
            missing.append(f"item_a: {result_a.get('reason')}")
        if not result_b.get("derivation_used"):
            missing.append(f"item_b(총계 code={total_code}): {result_b.get('reason')}")
        return {
            "item_a": result_a, "item_b": result_b, "diff": None,
            "derivation_used": False,
            "reason": " / ".join(missing),
        }

    diff = result_a["pct_change"] - result_b["pct_change"]
    return {
        "item_a": result_a, "item_b": result_b, "diff": diff,
        "derivation_used": True,
        "reason": (
            f"item A(axis_position={axis_position} code={axis_codes.get(axis_position)})와"
            f" item B(같은 축 총계 code={total_code})의 등락률 차이 - 둘 다"
            f" KOSIS 원자료 두 시점 레벨값으로 계산한 파생값(README 2.8 원칙과 동일)"
        ),
    }


# [2026-08-18 실측 발견] "제조업의 취업자 수는 441만명이다"처럼 실제
# 기사체 문장은 핵심어에 조사가 그대로 붙어 있는 경우가 대부분인데,
# _tokenize는 형태소 분석 없이 공백만 잘라서 "취업자수는"을 통짜 토큰으로
# 만든다. search_local의 FTS 쿼리는 이 토큰을 큰따옴표로 묶어(`"취업자수는"`)
# 정확히 그 문자열만 매칭하므로(부분/접두 매칭 아님), dimensions에 저장된
# 조사 없는 이름("취업자수")과 영영 안 맞아 후보 자체가 안 나온다 - "제조업
# 취업자 수"(조사 없음)로 테스트했을 때는 정상 매칭됐던 것과 비교해서
# 실측으로 확인했다(seed_ingest_employment_breakdown.py 검증 도중 발견).
#
# 형태소 분석기 없이 최대한 단순하게 대응한다: 대표적인 조사를 접미사로만
# 떼어보고, 뗀 결과를 원본에 "추가"한다(대체하지 않음) - 실제 단어가
# 우연히 조사와 같은 글자로 끝나 잘못 잘려도(예: "속도"->"속"+도 오인)
# 원본 토큰이 그대로 남아있어 기존에 되던 매칭을 깨뜨리지 않는다. 최악의
# 경우 스코어링에 노이즈 후보가 하나 더 섞이는 정도다(다중 표 결과에서
# 진짜 매칭이 tie-break로 여전히 앞서므로 감내할 수 있는 비용) - 원본
# 보존 우선이 모듈 전체의 "추측보다 안전한 쪽을 택한다" 원칙과 일관된다.
#
# 긴 조사부터 순서대로 시도해야 짧은 조사가 먼저 걸려 잘못 잘리는 걸
# 막는다(예: "취업자에서"는 "에서"부터 봐야지 "에"만 보고 "취업자에서"를
# "취업자에서"->"취업자에" + "서"로 오분해하는 일을 피함 - 아래 리스트는
# 길이 내림차순으로 나열).
_KOREAN_PARTICLES = (
    "에서는", "으로는", "에게는", "한테는",
    "에서", "에게", "한테", "부터", "까지", "이나", "이라는", "라는",
    "으로", "이라",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "뿐",
)


def _strip_korean_particle(token: str) -> Optional[str]:
    """토큰 끝의 대표적인 조사를 제거한 버전을 돌려준다(없으면 None).
    떼어낸 결과가 2글자 미만이면(예: "것이"->"것") 노이즈가 커서 버린다."""
    for p in _KOREAN_PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            return token[: -len(p)]
    return None


# [2026-08-18 실측 발견] "보건·사회복지서비스업"(claim 원문, 가운뎃점으로
# 병렬 연결된 명사구)은 "·"가 구두점이라 _tokenize에서 공백 취급돼
# "보건"+"사회복지서비스업" 두 토큰으로 갈리는데, 실제 KOSIS 축 이름은
# "보건업 및 사회복지 서비스업"이다 - 뒤쪽 조각들엔 "업"이 붙어있는데
# 앞쪽 조각("보건")엔 안 붙어서 정확히 안 맞는다(실측: dimensions_fts
# MATCH '"보건"' 0건, 실제 축 이름 토큰은 "보건업"). 전체 토큰에 접두
# 매칭을 적용해 이 문제를 풀어보려 했으나("보건"* -> "보건업" 매칭),
# "전년"/"동월"처럼 흔한 2글자 단어가 무관한 표의 긴 항목명에 접두로
# 우연히 걸리는 노이즈가 실측으로 확인돼(예: "제조업 취업자" claim이
# 소비자물가 등락률 표로 밀려남) 되돌렸다(_fts_match_query 주석 참고).
#
# 대신 "A·B" 패턴 자체("및"으로 병렬 연결된 명사구를 가운뎃점으로 압축
# 표기하는 한국어 관용 표기)만 좁게 겨냥한다: 마지막 조각이 "업"류
# 접미사로 끝나면, 그 접미사를 앞쪽 조각에도 빌려줘서 후보 토큰을
# 추가로 만든다("보건"+"업" -> "보건업"). 접두 매칭처럼 전체 토큰
# 매칭 방식을 흔들지 않고, "·"가 실제로 있는 곳에서만 발동하므로 다른
# claim에 노이즈를 퍼뜨리지 않는다.
_CATEGORY_SUFFIXES = ("서비스업", "산업", "업종", "관련업", "업")


def _borrow_category_suffix(text: str) -> List[str]:
    """text 안의 "A·B·C" 가운뎃점 병렬 명사구를 찾아, 마지막 조각이
    업종류 접미사로 끝나면 그 접미사를 앞쪽 조각들에도 붙인 후보를
    돌려준다(원본 조각은 이미 일반 토큰화 경로로 따로 들어가므로 여기선
    "빌려붙인" 버전만 추가로 만든다). 여러 접미사가 동시에 해당되면
    (예: "사회복지서비스업"은 "서비스업"과 "업" 둘 다에 걸림) 전부
    후보로 만든다 - 틀린 후보가 섞여도 원본 매칭을 대체하는 게 아니라
    OR로 추가되는 것뿐이라 안전하다."""
    extra: List[str] = []
    for group in re.findall(r"[가-힣]+(?:·[가-힣]+)+", text or ""):
        pieces = group.split("·")
        if len(pieces) < 2:
            continue
        last = pieces[-1]
        matched_suffixes = [s for s in _CATEGORY_SUFFIXES if last.endswith(s)]
        for piece in pieces[:-1]:
            for suf in matched_suffixes:
                if not piece.endswith(suf):
                    candidate = piece + suf
                    if len(candidate) >= 2:
                        extra.append(candidate)
    return extra


# [2026-08-18 Task #25 - 인구통계 별칭] "청년층 고용률"처럼 뉴스가 쓰는
# 인구통계 수식어는 KOSIS 축 코드 자체("15~29세")와 글자가 달라서, 이걸
# 모르면 우연히 겹치는 엉뚱한 ITEM으로 잘못 수렴한다 - 실측 확인:
# DT_1DE9046S에서 phrases=["청년층","고용률"]로 resolve_evidence_by_
# flat_match를 부르면 top 5가 전부 itm_id="T00"(ITEM 이름이 그대로
# "청년층인구"라 "청년층" 글자와 우연히 겹침)이고, 정작 원하는
# itm="T21"(고용률)×axis="15~29세" 조합은 후보에도 안 들어갔다.
# phrases=["고용률","15~29세"]로 바꾸면 그 조합이 매칭 2건(score=2,
# unexplained_axes=0)으로 명확히 1위가 되는 것까지 확인했다.
#
# 아래 매핑은 새로 추측한 값이 아니라, 레거시 파이프라인
# (kosis_config.py DEMOGRAPHIC_ROW_ALIAS_MAP)에 이미 있던 것을 그대로
# 재사용한다 - "청년 실업률" claim을 실제 KOSIS API로 검증하던 중
# (2026-07) LLM이 "청년"을 노이즈로 보고 지표명에서 빼버리는 사고를
# 겪고 나서 만들어진, 이미 실측으로 검증된 매핑이다. "청년층"/"고령층"
# 등 새 엔진에서 실제로 마주친 변형만 추가했다.
#
# [2026-08-22 실측 발견 - A82ae9f41-C011, 사용자 결정] KOSIS는 같은
# 연령대를 표마다 다르게 표기한다 - 실측 확인: DT_1DE9046S는 "* 15~29세"
# (물결)인데 DT_1DA7012S는 "15 - 29세"(대시+공백)를 쓴다. 값(2)에
# "15~29세" 하나만 있으면 후자 표의 breadcrumb과 literal로 안 겹쳐서
# corroboration(≥2 phrase) 문턱을 못 넘고, 사실상 완전 일치(rel_diff
# ~1e-15)하는 정답 후보가 버려지는 사고가 있었다(C011, README 참고).
# 섣부른 일반 정규화(대시/물결을 동일 취급하는 정규식 등)는 다른 표기
# 변형(예: "20-24세"처럼 공백 없는 대시)이 또 있을 수 있어 보류하고,
# 사용자 결정대로 "발견될 때마다" 값 리스트에 실측된 변형만 하나씩
# 추가하는 방식을 쓴다 - 값을 문자열 하나에서 리스트로 바꿔 여러 표기를
# 동시에 커버한다.
_DEMOGRAPHIC_ALIASES = {
    "청년": ["15~29세", "15 - 29세"],
    "청년층": ["15~29세", "15 - 29세"],
    "청소년": ["15~29세", "15 - 29세"],
    "고령자": ["65세 이상"],
    "고령": ["65세 이상"],
    "고령층": ["65세 이상"],
    "노인": ["65세 이상"],
    "여성": ["여자"],
    "남성": ["남자"],
}


def _tokenize(text: Optional[str]) -> List[str]:
    """원문장/키워드에서 FTS5에 넣을 토큰을 뽑는다 - 조사/구두점을
    공백으로 치환한 뒤 공백 분리(형태소 분석기 없이 최대한 단순하게,
    2글자 미만은 원칙적으로 노이즈가 많아 제외하되, 한글 1글자는 예외로
    허용한다). 조사가 붙은 토큰은 뗀 버전도, "A·B업" 가운뎃점 병렬구는
    접미사를 빌려붙인 버전도, 인구통계 수식어는 KOSIS 축 라벨로 별칭
    변환한 버전도 함께 추가한다
    (_strip_korean_particle/_borrow_category_suffix/_DEMOGRAPHIC_ALIASES
    참고 - 원본은 유지, 셋 다 "추가"만 하지 "대체"하지 않는다).

    [2026-08-22 실측 버그 수정 - A93bfa851-C007/C009] 원래 2글자 미만은
    전부 제외했는데, "빵(38.5%)"/"떡(25.8%)"처럼 한글 1음절 품목명이
    괄호 등 구두점 제거로 독립 토큰이 되는 경우 이 규칙에 걸려 "빵"/"떡"
    자체가 통째로 사라지고 "물가"처럼 흔한 동반 단어만 match_phrases에
    남는 사고가 실측으로 확인됐다:
    - C007(빵 물가 38.5%): match_phrases=['물가']만 남아서 "수입물가지수
      (품목별)"라는 완전히 무관한 표(빵과 무관, 심지어 통화기준 3파전
      동점)로 튐.
    - C009(떡 물가 25.8%): 표(DT_1J22001)는 맞게 골랐지만 "떡"이 사라져서
      실제 "떡" 품목(A01117, kosis_table_info로 실측 확인됨)이 아니라
      "전국 0 총지수"(헤드라인 CPI)로 잘못 매칭됨.
    한글 1글자만 예외로 허용하는 이유: 숫자 파편("13.1%"의 "13"/"1")은
    한글이 아니라서 이 예외에 안 걸리고, 여전히 아래 isdigit() 필터와
    무관하게 애초에 라인에서 걸러진다 - 순수 숫자/영문 1글자 파편에 대한
    기존 노이즈 방지는 그대로 유지된다."""
    if not text:
        return []
    cleaned = re.sub(r"[^\w가-힣]+", " ", str(text))
    raw_tokens = [
        t for t in cleaned.split()
        if len(t) >= 2 or (len(t) == 1 and "가" <= t <= "힣")
    ]
    tokens: List[str] = []
    seen = set()
    for t in raw_tokens:
        if t not in seen:
            tokens.append(t)
            seen.add(t)
        stripped = _strip_korean_particle(t)
        if stripped and stripped not in seen:
            tokens.append(stripped)
            seen.add(stripped)
        for candidate in (t, stripped):
            aliases = _DEMOGRAPHIC_ALIASES.get(candidate) if candidate else None
            for alias in (aliases or []):
                if alias not in seen:
                    tokens.append(alias)
                    seen.add(alias)
    for borrowed in _borrow_category_suffix(str(text)):
        if borrowed not in seen:
            tokens.append(borrowed)
            seen.add(borrowed)
    # [2026-08-21 실측 버그 수정 - A93bfa851-C018] 순수 숫자로만 된 토큰은
    # 여기서 전부 걸러낸다. "13.1%였다"처럼 소수점/기호가 공백으로
    # 치환되면서 "13"/"1" 같은 숫자 파편이 독립 토큰으로 살아남는데, 이런
    # 토큰은 claim의 "값"이지 표/항목을 가리키는 개념어가 아니다. 실측
    # 확인된 사고: 이 "13"이 "유가증권 순위별 거래"라는 완전히 무관한
    # 표의 순위 축 코드(이름이 문자 그대로 "13")와 FTS로 우연히 걸렸고,
    # bm25가 희귀한 토큰일수록 점수를 높게 줘서(코퍼스 전체에서 "13"이
    # 워낙 희귀함) 이 가짜 매칭(matched_term_count=1)이 진짜 정답표(주류/
    # 담배 2개 토큰 실제 일치, matched_term_count=2)를 점수로 역전해버렸다
    # - 정답표는 후보 목록엔 있었는데 순위에서 밀려 UNRESOLVED조차 아니고
    # 완전히 엉뚱한 표가 채택됐다(A93bfa851-C018, README 참고). "2020년"
    # 처럼 숫자+한글이 붙은 토큰은 이 필터에 안 걸린다(isdigit()이
    # False라서 그대로 남음) - 연도류 매칭은 그대로 유지된다.
    return [t for t in tokens if not t.isdigit()]


def build_axis_trees(conn: sqlite3.Connection, org_id: str, tbl_id: str) -> Dict[int, Dict[str, Any]]:
    """[2026-08-22 신규 - 사용자 설계, HCX 토큰 폭발 대응] iter_table_cell_
    texts는 facts에 실제로 존재하는 (itm_id, c1..c8) 조합, 즉 축들의
    카테시안 곱 하나하나를 "조상 이름까지 전부 이어붙인" flat text로
    만든다 - 그래서 표 하나에 축이 여러 개면(예: 지역 19개 × 지출목적별
    581개) 같은 조상 이름이 카테시안 곱 개수만큼 반복돼 문자 수가
    폭발한다(실측: DT_1J22001 한 표가 673,343자 - HCX 분당 토큰 한도를
    요청 한 번에 다 씀).

    이 함수는 카테시안 곱을 만들지 않고, `dimensions` 테이블에 있는
    "축 자체의" 트리 구조를 axis_position별로 딱 한 번씩만 렌더링한다 -
    같은 표라도 실제 고유 노드 수(예: 지역 19개 + 지출목적별 581개 = 600
    줄)에 비례하는 크기로 끝나고, 노드 이름이 조상 반복 없이 정확히 한 번만
    등장한다. claim이 언급 안 한 축(예: 지역)은 굳이 축소하지 않고 그
    축 전체를 그대로 보낸다 - "동점 후보 밖의 정답도 찾아야 한다"(예:
    "정부 빚" claim이 literal 매칭이 놓친 A01/총계를 찾아야 했던 실측
    버그, README "스물한 번째" 항목)는 기존 요구사항을 축 단위에서는
    그대로 지킨다(카테시안 곱만 없앨 뿐 축 자체의 노드는 안 자른다).

    반환: {axis_position: {"axis_label": str|None, "tree_text": str,
    "codes": set(그 축에 실제 존재하는 code 전체)}} - ITEM(obj_id=="ITEM")
    행은 별도 축이 아니라 이 표의 "항목"이라 여기 포함 안 한다(item_by_id로
    따로 다뤄야 함, 호출부 책임)."""
    rows = _dim_rows_for_table(conn, org_id, tbl_id)
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]

    by_axis: Dict[int, List[Dict[str, Any]]] = {}
    for r in category_rows:
        axis_position = r.get("axis_position")
        if axis_position is None:
            continue
        by_axis.setdefault(axis_position, []).append(r)

    trees: Dict[int, Dict[str, Any]] = {}
    for axis_position, axis_rows in by_axis.items():
        by_code = {r["code"]: r for r in axis_rows if r.get("code")}
        children_of: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for r in axis_rows:
            parent_code = r.get("parent_code")
            # 부모 code가 이 축 안에 실제로 없으면(다른 축과 code가 우연히
            # 겹쳤거나 진짜 루트) 루트 취급한다 - _row_children과 같은
            # axis_position 스코핑 원칙.
            key = parent_code if parent_code in by_code else None
            children_of.setdefault(key, []).append(r)

        lines: List[str] = []

        def _walk(node: Dict[str, Any], depth: int) -> None:
            name = node.get("name") or ""
            code = node.get("code") or ""
            lines.append("  " * depth + f"{name} [{code}]")
            for child in children_of.get(node.get("code"), []):
                _walk(child, depth + 1)

        for root in children_of.get(None, []):
            _walk(root, 0)

        axis_label = axis_rows[0].get("axis_label") if axis_rows else None
        trees[axis_position] = {
            "axis_label": axis_label,
            "tree_text": "\n".join(lines),
            "codes": set(by_code.keys()),
        }
    return trees


def _expand_terms(keywords: Optional[List[str]]) -> List[str]:
    """keywords(각 항목이 "제조업 취업자 수"처럼 여러 단어가 붙은 구일 수
    있음)를 전부 _tokenize로 쪼개 중복 없는 검색어 토큰 목록을 만든다.
    [2026-08-17 신규] _fts_match_query와 search_local의 표 순위 tie-break
    (매칭된 distinct 토큰 개수)가 같은 토큰 목록을 써야 일관되므로 분리."""
    terms: List[str] = []
    seen = set()
    for kw in keywords or []:
        for tok in _tokenize(kw):
            if tok not in seen:
                terms.append(tok)
                seen.add(tok)
    return terms


def _fts_match_query(keywords: Optional[List[str]]) -> Optional[str]:
    """[2026-08-18 실측 - 접두(prefix) 매칭은 시도했다가 되돌림] "보건"이
    "보건업"에 안 걸리는 문제(아래 _borrow_category_suffix 참고)를 SQLite
    FTS5의 접두 매칭(`"term"*`)으로 풀어보려 했으나, 전체 토큰에 일괄
    적용하니 "전년"/"동월"/"대비"처럼 흔한 2글자 단어가 전혀 무관한 표의
    "전년동월비(%)" 같은 긴 항목명에 접두로 걸려 노이즈가 실측으로
    확인됐다 - "제조업 취업자" claim이 등락률 표(DT_1J22042)에 밀려
    상위 후보에서 사라지는 회귀가 실제로 재현됨(원래는 DT_1DA7E06S_NEW가
    잘 나왔었음). 짧은 흔한 단어가 다른 도메인의 긴 복합어 접두에 우연히
    걸리는 게 원인이라 길이 기준으로 거르기도 애매하다("보건"도 2글자라
    "전년"과 길이로 구분이 안 됨) - 그래서 전면 접두 매칭은 되돌리고,
    "·"(가운뎃점) 병렬 명사구처럼 원인이 명확한 특정 패턴만 추가 후보
    토큰으로 좁혀서 대응한다(_borrow_category_suffix, _tokenize 참고)."""
    terms = _expand_terms(keywords)
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def _clean_tbl_nm(tbl_nm: Optional[str]) -> str:
    """표 이름에서 괄호 부가설명("(2020=100)" 등)을 떼어낸 핵심 문구만
    남긴다 - 그래야 원문장에 그 핵심 문구가 그대로 들어있는지 부분
    문자열로 비교할 수 있다."""
    return re.sub(r"\([^)]*\)", "", tbl_nm or "").strip()


def _axis_leaf_samples(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, obj_id: str, max_samples: int = 5,
) -> List[str]:
    """[2026-08-22 신규 - Task #1(PPI 품목별/기본분류 구분 불가) 대응,
    max_depth 실측 반증 후 대체] 이 축의 실제 리프(자식이 없는 노드)
    이름을 몇 개 그대로 샘플링해서 돌려준다.

    ## 왜 max_depth(트리 깊이) 대신 이걸 쓰는가 - 실측으로 반증됨

    처음엔 "분류 트리가 더 깊으면 더 세분화된 표"라는 일반 원칙(HS코드/
    COICOP류 통계분류체계의 흔한 설계 관례)으로 `_axis_max_depth`를
    만들어 썼는데, 사용자가 로컬에서 실제 DT_404Y016(품목별)/DT_404Y014
    (기본분류)를 덤프해보니 정반대였다: 016은 깊이 3에서 바로 "쌀"/
    "보리쌀"/"콩" 같은 개별 품목 리프가 나오는데, 014는 깊이 5까지
    가도 리프가 "곡류"/"콩류"/"채소" 같은 분류군 이름이었다 - 014가
    016보다 트리는 더 깊지만 실제로는 더 안 세분화된 것. "깊이=세분화
    정도"라는 일반 원칙이 이 표 쌍에는 안 맞았다(실측으로 폐기,
    README 참고).

    ## 대안: 리프 이름 자체를 직접 보여주고 판단은 HCX(AI)에 맡긴다

    "리프 이름이 개별 품목명이냐 분류군 이름이냐"는 사람이 눈으로 보면
    바로 판단되는데(축값 샘플이 이미 그런 용도로 쓰이고 있음), 이걸
    숫자 하나(깊이)로 손수 압축하려다 실측으로 반증됐다 - 압축하지 말고
    실제 리프 이름 몇 개를 그대로 hcx_stage1_resolver의 프롬프트에
    실어서, "이게 구체적 품목인지 분류군인지"는 이미 이 문제에 붙이기로
    한 HCX-007 자신이 읽고 판단하게 한다(사용자: "사람이 보면 자명하게
    판단할 수 있는 구조면 그 부분 자체에 AI를 붙이는 게 맞다")."""
    rows = conn.execute(
        "SELECT code, name, parent_code FROM dimensions WHERE org_id=? AND tbl_id=? AND obj_id=?",
        (org_id, tbl_id, obj_id),
    ).fetchall()
    by_code = {code for code, _, _ in rows if code}
    # 이 축 안에 진짜 부모-자식 관계(자식의 parent_code가 같은 축의
    # 실제 코드를 가리킴)가 하나도 없으면 - 모든 행이 이미 평면적인
    # 최상위 목록이면 - "리프"가 곧 list_registered_tables의 values와
    # 완전히 같은 것이므로 중복해서 보여줄 필요가 없다(빈 리스트로
    # 반환해서 호출부가 leaf_samples 키 자체를 생략하게 한다).
    has_hierarchy = any(parent_code in by_code for _, _, parent_code in rows if parent_code)
    if not has_hierarchy:
        return []
    parent_codes = {parent_code for _, _, parent_code in rows if parent_code in by_code}
    leaves = [name for code, name, _ in rows if code not in parent_codes and name]
    return leaves[:max_samples]


def _char_bigrams(text: Optional[str]) -> set:
    """공백을 지운 문자열의 2-글자 슬라이딩 윈도우 집합. 한국어는 형태소
    분석 없이 어절 단위 토큰화가 어렵지만(조사/어미가 붙음), 표 이름
    간 "겹치는 정도"를 보는 목적에는 문자 bigram의 자카드 유사도로도
    충분하다 - 임베딩/형태소 분석 같은 새 의존성을 추가하지 않는다."""
    s = (text or "").replace(" ", "")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _table_name_similarity(a: Optional[str], b: Optional[str]) -> float:
    """두 표 이름의 문자 bigram 자카드 유사도(0.0~1.0)."""
    bigrams_a, bigrams_b = _char_bigrams(a), _char_bigrams(b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    union = bigrams_a | bigrams_b
    return len(bigrams_a & bigrams_b) / len(union) if union else 0.0


# [2026-08-24 신규 - Task #1 연장, 사용자 지적("DB는 계속 커질 텐데 모든
# 표를 다 보여주는 건 아니다") 대응] axis_hints/leaf_samples는 애초에
# "표 이름이 비슷비슷해서 헷갈리는 표 쌍"을 구분하려고 추가한 것이었다
# (지출목적별/품목성질별/품목별(품목성질별) 소비자물가지수, 생산자물가지수
# 품목별/기본분류/특수분류) - 표 이름이 다른 표들과 전혀 안 겹치는
# 표라면애초에 axis_hints 없이 이름만 봐도 구분에 문제가 없었다. 이
# 임계값(0.3)은 실제 두 confusable 쌍으로 역산해서 정했다: "생산자물가
# 지수(품목별)"/"생산자물가지수(기본분류)" 문자 bigram 자카드 ≈0.44,
# "지출목적별 소비자물가지수"/"품목성질별 소비자물가지수" ≈0.47 - 둘 다
# 이 임계값을 넉넉히 넘는다. 무관한 표 이름 쌍(예: "GDP 대비 일반정부
# 총금융부채 비율" vs "성/연령별 경제활동인구")은 공유 bigram이 거의
# 없어 이 임계값에 한참 못 미친다. 이 값은 현재 카탈로그(26개 표)
# 기준 역산치이므로, 카탈로그가 훨씬 커지면(test_list_registered_
# tables.py의 회귀 테스트가 실패하면) 재검토가 필요하다 - 추측이 아니라
# 실측 가능한 지점을 코드에 남겨둔다.
_CONFUSABLE_NAME_SIMILARITY_THRESHOLD = 0.3


def _compute_confusable_flags(
    tbl_names: List[Optional[str]], threshold: float = _CONFUSABLE_NAME_SIMILARITY_THRESHOLD,
) -> List[bool]:
    """tbl_names[i]가 다른 어떤 tbl_names[j](i!=j)와도 threshold 이상
    비슷하면 True(= "표 이름만으로는 구분이 안 될 수 있어 axis_hints가
    필요") - 카탈로그 전체가 아니라 표 이름 문자열끼리만 비교하므로
    claim/DB 조회 없이 계산된다(그래서 O(n^2)이어도 표 수백 개 수준까지는
    저렴함, `_table_name_matches`의 기존 판단과 같은 전제)."""
    n = len(tbl_names)
    flags = [False] * n
    for i in range(n):
        for j in range(i + 1, n):
            if _table_name_similarity(tbl_names[i], tbl_names[j]) >= threshold:
                flags[i] = True
                flags[j] = True
    return flags


def list_registered_tables(
    conn: sqlite3.Connection, max_axis_values: int = 6, max_axes: int = 4,
) -> List[Dict[str, Any]]:
    """[2026-08-21 신규 - Task #80 확장, LLM 기반 Stage 1 표 선택용]
    tables_registry 전체를 org_id/tbl_id/tbl_nm/stat_nm/axis_hints
    딕셔너리 리스트로 돌려준다.

    axis_hints를 추가한 이유(실측 발견, 2026-08-21): 표 이름만으로는
    구분 안 되는 진짜 사례가 있다 - "지출목적별 소비자물가지수"/"품목성질별
    소비자물가지수"/"품목별 소비자물가지수(품목성질별)" 셋 다 ITEM은
    "소비자물가지수" 하나뿐이고, 이름만 봐서는 "주류"가 어느 표에
    속하는지 알 방법이 없다. 실제로 이 셋만 표 이름으로 HCX-007에
    물어봤더니(probe_c018_stage1_llm_table_select.py 실 API 검증) 정답
    (DT_1J22001)이 아니라 다른 두 표를 재현성 없이(콜마다 다르게) 골랐다
    - "주류 및 담배"라는 실제 분류값이 지출목적별 표의 최상위 분류축
    (`axis_label='지출목적별'`, `parent_code IS NULL`)에만 존재하는데,
    표 이름에는 이 정보가 없었기 때문이다. 각 표의 분류축 이름 + 최상위
    분류값 샘플을 같이 주면 이 정보가 채워진다.

    ITEM(obj_id='ITEM')은 표가 측정하는 지표 자체를 가리키는 축이라
    "축값 샘플"에서 제외한다(이미 tbl_nm/ITEM 자체가 대부분 표 제목과
    겹친다 - 예: CPI류는 셋 다 ITEM이 "소비자물가지수"뿐이라 구분에
    도움이 안 됨, 오히려 분류축의 최상위 "값"이 실제 구분 정보다).
    최상위 값(parent_code IS NULL)만 쓴다 - 리프까지 다 주면(실측:
    최대 581개) 프롬프트가 불필요하게 커진다.

    현재(2026-08-21 실측) 19개 표, 축 힌트까지 포함해도 총 텍스트가
    2000자 미만이라(가장 큰 축도 20여 개 값) 전체를 한 번에 LLM
    프롬프트에 넣어도 부담이 없다(`_table_name_matches`의 "표가
    수백~수천 개 되기 전까지는 전체 스캔이 싸다"는 기존 판단과 같은
    전제 - 표 개수가 실제로 그 규모에 도달하면 이 함수도 재검토 필요).

    [2026-08-22 추가 - Task #1, max_depth 실측 반증 후 leaf_samples로
    교체] 각 axis_hints 항목에 leaf_samples(그 축의 실제 리프 이름
    몇 개, `_axis_leaf_samples` 참고)를 같이 담는다. PPI 품목별
    (DT_404Y016)/기본분류(DT_404Y014)처럼 최상위 값 샘플만으로는 구분이
    안 되는 표 쌍이 실측으로 확인됐다(README 참고) - 처음엔 "분류 트리
    깊이"로 구분해보려 했으나 실측 결과 014가 016보다 트리는 더 깊은데
    리프는 더 안 구체적이라(깊이-세분화 상관관계 자체가 반증됨) 폐기,
    대신 리프 이름을 그대로 보여주고 "개별 품목이냐 분류군이냐" 판단은
    HCX-007에게 맡긴다. leaf_samples가 최상위 값 샘플(values)과 완전히
    같으면(트리가 얕아 이미 리프까지 보여준 경우) 중복이라 생략한다.

    [2026-08-24 신규 - 표가 계속 늘어나는 문제 대응] axis_hints는 모든
    표에 무조건 붙이지 않는다 - `_compute_confusable_flags`로 이름이
    다른 어떤 표와도 안 겹치는(bigram 유사도 < 임계값) 표는 애초에
    이름만으로 구분에 문제가 없었으므로 axis_hints 계산 자체(축별 DB
    조회 + `_axis_leaf_samples`)를 건너뛴다. 이름이 비슷한 표가 있는
    표만 축 상세를 계산해서 붙인다 - Stage 1 HCX 프롬프트 크기가
    표 개수가 아니라 "이름이 겹치는 표 군집의 개수"에 비례하게 만들어,
    표가 계속 늘어도(대부분 새 표는 기존 표들과 이름이 안 겹치는 새
    도메인일 가능성이 높음) 비용이 그만큼 같이 늘지 않게 한다."""
    rows = conn.execute(
        "SELECT org_id, tbl_id, tbl_nm, stat_nm FROM tables_registry"
    ).fetchall()
    confusable_flags = _compute_confusable_flags([tbl_nm for _, _, tbl_nm, _ in rows])
    result = []
    for (org_id, tbl_id, tbl_nm, stat_nm), needs_axis_detail in zip(rows, confusable_flags):
        if not needs_axis_detail:
            result.append({
                "org_id": org_id, "tbl_id": tbl_id, "tbl_nm": tbl_nm, "stat_nm": stat_nm,
                "axis_hints": [],
            })
            continue
        axis_rows = conn.execute(
            "SELECT DISTINCT obj_id, axis_label FROM dimensions "
            "WHERE org_id=? AND tbl_id=? AND obj_id != 'ITEM'",
            (org_id, tbl_id),
        ).fetchall()
        axis_hints = []
        for obj_id, axis_label in axis_rows[:max_axes]:
            root_rows = conn.execute(
                "SELECT code, name FROM dimensions WHERE org_id=? AND tbl_id=? "
                "AND obj_id=? AND parent_code IS NULL LIMIT ?",
                (org_id, tbl_id, obj_id, max_axis_values),
            ).fetchall()
            # [2026-08-21 실측 발견 - 스트레스 테스트 클러스터 준비 중]
            # 최상위(parent_code IS NULL) 값이 딱 1개뿐인 축이 있다 -
            # 예: 생산자물가지수 3종(품목별/기본분류/특수분류) 전부
            # "총지수" 하나가 유일한 최상위 값이고, 실제로 표를 구분해
            # 주는 이름("에너지구분"/"식료품구분" 등)은 그 밑 자식
            # 레벨에 있었다. 최상위가 1개뿐이면(구분력 없음) 그 자식
            # 레벨로 한 단계 내려가 샘플을 가져온다 - 여러 개면(이미
            # 구분력이 있으면, 예: 취업자류의 "계/남자/여자") 그대로 쓴다.
            if len(root_rows) == 1:
                root_code = root_rows[0][0]
                value_rows = conn.execute(
                    "SELECT name FROM dimensions WHERE org_id=? AND tbl_id=? "
                    "AND obj_id=? AND parent_code=? LIMIT ?",
                    (org_id, tbl_id, obj_id, root_code, max_axis_values),
                ).fetchall()
            else:
                value_rows = [(name,) for _, name in root_rows]
            values = [v[0] for v in value_rows if v[0]]
            if values:
                hint = {"axis_label": axis_label, "values": values}
                # [2026-08-22 신규 - max_depth 실측 반증 후 대체] 축값
                # 샘플이 표들 사이에 겹쳐도 실제 리프 이름은 다를 수
                # 있다 - _axis_leaf_samples 참고. 이 축에 진짜 계층이
                # 없으면(평면 목록) 헬퍼가 빈 리스트를 돌려주므로 자연히
                # 생략된다(values와 중복 방지).
                leaf_samples = _axis_leaf_samples(conn, org_id, tbl_id, obj_id)
                if leaf_samples:
                    hint["leaf_samples"] = leaf_samples
                axis_hints.append(hint)
        result.append({
            "org_id": org_id, "tbl_id": tbl_id, "tbl_nm": tbl_nm, "stat_nm": stat_nm,
            "axis_hints": axis_hints,
        })
    return result


def _table_name_matches(conn: sqlite3.Connection, raw_sentence: str, keywords: Optional[List[str]]) -> List[tuple]:
    """[2026-08-16 실측 발견] dimensions_fts는 항목/축 이름만 인덱싱하므로
    (예: "총지수"), claim이 표 이름 자체를 언급하지만 그 문구가 항목명에는
    없는 경우(예: "소비자물가지수"는 표 이름에만 있고 ITEM 행 이름은
    "총지수")를 완전히 놓친다 - 실제 시나리오("이달 소비자물가지수가
    발표됐다")로 테스트하다가 이 매칭 자체가 전혀 안 되는 걸 발견했다.

    tables_registry는 (지금 규모에서는, 앞으로도 표가 수백~수천 개 되기
    전까지는) 전체를 훑어도 충분히 싸므로, FTS 대신 단순 부분 문자열
    비교로 보완한다. 조사가 붙어 정확히 안 맞을 수 있어(예: "지수가")
    양방향으로 비교한다 - 표의 핵심 문구가 원문장 안에 있거나, 주어진
    키워드가 표 이름 안에 있으면 매칭으로 인정한다."""
    texts = [raw_sentence] if raw_sentence else []
    kws = [k for k in (keywords or []) if k]
    if not texts and not kws:
        return []
    rows = conn.execute("SELECT org_id, tbl_id, tbl_nm FROM tables_registry").fetchall()
    matched = []
    for org_id, tbl_id, tbl_nm in rows:
        core = _clean_tbl_nm(tbl_nm)
        if not core:
            continue
        hit = any(core in t for t in texts if t) or any(kw in core or core in kw for kw in kws)
        if hit:
            matched.append((org_id, tbl_id, tbl_nm))
    return matched


def search_local(
    conn: sqlite3.Connection,
    raw_sentence: str,
    keywords: Optional[List[str]] = None,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """claim 원문장(+선택적으로 미리 뽑아둔 keywords)에 맞는 표 후보를
    로컬 DB에서 규칙 기반으로 찾는다. keywords를 안 주면 원문장을 직접
    토큰화해서 쓴다(기존 파이프라인의 keyword_generator.py 산출물을
    그대로 받을 수 있게 하되, 독립적으로도 쓸 수 있게).

    두 경로로 후보 표를 모은다: (1) dimensions_fts로 항목/축 이름 매칭,
    (2) tables_registry.tbl_nm 부분 문자열 매칭(표 이름 자체가 언급된
    경우 - FTS만으로는 못 잡는다). 후보가 정해지면, 각 표의 "가진 항목
    구성"(measure_type 전체, FTS로 매칭된 항목뿐 아니라)을 다시 조회해서
    점수를 매긴다 - 표 이름 매칭으로만 찾은 후보도 정확한 채점이 되도록.

    반환: 점수 내림차순 후보 리스트, 각 항목은
    {org_id, tbl_id, tbl_nm, stat_nm, is_international, score, matched_items, reasons}.
    """
    tokens = keywords if keywords else _tokenize(raw_sentence)
    terms = _expand_terms(tokens)  # tie-break용 - FTS 쿼리와 같은 토큰 집합
    fts_query = _fts_match_query(tokens)

    fts_rows = []
    if fts_query:
        fts_rows = conn.execute(
            "SELECT f.org_id, f.tbl_id, f.obj_id, f.code, f.name, bm25(dimensions_fts) AS rank "
            "FROM dimensions_fts f WHERE dimensions_fts MATCH ? ORDER BY rank LIMIT 300",
            (fts_query,),
        ).fetchall()

    by_table: Dict[tuple, Dict[str, Any]] = {}
    for org_id, tbl_id, obj_id, code, name, rank in fts_rows:
        key = (org_id, tbl_id)
        entry = by_table.setdefault(key, {"best_rank": rank, "matches": []})
        entry["best_rank"] = min(entry["best_rank"], rank)  # bm25는 낮을수록 더 관련성 높음(SQLite 관례)
        entry["matches"].append({"obj_id": obj_id, "code": code, "name": name})

    for org_id, tbl_id, tbl_nm in _table_name_matches(conn, raw_sentence, keywords):
        key = (org_id, tbl_id)
        entry = by_table.setdefault(key, {"best_rank": 0.0, "matches": []})
        if not entry["matches"]:
            entry["matches"].append({"obj_id": None, "code": None, "name": f"[표 이름 매칭] {tbl_nm}"})

    if not by_table:
        return []

    sentence_text = raw_sentence or ""
    rate_intent = any(m in sentence_text for m in _RATE_INTENT_MARKERS)
    international_intent = any(m in sentence_text for m in _INTERNATIONAL_INTENT_MARKERS)

    candidates = []
    for (org_id, tbl_id), entry in by_table.items():
        reg = conn.execute(
            "SELECT tbl_nm, vw_cd, stat_nm FROM tables_registry WHERE org_id=? AND tbl_id=?",
            (org_id, tbl_id),
        ).fetchone()
        tbl_nm, vw_cd, stat_nm = reg if reg else (None, None, None)
        is_international = is_international_survey(vw_cd, stat_nm)

        # 이 표가 가진 전체 ITEM 행의 measure_type(검색 시점에 원본
        # name/unit_hint로 계산 - DB에 저장돼 있지 않다) - FTS로 매칭된
        # 항목뿐 아니라 표 전체를 봐야, 표 이름 매칭으로만 찾은 후보도(그
        # 표의 어떤 구체적 항목이 매칭됐는지 모르는 경우도) 정확히 채점된다.
        item_rows = conn.execute(
            "SELECT name, unit_hint FROM dimensions WHERE org_id=? AND tbl_id=? AND obj_id='ITEM'",
            (org_id, tbl_id),
        ).fetchall()
        measure_types = {_infer_measure_type(name, unit_hint) for name, unit_hint in item_rows}

        score = -entry["best_rank"]  # 부호 반전: 높을수록 좋게
        reasons = [f"매칭 {len(entry['matches'])}건"]

        # [2026-08-17 실측 버그 수정] bm25 최솟값만 보면 "취업자"만 걸리는
        # 표와 "취업자"+"제조업"이 둘 다 걸리는 표가 동점이 돼서, 더 구체적인
        # 표를 우선할 방법이 없었다(실측: A82ae9f41-C007 "제조업 취업자 수"가
        # 시도별 표로 잘못 뽑혀 산업 축이 아예 없는 전국 총계만 나온 사례).
        # bm25 점수 자체를 흔들지 않고, 이 표의 매칭 행 이름들에 실제로
        # 몇 개의 distinct 검색어가 걸렸는지를 별도 tie-break 키로 둔다.
        matched_term_count = sum(
            1 for t in terms if any(t in (m.get("name") or "") for m in entry["matches"])
        )

        # [2026-08-17 실측 버그 수정 - "우연한 단어 겹침" 문제] "교육 물가"
        # claim이 소비자물가지수 표(ITEM 행 이름이 정확히 "교육")보다 취업자
        # 표(산업분류 축의 리프 이름 "P 교육 서비스업(85)"에 "교육"이 부분
        # 문자열로 우연히 들어있을 뿐)를 더 높은 점수로 고르는 문제가 실측
        # 확인됨(A93bfa851-C029). bm25는 ITEM 매칭과 축/분류값 매칭을
        # 구분하지 않는다 - 하지만 ITEM은 표가 실제로 측정하는 핵심
        # 지표(컬럼) 이름이고, 축/분류값은 지역·연도·산업 등 거의 모든
        # 표에 존재하는 범용 분류 라벨이라 우연히 겹칠 확률이 훨씬 높다.
        # ITEM 이름이 직접 걸린 매칭에 확실한 가점을 줘서, "핵심 지표가
        # 정확히 일치하는 표"가 "분류 라벨에 단어가 우연히 겹친 다른 도메인
        # 표"보다 항상 우선하도록 한다.
        item_name_matches = [m for m in entry["matches"] if m.get("obj_id") == "ITEM"]
        if item_name_matches:
            score += 4
            reasons.append(f"핵심 측정 지표(ITEM) 이름 직접 매칭 {len(item_name_matches)}건(+4)")

        # [2026-08-18 실측 발견 + 사용자 제안 - 별표(*) 포괄분류 우선] "청년층
        # 고용률" claim이 DT_1DA7012S(연령 10살 단위 표, "20 - 29세"만 있고
        # "청년층"에 해당하는 그룹 구간 자체가 없음)와 DT_1DE9046S(연령별
        # 경제활동상태, "* 15~29세" 그룹 구간이 정확히 있음)를 완전 동점으로
        # 뽑아서 앞쪽이 우연히 선택되는 문제가 실측 확인됨 - 값도 완전히
        # 틀렸는데(전체 총계 63.6%를 골랐음) confident=True로 나가는 실제
        # 위험한 사례였다. KOSIS는 "기본 세분류들을 합친 상위 그룹 항목"을
        # 실제로 이름 앞에 "*"를 붙여 표시한다(실측 확인: `SELECT name FROM
        # dimensions WHERE name LIKE '* %'` -> "* 15~29세", "* 광공업(BC)"
        # 등 11건, 전부 세부 항목 여러 개를 하나로 묶은 것이었음). 뉴스가
        # "청년층"/"제조업" 같은 상위 개념어를 쓰고 표에 정확한 세부 구간을
        # 언급하지 않을 때는, 그 개념어가 실제로 이 "*" 표시 행과 매칭됐다는
        # 사실 자체가 "이 표가 그 개념을 진짜로 표현할 수 있다"는 강한
        # 신호다(반대로 세분류만 있는 표는 이런 매칭이 아예 안 생긴다) -
        # ITEM 매칭(+4)보다는 약하게, 하지만 동점을 확실히 깨도록 준다.
        starred_matches = [m for m in entry["matches"] if (m.get("name") or "").startswith("*")]
        if starred_matches:
            score += 3
            reasons.append(
                f"포괄분류(*) 축 직접 매칭 {len(starred_matches)}건(+3) - "
                "claim의 상위 개념어가 KOSIS 자체 표시 그룹 구간과 일치"
            )

        if rate_intent and "rate_of_change" in measure_types:
            score += 5
            reasons.append("등락률 의도 + rate_of_change 항목 있음(+5)")
        elif rate_intent and measure_types:
            score -= 3
            reasons.append(
                "등락률 의도인데 rate_of_change 항목 없음(-3, "
                "이번 세션 MISMATCH 12건 중 8건이 이 패턴이었음)"
            )

        # [2026-08-18 실측 보강] 기존 -5는 DT_2IFS002("소비자물가지수",
        # stat_nm="IMF")가 "핵심 지표(ITEM) 이름 직접 매칭 +4"까지 함께
        # 받는 실제 사례(A93bfa851-C026)에서 국내표(DT_1J22001)를 못
        # 이겼다(6.15 vs 3.93으로 국제표 승) - bm25 기본 점수 차이(국제표가
        # 축이 단순해서 오히려 더 높게 나옴, +7점 이상)까지 감안해 감점폭을
        # 올렸다. 재확인: 이 값을 바꿔도 international_intent=True인
        # 경우(명시적 국제 비교 claim)는 그대로 패널티가 0이라 영향 없음.
        if is_international and not international_intent:
            score -= 9
            reasons.append("국제기구 표인데 원문장에 국제/해외 언급 없음(-9)")
        elif is_international and international_intent:
            reasons.append("국제기구 표 + 원문장에 국제/해외 언급 있음(패널티 없음)")

        is_producer_price = any(m in (tbl_nm or "") for m in _PRODUCER_PRICE_TBL_NM_MARKERS)
        producer_intent = any(m in sentence_text for m in _PRODUCER_PRICE_INTENT_MARKERS)
        if is_producer_price and not producer_intent:
            score -= 9
            reasons.append("생산자/수입/수출물가 표인데 원문장에 해당 신호 없음(-9, 소비자물가로 추정)")
        elif is_producer_price and producer_intent:
            reasons.append("생산자/수입/수출물가 표 + 원문장에 해당 신호 있음(패널티 없음)")

        candidates.append({
            "org_id": org_id,
            "tbl_id": tbl_id,
            "tbl_nm": tbl_nm,
            "stat_nm": stat_nm,
            "is_international": bool(is_international),
            "score": score,
            "matched_term_count": matched_term_count,
            "matched_items": entry["matches"][:5],
            "reasons": reasons,
        })

    candidates.sort(key=lambda c: (c["score"], c["matched_term_count"]), reverse=True)
    return candidates[:top_n]

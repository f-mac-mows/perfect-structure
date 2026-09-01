"""[2026-08-17 신규] kosis_local_search.py + kosis_warehouse.db(로컬 DB)만
으로 new_kosis_agent.NewKosisAgent와 완전히 동일한 process_claim_group_
keywords 계약을 만족하는 "DB 기반 agent".

## 왜 필요한가

이 세션 앞부분에서 DB 웨어하우스(kosis_warehouse.py)와 로컬 검색 엔진
(kosis_local_search.py)을 만들고 실측 검증해왔지만, 전부 스크립트로
org_id/tbl_id를 직접 지정하거나 raw_sentence+keywords를 손으로 구성해서
단위 테스트한 것이었다 - 실제 프로덕션 입력 형태(run01_result.jsonl의
claim + run03_result.json의 claim별 matched_keywords)를 받아서 adapter.py
-> judgment.py까지 실제로 흘려본 적은 없었다.

adapter.py는 이미 이런 교체를 염두에 두고 설계돼 있었다(run_search_and_
judge의 agent 파라미터, new_kosis_agent.py의 "adapter.py 쪽 변경 없이
agent 인스턴스만 바꿔 끼우면 된다"는 주석). 즉 process_claim_group_
keywords(claims, keywords_by_claim_id, category_hint) -> Dict[claim_id,
evidence_payload] 라는 계약만 그대로 만족하면, adapter.py/judgment.py는
한 줄도 안 고치고 라이브 API 기반 NewKosisAgent를 이 LocalDbAgent로
바꿔 끼울 수 있다.

## 이 파일이 하지 않는 것 (범위)

- claim 그룹 라우팅(direct/derived_comparison/excluded 분류)은
  adapter.route_claim_group을 그대로 재사용한다 - 검색 방식과 무관한
  로직이라 다시 만들지 않는다.
- derived_comparison(파생 비교값) 조합 로직도 NewKosisAgent와 동일하게
  "이미 찾은 형제 claim들의 결과 조합"이라 그대로 가져온다.
- 표 자체가 아직 없을 때의 cache-miss 확장(kosis_warehouse.ensure_tables_
  for_claim)은 안 쓴다 - 아직 없는 표는 그냥 not_found로 떨어진다(추측
  하지 않는다는 원칙과 일관, 이 확장은 여전히 별도 배선 지점으로 남음).
  [2026-08-22 갱신 - Task #28] 다만 "표는 있는데 이 기간만 없을 때"의
  cache-miss(kosis_warehouse.fetch_scoped_slice)는 이제 배선됐다 -
  resolve_claim_evidence에 kosis_client를 넘기면(opt-in) no_data 직전에
  이미 확정된 org_id/tbl_id/itm_id/axis_codes 그대로 기간만 바꿔 온디맨드로
  한 번 더 요청한다. 두 cache-miss는 "표를 못 찾음" vs "표는 찾았는데
  기간이 없음"으로 성격이 달라 배선 시점을 분리했다.

## YoY/기간 비교 파생 (2026-08-17 배선 완료)

처음엔 "공정한 비교(구 파이프라인 대비)를 위해 일부러 안 넣는다"고 여기
적어뒀었는데, 실제로 구 파이프라인(new_kosis_agent.py)을 다시 읽어보니
`derivation_used`/`derivation_note`를 그대로 통과시키는 필드만 있을 뿐
실제로 그 값을 계산해서 채우는 코드가 어디에도 없었다(Task #5 "YoY %등락률
파생 기능"이 "진단만 하고 구현은 보류"로 완료 처리된 이유) - 즉 구
파이프라인도 이 claim들(A93bfa851-C024 등 "OO% 상승했다"류)에서는 원자료
지수값을 그대로 내놓고 MISMATCH를 냈다(run04로 실측 확인, 118.69 = 이
agent가 찾은 값과 완전히 일치). "공정한 비교"를 지키려고 미룰 이유가
없었다 - 오히려 이번에 붙이면 구 파이프라인에는 없던 개선이 된다.

`resolve_claim_evidence`가 이제 다음을 판별한다: 매칭된 항목 자체가 이미
등락률류(`_infer_measure_type`=="rate_of_change")면 그대로 직접 조회
(기존과 동일). 아니라면(지수/절대값류 항목인데) claim이 unit="%"이면서
원문장에 실제 변화 동사(올랐다/상승/증가 등)가 있을 때만 파생이 필요하다고
판단한다(unit="%" 하나만으로는 트리거하지 않음 - "고용률 70.3%"처럼 %가
그 항목 자체의 고유 단위인 level claim과 구분하기 위해서다. 실제로
A82ae9f41-C003이 그 반례였는데, 이건 "역대 최대치"라 어차피 route_claim_
group 이전에 judgment.py가 record-claim으로 따로 처리해서 여기까지 오지도
않는다 - 그래도 트리거 조건은 그 구분과 무관하게 정확해야 한다).

기준 시점(reference_period)은 원문장에서 명시적으로 추출을 시도한다
("2020년 9월에 비해"/"2020년 대비" -> 그 연월, "5년 전" -> target에서
N년 전) - 못 찾으면 `resolve_yoy_change`(전년 동월/동분기, "전년비" 계열
claim이 압도적으로 이 기준)로 기본값을 쓴다. 두 시점 중 하나라도 facts에
없으면(추측 금지 원칙) query_status="no_data"로 명확히 실패 처리하고,
원자료 단일 시점 값으로 조용히 대체하지 않는다 - 잘못된 값으로 우연히
맞는 것보다 "이 비교는 못 했다"고 정직하게 실패하는 쪽을 택했다.
"""

import logging
import re
import sqlite3
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

import kosis_local_search as kls
import kosis_warehouse as wh
from adapter import route_claim_group, _parse_claimed_value
# [2026-08-24 신규 - "역대 최고/최저" claim 배선] judgment.py는 stdlib만
# import하는 독립 모듈이라(judgment.py 모듈 docstring 참고) 여기서
# module-level import해도 순환 import가 안 생긴다(adapter.py가 judgment를
# 함수 안에서만 지연 import하는 이유는 local_db_agent<->adapter 순환 때문이지,
# judgment는 그 순환에 관여하지 않는다).
from judgment import _is_record_claim

logger = logging.getLogger("Task2.KosisChatAgent")

# [2026-08-17 신규] claim 원문장이 "변화"를 서술하는지 판별하는 동사 마커.
# kosis_local_search._RATE_INTENT_MARKERS와 달리 "%"/"퍼센트"/"포인트" 같은
# 기호 마커는 일부러 뺐다 - unit="%" 자체는 "고용률 70.3%"처럼 그 항목의
# 고유 단위(레벨값)일 수도 있어서, 기호만으로는 "변화율을 주장하는 claim"과
# "그 자체가 %인 수준값 claim"을 못 가른다. 실제 변화 동사가 있어야만
# "두 시점 비교가 필요하다"는 신호로 삼는다.
_RATE_CHANGE_VERB_MARKERS = (
    "올랐다", "내렸다", "늘었다", "줄었다",
    "증가", "감소", "상승", "하락", "급등", "급락",
)


def _claim_number_change_window(
    claim: Dict[str, Any],
    anchor_suffix_pattern: str,
    sibling_values: Optional[Iterable[Any]] = None,
) -> Optional[str]:
    """claim 자신의 값(claim["value"])이 원문장에 등장하는 위치 바로 뒤부터
    "다음 숫자가 나오기 전까지"만 잘라 돌려준다 - 한 문장에 숫자가 여러 개일
    때, 뒤쪽 동사가 "이 claim의 숫자"를 서술하는지 "다른 숫자"를 서술하는지
    구분하기 위한 공용 윈도우 추출기(2026-08-17 실측 버그 수정, 원래는
    _needs_rate_derivation 안에 %전용으로 박혀 있었다 - 2026-08-18에
    "명" 단위 증가/감소 claim까지 다루려고 분리했다. anchor_suffix_pattern
    으로 값 뒤에 어떤 게 와야 "이 claim의 숫자"로 인정할지 호출부가
    정한다(%claim은 "%", "명" claim은 "명" 등 - 하드코딩하지 않는 이유는
    claim.unit이 claim마다 다르기 때문).

    anchor(값+단위)를 원문장에서 못 찾으면(반올림 등으로 표기가 다른 드문
    경우) None 대신 원문장 전체를 돌려준다 - 그 경우까지 막으면 너무
    보수적이라 실측 사례(C024 등)를 놓칠 수 있어서다.

    [2026-08-18 실측 버그 수정 - 열거형 문장, 1차] "다음 숫자가 나오면
    무조건 멈춘다"는 원래 "다른 claim의 숫자를 침범하지 않으려는" 안전
    장치였는데, 실측(A93bfa851-C006 "과일(35.2%)과 우유·치즈 및 계란
    (30.7%) 등은 5년 전에 비해 30% 넘게 급등했다")에서 그 "다음 숫자"가
    다른 claim의 값이 아니라 "5년"(기준시점 연수)이나 "30%"(무관한
    문턱값)인 경우까지 걸려서, 진짜 동사(급등했다)에 닿기도 전에 윈도우가
    끊겨버리는 문제가 있었다.

    [2026-08-18 실측 버그 수정 - 열거형 문장, 2차] 1차 수정은 "형제의
    값과 정확히 일치하면 거기서 멈춘다"였는데, 3개 이상이 나열된 경우
    (실측: A93bfa851-C007~C010 "빵(38.5%), 케이크(31.7%), 떡(25.8%),
    라면(25.3%) 등이 크게 오르며 ... 올랐다")에는 그 "형제의 값"들이
    바로 다음다음 형제로 계속 이어져서, 맨 마지막 형제(라면)만 진짜
    동사에 닿고 나머지는 여전히 바로 다음 형제 숫자에서 멈춰버렸다(1차
    수정 전과 증상은 같되 원인만 바뀐 것). 열거형으로 묶인 형제들은
    정의상 하나의 동사를 공유하므로, 형제의 값은 "멈추는 지점"이 아니라
    "건너뛰는 지점"으로 바꾼다 - 윈도우 안에서 만나는 숫자가 형제의 값과
    일치하면 계속 지나치고, 형제 목록에 없는(=이 그룹과 무관한) 숫자를
    만났을 때만 멈춘다. sibling_values가 없으면(형제 claim 정보를 호출부가
    못 넘겨줬거나, 애초에 형제가 없는 단독 claim) 안전하게 기존 동작
    (아무 숫자에서나 멈춤 - 실측: A82ae9f41-C010 "45.6%로 전년 동월 대비
    1%포인트 하락했다"처럼 무관한 변화폭 숫자를 건너뛰면 안 되는 단독
    claim 케이스는 이 폴백으로 계속 보호된다)으로 그대로 폴백한다."""
    raw = claim.get("claim") or ""
    value_str = str(claim.get("value") or "").strip()
    if not raw or not value_str:
        return None
    m = re.search(re.escape(value_str) + r"\s*" + anchor_suffix_pattern, raw)
    if not m:
        return raw
    window = raw[m.end():]

    sibling_strs = {
        s
        for v in (sibling_values or [])
        if (s := str(v or "").strip()) and s != value_str
    }
    if sibling_strs:
        # [2026-08-18 실측 버그 수정 - 열거형 문장, 3차] 2차 수정("형제
        # 값이 아닌 숫자를 만나면 멈춘다")은 형제가 딱 1명뿐일 때(C006)는
        # 맞았지만, 3명 이상 이어질 때(C007~C010, "빵 및 곡물(28.0%)"처럼
        # 1번이 claim으로 안 뽑아준 열거 항목까지 끼면) 여전히 그 안
        # 뽑힌 숫자에서 멈춰버렸다 - "형제 값이면 지나치고, 아니면
        # 멈춘다"는 규칙 자체가, 이 그룹에 속하지만 1번이 놓친 항목까지는
        # 다 못 커버했다. sibling_values가 있다는 것 자체가 "이 원문장은
        # 이미 열거형 claim 그룹으로 확인됐다"는 뜻이므로, 그 안에서는
        # 아예 숫자로 멈추지 않고 원문장 끝까지 전부 동사를 찾는다(단독
        # claim, 즉 sibling_values가 없을 때만 기존의 보수적인 "아무
        # 숫자에서나 멈춤" 폴백이 적용된다 - A82ae9f41-C010 같은 단독
        # claim의 오탐 방지는 여전히 그 폴백이 담당한다).

        # 3차 수정 그대로: sibling_values가 있으면(=이 원문장이 이미
        # 열거형 claim 그룹으로 확인됨) 숫자로 멈추지 않고 window를
        # 그대로 돌려준다(형제든 아니든 어떤 숫자를 만나도 자르지 않음 -
        # 이 설계는 이번 세션에서 바꾼 적 없다, 그대로 유지)."""
        return window

    # [2026-08-21 실측 버그 수정 - Task #26] sibling_values가 없는 단독
    # claim 폴백. 원래는 "다음 숫자가 나오면 무조건 멈춘다"(A82ae9f41-C010
    # "45.6%로 전년 동월 대비 1%포인트 하락했다"처럼 무관한 변화폭
    # 숫자에서 멈춰 오탐을 막는 보호)였는데, 실측(A82ae9f41-C011 "청년층
    # 고용률은 작년 5월(-0.7%포인트)부터 1년 넘게 감소세를 이어가고
    # 있다")에서 claim 숫자 바로 뒤에 오는 "1년"이 다른 claim의 값이
    # 아니라 순수 기간 길이("1년 넘게" = "1년 이상")라서 거기서 멈추면
    # 진짜 동사("감소세를 이어가고 있다")를 아예 못 보는 문제가 나왔다.
    # "N년"(기간) 패턴의 숫자만 건너뛰고 그 뒤에서 계속 진짜 정지 지점을
    # 찾는다 - "N년" 뒤가 아닌 숫자(다른 claim의 값 등, 예: C010의
    # "1%포인트")를 만나면 기존대로 거기서 멈춘다(C010/C002 회귀 확인:
    # "1"/"0.1" 뒤에 "%포인트"가 오지 "년"이 아니므로 그대로 멈춤 유지).
    search_pos = 0
    while True:
        next_digit = re.search(r"\d+", window[search_pos:])
        if not next_digit:
            break
        abs_start = search_pos + next_digit.start()
        abs_end = search_pos + next_digit.end()
        if re.match(r"\s*년", window[abs_end:]):
            search_pos = abs_end
            continue
        window = window[:abs_start]
        break
    return window


def _prefix_before_claim_number(
    claim: Dict[str, Any], anchor_suffix_pattern: str
) -> Optional[str]:
    """`_claim_number_change_window`의 앞쪽 버전 - claim 값이 원문장에
    등장하는 위치 바로 앞(직전 숫자 또는 문장 시작까지)을 잘라 돌려준다.

    [2026-08-21 실측 버그 수정 - A93bfa851-C017] "주류 및 담배는 상승률이
    5.0%에 그쳤지만..."처럼, "이 값이 이미 비율(등락률)이다"라는 단서
    ("률")가 숫자 **뒤**가 아니라 숫자 **바로 앞**에 오는 문장 구조가
    실측(90개 claim 배치)으로 발견됐다 - 기존 `_window_has_rate_comparison`
    은 window(숫자 뒤)에서만 "률"을 찾아서, "그쳤지만"이라는 명백한 비교
    동사가 window 안에 있는데도 "률"을 못 찾아 False로 판정, 원자료
    지수값(105.05, "2020=100")을 claim의 5.0%와 그대로 비교해 거짓
    MISMATCH가 났다. "률" 단서만 앞쪽도 보게 넓힌다(비교 동사 자체는
    실측된 모든 사례에서 숫자 뒤에만 왔으므로 window 쪽은 그대로 둔다)."""
    raw = claim.get("claim") or ""
    value_str = str(claim.get("value") or "").strip()
    if not raw or not value_str:
        return None
    m = re.search(re.escape(value_str) + r"\s*" + anchor_suffix_pattern, raw)
    if not m:
        return None
    prefix = raw[: m.start()]
    prev_digit_end = None
    for match in re.finditer(r"\d", prefix):
        prev_digit_end = match.end()
    return prefix[prev_digit_end:] if prev_digit_end is not None else prefix


def _window_has_change_verb(window: Optional[str]) -> bool:
    """window(비교/변화 대상 텍스트 조각) 안에 실제 변화 동사가 있는지
    확인한다. [2026-08-17 실측 버그 수정] "상승"/"증가" 등 동사 마커가
    "상승률"/"증가율"처럼 명사형 접미사 "률"이 바로 뒤에 붙으면 이 claim
    숫자의 변화를 서술하는 동사가 아니라 "평균 상승률" 같은 별개의 일반
    명사구일 수 있다(실측: A93bfa851-C026 "...16.2%로 평균 상승률과 거의
    유사했다" - 진짜 동사는 "유사했다"인데 "상승률" 안의 "상승"이 우연히
    매칭됐었다). "률"로 안 끝나는 경우만 진짜 동사로 인정한다."""
    if not window:
        return False
    return any(
        re.search(re.escape(marker) + r"(?!률)", window)
        for marker in _RATE_CHANGE_VERB_MARKERS
    )


# [2026-08-18 신규 - 실측 발견] "상승" 등 방향성 동사가 이 claim 숫자
# 바로 뒤에 없어도, "이미 계산된 비율(등락률/상승률 등)과 이 값을
# 견준다"는 비교 구문 자체가 "이 %값도 등락률류다"라는 신호일 수 있다
# (실측: A93bfa851-C025/C026 "...16.2%로 평균 상승률과 거의 유사했다",
# C012~C014 "...상승률이 20%를 넘겼다" - 둘 다 방향성 동사(올랐다/증가
# 등)는 전혀 없고 "유사하다"/"넘다"라는 비교 동사만 있어서 기존
# _window_has_change_verb로는 못 잡았다). "률"이 window 안에 있고(=
# 비교 대상이 이미 비율이라는 뜻) 동시에 이런 비교 동사가 있으면, 그
# 자체로 "이 claim 값도 원자료가 아니라 등락률"이라는 신호로 삼는다.
_RATE_COMPARISON_VERB_MARKERS = (
    "유사했다", "비슷했다", "비슷하다", "유사하다",
    # [2026-08-21 실측 버그 수정 - A93bfa851-C017] "그쳤다"(선언형 종결
    # 어미)만 있으면 "그쳤지만"(양보형, "~에 그쳤지만")처럼 다른 어미가
    # 붙은 실제 문장에서 부분 문자열로 안 걸린다 - "다"만 뗀 "그쳤"으로
    # 바꿔 그쳤다/그쳤지만/그쳤는데/그쳤고 등 활용형을 커버한다("그치다"
    # 어간 "그치"는 못 쓴다 - 그치+었→그쳤은 모음 축약이라 "그치"가
    # "그쳤"의 부분 문자열이 아니다, 한글 활용형은 어간+어미 단순
    # 이어붙이기가 아님을 실측으로 재확인). 나머지 마커("넘겼다" 등)는
    # 이번에 실측으로 문제가 확인된 게 아니라서 추측으로 같이 바꾸지
    # 않는다(같은 활용형 문제가 있을 수 있다는 건 README에 남겨서 다음에
    # 실측되면 그때 고친다).
    "넘겼다", "넘었다", "그쳤", "하회했다", "웃돌았다", "밑돌았다",
)


# [2026-08-22 신규 - Task #25, 실측 발견 A93bfa851-C018] claim.metric/
# metric_normalized 필드가 이 값 자체를 "등락률" 개념으로 이미 명시한
# 경우의 접미사 목록 - _needs_rate_derivation이 raw_sentence 파싱(window/
# prefix)으로 단서를 못 찾을 때의 마지막 폴백으로 쓴다. 방향성 접두
# ("상승"/"증가"/"하락"/"등락"/"변동")가 붙은 복합어만 인정하고 "률"/"율"
# 단독은 넣지 않는다 - "고용률"/"실업률"/"참가율"처럼 그 자체가 KOSIS가
# 직접 제공하는 level 지표(파생이 필요 없음)의 이름도 전부 "률"/"율"로
# 끝나므로, 단독 접미사로는 이걸 걸러낼 수가 없다("고용률" 항목은
# measure_type=="rate_of_change"로 이 함수 맨 위에서 이미 걸러지지만,
# claim.metric 문자열 자체만 보는 이 체크는 그 안전장치와 독립적이라
# 이중으로 보수적으로 둔다).
_RATE_DERIVATION_METRIC_SUFFIXES = (
    "상승률", "증가율", "증가률", "하락률", "하락율", "등락률", "변동률", "변동율",
)


def _window_has_rate_comparison(window: Optional[str], prefix: Optional[str] = None) -> bool:
    """window 안에 이미 계산된 비율("상승률"/"등락률"/"증가율"/"하락률" 등
    "률"로 끝나는 명사)이 언급되면서, 동시에 그 비율과 이 claim의 값을
    견주는 비교 동사(유사하다/넘다/그치다/하회하다/웃돌다 등, 방향성
    변화 동사와는 다른 부류)가 있는지 확인한다 - `_window_has_change_verb`
    와 상호보완적이다(그쪽은 "이 값 자체가 변했다"는 방향성 서술, 이쪽은
    "이 값이 이미 비율인데 다른 비율과 비슷/초과/미달했다"는 비교 서술).

    [2026-08-21 실측 버그 수정 - A93bfa851-C017] "률" 단서는 원래 window
    (숫자 뒤)에서만 찾았는데, "상승률이 5.0%에 그쳤다"처럼 그 단서가
    숫자 **앞**에 오는 문장 구조가 실측으로 발견됐다. `prefix`(숫자 바로
    앞 텍스트, `_prefix_before_claim_number` 참고)가 주어지면 거기서도
    "률"을 찾는다 - 비교 동사 자체는 여전히 window에서만 찾는다(실측된
    모든 사례에서 동사는 항상 숫자 뒤에 왔음)."""
    if not window:
        return False
    has_rate_word = "률" in window or (prefix is not None and "률" in prefix)
    if not has_rate_word:
        return False
    return any(marker in window for marker in _RATE_COMPARISON_VERB_MARKERS)


def _needs_rate_derivation(
    claim: Dict[str, Any],
    measure_type: str,
    sibling_values: Optional[Iterable[Any]] = None,
) -> bool:
    """매칭된 항목이 이미 등락률류가 아니고(measure_type != "rate_of_change"),
    claim이 unit="%"이면서 그 claim 자신의 숫자 바로 뒤에 실제 변화
    동사가 붙어있을 때만 True.

    [2026-08-17 실측 버그 수정] 처음엔 원문장 전체에 변화 동사가 있는지만
    봤는데, 실측(A82ae9f41-C010: "15~29세 고용률은 45.6%로 전년 동월
    대비 1%포인트 하락했다")에서 이게 잘못 트리거되는 걸 발견했다 -
    claim이 주장하는 값은 45.6%(수준값)인데, "하락했다"는 문장 뒤쪽의
    다른 숫자(1%포인트, 변화폭)를 서술하는 동사였다. `_claim_number_
    change_window`로 "이 claim의 숫자" 바로 뒤~다음 숫자 전까지만 잘라서
    본다.

    [2026-08-18 갱신 - 열거형 문장 한계 해소] 이전엔 "빵(38.5%),
    케이크(31.7%) 등이 크게 오르며 ... 올랐다"처럼 여러 항목이 동사
    하나를 공유하는 열거형 문장은 과소트리거됐다(각 숫자 바로 뒤에 다른
    숫자가 끼어 window가 동사 전에 끊김). sibling_values(같은 원문장을
    공유하는 다른 claim들의 값 - 호출부가 claim 그룹에서 모아서 넘겨줌)를
    `_claim_number_change_window`에 전달하면, 그 형제들의 값이 아닌
    숫자(연수/문턱값 등)는 지나치고 형제의 진짜 숫자에서만 멈추게 되어
    이 열거형 케이스도 올바르게 트리거된다(실측: A93bfa851-C006 확인).
    sibling_values를 안 넘기면(호출부가 형제 정보를 모으지 않았거나 애초에
    단독 claim이면) 기존 보수적 동작으로 안전하게 폴백한다.

    [2026-08-19 신규 - 1번 확정 스키마] claim에 `value_type`이 있으면
    (claims_schema_1번_v2.md 참고) 위 raw_sentence 동사 추론을 전부
    건너뛰고 그 값을 그대로 신뢰한다 - 1번이 문장을 직접 보고 level/
    change_rate/change_amount로 이미 분류해준 것이므로, 우리가 원문장
    조각(window)에서 동사를 다시 찾아내는 것보다 더 정확하다. value_type이
    없는 claim(아직 구 포맷이거나, 실제 데이터가 이 필드를 안 채운 경우)만
    아래 기존 휴리스틱으로 폴백한다."""
    if measure_type == "rate_of_change":
        return False
    value_type = (claim.get("value_type") or "").strip()
    if value_type:
        return value_type == "change_rate"
    if (claim.get("unit") or "").strip() != "%":
        return False
    window = _claim_number_change_window(claim, r"%", sibling_values=sibling_values)
    prefix = _prefix_before_claim_number(claim, r"%")
    if _window_has_change_verb(window) or _window_has_rate_comparison(window, prefix):
        return True
    # [2026-08-22 신규 - Task #25, 실측 발견 A93bfa851-C018] window/prefix
    # 둘 다 raw_sentence 파싱에 기대는데, "이 중 주류만 보면 13.1%였다"
    # 처럼 claim 숫자 뒤가 "였다."뿐이고 앞에도 "률" 단서가 없는 문장
    # 구조는 이 둘로 못 잡는다(같은 문장의 형제 claim C017 "상승률이
    # 5.0%에 그쳤지만"은 잡히는데, C018은 못 잡힘 - 실측 확인, 90개
    # 배치에서 C018만 UNVERIFIED_UNRESOLVED로 남음). 1번이 이미 준
    # metric/metric_normalized 필드 자체가 "주류 물가 상승률"처럼 이
    # claim이 원래 등락률 개념이라고 명시적으로 말해주고 있으므로,
    # raw_sentence를 다시 파싱하는 것보다 이걸 직접 신호로 쓴다.
    # "고용률"/"참가율"류(그 자체가 KOSIS가 직접 제공하는 level 지표)와
    # 헷갈리지 않도록 "률" 단독이 아니라 방향성 접두가 붙은 복합어만
    # 인정한다 - "고용률"은 애초에 measure_type=="rate_of_change"로 이
    # 함수 맨 위에서 이미 걸러지므로 실제로 겹칠 일은 없지만(matched
    # KOSIS 항목명 기준 판별이라 claim.metric과는 독립적), 혹시 그
    # 안전장치가 없는 경로에서도 이중으로 안전하게 두는 것.
    metric_text = str(claim.get("metric_normalized") or claim.get("metric") or "")
    if any(metric_text.endswith(suffix) for suffix in _RATE_DERIVATION_METRIC_SUFFIXES):
        return True
    return False


# [2026-08-18 신규 - 사용자 제안: "비교 표현이면 후보군을 전부 조회해야
# 하는데, 몇 개를 조회할지 판단하는 앞단이 비어 있다"] _needs_rate_
# derivation은 unit="%"인 claim만 다뤘다 - "명" 단위 증가/감소 claim
# (실측: C005 "전문·과학기술서비스업(10만2000명)... 증가세를 보였다" -
# 10만2000명은 원자료 총계가 아니라 전년 대비 증가분이었다, 실측 확인:
# 1505.1-1402.9=102.2천명으로 거의 정확히 일치)은 이 조건에 아예 안
# 걸려서 파생 후보가 되지도 못했다.
#
# "몇 개 값을 조회해야 하는가"에 대한 답: 이 프로젝트에서 실측된 모든
# 비교 표현 claim은 예외 없이 "이 시점 대비 저 시점" 쌍대비교였다(3개
# 이상 시점을 동시에 비교하는 claim은 아직 실측된 바 없다 - Decision
# 003: 확실하지 않으면 추측하지 않는다는 원칙에 따라 지금은 정확히
# 2개(target period + reference period 1개)로 고정한다. reference
# period 자체는 이미 _extract_explicit_reference_period(명시적 기준
# 시점) -> resolve_yoy_change(전년 동월 기본값) 순으로 정확히 1개로
# 좁혀지므로, "몇 개"의 답은 "target 1개 + reference 1개 = 2개"로
# 이미 결정 가능한 문제였다 - 다만 unit 게이트가 %로만 좁아서 이
# 로직 자체가 "명" claim에는 아예 시도되지 않았을 뿐이다.
#
# unit_category(diff 연산이 뺄셈이냐 나눗셈이냐)는 claim.unit으로
# 결정한다: "%" -> pct_change(나눗셈), 그 외 절대단위(명/원/건 등)
# -> raw difference(뺄셈). 두 경우 다 "이 claim 숫자 바로 뒤에 변화
# 동사가 있다"는 같은 트리거 조건을 공유하므로 _window_has_change_verb를
# 그대로 재사용한다.
_DIFFABLE_UNIT_MARKERS = ("명", "개", "건", "원", "톤", "가구", "억원", "만원")


def _claim_expresses_pairwise_change(
    claim: Dict[str, Any],
    sibling_values: Optional[Iterable[Any]] = None,
) -> bool:
    """claim이 "이 시점 대비 저 시점" 쌍대비교(증가/감소/등락)를 표현하는지
    - unit이 %거나 절대단위(명/원/건 등)이고, 그 claim 자신의 숫자 바로
    뒤에 실제 변화 동사가 있을 때만 True. `_needs_rate_derivation`과 달리
    unit="%"로 제한하지 않는다(위 모듈 주석 참고) - search_by_diff의
    사전 게이트로 쓴다. sibling_values는 _claim_number_change_window에
    그대로 전달한다(2026-08-18 - 열거형 문장 대응, 위 _needs_rate_derivation
    주석 참고).

    [2026-08-19 신규 - 1번 확정 스키마] `_needs_rate_derivation`과 동일한
    이유로, claim에 `value_type`이 있으면 그 값을 그대로 신뢰한다
    (claims_schema_1번_v2.md 참고) - "change_amount"면 True. value_type이
    없는 claim만 기존 raw_sentence 동사 탐색으로 폴백한다.

    [2026-08-21 실측 발견 - Task #26, 90개 claim 배치] unit="%포인트"
    (예: A82ae9f41-C011 "청년층 고용률은... -0.7%포인트... 감소세를
    이어가고 있다")는 이 함수가 이제까지 아예 안 잡았다 - `is_percent`가
    `unit == "%"` 정확 일치만 봐서 "%포인트"는 걸러졌고, `_DIFFABLE_
    UNIT_MARKERS`(명/개/건/원 등)에도 "포인트"가 없었다. 그 결과
    search_by_diff 값 기반 빠른 경로를 아예 못 타고 기존 Stage 1/2/3
    으로 넘어갔는데, 거기는 %포인트(두 시점 수준값의 차이)를 다루는
    코드가 없어서 단일 시점 수준값(예: 63.5%)을 claim의 -0.7과 그대로
    비교해 거짓 MISMATCH가 났다. %포인트는 이미 비율인 지표의 절대
    변화폭이라 나눗셈(pct_change)이 아니라 뺄셈(difference)으로 다뤄야
    한다 - 아래 mode 선택(`"pct_change" if unit == "%" else "difference"`)
    이 이미 이 구분을 정확히 하고 있었으므로(unit이 "%"가 아니면 자동
    으로 difference 모드), 이 함수의 트리거 조건만 넓히면 된다."""
    value_type = (claim.get("value_type") or "").strip()
    if value_type:
        return value_type == "change_amount"
    unit = (claim.get("unit") or "").strip()
    if not unit:
        return False
    is_percent = unit == "%"
    is_percentage_point = unit in ("%포인트", "%p", "%P") or "퍼센트포인트" in unit
    is_count_like = any(m in unit for m in _DIFFABLE_UNIT_MARKERS)
    if not (is_percent or is_percentage_point or is_count_like):
        return False
    anchor_suffix = r"%" if (is_percent or is_percentage_point) else re.escape(unit)
    window = _claim_number_change_window(claim, anchor_suffix, sibling_values=sibling_values)
    return _window_has_change_verb(window)


def _extract_explicit_reference_period(raw_sentence: str, target_digits: str) -> Optional[str]:
    """원문장에서 claim이 명시한 비교 기준 시점을 뽑는다 - 없으면 None
    (호출부가 resolve_yoy_change로 기본값인 "1년 전"을 쓴다).

    세 패턴을 시도한다(우선순위 순):
    1. "YYYY년 M월에 비해/대비/보다" -> 그 연월(예: "2020년 9월에 비해" -> "202009")
    2. "YYYY년에 비해/대비/보다" -> 그 연도만(월 없는 표현, 예: "2020년 대비" -> "2020")
    3. "N년 전/간/동안" -> target_digits에서 N년을 뺀 시점(월/일부는 target과
       동일하게 유지 - 예: target="202509", "5년 전"/"5년간" -> "202009")

    실제 표본 사례(A93bfa851-C005 "5년 전에 비해", C001 "5년간 ... 상승한
    것으로 분석됐다")를 보고 패턴 3을 추가했다 - "전년"(1년 전)은 이
    함수가 아니라 호출부의 resolve_yoy_change 기본값으로 이미 커버된다."""
    if not raw_sentence or not target_digits:
        return None
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(?:에\s*비해|대비|보다)", raw_sentence)
    if m:
        year, month = m.group(1), m.group(2)
        return f"{year}{int(month):02d}"
    m = re.search(r"(\d{4})\s*년\s*(?:에\s*비해|대비|보다)", raw_sentence)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*년\s*(?:전|간|동안)", raw_sentence)
    if m and len(target_digits) >= 4:
        n = int(m.group(1))
        year = int(target_digits[:4])
        rest = target_digits[4:]
        return f"{year - n}{rest}"
    return None


def _resolve_reference_period(claim: Dict[str, Any], target_digits: Optional[str]) -> Optional[str]:
    """[2026-08-19 신규 - 1번 확정 스키마] claim에 comparison_basis/
    comparison_period(claims_schema_1번_v2.md 참고)가 있으면 raw_sentence
    정규식 추출(`_extract_explicit_reference_period`) 없이 그 값을 최우선
    으로 쓴다 - 1번이 문장을 직접 보고 "언제와 비교하는지"까지 이미
    확정해준 것이므로 우리가 "5년 전에 비해"류 표현을 다시 정규식으로
    긁어내는 것보다 근본적으로 더 정확하다.

    - comparison_period가 있으면(예: "2024-06") 정규화해서 그대로 쓴다.
    - comparison_basis="YOY"면 kls._yoy_reference_period로 계산한다
      (comparison_period가 비어있는데 YOY라고만 온 경우 대비).
    - comparison_basis="PREV_PERIOD"는 [미구현 - 실측 대기] 아직 실제
      PREV_PERIOD claim이 관측된 바 없어, 표의 시점 단위(월/분기/연)를
      몇 칸 물러나야 하는지 추측하지 않는다(Decision 003) - None을
      돌려주고 호출부의 YoY 기본값 폴백에 맡긴다.
    - 둘 다 없으면(구 포맷, 혹은 이 필드들이 비어 온 경우) None을 돌려줘서
      호출부가 기존 raw_sentence 추출(`_extract_explicit_reference_period`)
      로 폴백하게 한다."""
    comparison_period = str(claim.get("comparison_period") or "").strip()
    comparison_basis = str(claim.get("comparison_basis") or "").strip()
    if comparison_period:
        normalized = kls._normalize_period_digits(comparison_period)
        if normalized:
            return normalized
    if comparison_basis == "YOY":
        return kls._yoy_reference_period(target_digits) if target_digits else None
    if comparison_basis == "PREV_PERIOD":
        return None
    return None


# [2026-08-22 신규 - Task #29 Step 3] item_diff(C003/C004류) 판단을 위한
# 두 안전장치. HCX-007의 item_diff mode 분류는 90건 합성 평가셋 실측에서
# 정확도 53%로 세 모드 중 가장 낮았다(README "스물다섯 번째" 항목) - HCX
# 판단 하나만으로 새 파생 경로를 트는 건 위험하다고 보고, (1) 원문에 실제로
# "전체/총지수와 비교한다"는 로컬 텍스트 근거가 있는지, (2) axis_codes 중
# 총계로 바꿔치기할 축이 모호하지 않게 정확히 하나로 좁혀지는지 - 이 둘을
# 모두 만족해야 item_diff 경로를 시도한다.
_TOTAL_COMPARISON_KEYWORDS = ("전체", "총지수", "평균", "총계")


def _has_total_comparison_keyword(text: str) -> bool:
    """claim 원문에 "전체/총지수/평균/총계" 같은, item_diff(이 항목 vs
    표 전체 총계)를 실제로 암시하는 로컬 텍스트 근거가 있는지 확인한다 -
    HCX가 item_diff라고 답해도 이 근거가 없으면 트리거하지 않는다(2차
    corroboration, kosis_local_search.search_by_diff가 match_phrases
    최소 2개 완전 corroboration만 신뢰하는 것과 같은 안전 원칙)."""
    if not text:
        return False
    return any(kw in text for kw in _TOTAL_COMPARISON_KEYWORDS)


def _find_swappable_axis_position(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, axis_codes: Dict[int, str]
) -> Optional[int]:
    """axis_codes(이미 확정된 item A의 축 조합) 중, kls._axis_total_code로
    찾은 "그 축의 총계 코드"가 현재 코드와 다른(=총계가 아닌 구체적 leaf에
    있는) 축이 정확히 하나뿐이면 그 axis_position을 반환한다. 0개(모든 축이
    이미 총계)거나 2개 이상(어느 축을 바꿔야 할지 모호함)이면 None -
    추측하지 않는다(resolve_item_diff_change를 호출할지 말지의 게이트)."""
    if not axis_codes:
        return None
    rows = kls._dim_rows_for_table(conn, org_id, tbl_id)
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]
    candidates = []
    for axis_position, code in axis_codes.items():
        total_code = kls._axis_total_code(category_rows, axis_position)
        if total_code and total_code != code:
            candidates.append(axis_position)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _lookup_cell_by_axis_codes(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: Optional[str],
    resolved_axis_codes: Dict[int, str],
    axis_trees: Dict[int, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """[2026-08-22 신규 - hcx_axis_resolve_fn(축 트리 기반 리졸버) 전용]
    HCX가 확정한 일부 축(resolved_axis_codes)과, 나머지 축의 기본값을
    합쳐 facts에서 정확히 하나의 셀을 찾는다. 기본값은 두 소스를 순서대로
    시도한다: ① kls._axis_total_code(합계/소계/전체/계/총지수류 라벨 -
    resolve_item_diff_change 등에서 이미 쓰던 헬퍼), ② 그게 없으면
    kls._AXIS_LABEL_DEFAULT_NAME(축 종류별 기본값 이름 - "시도별"->"전국"
    같은, "합계"류 라벨이 아닌 축을 위한 별도 테이블). 실측 버그(A93bfa851-
    C007/C009): "시도별" 축은 ①로 못 찾는데(그런 이름의 리프가 없음) ②를
    안 썼더니 이 축이 끝내 안 채워져서, HCX가 카테고리 축은 정확히 골랐어도
    (예: A01116="빵") 지역 축이 안 좁혀져 19개 지역 전부와 매치되는 바람에
    "유일하게 못 찾음"으로 조용히 실패했었다.

    두 기본값 다 없는 축은 그냥 비워둔다(강제로 추측하지 않음) - 그 축에
    실제로 서로 다른 값이 여러 개 있으면 아래 필터가 여러 셀에 매치돼
    "유일하게 못 정함"으로 안전하게 None을 반환한다(resolve_evidence_
    by_flat_match가 동점을 tie 리스트로 남기는 것과 같은 원칙 - 확신
    없으면 조용히 아무거나 고르지 않는다).

    표 전체를 다시 카테시안 곱으로 펼치는 kls.iter_table_cell_texts를
    여기서 부르지만, 이건 이제 HCX로 보내는 게 아니라 순수 로컬 조회라
    토큰 비용이 없다 - 이 함수를 만든 이유(축 트리로 바꾼 것) 자체가
    "HCX에 보내는 텍스트"를 줄이는 것이었지, 로컬 조회 방식을 바꾸는
    게 아니다(같은 셀 열거 로직을 두 곳에서 다르게 만들면 어긋날
    위험이 있다는 iter_table_cell_texts 자체의 설계 원칙과 일관)."""
    rows = kls._dim_rows_for_table(conn, org_id, tbl_id)
    category_rows = [r for r in rows if r["obj_id"] != "ITEM"]

    merged: Dict[int, str] = dict(resolved_axis_codes)
    for axis_position, tree in axis_trees.items():
        if axis_position in merged:
            continue
        default_code = kls._axis_total_code(category_rows, axis_position)
        if not default_code:
            # [2026-08-22 실측 버그 수정 - A93bfa851-C007/C009] kls._axis_
            # total_code는 "합계/소계/전체/계/총지수"류 라벨만 찾는다 -
            # "시도별" 축의 기본값은 그런 이름이 아니라 "전국"이라(kls.
            # _AXIS_LABEL_DEFAULT_NAME에 이미 등록돼 있음, resolve_
            # evidence_by_flat_match의 unexplained_axes 감점 예외와 같은
            # 축 종류별 기본값 테이블) 여기서도 놓쳐서 이 축이 끝내
            # 안 채워졌다. 실측: 빵(C007)/떡(C009) 둘 다 HCX가 정확한
            # 축 2 code(A01116/A01117)를 돌려줬는데도, 축 1(시도별)이
            # 안 채워져서 필터가 "전국/서울/부산/..." 19곳 전부와 매치돼
            # len(matches)==1을 못 만족하고 조용히 None으로 떨어졌다.
            axis_label = tree.get("axis_label")
            default_name = kls._AXIS_LABEL_DEFAULT_NAME.get(axis_label)
            if default_name:
                default_row = next(
                    (
                        r for r in category_rows
                        if r.get("axis_position") == axis_position and r.get("name") == default_name
                    ),
                    None,
                )
                if default_row:
                    default_code = default_row.get("code")
        if default_code:
            merged[axis_position] = default_code

    cell_texts_full = kls.iter_table_cell_texts(conn, org_id, tbl_id)
    matches = [
        c for c in cell_texts_full
        if c.get("itm_id") == itm_id
        and all(c.get("axis_codes", {}).get(pos) == code for pos, code in merged.items())
    ]
    return matches[0] if len(matches) == 1 else None


def _claimed_value(claim: Dict[str, Any]) -> Optional[float]:
    """[2026-08-19 신규 - 1번 확정 스키마] claim에 value_num(1번이 이미
    숫자로 정규화해준 값, 예: "18만3000" -> 183000)이 있으면 그걸 그대로
    쓴다 - `_parse_claimed_value`의 조/억/만 환산 파싱을 다시 할 필요가
    없다. 없으면(구 포맷) 기존 `_parse_claimed_value(claim.get("value"))`
    로 폴백한다."""
    value_num = claim.get("value_num")
    if value_num is not None:
        try:
            return float(value_num)
        except (TypeError, ValueError):
            pass
    try:
        return _parse_claimed_value(claim.get("value"))
    except (ValueError, TypeError):
        return None


def _period_digits_to_prd_se(period_digits: str) -> str:
    """정규화된 period 숫자열(예: "2025"/"202509")의 자릿수로 주기를
    결정론적으로 판별한다 - 4자리=연간(Y), 5자리=분기(Q), 6자리=월(M).
    새로 만든 추정이 아니라 `new_kosis_agent.py`의 `_period_to_prd_se`
    (기존 라이브 API 파이프라인이 이미 쓰던 규칙)를 그대로 재사용한다 -
    같은 판별을 새로 추측하면 두 agent가 서로 다른 규칙으로 갈릴 위험이
    있어서다. `fetch_scoped_slice`(아래 no_data 온디맨드 백필)를 부를 때
    필요한 prd_se를 이 규칙으로 정한다."""
    return {4: "Y", 5: "Q", 6: "M"}.get(len(str(period_digits or "")), "Y")


def _attach_record_extremes(
    result: Dict[str, Any],
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    axis_codes: Dict[int, str],
    period_digits: str,
    raw_sentence: str,
) -> Dict[str, Any]:
    """[2026-08-24 신규 - "역대 최고/최저" claim 배선, CLAUDE.md 지시로
    kosis_warehouse.get_record()를 실제 판정 경로에 연결] claim이 역대 claim
    패턴이면(judgment._is_record_claim), 이미 이 함수 호출부가 확정해 놓은
    (org_id, tbl_id, itm_id, axis_codes)로 kosis_warehouse.records 테이블을
    조회해서 전체 기간 최댓값/최솟값을 result에 덧붙인다.

    "이 값이 정말 역대 기록인가"(방향 판별, 시점 일치 확인, 최종 VERIFIED/
    MISMATCH 판정)는 여기서 하지 않는다 - 그건 judgment.py의 몫
    (_check_record_claim, 6.5절)이다. 여기서 하는 건 순수 조회 + "이
    claim의 시점이 records가 말하는 최댓값/최솟값 시점과 같은가"라는
    사실 확인(claim_period_matches_max/min)뿐이다 - 이 비교는 이 함수
    스코프 안에서만 쓸 수 있는, 이미 정규화된 period_digits(KOSIS prd_de
    포맷)가 있어야 가능하고, judgment.py는 일부러 KOSIS 시점 포맷을 모르는
    채로 남겨뒀다(모듈 독립성 원칙, judgment.py docstring 참고).

    get_record()는 순수 SELECT라서(kosis_warehouse.py get_record docstring
    참고) 이 함수가 이미 읽기 전용 계약으로 받는 conn 그대로 써도
    CLAUDE.md의 "DB 파일에 직접 쓰기/삭제 금지" 규칙과 무관하다.

    records 테이블에 해당 계열이 아직 없으면(그 표/항목이 아직 records
    계산 대상으로 적재되지 않았거나, wide 표라 온디맨드 배선이 이 계열까지는
    안 닿았을 수 있음) 조용히 아무것도 안 붙이고 result를 그대로 돌려준다 -
    judgment.py는 이 필드들이 없으면(None) 기존처럼 UNVERIFIED_RECORD_CLAIM
    으로 declining한다(이 프로젝트의 다른 신규 필드들과 동일한 폴백 원칙).
    """
    if not _is_record_claim(raw_sentence):
        return result
    prd_se = _period_digits_to_prd_se(period_digits)
    try:
        record_row = wh.get_record(conn, org_id, tbl_id, itm_id, prd_se, axis_codes)
    except Exception as e:
        logger.warning(
            f"[역대 claim - records 조회 실패, 조용히 폴백] {org_id}/{tbl_id}"
            f" itm={itm_id} prd_se={prd_se} - {e}"
        )
        return result
    if not record_row:
        return result
    result["record"] = {
        "max_value": record_row.get("max_value"),
        "max_prd_de": record_row.get("max_prd_de"),
        "min_value": record_row.get("min_value"),
        "min_prd_de": record_row.get("min_prd_de"),
        "coverage_strt_prd_de": record_row.get("coverage_strt_prd_de"),
        "coverage_end_prd_de": record_row.get("coverage_end_prd_de"),
        "claim_period_matches_max": (
            (period_digits == record_row.get("max_prd_de"))
            if record_row.get("max_prd_de") else None
        ),
        "claim_period_matches_min": (
            (period_digits == record_row.get("min_prd_de"))
            if record_row.get("min_prd_de") else None
        ),
    }
    return result


def _attach_purpose_check(
    result: Dict[str, Any],
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    claim: Dict[str, Any],
    raw_sentence: str,
    kosis_client: Optional[Any],
    hcx_purpose_verify_fn: Optional[Callable[..., Optional[Dict[str, Any]]]],
) -> Dict[str, Any]:
    """[2026-08-28 신규 - 목적 검증(purpose verification) 게이트, 배추가격/
    DT_114054_112 사례로 사용자가 지적한 아키텍처 갭 대응] _attach_record_
    extremes와 완전히 같은 위치(derivation.used=False인 "최종 확정" 성공
    경로 2곳)에서만 호출된다 - derivation.used=True인 경로(파생값)는 이미
    judgment._check_unverified가 UNVERIFIED_DERIVED_NEEDED로 먼저 가로채서
    _check_purpose_mismatch까지 도달하지 않으므로, 그 경로들에 이 검증을
    붙여봐야 어차피 안 쓰인다 - 비용을 아끼려고 일부러 안 붙인다(사용자가
    합의한 "비용 절감" 절충안의 실제 구현).

    [2026-08-28 갱신 - 사용자 결정: "표 적재 시점에 DB 저장"] 목적 설명
    조회 우선순위가 바뀌었다:
    1. 먼저 `wh.get_table_purpose_cached(conn, org_id, tbl_id)`로 tables_
       registry에 표 적재 시점(`kosis_warehouse.ingest_table`)에 이미
       캐시된 writing_purps/examin_objrange를 읽는다 - 순수 SELECT라
       비용이 사실상 0이고, 같은 표가 여러 claim에서 반복 채택돼도 API를
       중복 호출하지 않는다.
    2. 캐시 행이 없거나(구버전 DB - 이 기능 이전에 적재된 표) `purpose_
       fetched_at`이 없으면(적재 당시 get_stat_explanation 자체가
       실패했었음) `kosis_client`가 있을 때만 라이브 API로 폴백한다(기존
       동작 - Task #28 온디맨드 백필과 동일한 opt-in 관용).
    3. 캐시에 `purpose_fetched_at`은 있는데 writing_purps/examin_objrange가
       둘 다 비어 있으면(적재 당시 이미 시도했지만 못 가져왔음) 재시도
       하지 않는다 - 근거 없이 API를 계속 두드리지 않는다는 폴백 원칙.

    `hcx_purpose_verify_fn`이 없으면(기본값 - 대부분의 회귀 테스트) 이
    검증 전체를 시도하지 않는다. `kosis_client`는 이제 "캐시가 없을 때의
    폴백"에만 필요하다 - 캐시에 이미 텍스트가 있으면 `kosis_client` 없이도
    (`hcx_purpose_verify_fn`만 있으면) 검증이 동작한다(캐시 우선 설계의
    핵심 이점).

    실패(캐시 조회 예외, get_stat_explanation 예외, 빈 설명, HCX 호출/파싱
    실패)는 전부 조용히 삼키고 result를 그대로 돌려준다 - 근거 없이 판정을
    낮추는 것보다 "검증을 못 했다"는 게 더 안전한 기본값이다(다른 신규
    필드들과 동일한 폴백 원칙). HCX 호출 자체가 실패한 경우만 진단용
    purpose_check_error를 남긴다(hcx_fallback_error와 같은 투명성 원칙)."""
    if hcx_purpose_verify_fn is None:
        return result

    purpose_text = None
    cached = None
    try:
        cached = wh.get_table_purpose_cached(conn, org_id, tbl_id)
    except Exception as e:
        logger.warning(
            f"[목적 검증 - 캐시(tables_registry) 조회 실패, 조용히 건너뜀] {org_id}/{tbl_id} - {e}"
        )

    if cached is not None and cached.get("purpose_fetched_at"):
        # 표 적재 시점에 이미 시도됨(성공이든 실패든) - 재시도하지 않는다.
        purpose_text = "\n".join(
            v for v in (cached.get("writing_purps"), cached.get("examin_objrange")) if v
        ).strip() or None
    elif kosis_client is not None:
        # 캐시가 없거나(구버전 DB) 아직 적재 시점에 시도한 적 없음 - 라이브
        # API로 폴백(기존 동작).
        try:
            expl = kosis_client.get_stat_explanation(org_id, tbl_id)
        except Exception as e:
            logger.warning(
                f"[목적 검증 - get_stat_explanation 실패, 조용히 건너뜀] {org_id}/{tbl_id} - {e}"
            )
            return result
        if expl:
            purpose_text = "\n".join(
                v for v in (expl.get("writingPurps"), expl.get("examinObjrange")) if v
            ).strip() or None

    if not purpose_text:
        return result

    try:
        verdict = hcx_purpose_verify_fn(
            raw_sentence,
            result.get("table_name"),
            purpose_text,
            _claimed_value(claim),
            (claim.get("unit") or "").strip() or None,
            claim.get("period"),
        )
    except Exception as e:
        logger.warning(
            f"[목적 검증 HCX 호출 실패 - 조용히 건너뜀] {org_id}/{tbl_id} - {e}"
        )
        result["purpose_check_error"] = str(e)
        return result

    if verdict is None:
        return result
    result["purpose_mismatch"] = verdict.get("mismatch")
    result["purpose_mismatch_note"] = verdict.get("reason")
    return result


def _resolve_series_siblings(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    tbl_nm: Optional[str],
    itm_id: str,
    axis_codes: Dict[int, str],
    period_digits: str,
    claim: Dict[str, Any],
    raw_sentence: str,
) -> Dict[str, Any]:
    """[2026-08-28 포팅 - CLAUDE.md "동명표(원지수/계절조정 등) 처리" 결정
    참고, 사용자가 "구 아키텍처에서 이미 다 해결했는데 자꾸 빠진다"고 지적해
    포팅함] 구 kosis_agent.py(`backup/20260815_kosis_refactor/kosis_agent.py`)
    의 `_disambiguate_table_candidates`를 로컬 웨어하우스 조회로 그대로
    옮긴 것 - 우선순위(추측하지 않는다, Decision 003)까지 동일하게 유지:

    1. `kls.find_sibling_tables`로 표제목이 계열 접미사(원지수/계절조정
       등)만 다른 형제 표를 찾는다. 형제가 없으면(자기 자신뿐) 바로
       `{"switched": False}`.
    2. claim 원문(raw_sentence)에 계열 명시어가 literal하게 있으면
       (`kls.detect_series_qualifier`), 그 단어가 표제목에 있는 형제로
       유일하게 좁혀질 때만 그쪽으로 갈아탄다(이미 확정된 표 자체가 그
       계열이면 갈아탈 필요 없음 - switched=False). 명시어가 없거나
       여럿에 걸치면(못 좁힘) 3번으로 계속 진행한다.
    3. claim의 claimed_value와 각 형제 표의 같은 (itm_id, axis_codes,
       period_digits) 셀 값을 대조한다 - 확정된 표가 아닌 다른 형제가
       상대오차 5% 이내로 가장 잘 맞고, 그 차이가 차점자와 확실히
       구분될 때만(동점이면 추측하지 않음) 그쪽으로 갈아탄다.
    4. 위 어느 것도 명확한 근거를 못 주면 원래 확정된 표를 그대로 둔다
       (`{"switched": False}`) - 이 함수는 표를 "더 나은 근거가 있을
       때만" 바꾸지, 애매하면 절대 추측해서 바꾸지 않는다.

    이 함수는 절대 예외를 던지지 않는다 - 어떤 단계에서든 실패하면 조용히
    `{"switched": False}`로 안전하게 폴백한다(다른 신규 게이트들과 동일한
    폴백 원칙 - 형제 표 확인 자체가 claim 판정 전체를 죽이면 안 됨)."""
    try:
        siblings = kls.find_sibling_tables(conn, org_id, tbl_id, tbl_nm)
    except Exception as e:
        logger.warning(f"[동명표 형제 확인 실패 - 조용히 건너뜀] {org_id}/{tbl_id} - {e}")
        return {"switched": False}

    if len(siblings) <= 1:
        return {"switched": False}

    try:
        qualifier = kls.detect_series_qualifier(raw_sentence)
        candidates_for_value_compare = siblings
        if qualifier:
            matching = [s for s in siblings if qualifier in (s.get("tbl_nm") or "")]
            if len(matching) == 1:
                target = matching[0]
                if (target["org_id"], target["tbl_id"]) == (org_id, tbl_id):
                    return {"switched": False}  # 이미 명시어와 일치하는 표
                cell = kls.fetch_cell_value(
                    conn, target["org_id"], target["tbl_id"], itm_id, axis_codes, period_digits
                )
                if cell is not None and cell.get("value") is not None:
                    logger.info(
                        f"[동명표 형제 확정 - 원문 계열 명시어] {org_id}/{tbl_id} -> "
                        f"{target['org_id']}/{target['tbl_id']} (명시어 '{qualifier}')"
                    )
                    return {
                        "switched": True,
                        "org_id": target["org_id"], "tbl_id": target["tbl_id"],
                        "tbl_nm": target["tbl_nm"],
                        "value": cell["value"], "unit": cell["unit"],
                        "note": f"형제 표(동명표) 확정 - claim 원문의 계열 명시어 '{qualifier}'",
                    }
                return {"switched": False}
            if matching:
                candidates_for_value_compare = matching

        claimed_value = _claimed_value(claim)
        if claimed_value is None:
            return {"switched": False}

        scored = []
        for s in candidates_for_value_compare:
            cell = kls.fetch_cell_value(
                conn, s["org_id"], s["tbl_id"], itm_id, axis_codes, period_digits
            )
            if cell is None or cell.get("value") is None:
                continue
            rel_diff = abs(cell["value"] - claimed_value) / max(abs(claimed_value), 1e-9)
            scored.append((rel_diff, s, cell))
        if not scored:
            return {"switched": False}
        scored.sort(key=lambda x: x[0])
        best_rel_diff, best_sibling, best_cell = scored[0]
        if (best_sibling["org_id"], best_sibling["tbl_id"]) == (org_id, tbl_id):
            return {"switched": False}
        if best_rel_diff > 0.05:
            return {"switched": False}
        if len(scored) > 1 and scored[1][0] <= best_rel_diff:
            return {"switched": False}  # 동점/근소한 차이 - 추측하지 않음
        logger.info(
            f"[동명표 형제 확정 - 실값 대조] {org_id}/{tbl_id} -> "
            f"{best_sibling['org_id']}/{best_sibling['tbl_id']} (상대오차 {best_rel_diff*100:.2f}%)"
        )
        return {
            "switched": True,
            "org_id": best_sibling["org_id"], "tbl_id": best_sibling["tbl_id"],
            "tbl_nm": best_sibling["tbl_nm"],
            "value": best_cell["value"], "unit": best_cell["unit"],
            "note": (
                f"형제 표(동명표) 확정 - claim 수치와 실값 대조"
                f"({best_rel_diff*100:.2f}% 이내로 최적 일치)"
            ),
        }
    except Exception as e:
        logger.warning(f"[동명표 disambiguation 실패 - 조용히 건너뜀] {org_id}/{tbl_id} - {e}")
        return {"switched": False}


def resolve_claim_evidence(
    conn: sqlite3.Connection,
    claim: Dict[str, Any],
    keywords: List[str],
    sibling_values: Optional[Iterable[Any]] = None,
    stage1_keywords: str = "run03",
    hcx_resolve_fn: Optional[
        Callable[[List[str], str, Optional[float], Optional[str], Optional[str]], Optional[int]]
    ] = None,
    hcx_axis_resolve_fn: Optional[
        Callable[
            [Dict[int, Dict[str, Any]], str, Optional[str], Optional[float], Optional[str], Optional[str]],
            Optional[Dict[int, str]],
        ]
    ] = None,
    hcx_table_resolve_fn: Optional[
        Callable[[List[Dict[str, Any]], str, Optional[float], Optional[str], Optional[str]], Optional[int]]
    ] = None,
    kosis_client: Optional[Any] = None,
    write_conn: Optional[sqlite3.Connection] = None,
    hcx_stage3_fn: Optional[
        Callable[[str, str, Optional[float], Optional[str]], Optional[Dict[str, Any]]]
    ] = None,
    hcx_purpose_verify_fn: Optional[
        Callable[
            [str, Optional[str], str, Optional[float], Optional[str], Optional[str]],
            Optional[Dict[str, Any]],
        ]
    ] = None,
) -> Dict[str, Any]:
    """claim 하나(run01 형식: claim/claim_id/value/unit/period)와
    keywords(run03이 이미 골라준 claim별 matched_keywords)를 받아,
    kosis_local_search.py의 로컬 DB 검색/해석 함수만으로 evidence를
    만든다. 반환 딕셔너리는 new_kosis_agent.NewKosisAgent.
    _fetch_result_to_evidence와 완전히 같은 계약(org_id/table_id/
    table_name/normalized_value/normalized_unit/query_status/
    derivation/candidates_tried)을 맞춘다 - adapter.py가 agent 종류를
    몰라도 되게 하기 위해서다.

    3단계: ① search_local로 표 후보(Stage 1) ② resolve_evidence_by_
    flat_match로 그 표 안 item/축 후보(Stage 2, 동점이면 disambiguate_
    by_value로 값 기반 타이브레이크) ③ 확정된 셀에서 claim이 말하는
    시점(period)의 실제 값을 facts에서 직접 조회(Stage 3).

    sibling_values: [2026-08-18 신규] 같은 원문장(claim["claim"])을 공유하는
    다른 claim들의 값 - 열거형 문장("과일(35.2%)과 우유(30.7%) 등은 5년
    전에 비해 30% 넘게 급등했다")에서 변화 동사 트리거 판정에 쓴다(호출부인
    LocalDbAgent.process_claim_group_keywords가 claim 그룹을 raw_sentence
    기준으로 묶어서 모아 넘겨준다). 안 넘기면(단독 claim 등) 기존 동작으로
    안전하게 폴백한다.

    stage1_keywords: [2026-08-18 신규 - A/B 실험용, 2026-08-21 세 번째
    값 추가] Stage 1(표 후보 확정)에 어떤 경로를 쓸지 - 기본값
    "run03"(현재 프로덕션 동작, run03의 LLM 패러프레이즈로 kls.
    search_local 호출 - 실측: 14/14 표 찾기 성공, 모듈 docstring 참고),
    "metric_normalized"(1번이 이미 분리해서 준 metric_normalized 토큰만
    써서 kls.search_local 호출, 사용자 제안), "llm_table_select"
    (2026-08-21 신규 - README "열세 번째" 항목, CLAUDE.md "담당 범위
    정정" 참고) 세 값만 유효하다.

    "llm_table_select"는 run03/로컬 FTS(search_local)를 아예 건너뛰고
    로컬 tables_registry 전체(kls.list_registered_tables)와 claim을
    hcx_table_resolve_fn(보통 hcx_stage1_resolver.resolve_table_with_
    hcx007) 한 콜에 넘겨 표를 직접 고르게 한다 - run02/run03도 이제
    사용자 담당이 됐고, run03가 의존하는 KOSIS 라이브 검색 자체가
    불안정하다는 게 실측 확인돼 있어서(README 2.1), 그리고 로컬 FTS
    폴백도 순수 숫자 토큰 충돌 같은 별도 버그에 취약하다는 게 실측
    확인돼서(A93bfa851-C018, README "열세 번째" 항목) 만든 대안 경로다.
    hcx_table_resolve_fn이 없거나 호출/파싱이 실패하거나 HCX가 확신
    없다고 답하면(None), 기존 search_local 경로로 조용히 폴백하지
    않고 바로 "not_found"로 끝낸다 - 두 경로의 실측 성능이 섞이지
    않도록 A/B 비교 목적상 의도적으로 분리했다.

    run03_result.json 없이도(예: 1번이 나중에 정말 완벽하게 분리해서
    준다면 run03 없이 바로 이 파이프라인을 쓸 수도 있는 시나리오) 얼마나
    성능이 달라지는지 90개 claim 전체로 실측 비교하기 위한 스위치 -
    결론이 나면(A/B 결과 기록은 Research Overview 2.md) 기본값을
    유지할지 바꿀지, 아니면 이 파라미터 자체를 없앨지 정한다.

    [2026-08-24 삭제됨 - "안 쓰기로 한 로직" 정리] 여기 있던 embed_fn/
    dim_embed_cache 파라미터(Task #80, 2026-08-20, Stage 2 갭을 CLOVA
    임베딩으로 메우던 경로 - vdb_discovery.embedding_expand_phrases)는
    프로덕션(run04_local.py/adapter.py 기본 LocalDbAgent())에서 한 번도
    쓰인 적 없고, 바로 아래 hcx_resolve_fn(HCX-007 단일 콜)이 같은 역할을
    대체하면서 완전히 안 쓰는 코드가 됐다. 2026-08-24 실측(90건 배치를
    실수로 이 경로로 돌렸다가 CLOVA 429 캐스케이딩 재확인) 이후 사용자가
    명시적으로 삭제를 지시해 제거했다.

    hcx_resolve_fn: [2026-08-21 신규 - Task #80 전환, README "열 번째"
    항목 참고] Stage 2 갭(표는 확정, 문자 그대로 매칭 실패) 폴백 자리를
    채운다 - 셀 하나하나를 따로 임베딩하는 대신, 표의 distinct 셀 텍스트
    "전체"와 claim을 HCX-007 한 콜에 담아 정답 셀의 index(또는 확신
    없으면 None)를 받아온다(hcx_stage2_resolver.resolve_cell_with_hcx007).
    셀 개수가 아무리 많아도(실측 최대 11,032개) 표당 1콜이라 임베딩의
    QPM 병목과 max_cells 상한을 그대로 안 물려받고, top_k로 후보를 미리
    자르지 않으니 "정답이 후보군 밖으로 밀려나는" 문제도 구조적으로
    없다(둘 다 실측으로 확인된 임베딩 경로의 한계 - README 아홉/열 번째
    항목, 이제 위에서 삭제된 임베딩 경로 얘기).
    hcx_resolve_fn은 단일 index만 돌려주므로(순위 목록이 아님)
    disambiguate_by_value로 더 좁힐 동점 후보가 애초에 없다 - HCX의
    판단을 그대로 신뢰한다는 뜻이라, 이 판단 자체가 틀릴 수 있다는
    한계는 남아있다(값으로 최종 교차검증하는 안전장치는 아직 없음 -
    README에 미해결로 남겨둠).

    hcx_axis_resolve_fn: [2026-08-22 신규 - 사용자 실측 발견/설계 대응]
    hcx_resolve_fn과 별개 자리 - weak_literal_tie(동점은 났는데 근거가
    약함, 1138줄 부근) 전용이다. 원래 이 상황에서도 hcx_resolve_fn을
    그대로 썼는데(표 전체를 flat text로 펼쳐서 보냄), 축이 여러 개인
    큰 표(DT_1J22001 - 지역 19 × 지출목적별 581)에서 카테시안 곱이
    673,343자까지 폭발해 HCX 분당 토큰 한도(60,000)를 요청 한 번으로
    다 써버리는 게 실측 확인됐다(A93bfa851-C007/C009, x-ratelimit-
    remaining-tokens=0 응답 헤더로 확인 - 429가 요청 *횟수* 제한이
    아니라 *토큰* 제한이었다는 뜻). kosis_local_search.build_axis_trees
    (카테시안 곱 없이 축마다 압축된 트리 하나씩, 조상 이름 반복 없음)와
    hcx_tree_resolver.resolve_axis_codes_with_hcx007(축별 코드 매핑을
    받아옴)을 넘기면 이 경로를 쓴다 - 표 크기와 무관하게 실제 고유 노드
    수에 비례하는 크기로 끝나므로 큰 표에서도 안전하다. None이면(기본값)
    이 경로 자체가 비활성화되고, weak_literal_tie는 hcx_resolve_fn(있으면)
    또는 disambiguate_by_value로 그대로 폴백한다 - 기존 동작 안 바뀜.
    hcx_resolve_fn과 hcx_axis_resolve_fn을 둘 다 넘기면 weak_literal_tie는
    hcx_axis_resolve_fn을 우선한다(토큰 안전한 쪽을 먼저 시도) - gap
    폴백(후보 0개, 990줄 부근)은 이 인자와 무관하게 여전히 hcx_resolve_fn만
    쓴다(그쪽은 애초에 후보가 없어 "동점 후보 밖 정답 찾기"가 아니라
    "아무 후보나 찾기"라 표 전체를 보여줄 필요/이유가 여전히 유효함).

    hcx_table_resolve_fn: [2026-08-21 신규 - stage1_keywords=
    "llm_table_select"일 때만 쓰임] 로컬 tables_registry 전체(list[dict],
    kls.list_registered_tables 형식)와 claim을 받아 표 index(또는 확신
    없으면 None)를 돌려주는 콜러블(보통 hcx_stage1_resolver.resolve_
    table_with_hcx007) - hcx_resolve_fn(Stage 2용)과 이름은 비슷하지만
    별개 자리(Stage 1용)다. stage1_keywords가 다른 값이면 이 인자는
    아예 안 쓰인다(기본값 None으로 둬도 기존 두 모드는 전혀 안 변함).

    kosis_client/write_conn: [2026-08-22 신규 - Task #28, 사용자 지적으로
    배선] Stage 3에서 item/축까지 이미 확정됐는데(itm_id/axis_codes 결정)
    그 시점(period)의 값이 로컬 facts에 없을 때(no_data), 온디맨드 백필을
    시도하기 전에 먼저 fact_coverage로 "이 주기(prd_se) 자체가 KOSIS에
    없다"를 공짜로(API 호출 없이) 걸러낼 수 있는지 본다(Task #28-2, 사용자
    제안 - ingest_table 배치 적재가 KOSIS getMeta(period)가 보고하는 주기를
    전부 훑어 record_coverage를 남기므로, 이미 기록된 주기 목록에 없으면
    "적재를 안 한 것"이 아니라 "KOSIS 자체에 그 주기가 없다"는 뜻이다 -
    kosis_client 유무와 무관하게 항상 이 필터를 먼저 적용한다). 이 필터를
    통과하면(또는 fact_coverage가 아예 비어 판단 근거가 없으면) 이미
    확정된 org_id/tbl_id/itm_id/axis_codes 그대로 기간만 바꿔 KOSIS에
    온디맨드로 한 번 더 요청해 채운다(kosis_warehouse.fetch_scoped_slice 재사용 -
    "적재 범위 정책 2번 레버"로 이미 존재했지만 이 파일에는 배선 안 돼
    있었다. cache-miss 확장이 아예 안 쓰인다던 모듈 docstring의 예전
    설명은 "표 자체가 없을 때"(kosis_warehouse.ensure_tables_for_claim)
    얘기였고, "표는 있는데 이 기간만 없을 때"는 별도 배선 지점이라고
    그 문서에도 명시돼 있었다 - 이번에 그 지점을 채운다). 둘 다 None(기본값)
    이면 기존 동작 그대로(순수 읽기, 네트워크 없는 회귀 테스트 포함 전혀
    안 바뀜) - kosis_client가 있어야만 시도하고, 실패해도(네트워크/API
    예외) 그대로 삼키고 기존 no_data 경로로 안전하게 폴백한다(embed_fn/
    hcx_resolve_fn과 같은 에러 처리 관례).

    hcx_stage3_fn: [2026-08-22 신규 - Task #29 Step 3, opt-in - 기본값
    None이면 기존 동작 그대로(regex 휴리스틱 `_needs_rate_derivation`/
    `_resolve_reference_period`/`_extract_explicit_reference_period`만
    씀, 네트워크 없는 회귀 테스트 포함 전혀 안 바뀜)] hcx_stage3_resolver.
    resolve_comparison_mode_with_hcx007과 같은 시그니처
    (claim_text, target_period, claimed_value, claimed_unit) ->
    {"mode","reference_period"}|None. 90건 합성 평가셋 실측(README
    "스물다섯 번째" 항목)에서 HCX-007은 mode 분류(비교가 있는가/
    item_diff인가) 자체는 쓸 만했지만 reference_period **숫자 계산**은
    안 믿을 만했다(전년동월비인데 월이 어긋나거나 "N년 전" 산수가 몇 년씩
    틀리는 사례 확인) - 그래서 이 함수는 hcx_stage3_fn의 반환값 중
    mode만 신뢰하고, reference_period 숫자는 그대로 쓰지 않고 항상 기존
    결정적 경로(`_resolve_reference_period`/`_extract_explicit_reference_
    period`)로 다시 계산한다.

    이 opt-in이 실제로 여는 새 기능은 딱 하나 - mode="item_diff"(C003/
    C004류, "이 항목의 등락률이 전체/총지수 등락률보다 N%포인트 높다")
    다. 기존 `_needs_rate_derivation`은 이 케이스를 아예 다루지 않았다
    (period_change/yoy 두 갈래뿐). mode="single"/"period_change"는
    기존 regex 휴리스틱이 이미 검증돼 있으므로(회귀 테스트 다수) 이
    함수가 덮어쓰지 않는다 - hcx_stage3_fn이 이 두 mode를 반환해도
    기존 `_needs_rate_derivation` 판단을 그대로 쓴다.

    item_diff는 HCX 판단(53% 정확도, 실측)만으로 트리거하지 않는다 -
    `_has_total_comparison_keyword`(원문에 "전체"/"총지수"/"평균"/"총계"
    같은 로컬 키워드가 실제로 있는지)를 반드시 함께 만족해야 한다(2차
    corroboration, 순수 HCX 오탐을 줄이기 위함). 그리고 axis_codes 중
    "총계로 바꿔치기할 축"이 정확히 하나로만 좁혀질 때만(`_find_
    swappable_axis_position`) 시도한다 - 여러 축이 후보가 되면(모호함)
    추측하지 않고 시도 자체를 건너뛴다.

    LocalDbAgent가 `self.conn`을 읽기 전용(mode=ro)으로만 여는 이유가
    "이 agent는 검색만 하지 적재는 안 한다"는 명시적 설계였으므로,
    fetch_scoped_slice가 필요로 하는 쓰기 가능 커넥션은 read-only인
    conn과 별개로 write_conn에 받는다 - kosis_client를 넘긴 호출부만
    별도로 쓰기 가능 커넥션을 열어 넘기게 해서(LocalDbAgent.__init__
    참고) 이 함수 자체의 기존 읽기 전용 계약은 안 깨진다. 백필이
    실제로 write_conn에 commit되면, 같은 DB 파일을 보는 conn(read-only)
    으로 재조회해도 그 값이 그대로 보인다(SQLite 표준 동작 - 별도
    커넥션이어도 커밋된 내용은 공유).

    hcx_purpose_verify_fn: [2026-08-28 신규 - 목적 검증(purpose
    verification) 게이트, 사용자가 실제 KOSIS URL 2건(DT_114054_112 -
    외식업 식재료 구매행태, DT_143002_E002 - 농가경제 소득분석)으로
    지적한 아키텍처 갭 대응] 표/축 이름 매칭까지 전부 성공해도(예: "배추
    가격" claim이 이름만 보고 "채소류 월평균 구매금액" 표에 걸리는 경우),
    그 표의 실제 작성 목적이 claim의 의도(소비자 소매가)와 다를 수 있다.
    (claim_text, table_nm, table_purpose_text, claimed_value, claimed_unit,
    claimed_period) -> {"mismatch": bool, "reason": str|None} | None을
    받는 콜러블(보통 hcx_purpose_resolver.resolve_purpose_with_hcx007)을
    넘기면, _attach_purpose_check가 derivation.used=False인 "최종 확정"
    성공 경로 2곳에서만(_attach_record_extremes와 같은 위치 - 비용 절감을
    위해 표를 이미 확정한 뒤 딱 1번만, 사용자와 합의된 설계) kosis_client.
    get_stat_explanation(org_id, tbl_id)로 표의 공식 작성 목적을 실제로
    조회해 claim과 대조한다. kosis_client도 함께 넘겨야 실제로 동작한다
    (get_stat_explanation 호출에 필요) - 기본값 None이면 이 검증 전체가
    꺼진 채로 기존 동작과 완전히 동일하다(다른 opt-in 신규 파라미터들과
    동일한 관용). 결과는 result["purpose_mismatch"]/["purpose_mismatch_note"]
    로 붙고, judgment.py의 _check_purpose_mismatch가 이를 실제 게이트로
    써서 UNVERIFIED_PURPOSE_MISMATCH로 판정을 낮춘다(장식적 텍스트가
    아니라 Decision 003과 동일한 강제 패턴)."""
    raw_sentence = claim.get("claim") or ""
    phrases = [k for k in (keywords or []) if k]

    # [2026-08-17 실측 발견 - 실제 run03 데이터로 처음 돌려보고 발견]
    # run03의 matched_keywords는 LLM이 만든 여러 단어짜리 "패러프레이즈"
    # 다(예: "가정용품 및 가사서비스 물가", "가사서비스 물가동향" 등) -
    # KOSIS 자체 검색엔진(제목/설명 기반, 어느 정도 유사어에 관대함)을
    # 겨냥해 만든 문구라 표 찾기(Stage 1)엔 실제로 잘 먹혔다(14건 전부
    # 정확한 표를 찾음, 실측 확인). 근데 Stage 2(resolve_evidence_by_
    # flat_match)는 breadcrumb 텍스트에 phrase가 "그대로" 부분 문자열로
    # 들어있는지를 본다 - "가정용품 및 가사서비스 물가"라는 9어절짜리
    # 문구는 실제 KOSIS 항목명("가정용품·가사서비스", 및/물가 없음,
    # 가운뎃점)과 절대 안 겹친다. 반면 원문장을 단어 단위로 토큰화하면
    # ("가정용품", "가사서비스" 등 개별 토큰) 그 안의 "가정용품"/
    # "가사서비스" 각각은 breadcrumb와 정확히 겹친다(실측: score=3으로
    # 유일하게 T007 선택됨). 그래서 Stage 1은 run03 keywords를 쓴다.
    #
    # [2026-08-17 두 번째 실측 발견 - 사용자가 미리 짚은 문제] Stage 2를
    # raw_sentence 전체 토큰화로 하면 또 다른 문제가 생긴다 - 1번 팀원의
    # claim 분리가 완벽하지 않아서, 같은 원문장에서 여러 claim_id가
    # 나올 때(예: "기타 식료품(21.4%), 육류(21.1%), 어류 및 수산(20.0%)"
    # 한 문장에 세 지표) raw_sentence를 통째로 토큰화하면 세 claim_id가
    # 전부 "기타"/"식료품"/"육류"/"어류"/"수산" 토큰을 다 같이 받는다 -
    # 실측 확인: C012("기타 식료품")와 C014("어류 및 수산")가 서로 다른
    # claim인데 같은 항목(둘 다 같은 itm_id)으로 잘못 수렴했다. 1번
    # 팀원은 raw_sentence는 공유해도 metric_normalized는 claim마다 이미
    # 분리해서 준다("기타 식료품 물가" vs "어류 및 수산 물가") - 이걸
    # 쓰면 그 claim에만 해당하는 토큰만 남아 오염이 없다(실측 재확인:
    # metric_normalized 토큰화로 바꾸니 두 claim이 서로 다른 축/값으로
    # 정확히 갈림). metric_normalized가 없는 경우에만 raw_sentence로
    # 폴백한다.
    metric_text = claim.get("metric_normalized") or claim.get("metric") or ""
    match_phrases = kls._tokenize(metric_text) if metric_text else kls._tokenize(raw_sentence)

    # [2026-08-18 신규 - VDB 설계 문서 진입점 ② "값 기반 검색"을 이름/축
    # 매칭(Stage 1/2)보다 먼저 시도한다.
    #
    # 왜 먼저인가(사용자 제안): 48개 claim 재검증에서 Stage 1이 완전히
    # 엉뚱한 표를 고르는 실측 사례(C006/C007 - run03 패러프레이즈의
    # "경제활동"이란 단어가 산업 축조차 없는 표 이름과 우연히 겹침)가
    # 나왔다. 이 claim들의 진짜 정답 값은 정답 표에 실재하므로, 이름
    # 매칭이 뭘 고르든 상관없이 값+시점으로 먼저 찾으면 이 오류 유형을
    # 근본적으로 우회할 수 있다.
    #
    # 값만으로 순환논리에 빠지지 않도록: match_phrases 전부가(단 하나도
    # 안 빠지고) 후보의 item/축 breadcrumb에 걸렸을 때만("완전 corroboration")
    # 채택한다 - 실측 확인: "취업자"류 흔한 ITEM명 하나만 우연히 걸리는
    # 건(C006에서 실제로 재현됨, matched=1) 걸러지고, "제조업"+"취업자"가
    # 둘 다 걸리는 진짜 정답(C007, matched=2/2)만 통과한다. 하나라도
    # 못 채우면(예: claim이 원자료가 아니라 YoY 파생값을 요구하는 경우 -
    # C005/C006처럼 "증가분" 숫자 자체는 어느 facts 행에도 없음) 조용히
    # 아래 기존 Stage 1/2/3 경로로 넘어간다 - 동작이 안 바뀐다.
    period_digits_early = kls._normalize_period_digits(str(claim.get("period") or ""))
    claimed_value_for_search = _claimed_value(claim)

    # [2026-08-24 신규 - 실측 버그 발견, test_claims_schema_v2.py
    # "explicit comparison_period" 케이스] claim이 변화율(change_rate)을
    # 주장하는데, 값 기반 검색(아래)은 "이 표에 claimed_value와 숫자가
    # 비슷한 raw 항목이 있는가"만 보고 그 항목이 실제로 등락률류인지는
    # 확인하지 않는다 - 실측 재현: "2020년 9월에 비해 22.9% 올랐다"
    # (value_type=change_rate)가 완전히 무관한 표(DT_1J22001, 지출목적별
    # 소비자물가"지수")의 항목 하나가 우연히 값 22.69(단위 "2020=100",
    # 즉 지수값이지 등락률이 아님)를 가졌다는 이유만으로 채택돼버렸다.
    # claim은 22.9% 등락률을 주장하는데 매칭된 값은 지수값이라 단위/의미
    # 자체가 다르다 - phrase corroboration(>=2)만으로는 이 종류의 우연을
    # 못 거른다. declares_change_rate이면 아래 값 기반 검색 결과를 그대로
    # 못 믿고, 매칭된 항목이 실제로 kls._infer_measure_type()=="rate_of_
    # change"(KOSIS가 이미 등락률로 내주는 항목)일 때만 신뢰한다 - 아니면
    # 조용히 기존 Stage 1/2/3(파생 계산 경로, comparison_period/YoY 처리)
    # 로 넘어간다(값 기반 검색을 아예 안 쓴 것처럼 폴백 - 동작이 원래
    # "값 기반 검색이 없던 시절"과 같아짐, 새 오류 유형 도입 아님).
    #
    # 1번 신규 스키마의 value_type이 있으면 그걸 그대로 신뢰(가장 정확).
    # 없으면(구 포맷) 기존 _needs_rate_derivation류가 보는 것과 같은 신호
    # (unit="%" + 원문장에 실제 변화 동사)로 근사 판별한다 - 이 단계에선
    # 아직 measure_type을 모르므로(후보를 찾기 전이라) _needs_rate_
    # derivation을 그대로 호출할 수는 없고, 그 함수가 보는 신호 중 이
    # 시점에도 이미 알 수 있는 것만 가볍게 재사용한다.
    value_type = (claim.get("value_type") or "").strip()
    if value_type:
        declares_change_rate = value_type == "change_rate"
    else:
        declares_change_rate = (
            (claim.get("unit") or "").strip() == "%"
            and any(m in raw_sentence for m in _RATE_CHANGE_VERB_MARKERS)
        )

    # [2026-08-18 실측 발견 - 안전장치] match_phrases가 1개뿐이면 "전부
    # corroboration"이 사실상 무의미해질 수 있다 - 실측: "빵 물가"의
    # _tokenize 결과가 ['물가']뿐이었다(1글자 품목명 "빵"이 토큰화 길이
    # 필터(>=2자)에 걸려 빠짐). "물가"는 거의 모든 CPI 셀에 다 걸리는
    # 범용 단어라, 이걸로 "완전 일치"를 주장하면 완전히 무관한 품목(체리)
    # 이 우연히 비슷한 값을 가졌다는 이유만으로 채택되는 실제 오탐이
    # 재현됐다(claim "빵 38.5%" -> 광주 체리 38.65로 오채택). 최소
    # 2개 이상의 서로 다른 phrase가 다 걸려야만("가짜 corroboration"이
    # 아니라 진짜 구체적 단어 조합) 값 기반 검색을 신뢰한다.
    if (
        claimed_value_for_search is not None
        and period_digits_early
        and match_phrases
        and len(match_phrases) >= 2
    ):
        value_hits = kls.search_by_value(
            conn, claimed_value_for_search, period_digits_early,
            match_phrases=match_phrases, tolerance=0.01, top_n=5,
        )
        # [2026-08-19 실측 버그 수정] 원래는 match_phrases 전부(100%) 일치를
        # 요구했는데, kls._tokenize가 가운뎃점 복합어에 접미사를 빌려붙여
        # 여러 "표기 후보"를 만들어내는 경우(예: "전문·과학기술서비스업
        # 취업자" -> ['전문','과학기술서비스업','취업자','전문서비스업',
        # '전문업'] 5개) 그 후보들은 "전부 같이 나와야 할 단어"가 아니라
        # "여러 갈래 표기 중 하나"라 실측(A82ae9f41-C005)에서 실제 KOSIS
        # 항목명("전문 과학 및 기술 서비스업")엔 5개 중 2개(전문/취업자)만
        # 걸렸다 - 100% 요구가 진짜 정답(rel_diff 0.2% 이내)까지 걷어내고
        # 있었다. 위 len(match_phrases)>=2 게이트가 이미 "물가" 1개짜리
        # 범용 매칭 오탐(빵 38.5%->체리 사례)을 막고 있으므로, corroboration
        # 기준을 "match_phrases 최소 2개 일치"로 낮춰도 그 안전장치는 그대로
        # 유지된다(2개 미만이면 애초에 이 블록에 진입하지 않음).
        full_corroboration = [
            v for v in value_hits if v["matched_phrase_count"] >= 2
        ]
        if declares_change_rate:
            # [2026-08-24 신규 - 위 declares_change_rate 주석 참고] claim이
            # 등락률을 주장하는데 매칭된 항목이 KOSIS 등락률 항목(rate_of_
            # change)이 아니면(지수/절대값 등) 값이 우연히 비슷했을
            # 가능성이 높으므로 후보에서 제외한다.
            full_corroboration = [
                v for v in full_corroboration
                if kls._infer_measure_type(v.get("itm_nm"), v.get("unit")) == "rate_of_change"
            ]
        if full_corroboration:
            winner = full_corroboration[0]
            logger.info(
                f"[값 기반 검색 채택] claim_id={claim.get('claim_id')} "
                f"table={winner['tbl_id']} item={winner['itm_id']} "
                f"axis={winner['axis_codes']} rel_diff={winner['rel_diff']:.4f} "
                f"(match_phrases {len(match_phrases)}개 전부 corroboration)"
            )
            result = {
                "org_id": winner["org_id"], "table_id": winner["tbl_id"],
                "table_name": winner["tbl_nm"],
                "normalized_value": winner["raw_value"], "normalized_unit": winner["unit"],
                "query_status": "success",
                "derivation": {
                    "used": False,
                    "note": (
                        f"값 기반 검색으로 채택 - claim 값과 {winner['rel_diff']*100:.2f}% "
                        f"이내로 일치 + phrase {len(match_phrases)}개 전부 텍스트로도 확인됨"
                    ),
                },
                "confident": True,
                "value_search_used": True,
            }
            # [2026-08-28 신규 - 동명표(원지수/계절조정 등) 확인, CLAUDE.md
            # 결정 참고] 값 기반 검색이 형제 표 중 잘못된 계열을 골랐을
            # 수 있으므로, record-claim/목적 검증보다 먼저 확인해서 필요
            # 하면 표/값 자체를 교체한다.
            sibling_result = _resolve_series_siblings(
                conn, winner["org_id"], winner["tbl_id"], winner["tbl_nm"],
                winner["itm_id"], winner["axis_codes"], period_digits_early,
                claim, raw_sentence,
            )
            if sibling_result.get("switched"):
                result["org_id"] = sibling_result["org_id"]
                result["table_id"] = sibling_result["tbl_id"]
                result["table_name"] = sibling_result["tbl_nm"]
                result["normalized_value"] = sibling_result["value"]
                result["normalized_unit"] = sibling_result["unit"]
                result["derivation"]["note"] += f"; {sibling_result['note']}"
                winner = {
                    **winner,
                    "org_id": sibling_result["org_id"], "tbl_id": sibling_result["tbl_id"],
                    "tbl_nm": sibling_result["tbl_nm"],
                }
            # [2026-08-24 신규 - "역대 최고/최저" claim 배선] 이 경로는
            # derivation.used=False(단일 시점 원자료 직접 매칭)라 judgment.py의
            # _check_unverified(DERIVED_NEEDED)에 걸리지 않고 _check_record_claim
            # 까지 도달할 수 있는 경로다 - 역대 claim이면 이미 확정된
            # (org_id,tbl_id,itm_id,axis_codes)로 records 테이블도 같이 조회해
            # 실어 보낸다.
            result = _attach_record_extremes(
                result, conn, winner["org_id"], winner["tbl_id"], winner["itm_id"],
                winner["axis_codes"], period_digits_early, raw_sentence,
            )
            # [2026-08-28 신규 - 목적 검증 게이트] 같은 이유로 이 경로도
            # derivation.used=False인 "최종 확정" 성공 경로라 목적 검증
            # 대상이다 - _attach_purpose_check 문서 참고.
            return _attach_purpose_check(
                result, conn, winner["org_id"], winner["tbl_id"], claim, raw_sentence,
                kosis_client, hcx_purpose_verify_fn,
            )

    # [2026-08-18 신규 - search_by_value의 비교판, 같은 이유로 Stage 1/2
    # 보다 먼저 시도] claim이 "이 시점 대비 저 시점" 쌍대비교를 표현하면
    # (_claim_expresses_pairwise_change) search_by_diff로 표를 가리지
    # 않고 직접 찾는다. C005/C006류("~도 마찬가지로 증가세를 보였다")가
    # 이 경로로 해결된다 - C006은 실측 확인(DT_1DA7E33S_NEW 코드 85,
    # 71.7천명, phrase 2/2 corroboration). C005는 여러 항목이 동사
    # 하나를 공유하는 열거형 문장이라(_claim_expresses_pairwise_change
    # 자체가 안전하게 False를 반환 - _needs_rate_derivation과 같은 이유
    # 로 "다른 숫자의 동사"를 이 claim 것으로 오인하지 않는다) 이 경로를
    # 안 타고 기존 Stage 1/2/3으로 넘어간다(여전히 no_data로 안전하게
    # 끝남 - Future Work).
    if (
        claimed_value_for_search is not None
        and period_digits_early
        and match_phrases
        and len(match_phrases) >= 2
        and _claim_expresses_pairwise_change(claim, sibling_values=sibling_values)
    ):
        unit = (claim.get("unit") or "").strip()
        mode = "pct_change" if unit == "%" else "difference"
        reference_digits = _resolve_reference_period(claim, period_digits_early)
        if not reference_digits:
            reference_digits = _extract_explicit_reference_period(raw_sentence, period_digits_early)
        if not reference_digits:
            reference_digits = kls._yoy_reference_period(period_digits_early)
        if reference_digits:
            diff_hits = kls.search_by_diff(
                conn, claimed_value_for_search, period_digits_early, reference_digits,
                match_phrases=match_phrases, mode=mode, tolerance=0.02, top_n=5,
            )
            # [2026-08-19 실측 버그 수정 - 위 search_by_value 블록과 동일한
            # 이유] match_phrases 100% 대신 최소 2개 일치로 완화한다.
            full_corroboration_diff = [
                d for d in diff_hits if d["matched_phrase_count"] >= 2
            ]
            if full_corroboration_diff:
                winner = full_corroboration_diff[0]
                logger.info(
                    f"[값 기반 비교 검색 채택] claim_id={claim.get('claim_id')} "
                    f"table={winner['tbl_id']} item={winner['itm_id']} "
                    f"axis={winner['axis_codes']} mode={mode} rel_diff={winner['rel_diff']:.4f} "
                    f"(match_phrases {len(match_phrases)}개 전부 corroboration)"
                )
                return {
                    "org_id": winner["org_id"], "table_id": winner["tbl_id"],
                    "table_name": winner["tbl_nm"],
                    "normalized_value": winner["computed"],
                    "normalized_unit": "%" if mode == "pct_change" else unit,
                    "query_status": "success",
                    "derivation": {
                        "used": True,
                        "note": (
                            f"값 기반 비교 검색으로 채택 - {reference_digits}->{period_digits_early} "
                            f"{mode} 계산값이 claim 값과 {winner['rel_diff']*100:.2f}% 이내로 일치 + "
                            f"phrase {len(match_phrases)}개 전부 텍스트로도 확인됨"
                        ),
                    },
                    "confident": True,
                    "value_search_used": True,
                }

    # [2026-08-26 신규 - 진단 가시성] Stage 1이 llm_table_select 모드에서
    # HCX-007 실패/불확신으로 FTS 폴백을 탔는지를 최종 결과 어디서든(성공
    # 경로 포함) 확인할 수 있도록, hcx_fallback_used와 같은 패턴으로 함수
    # 전체 스코프에서 초기화해둔다 - "값이 맞았다"만이 아니라 "어느 경로로
    # 찾았는지"(비싼 HCX 콜 성공 vs 저렴한 FTS 구제)도 배치 결과에서
    # 구분할 수 있어야 비용/안정성 모니터링이 가능하다.
    llm_table_select_fallback_used = False

    if stage1_keywords == "llm_table_select":
        # [2026-08-21 신규 - Task #80 확장, README "열세 번째" 항목] 원래는
        # run03/로컬 FTS(search_local)를 아예 건너뛰고 로컬에 적재된 표
        # 전체를 HCX-007 한 콜에 보여줘 직접 표를 고르게 하는 경로였다.
        # 그때는 A/B 비교 목적상 실패 시 search_local로 조용히 폴백하지
        # 않고 바로 "not_found"로 끝내기로 했었다 - 두 경로의 실측 성능이
        # 섞이면 비교가 불가능해지기 때문.
        #
        # [2026-08-26 변경 - 실측 발견, A2e46e4ac-C022/C023/C024] 그 A/B
        # 비교용 제약이 `run04_local.py`가 llm_table_select를 프로덕션
        # 기본값으로 승격할 때(README "마흔여섯 번째") 재검토 없이 그대로
        # 따라왔다는 게 드러났다 - "딸기"/"바나나" claim이 HCX-007 표
        # 선택에서 실패(axis_hints/leaf_samples 절단 + 호출 자체의 비결정성,
        # probe_fruit_stage1_diagnosis.py로 실측 확인)했는데, 같은 표의
        # 리프 이름은 `dimensions_fts`(전체 dimensions를 인덱싱, axis_hints
        # 처럼 잘리지 않음)에 그대로 있어서 FTS 기반 `search_local`이라면
        # 바로 찾았을 claim이었다. A/B 비교는 이미 끝났으므로(README
        # "마흔여섯 번째"에서 llm_table_select 승격 근거로 이미 사용됨),
        # 이제는 "성능 비교를 위해 순수해야 한다"는 이유가 사라졌다 -
        # HCX-007이 실패/불확신일 때만 search_local로 폴백해서 두 경로의
        # 장점(HCX-007의 의미 추론 + FTS의 결정적/저비용 정확 매칭)을 같이
        # 취한다. FTS 쪽 keywords는 phrases(run03류)가 아니라 match_phrases
        # (metric_normalized 우선)를 쓴다 - 이 claim 하나만의 정확한 개념을
        # 검색해야, 같은 원문장을 공유하는 다른 claim(예: 같은 문장의
        # "딸기"/"바나나")과 뒤섞이지 않는다(2026-08-17 실측 버그, README
        # 참고, 와 같은 원칙).
        table_list = kls.list_registered_tables(conn)
        resolved_table_index = None
        llm_table_select_error = None
        if hcx_table_resolve_fn is None:
            llm_table_select_error = "hcx_table_resolve_fn 미지정"
        else:
            try:
                resolved_table_index = hcx_table_resolve_fn(
                    table_list, metric_text or raw_sentence,
                    claimed_value_for_search, (claim.get("unit") or "").strip() or None,
                    claim.get("period"),
                )
            except Exception as e:
                llm_table_select_error = str(e)

        org_id = tbl_id = tbl_nm = None
        if resolved_table_index is not None and 0 <= resolved_table_index < len(table_list):
            chosen_table = table_list[resolved_table_index]
            org_id, tbl_id = chosen_table["org_id"], chosen_table["tbl_id"]
            tbl_nm = chosen_table.get("tbl_nm")

        if org_id is None:
            # HCX-007이 실패했거나(예외/미지정) 확신 없음(None/범위 밖
            # index)을 반환한 경우 - FTS로 폴백.
            llm_table_select_fallback_used = True
            table_candidates = kls.search_local(
                conn, raw_sentence, keywords=match_phrases or None, top_n=5
            )
            if not table_candidates:
                return {
                    "query_status": "not_found",
                    "candidates_tried": [t.get("tbl_nm") for t in table_list[:5]],
                    "llm_table_select_error": llm_table_select_error,
                    "llm_table_select_fallback_used": True,
                    "llm_table_select_fallback_found": False,
                }
            top_table = table_candidates[0]
            org_id, tbl_id = top_table["org_id"], top_table["tbl_id"]
            tbl_nm = top_table.get("tbl_nm")
    else:
        stage1_kw = match_phrases if stage1_keywords == "metric_normalized" else phrases
        table_candidates = kls.search_local(conn, raw_sentence, keywords=stage1_kw or None, top_n=5)
        if not table_candidates:
            return {"query_status": "not_found", "candidates_tried": []}

        top_table = table_candidates[0]
        org_id, tbl_id = top_table["org_id"], top_table["tbl_id"]
        tbl_nm = top_table.get("tbl_nm")

    item_candidates = kls.resolve_evidence_by_flat_match(conn, org_id, tbl_id, match_phrases, top_n=5)

    # [2026-08-24 삭제됨 - "안 쓰기로 한 로직" 정리] embedding_fallback_
    # used/error 필드 자체는 반환 스키마 호환을 위해 남겨두되(항상 False/
    # None), 이 필드를 True로 만들던 CLOVA 임베딩 재시도 블록(Task #80,
    # vdb_discovery.embedding_expand_phrases 호출)은 프로덕션에서 한 번도
    # 안 쓰이고 hcx_resolve_fn(바로 아래)이 대체하면서 삭제됐다.
    embedding_fallback_used = False
    embedding_fallback_error = None
    hcx_fallback_used = False
    hcx_fallback_error = None

    # [2026-08-21 신규 - Task #80 전환] hcx_resolve_fn이 주어졌으면 이걸
    # 시도한다 - 셀 단위 임베딩의 QPM 병목/top-k truncation 실측 한계를
    # 피하려고 만든 경로다(위 docstring 참고). 실패하거나(HCX 호출 자체
    # 오류) 확신 없음(None)이면 기존과 동일하게 unresolved로 끝난다.
    if not item_candidates and hcx_resolve_fn is not None:
        cell_texts_full = kls.iter_table_cell_texts(conn, org_id, tbl_id)
        text_list = [c["text"] for c in cell_texts_full if c.get("text")]
        try:
            resolved_index = hcx_resolve_fn(
                text_list, metric_text or raw_sentence,
                claimed_value_for_search, (claim.get("unit") or "").strip() or None,
                claim.get("period"),
            )
        except Exception as e:
            resolved_index = None
            hcx_fallback_error = str(e)
            logger.warning(
                f"[Stage 2 HCX-007 폴백 실패 - 이 claim만 건너뜀] "
                f"{claim.get('claim_id')} - {e}"
            )
        if resolved_index is not None and 0 <= resolved_index < len(cell_texts_full):
            resolved_cell = cell_texts_full[resolved_index]
            item_candidates = [{
                "itm_id": resolved_cell["itm_id"],
                "axis_codes": resolved_cell["axis_codes"],
                "text": resolved_cell["text"],
                "itm_nm": resolved_cell.get("itm_nm"),
                # [실측 전 - 방어적 표시] HCX가 표 전체를 보고 단일 후보 하나만
                # 돌려주므로 literal 점수 개념 자체가 없다 - tie 계산이 자기
                # 자신과만 비교해 항상 len(tie)==1이 되도록 상수를 넣어둔다
                # (disambiguate_by_value로 더 좁힐 동점 후보가 없다는 뜻).
                "score": 1, "unexplained_axes": 0, "ancestor_only_hits": 0,
            }]
            hcx_fallback_used = True

    if not item_candidates:
        return {
            "query_status": "unresolved",
            "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
            "candidates_tried": [tbl_nm] if tbl_nm else [],
            "embedding_fallback_error": embedding_fallback_error,
            "embedding_fallback_used": embedding_fallback_used,
            "hcx_fallback_error": hcx_fallback_error,
            "hcx_fallback_used": hcx_fallback_used,
            "llm_table_select_fallback_used": llm_table_select_fallback_used,
        }

    top = item_candidates[0]
    # [2026-08-18 실측 버그 수정] score/unexplained_axes 두 기준만 같으면
    # "동점"으로 봤는데, kosis_local_search.resolve_evidence_by_flat_match가
    # 이제 3번째 정렬 기준으로 ancestor_only_hits(phrase가 자기 이름에서
    # 나왔는지 조상한테서만 상속됐는지)까지 반영한다 - 이 세 번째 기준까지
    # 같아야 진짜 동점이다. 실측(A93bfa851-C024): "가정용품 및 가사서비스"
    # 집계행(E, ancestor_only_hits=0)이 이미 score/unexplained_axes/
    # ancestor_only_hits 셋 다로 유일한 1위인데, 이 체크가 앞 두 기준만
    # 보면 그 하위 중간 분류(E04/E041 등, ancestor_only_hits=1으로 더
    # 낮은 순위)까지 "동점"으로 잘못 묶어서 disambiguate_by_value로
    # 넘어가고, 값 기반으로도 못 가르면(파생값 claim이라 원자료 레벨값과
    # 직접 비교가 안 됨) confident=False로 떨어지는 문제가 있었다.
    tie = [
        c for c in item_candidates
        if c["score"] == top["score"]
        and c.get("unexplained_axes") == top.get("unexplained_axes")
        and c.get("ancestor_only_hits") == top.get("ancestor_only_hits")
    ]

    # [2026-08-20 신규 - Task #80 실측 발견, 값 기반 재검증 확장] embedding
    # 폴백으로 얻은 phrase는 claim 원문 그대로가 아니라 embedding이 "번역"한
    # 근사 문자열이라, resolve_evidence_by_flat_match의 score가 유일하게
    # 1위를 골라도(=위 tie 기준으로는 "동점 아님") 그 1위가 진짜 정답이란
    # 보장이 literal match보다 약하다. 실측(184/DT_102006_001, "나랏빚"
    # 프로브로 확인): "국가채무 GDP 대비"(비율, claim이 원하는 절대값과
    # 스케일이 다름)가 "국가채무"(claim이 실제로 원하는 항목)를 부분
    # 문자열로 포함하는 복합 라벨이라 문자 등장 횟수가 우연히 더 많이
    # 잡혀 score가 더 높게 나왔다 - literal 기준 "동점"이 아니라 명확한
    # 1위였으므로 기존 tie 로직대로면 disambiguate_by_value가 아예 안
    # 불렸을 것이다. 그래서 embedding_fallback_used일 때는 exact-tie
    # 여부와 무관하게 상위 후보 전체(item_candidates, 이미 top_n=5로
    # 제한됨)를 값 기반 재검증 대상으로 넓힌다 - disambiguate_by_value
    # 자체는 "claim 값에 유일하게 가까운 후보가 정확히 1개일 때만" 채택
    # 하므로(모듈 docstring 참고), 후보 풀을 넓혀도 엉뚱한 후보를 억지로
    # 채택할 위험은 늘지 않는다(안 맞으면 여전히 None -> unconfident로
    # 남는다). literal match만 쓴 경로(embedding_fallback_used=False)는
    # 기존 동작(exact tie일 때만) 그대로 유지 - 이미 실측 검증된 동작을
    # 안 건드린다.
    # [2026-08-21 신규 - Task #80 로직 개선, "정부 빚" 실측 버그 대응]
    # literal tie가 흔한 단일 토큰 하나로만 만들어졌으면, 그 자체가 "이
    # 후보들이 진짜 그럴듯한 후보"라는 근거가 아니라 "매칭 근거가 사실상
    # 없다"는 신호에 가깝다 - search_by_value가 이미 matched_phrase_count
    # >=2를 corroboration 기준으로 쓰는 것과 같은 원칙을 여기 literal
    # Stage 2 동점 판정에도 적용한다. 실측(184/DT_102006_001, "정부 빚이
    # 사상 최대를 기록했다"): "정부"라는 한 토큰이 "중앙정부 채무"/"지방정부
    # 순채무" 양쪽에 부분 문자열로 걸려 동점(matched_phrases=['정부'] 각각
    # 1개뿐)이 됐고, 그 뒤 disambiguate_by_value의 5% 값 허용오차가 "전체
    # vs 그 안의 큰 부분집합"처럼 원래 값이 비슷한 두 후보를 우연히 값이
    # 가깝다는 이유만으로 confident=True로 잘못 확정해버렸다(가장 위험한
    # 유형 - 조용히 틀림). 유가증권처럼 서로 다른 phrase 2개 이상("1"+
    # "거래량")이 corroborate하는 진짜 동점에는 이 조건이 걸리지 않는다
    # (test_local_search_special_tables.py 회귀로 확인) - 그런 동점은
    # 여전히 기존처럼 disambiguate_by_value로 직접 푼다.
    #
    # hcx_resolve_fn이 있으면, 이런 "약한 동점"에서는 값으로 바로 풀기
    # 전에 표 전체를 HCX-007에 보여줘 의미로 판단하게 한다(top_k truncation
    # 없이 전체 맥락을 보므로 "정부"라는 단어가 국가채무 전체를 가리키는
    # 관용구인지 판단할 여지가 literal 매칭보다 크다). HCX가 확신 없으면
    # (None) 또는 hcx_resolve_fn 자체가 없으면 기존 disambiguate_by_value
    # 경로로 조용히 폴백한다 - 동작이 안 바뀐다.
    #
    # [2026-08-21 실측 버그 수정 - Task #15, "물가" 범용 토큰 오탐] 원래
    # len(matched_phrases) < 2로만 봤는데, 이러면 "주류"+"물가" 두 phrase가
    # 다 걸려도(실측: A93bfa851-C018) 무조건 corroboration 기준(>=2)을
    # 통과해버렸다 - "물가"는 표의 ITEM명("소비자물가지수")이 표 전체에서
    # 하나뿐이라 그 표의 모든 후보에 100% 걸리는 범용 토큰이라 실제로는
    # 아무것도 구분 못 해주는데도 개수만 채운 것. kosis_local_search.
    # resolve_evidence_by_flat_match가 이제 그런 item_nm 전용/범용 매치를
    # 뺀 distinguishing_phrase_count를 별도로 계산해 돌려주므로, 있으면
    # 그걸 쓰고 없으면(과거 경로/다른 생성자) 기존 matched_phrases 개수로
    # 조용히 폴백한다 - 이미 실측 검증된 "정부"/"유가증권" 케이스 동작은
    # 안 바뀐다(둘 다 애초에 item_nm 전용 매치가 없었으므로 distinguishing_
    # phrase_count == len(matched_phrases)로 동일).
    weak_literal_tie = (
        not embedding_fallback_used
        and not hcx_fallback_used
        and len(tie) > 1
        and all(
            c.get("distinguishing_phrase_count", len(c.get("matched_phrases") or [])) < 2
            for c in tie
        )
    )
    axis_tie_resolved = False
    if weak_literal_tie and hcx_axis_resolve_fn is not None:
        # [2026-08-22 실측 버그 수정 - 사용자 실측 발견/설계] 원래 여기서도
        # Stage 2 갭 폴백(후보 0개일 때)과 똑같이 kls.iter_table_cell_texts로
        # 표 전체를 카테시안 곱 flat text로 펼쳐 HCX에 보내고 있었다. 축이
        # 여러 개인 큰 표(DT_1J22001 - 지역 19 × 지출목적별 581)에서 이
        # 카테시안 곱이 67만 자(content_chars=673343)까지 폭발해 HCX 분당
        # 토큰 한도(60,000)를 요청 한 번으로 다 써버리는 게 실측 확인됐다
        # (A93bfa851-C007/C009, x-ratelimit-remaining-tokens=0 응답 헤더로
        # 확인 - 요청 *횟수* 제한이 아니라 *토큰* 제한이었다). 그렇다고
        # tie 후보만 보내는 것도 안 된다 - "정부 빚" 실측 버그(README
        # "스물한 번째")처럼 literal 매칭이 애초에 놓친 정답(tie 밖의 축
        # 값)을 HCX가 찾아줘야 하는 경우가 이미 검증돼 있다
        # (test_weak_literal_tie_uses_hcx_instead_of_loose_value_
        # tolerance). 그래서 카테시안 곱만 없애고(축마다 압축된 트리,
        # kls.build_axis_trees) 축 자체는 안 자른 채 그대로 보낸다 - 표
        # 크기와 무관하게 실제 고유 노드 수에 비례하는 크기로 끝난다.
        axis_trees = kls.build_axis_trees(conn, org_id, tbl_id)
        tie_itm_ids = {c.get("itm_id") for c in tie if c.get("itm_id")}
        if len(tie_itm_ids) != 1:
            # [범위 제한 - 정직하게 명시] 이 경로는 아직 item(itm_id)까지
            # 축 트리로 같이 판단하지 않는다(build_axis_trees는 ITEM 행을
            # 뺀다) - tie 후보들의 itm_id가 전부 같을 때만(단일 품목 표,
            # 실측 확인된 두 버그 케이스 DT_1J22001이 전부 이 경우) 이
            # 경로를 쓴다. itm_id까지 갈리는 표는 아직 실측된 사례가 없어
            # 추측으로 확장하지 않는다 - hcx_resolve_fn(있으면)으로 폴백.
            resolved_axis_codes = None
        else:
            item_names_for_tie = {c.get("itm_nm") for c in tie if c.get("itm_nm")}
            item_context = ", ".join(sorted(item_names_for_tie)) if item_names_for_tie else None
            try:
                resolved_axis_codes = hcx_axis_resolve_fn(
                    axis_trees, metric_text or raw_sentence, item_context,
                    claimed_value_for_search, (claim.get("unit") or "").strip() or None,
                    claim.get("period"),
                )
            except Exception as e:
                resolved_axis_codes = None
                hcx_fallback_error = str(e)
                logger.warning(
                    f"[Stage 2 약한 literal 동점 - 축 트리 HCX 재확인 실패, 기존 값 검증으로 폴백] "
                    f"{claim.get('claim_id')} - {e}"
                )
            # [2026-08-22 신규 - 진단성 개선] "축 트리 HCX가 실제로 뭘
            # 골랐는지"가 지금까지 로그에 전혀 안 남아서, 실패해도 "왜"
            # 실패했는지(HCX가 None을 줬는지 / 있는 코드를 줬는데 facts
            # 조합이 실제로 없는지) 재현 없이는 알 수 없었다 - 원인을
            # 바로 알 수 있게 결과를 항상 로그에 남긴다.
            logger.info(
                f"[Stage 2 약한 literal 동점 - 축 트리 HCX 결과] "
                f"{claim.get('claim_id')} resolved_axis_codes={resolved_axis_codes}"
            )
        if resolved_axis_codes:
            resolved_cell = _lookup_cell_by_axis_codes(
                conn, org_id, tbl_id, tie[0].get("itm_id"), resolved_axis_codes, axis_trees,
            )
            if resolved_cell is None:
                logger.warning(
                    f"[Stage 2 약한 literal 동점 - 축 트리 HCX가 코드는 줬지만 facts에서 "
                    f"유일한 셀을 못 찾음 - 기존 값 검증으로 폴백] {claim.get('claim_id')} "
                    f"resolved_axis_codes={resolved_axis_codes}"
                )
            if resolved_cell is not None:
                top = {
                    "itm_id": resolved_cell["itm_id"],
                    "axis_codes": resolved_cell["axis_codes"],
                    "text": resolved_cell["text"],
                    "itm_nm": resolved_cell.get("itm_nm"),
                    "score": 1, "unexplained_axes": 0, "ancestor_only_hits": 0,
                }
                tie = [top]
                hcx_fallback_used = True
                axis_tie_resolved = True
    # [2026-08-22 수정 - 폴백 순서 버그] 원래 elif로 둬서 hcx_axis_resolve_fn을
    # "시도했지만 못 풀었을 때"(itm_id가 갈리는 tie라 아예 건너뛴 경우
    # 포함) hcx_resolve_fn으로 이어지지 않는 문제가 있었다 - axis_tie_
    # resolved로 "축 트리 경로가 실제로 풀었는지"만 보고, 못 풀었으면
    # (또는 애초에 hcx_axis_resolve_fn이 없으면) 항상 여기로 내려와
    # hcx_resolve_fn을 마저 시도한다(test_weak_literal_tie_axis_resolve_
    # fn_skipped_when_tie_spans_multiple_items로 회귀 확인).
    if weak_literal_tie and not axis_tie_resolved and hcx_resolve_fn is not None:
        # 표 전체를 flat text로 펼쳐 HCX에 보낸다 - 표가 크면 위와 같은
        # 토큰 폭발 위험이 그대로 있지만, hcx_axis_resolve_fn을 안 넘긴
        # (또는 이 tie엔 못 쓴) 호출부의 기존 동작은 안 바꾼다.
        cell_texts_full = kls.iter_table_cell_texts(conn, org_id, tbl_id)
        text_list = [c["text"] for c in cell_texts_full if c.get("text")]
        try:
            resolved_index = hcx_resolve_fn(
                text_list, metric_text or raw_sentence,
                claimed_value_for_search, (claim.get("unit") or "").strip() or None,
                claim.get("period"),
            )
        except Exception as e:
            resolved_index = None
            hcx_fallback_error = str(e)
            logger.warning(
                f"[Stage 2 약한 literal 동점 - HCX 재확인 실패, 기존 값 검증으로 폴백] "
                f"{claim.get('claim_id')} - {e}"
            )
        if resolved_index is not None and 0 <= resolved_index < len(cell_texts_full):
            resolved_cell = cell_texts_full[resolved_index]
            top = {
                "itm_id": resolved_cell["itm_id"],
                "axis_codes": resolved_cell["axis_codes"],
                "text": resolved_cell["text"],
                "itm_nm": resolved_cell.get("itm_nm"),
                "score": 1, "unexplained_axes": 0, "ancestor_only_hits": 0,
            }
            tie = [top]
            hcx_fallback_used = True

    disambiguation_pool = tie
    if embedding_fallback_used and len(item_candidates) > 1:
        disambiguation_pool = item_candidates

    disambiguated_note = None
    confident = True
    if len(disambiguation_pool) > 1:
        confident = False
        claimed_value = _claimed_value(claim)
        if claimed_value is not None:
            dis = kls.disambiguate_by_value(
                conn, org_id, tbl_id, disambiguation_pool, claimed_value, period=claim.get("period"),
            )
            if dis.get("resolved"):
                top = dis["resolved"]
                disambiguated_note = dis.get("reason")
                confident = True
        # 값도 없거나 값으로도 못 가르면: top(원래 1등)을 그대로 쓰되
        # confident=False로 남긴다 - 추측으로 하나를 확신 있게 골랐다고
        # 하지 않는다(disambiguate_by_value 모듈 docstring의 원칙).
    # [2026-08-19 신규 - 설명 문구 정확성 버그 수정] confident=False로
    # 끝난 이유(동점 후보가 몇 개였고 뭐였는지)를 여기서 문자열로 남겨서
    # adapter.py/judgment.py가 "표 이름/설명만 보고 고른 추정"이라는 뭉뚱그린
    # 문구 대신 실제 사유를 보여줄 수 있게 한다 - 실측(A82ae9f41-C001 등)에서
    # 표/항목을 실제로 찾았는데도 "(후보 없음)"으로 뜨는 문제를 조사하다가
    # 발견함(adapter.py가 candidates_tried를 이 success 경로에서 안 읽어오고
    # 있었던 게 1차 원인, 이건 진단 정보 부족이 2차 원인).
    confidence_note = None
    if not confident:
        # [2026-08-19 수정] itm_nm을 우선 쓰면 "소비자물가지수"처럼 항목명은
        # 같고 축(지역/분류)만 다른 동점 후보들이 전부 똑같은 이름으로 찍혀서
        # 오히려 뭐가 다른지 안 보인다 - 축까지 포함한 전체 breadcrumb text를
        # 우선 쓴다(실측: A93bfa851-C007/C009 등에서 이름 5개가 전부 동일하게
        # 찍히는 걸 보고 발견).
        tie_names = [c.get("text") or c.get("itm_nm") or "?" for c in disambiguation_pool][:5]
        pool_label = "임베딩 폴백 후보" if embedding_fallback_used else "동점 후보"
        confidence_note = f"{pool_label} {len(disambiguation_pool)}개(값으로도 못 가름): {', '.join(tie_names)}"

    itm_id = top["itm_id"]
    axis_codes = top["axis_codes"]

    period_digits = kls._normalize_period_digits(str(claim.get("period") or ""))
    if not period_digits:
        return {
            "query_status": "error",
            "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
            "error_message": f"claim의 period를 정규화하지 못함: {claim.get('period')!r}",
        }

    where = ["org_id=?", "tbl_id=?", "itm_id=?"]
    params: List[Any] = [org_id, tbl_id, itm_id]
    for axis, code in axis_codes.items():
        where.append(f"c{axis}=?")
        params.append(code)
    where.append("prd_de=?")
    params.append(period_digits)
    row = conn.execute(
        f"SELECT value, unit FROM facts WHERE {' AND '.join(where)}", params
    ).fetchone()

    backfill_attempted = False
    backfill_error = None
    meta_filtered = False
    if not row or row[0] is None:
        prd_se = _period_digits_to_prd_se(period_digits)
        # [2026-08-22 신규 - Task #28-2, 사용자 제안] 온디맨드 백필(API
        # 호출)을 시도하기 전에, 이미 이 표에 대해 기록된 fact_coverage로
        # "이 주기(prd_se) 자체가 KOSIS에 없다"를 공짜로(로컬 조회만으로)
        # 걸러낼 수 있는지 먼저 본다. ingest_table의 배치 적재 루프는
        # KOSIS getMeta(period)가 보고하는 주기를 전부(월/분기/년 등)
        # 훑으면서 성공한 것마다 record_coverage를 남긴다(kosis_warehouse.
        # ingest_table 참고) - 즉 이 표에 대해 fact_coverage가 하나라도
        # 있다면, 거기 없는 prd_se는 "적재를 안 한 것"이 아니라 "KOSIS
        # 자체에 그 주기가 없다"는 뜻이다(실측 확인: 국가채무(D1)/
        # 품목군별 국내판매액 변동현황 둘 다 배치 적재 로그에 "년"만
        # 찍혔고, 사용자가 KOSIS 홈페이지에서 직접 확인한 것과 일치했다).
        # fact_coverage가 아예 비어있으면(이 표가 아직 한 번도 배치
        # 적재를 안 거쳤거나 커버리지 기록 이전 버전으로 적재된 경우)
        # "없다"고 단정할 근거가 없으므로(실측 우선 원칙) 이 필터를 건너
        # 뛰고 기존 온디맨드 백필로 넘어간다.
        known_prd_se = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT prd_se FROM fact_coverage WHERE org_id=? AND tbl_id=?",
                (org_id, tbl_id),
            )
        }
        if known_prd_se and prd_se not in known_prd_se:
            meta_filtered = True
            logger.info(
                f"[no_data - 메타 사전 필터] {claim.get('claim_id')} "
                f"{org_id}/{tbl_id}는 prd_se={known_prd_se}만 있고 "
                f"'{prd_se}'는 없음 - 온디맨드 백필 없이 KOSIS 미보유로 확정"
            )
        elif kosis_client is not None and write_conn is not None:
            # [2026-08-22 신규 - Task #28] 표/항목/축은 이미 확정됐는데 이
            # 시점만 로컬에 없는 경우 - 같은 (org_id, tbl_id, itm_id,
            # axis_codes) 그대로 기간만 이 claim이 필요로 하는 시점으로
            # 바꿔 KOSIS에 온디맨드로 요청한다(kosis_warehouse.fetch_
            # scoped_slice, compute_records=True 기본값이라 이 항목/축의
            # 전체 수록기간을 한 번에 당겨온다 - 파생 비교(YoY 등)에
            # 필요한 reference 시점도 같이 채워질 가능성이 높다). 실패
            # (네트워크/API 예외, 또는 KOSIS에도 정말 이 시점이 없는 경우)
            # 는 조용히 삼키고 기존 no_data 경로로 폴백한다 - 추측하지
            # 않는다는 원칙은 그대로 유지.
            backfill_attempted = True
            try:
                wh.fetch_scoped_slice(
                    kosis_client, write_conn, org_id, tbl_id, prd_se, itm_id,
                    period_digits, period_digits, objl_fixed=axis_codes,
                )
            except Exception as e:
                backfill_error = str(e)
                logger.warning(
                    f"[no_data 온디맨드 백필 실패 - 기존 no_data 경로로 폴백] "
                    f"{claim.get('claim_id')} {org_id}/{tbl_id} itm={itm_id} "
                    f"prd_se={prd_se} period={period_digits} - {e}"
                )
            else:
                row = conn.execute(
                    f"SELECT value, unit FROM facts WHERE {' AND '.join(where)}", params
                ).fetchone()

    if not row or row[0] is None:
        return {
            "query_status": "no_data",
            "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
            # [2026-08-19 신규 - 설명 문구 정확성 버그 수정] 항목/축은 이미
            # 확정됐는데(tbl_nm/itm_id까지 다 나온 상태) 그 시점 데이터가 로컬
            # DB에 없을 뿐이라는 걸 명시한다 - "축을 확정 못 함"과는 다른
            # 사유라 뭉개면 안 됨(위 confidence_note 주석 참고).
            #
            # [2026-08-22 신규 - Task #28] 온디맨드 백필까지 시도했는데도
            # 없으면("KOSIS에도 없음"까지 확인된 상태) 사유를 구분해서
            # 남긴다 - 시도조차 안 한 경우(kosis_client 없음)와 시도했지만
            # 실패/여전히 없는 경우를 섞으면 안 됨(실측 우선 원칙 - 실제로
            # KOSIS까지 확인했는지 여부는 이후 판정에서 중요한 차이다).
            #
            # [2026-08-22 신규 - Task #28-2] meta_filtered(fact_coverage로
            # 이 주기 자체가 없다고 이미 확인됨)는 온디맨드 백필과는 또
            # 다른 근거다 - API를 호출해서 확인한 게 아니라 이전 배치
            # 적재 때 KOSIS getMeta(period)가 이미 알려준 사실이므로,
            # 셋을 구분해서 남긴다(안 시도함 / API로 시도해서 확인함 /
            # 메타로 이미 알고 있었음).
            "error_message": (
                (
                    f"항목/분류값({top.get('itm_nm')})은 확정했지만 해당 시점"
                    f"({period_digits})의 주기 자체가 KOSIS에 없음(적재 시점"
                    f" getMeta로 이미 확인됨, API 재호출 없이 확정) - 추측으로"
                    " 대체하지 않음."
                ) if meta_filtered else (
                    f"항목/분류값({top.get('itm_nm')})은 확정했지만 해당 시점"
                    f"({period_digits})의 데이터가 KOSIS에도 없음(온디맨드 백필"
                    f" 시도함{f' - {backfill_error}' if backfill_error else ''}) -"
                    " 추측으로 대체하지 않음."
                ) if backfill_attempted else (
                    f"항목/분류값({top.get('itm_nm')})은 확정했지만 해당 시점"
                    f"({period_digits})의 데이터가 로컬 DB에 없음 - 추측으로"
                    " 대체하지 않음."
                )
            ),
            "embedding_fallback_used": embedding_fallback_used,
            "hcx_fallback_used": hcx_fallback_used,
            "llm_table_select_fallback_used": llm_table_select_fallback_used,
            "backfill_attempted": backfill_attempted,
            "meta_filtered": meta_filtered,
        }

    # [2026-08-17 신규] 매칭된 항목이 이미 등락률류(예: DT_1J22041의 "전년비"
    # 항목)인지 먼저 확인한다 - measure_type 판별은 항목명+단위만 보면
    # 되므로 facts 조회 없이도 할 수 있지만, 어차피 위에서 이미 row(unit)를
    # 가져왔으니 그대로 재사용한다.
    measure_type = kls._infer_measure_type(top.get("itm_nm"), row[1])

    # [2026-08-22 신규 - Task #29 Step 3] item_diff(C003/C004류) opt-in 경로 -
    # hcx_stage3_fn이 주어졌고, HCX가 item_diff라고 답했고, 원문에 로컬
    # 키워드 근거도 있고, 바꿔치기할 축이 모호하지 않을 때만 시도한다.
    # 이 넷 중 하나라도 안 맞으면 조용히 삼키고 아래 기존
    # _needs_rate_derivation 경로로 폴백한다 - hcx_stage3_fn=None(기본값)
    # 이면 이 블록 전체가 실행되지 않아 기존 동작과 완전히 동일하다.
    if hcx_stage3_fn is not None and measure_type != "rate_of_change":
        stage3_result = None
        try:
            stage3_result = hcx_stage3_fn(
                raw_sentence, period_digits, _claimed_value(claim), claim.get("unit"),
            )
        except Exception as e:
            logger.warning(
                f"[Stage 3 HCX 판단 실패 - 기존 휴리스틱으로 폴백] {claim.get('claim_id')} - {e}"
            )
        if (
            stage3_result
            and stage3_result.get("mode") == "item_diff"
            and _has_total_comparison_keyword(raw_sentence)
        ):
            axis_position = _find_swappable_axis_position(conn, org_id, tbl_id, axis_codes)
            # [주의 - README "스물다섯 번째" 실측] hcx_stage3_fn이 돌려준
            # reference_period 숫자는 신뢰하지 않는다(HCX의 날짜 산수
            # 자체가 부정확한 사례가 확인됨) - 항상 기존 결정적 경로로
            # 다시 계산한다.
            reference_digits = _resolve_reference_period(claim, period_digits)
            if not reference_digits:
                reference_digits = _extract_explicit_reference_period(raw_sentence, period_digits)
            if axis_position is not None and reference_digits:
                item_diff_derived = kls.resolve_item_diff_change(
                    conn, org_id, tbl_id, itm_id, axis_codes, axis_position,
                    period_digits, reference_digits,
                )
                if item_diff_derived.get("derivation_used"):
                    return {
                        "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
                        "normalized_value": item_diff_derived["diff"], "normalized_unit": "%포인트",
                        "query_status": "success",
                        "derivation": {
                            "used": True, "mode": "item_diff",
                            "note": item_diff_derived.get("reason"), "hcx_stage3_used": True,
                        },
                        "confident": confident,
                        "confidence_note": confidence_note,
                        "embedding_fallback_used": embedding_fallback_used,
                        "hcx_fallback_used": hcx_fallback_used,
                        "llm_table_select_fallback_used": llm_table_select_fallback_used,
                    }
                logger.info(
                    f"[item_diff 파생 실패 - 기존 경로로 폴백] {claim.get('claim_id')}"
                    f" - {item_diff_derived.get('reason')}"
                )
            else:
                logger.info(
                    f"[item_diff 조건 불충족(축 모호 또는 reference_period 없음) - 기존 경로로 폴백]"
                    f" {claim.get('claim_id')} axis_position={axis_position} reference_digits={reference_digits}"
                )

    if _needs_rate_derivation(claim, measure_type, sibling_values=sibling_values):
        reference_digits = _resolve_reference_period(claim, period_digits)
        if not reference_digits:
            reference_digits = _extract_explicit_reference_period(raw_sentence, period_digits)
        if reference_digits:
            derived = kls.resolve_period_change(
                conn, org_id, tbl_id, itm_id, axis_codes, period_digits, reference_digits,
            )
        else:
            derived = kls.resolve_yoy_change(
                conn, org_id, tbl_id, itm_id, axis_codes, period_digits,
            )
        if not derived.get("derivation_used"):
            return {
                "query_status": "no_data",
                "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
                "error_message": (
                    f"claim은 변화율(%)을 주장하지만 매칭된 항목은 원자료값이라"
                    f" 두 시점을 비교해 파생해야 함 - {derived.get('reason')}"
                ),
            }
        return {
            "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
            "normalized_value": derived["pct_change"], "normalized_unit": "%",
            "query_status": "success",
            "derivation": {"used": True, "note": derived.get("reason")},
            "confident": confident,
            "confidence_note": confidence_note,
            "embedding_fallback_used": embedding_fallback_used,
            "hcx_fallback_used": hcx_fallback_used,
            "llm_table_select_fallback_used": llm_table_select_fallback_used,
        }

    result = {
        "org_id": org_id, "table_id": tbl_id, "table_name": tbl_nm,
        "normalized_value": row[0], "normalized_unit": row[1],
        "query_status": "success",
        "derivation": {"used": False, "note": disambiguated_note},
        "confident": confident,
        "confidence_note": confidence_note,
        "embedding_fallback_used": embedding_fallback_used,
        "hcx_fallback_used": hcx_fallback_used,
        "llm_table_select_fallback_used": llm_table_select_fallback_used,
    }
    # [2026-08-28 신규 - 동명표(원지수/계절조정 등) 확인, CLAUDE.md 결정
    # 참고] Stage 1/2/3이 형제 표 중 잘못된 계열을 확정했을 수 있으므로,
    # record-claim/목적 검증보다 먼저 확인해서 필요하면 표/값 자체를
    # 교체한다 - 이 경로가 이 검증의 주 대상이다(Stage 1/2/3 전체를 거친
    # "정통" 성공 경로).
    sibling_result = _resolve_series_siblings(
        conn, org_id, tbl_id, tbl_nm, itm_id, axis_codes, period_digits,
        claim, raw_sentence,
    )
    if sibling_result.get("switched"):
        org_id = sibling_result["org_id"]
        tbl_id = sibling_result["tbl_id"]
        tbl_nm = sibling_result["tbl_nm"]
        result["org_id"] = org_id
        result["table_id"] = tbl_id
        result["table_name"] = tbl_nm
        result["normalized_value"] = sibling_result["value"]
        result["normalized_unit"] = sibling_result["unit"]
        result["derivation"]["note"] = (
            f"{result['derivation']['note']}; {sibling_result['note']}"
            if result["derivation"]["note"] else sibling_result["note"]
        )
    # [2026-08-24 신규 - "역대 최고/최저" claim 배선] 위와 같은 이유 -
    # derivation.used=False라 record-claim 판정까지 도달 가능한 마지막
    # 성공 경로(Stage 1/2/3으로 확정된 (org_id,tbl_id,itm_id,axis_codes)).
    result = _attach_record_extremes(
        result, conn, org_id, tbl_id, itm_id, axis_codes, period_digits, raw_sentence,
    )
    # [2026-08-28 신규 - 목적 검증 게이트] 같은 이유로 이 경로가 목적 검증의
    # 주 대상이다(_attach_purpose_check 문서 참고) - 배추가격 사례처럼 순수
    # 원자료 직접 매칭 claim이 정확히 이 경로를 탄다.
    return _attach_purpose_check(
        result, conn, org_id, tbl_id, claim, raw_sentence, kosis_client, hcx_purpose_verify_fn,
    )


def _sibling_group_key(claim: Dict[str, Any]) -> Any:
    """[2026-08-19 신규 - 1번 확정 스키마] 형제 claim(같은 문장을 공유하는
    claim)을 묶을 때 쓸 키. sent_id가 있으면(claims_schema_1번_v2.md 참고)
    (article_id, sent_id) 조합을 최우선으로 쓴다 - article_id 없이
    sent_id만 쓰면 "s005"처럼 짧은 값이 서로 다른 기사끼리 우연히 겹칠 수
    있어서 반드시 같이 묶는다. 둘 다 없으면(구 포맷) raw_sentence 문자열
    완전일치로 폴백한다."""
    sent_id = claim.get("sent_id")
    if sent_id:
        return (claim.get("article_id"), sent_id)
    return claim.get("claim") or ""


class LocalDbAgent:
    """NewKosisAgent와 같은 process_claim_group_keywords 계약을 만족하는
    DB 기반 agent - adapter.py의 run_search_and_judge(agent=LocalDbAgent())
    에 그대로 넘기면 라이브 API 없이 로컬 DB만으로 전체 파이프라인이
    돈다. 기본은 읽기 전용 연결(mode=ro)만 쓴다 - 이 agent는 검색만
    하지 적재는 안 한다(적재는 원래 kosis_warehouse.ingest_table 쪽
    책임).

    [2026-08-22 갱신 - Task #28] 위 "적재는 안 한다"는 기본 동작 얘기고,
    표/항목/축은 이미 확정됐는데 그 시점만 로컬에 없는 no_data 케이스는
    예외다 - `kosis_client`를 명시적으로 넘기면(opt-in) 그 경우에 한해
    kosis_warehouse.fetch_scoped_slice로 온디맨드 백필을 시도한다(자세한
    설명은 resolve_claim_evidence의 kosis_client/write_conn 문서 참고).
    이때도 self.conn(검색용)은 여전히 읽기 전용이고, 쓰기는 별도로 연
    self.write_conn만 쓴다 - "검색은 읽기 전용"이라는 원칙 자체는 안
    깨진다."""

    def __init__(
        self,
        db_path: str = "kosis_warehouse.db",
        stage1_keywords: str = "run03",
        hcx_resolve_fn: Optional[
            Callable[[List[str], str, Optional[float], Optional[str], Optional[str]], Optional[int]]
        ] = None,
        hcx_axis_resolve_fn: Optional[
            Callable[
                [Dict[int, Dict[str, Any]], str, Optional[str], Optional[float], Optional[str], Optional[str]],
                Optional[Dict[int, str]],
            ]
        ] = None,
        hcx_table_resolve_fn: Optional[
            Callable[[List[Dict[str, Any]], str, Optional[float], Optional[str], Optional[str]], Optional[int]]
        ] = None,
        kosis_client: Optional[Any] = None,
        hcx_stage3_fn: Optional[
            Callable[[str, str, Optional[float], Optional[str]], Optional[Dict[str, Any]]]
        ] = None,
        hcx_purpose_verify_fn: Optional[
            Callable[
                [str, Optional[str], str, Optional[float], Optional[str], Optional[str]],
                Optional[Dict[str, Any]],
            ]
        ] = None,
    ):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.db_path = db_path
        # [2026-08-18 신규 - A/B 실험용] resolve_claim_evidence의
        # stage1_keywords 그대로 통과 - "run03"(기본, 프로덕션 동작)
        # 또는 "metric_normalized"(실험). 자세한 설명은 resolve_claim_
        # evidence 문서 참고.
        self.stage1_keywords = stage1_keywords
        # [2026-08-24 삭제됨 - "안 쓰기로 한 로직" 정리] 여기 있던 embed_fn/
        # dim_embed_cache(Task #80, 2026-08-20, CLOVA 임베딩 기반 Stage 2
        # 갭 폴백)는 프로덕션에서 한 번도 안 쓰이고 hcx_resolve_fn(바로
        # 아래)이 대체하면서 완전히 안 쓰는 코드가 됐다 - 사용자 지시로 제거.
        # [2026-08-21 신규 - Task #80 전환] 기본값 None - 안 넘기면 이
        # 경로가 완전히 꺼진 채로 기존 unresolved로 동작한다. 실제 HCX-007
        # 호출은 네트워크가
        # 되는 로컬 환경에서만 만들 수 있으므로(hcx_stage2_resolver.
        # resolve_cell_with_hcx007 기반), 이 샌드박스 회귀 테스트는 계속
        # hcx_resolve_fn=None 또는 결정적 fake로 돈다.
        self.hcx_resolve_fn = hcx_resolve_fn
        # [2026-08-22 신규 - 사용자 실측 발견/설계] weak_literal_tie 전용
        # 축 트리 기반 리졸버 - 기본값 None(안 넘기면 이 경로가 완전히
        # 꺼진 채로, hcx_resolve_fn/disambiguate_by_value로 기존처럼
        # 동작한다). 자세한 설명은 resolve_claim_evidence의 hcx_axis_
        # resolve_fn 문서 참고(hcx_tree_resolver.resolve_axis_codes_
        # with_hcx007 기반 - 실제 호출은 네트워크가 되는 로컬 환경에서만
        # 만들 수 있다).
        self.hcx_axis_resolve_fn = hcx_axis_resolve_fn
        # [2026-08-21 신규 - Task #80 확장] stage1_keywords="llm_table_
        # select"일 때만 쓰이는 Stage 1 표 선택 콜러블 - 기본값 None(안
        # 넘기면 이 모드를 써도 "not_found"로 안전하게 끝남, 다른 두
        # stage1_keywords 모드는 이 값이 뭐든 전혀 영향 없음).
        self.hcx_table_resolve_fn = hcx_table_resolve_fn
        # [2026-08-22 신규 - Task #29 Step 3] 기본값 None - 안 넘기면
        # item_diff opt-in 경로가 완전히 꺼진 채로(기존 동작과 동일하게)
        # 동작한다. 자세한 설명은 resolve_claim_evidence의 hcx_stage3_fn
        # 문서 참고(90건 합성 평가셋 실측 - README "스물다섯 번째" 항목).
        self.hcx_stage3_fn = hcx_stage3_fn
        # [2026-08-22 신규 - Task #28] 기본값 None - 안 넘기면 no_data
        # 온디맨드 백필이 완전히 꺼진 채로(기존 동작과 동일하게) 동작한다.
        # kosis_client를 넘기면 그때만 별도 쓰기 가능 커넥션(self.write_conn)
        # 을 추가로 연다 - self.conn은 여전히 읽기 전용으로 남긴다("이
        # agent는 검색만 하지 적재는 안 한다"는 클래스 docstring의 기존
        # 원칙은 기본 동작에서는 안 바뀐다, kosis_client를 명시적으로
        # 넘긴 호출부만 opt-in으로 예외를 허용). 실제 KosisApiClient는
        # 네트워크가 되는 로컬 환경에서만 만들 수 있으므로, 이 샌드박스
        # 회귀 테스트는 계속 kosis_client=None(기존 동작) 또는 fake로 돈다.
        self.kosis_client = kosis_client
        self.write_conn = sqlite3.connect(db_path) if kosis_client is not None else None
        # [2026-08-28 신규 - 목적 검증(purpose verification) 게이트] 기본값
        # None - 안 넘기면 이 검증 전체가 꺼진 채로 기존 동작과 완전히
        # 동일하다. kosis_client도 같이 넘겨야 실제로 동작한다(_attach_
        # purpose_check가 get_stat_explanation 호출에 kosis_client를 씀) -
        # 자세한 설명은 resolve_claim_evidence의 hcx_purpose_verify_fn
        # 문서 참고.
        self.hcx_purpose_verify_fn = hcx_purpose_verify_fn

    def process_claim_group_keywords(
        self,
        claims: List[Dict[str, Any]],
        keywords_by_claim_id: Dict[str, List[str]],
        category_hint: Optional[Union[str, List[str]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        routing = route_claim_group(claims)
        logger.info(
            f"[LocalDbAgent 라우팅] direct={len(routing['direct'])},"
            f" derived_comparison={len(routing['derived_comparison'])},"
            f" excluded={len(routing['excluded'])}"
        )
        results: Dict[str, Dict[str, Any]] = {}

        for c in routing["excluded"]:
            results[c["claim_id"]] = {"query_status": "not_eligible"}

        # [2026-08-18 신규 - 열거형 문장 대응] "과일(35.2%)과 우유(30.7%)
        # 등은 5년 전에 비해 30% 넘게 급등했다"처럼 1번이 이미 claim_id는
        # 쪼개 줬지만 raw_sentence(claim["claim"])는 원문 그대로 각
        # claim_id에 그대로 복사해서 준다(실측 확인, run01_result.jsonl -
        # 이게 맞는 설계다: 문장을 잘라버리면 "5년 전에 비해" 같은 공유
        # 수식어 자체가 없어진다). raw_sentence가 같은 claim들을 한
        # 그룹으로 묶어 "이 claim 말고 형제들의 값"을 모아두면,
        # resolve_claim_evidence -> _claim_number_change_window가 동사
        # 탐색 윈도우를 형제의 진짜 숫자에서만 끊고, 무관한 숫자(연수/
        # 문턱값)는 지나칠 수 있다.
        #
        # [2026-08-19 갱신 - 1번 확정 스키마] sent_id가 있으면(claims_
        # schema_1번_v2.md 참고) raw_sentence 문자열 완전일치 대신 그걸로
        # 묶는다 - article_id도 같이 묶는 이유는 sent_id 혼자서는("s005")
        # 서로 다른 기사끼리 우연히 겹칠 수 있어서다. sent_id가 없으면
        # (구 포맷) 기존 raw_sentence 완전일치로 폴백한다.
        by_sibling_key: Dict[Any, List[Dict[str, Any]]] = {}
        for c in routing["direct"]:
            by_sibling_key.setdefault(_sibling_group_key(c), []).append(c)

        for c in routing["direct"]:
            keywords = keywords_by_claim_id.get(c["claim_id"], [])
            siblings = by_sibling_key.get(_sibling_group_key(c), [])
            sibling_values = [
                s.get("value") for s in siblings if s.get("claim_id") != c.get("claim_id")
            ]
            results[c["claim_id"]] = resolve_claim_evidence(
                self.conn, c, keywords,
                sibling_values=sibling_values,
                stage1_keywords=self.stage1_keywords,
                hcx_resolve_fn=self.hcx_resolve_fn,
                hcx_axis_resolve_fn=self.hcx_axis_resolve_fn,
                hcx_table_resolve_fn=self.hcx_table_resolve_fn,
                kosis_client=self.kosis_client,
                write_conn=self.write_conn,
                hcx_stage3_fn=self.hcx_stage3_fn,
                hcx_purpose_verify_fn=self.hcx_purpose_verify_fn,
            )

        # [derived_comparison 조합] new_kosis_agent.NewKosisAgent.
        # process_claim_group_keywords와 동일한 로직 - "이미 찾은 형제
        # claim들의 결과를 조합"하는 부분이라 검색 방식(로컬 DB vs 라이브
        # API)과 무관하다. 여기서 다시 구현하는 대신 그대로 가져온다.
        for item in routing["derived_comparison"]:
            c = item["claim"]
            sources = item["sources"]
            points = []
            table_ref: Optional[Dict[str, Any]] = None
            for s in sources:
                s_result = results.get(s["claim_id"])
                if not s_result or s_result.get("query_status") != "success":
                    points = None
                    break
                points.append({
                    "period": s.get("period"),
                    "value": s_result.get("normalized_value"),
                    "unit": s_result.get("normalized_unit"),
                })
                if table_ref is None:
                    table_ref = s_result

            if points:
                results[c["claim_id"]] = {
                    "org_id": (table_ref or {}).get("org_id"),
                    "table_id": (table_ref or {}).get("table_id"),
                    "table_name": (table_ref or {}).get("table_name"),
                    "is_comparison": True,
                    "values": points,
                    "query_status": "success",
                }
            else:
                results[c["claim_id"]] = {
                    "query_status": "error",
                    "error_message": (
                        "파생 비교값의 소스 claim 중 일부를 로컬 DB에서"
                        " 확인하지 못해 비교값을 만들 수 없습니다."
                    ),
                }

        return results

    def close(self) -> None:
        self.conn.close()

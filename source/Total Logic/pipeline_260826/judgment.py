"""판정 확정 모듈 — 파이프라인의 마지막 단계(사용자 노출 직전).

같은 폴더의 "판정 로직 설계 노트.md"의 스펙을 그대로 구현한다. 이 모듈은
검색(retrieval)도 해석(resolution)도 하지 않는다 - 그건 이전 단계
(2주차 챗봇에서 검증한 resolve_target_table/resolve_target_item/
_resolve_table_with_verification 계열 로직, 종합 프로젝트에서는 Backend
6단계에 해당)의 책임이다. 이 모듈이 받는 건 이미 확정되었거나 확정
시도가 끝난 결과물뿐이고, 역할은 순수하게 "이 claim과 이 값을 놓고
VERIFIED/MISMATCH/UNVERIFIED(세부 사유) 중 뭐라고 부를 것인가"를
결정하는 것과, 그 이유를 사람이 읽을 수 있는 문장으로 만드는 것이다.

핵심 원칙(Decision Log 003 계승): "값이 맞다"는 "공식 확인됐다"와 다르다.
UNVERIFIED는 실패가 아니라 올바른 출력이고, 우리가 직접 계산한(파생)
값은 그 자체로 VERIFIED 취급하지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional

# [2026-08-10 디버그 계측] AI_REINTERPRET_DEBUG=1 환경변수를 켜면
# _ai_reinterpret_sentence가 실패할 때(예외/응답 파싱 실패/선택지 불일치)
# 원인을 stderr에 남긴다. 평소엔 완전히 조용하다(기존 하이브리드 원칙
# 4번 "조용한 폴백"은 그대로 유지) - 실측 디버깅 목적의 옵트인 계측이다.
_AI_REINTERPRET_DEBUG = os.environ.get("AI_REINTERPRET_DEBUG") == "1"


# ---------------------------------------------------------------------
# 단위 카테고리 - kosis_text_utils.py의 _unit_categories와 동일한 분류
# 체계를 그대로 재사용한다(카테고리별로 오차 기준의 "종류"(절대오차 vs
# 상대오차) 자체가 다르기 때문에, 이미 검증된 분류를 새로 만들지 않는다).
# 이 모듈이 kosis_text_utils.py와 같은 저장소에 놓인다면, 아래
# UnitCategory 상수 대신 TextUtilsMixin._unit_categories()를 직접 호출해
# 중복을 없애는 쪽을 권장한다 - 여기서는 이 모듈만 떼어 봐도 동작하도록
# 독립적으로 남겨둔다.
# ---------------------------------------------------------------------
class UnitCategory:
    PERSON = "person"
    MONEY = "money"
    PERCENT = "percent"
    COUNT = "count"
    OTHER = "other"
    # [신규 - 2026-08-10, README 3장 D] "두 배로 뛰었다"/"절반으로 줄었다"
    # 처럼 곱셈/나눗셈 비율로 표현한 claim 전용. claimed_value는 비율
    # 자체(예: 2.0, 0.5)를 뜻하고, 비교는 diff/pct_change가 아니라
    # base.value/reference.value 비율로 이뤄진다(아래 5.1 참고).
    MULTIPLIER = "multiplier"


class Mode(str, Enum):
    """세 가지 판정 모드. 같은 입력에 대해 병렬로 돌려볼 수 있도록 순수
    함수 파라미터로 받는다 - 프론트에서 "엄격 기준/완화 기준"을 동시에
    보여주는 UI도 추가 개발 없이 지원 가능하다."""

    STRICT = "strict"
    TOLERANCE = "tolerance"
    RAW_ONLY = "raw_only"


class Verdict(str, Enum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNVERIFIED_NOT_FOUND = "UNVERIFIED_NOT_FOUND"
    UNVERIFIED_UNRESOLVED = "UNVERIFIED_UNRESOLVED"
    UNVERIFIED_DERIVED_NEEDED = "UNVERIFIED_DERIVED_NEEDED"
    # [신규 - 2026-08-10, README 3장 H] "역대 최고/최저"류 기록 주장 전용.
    # [2026-08-24 갱신 - records 테이블 배선 완료] 원래는 "이 모듈이 전체
    # 과거 시계열을 안 가지고 있어 구조적으로 검증 불가"였지만, 이제
    # kosis_warehouse.records(전체 기간 최댓값/최솟값 요약)가 배선되어
    # 대부분의 역대 claim은 VERIFIED/MISMATCH로 직접 판정된다. 이 verdict는
    # 이제 (a) "최고/최저" 중 어느 방향인지 문장에서 판별 안 될 때, 또는
    # (b) 그 표/항목이 아직 records 계산 대상으로 적재되지 않아 대조할
    # 데이터 자체가 없을 때만 남는다 - _check_record_claim 참고.
    UNVERIFIED_RECORD_CLAIM = "UNVERIFIED_RECORD_CLAIM"
    # [신규 - 2026-08-28, 배추가격/DT_114054_112 사례로 사용자가 지적한
    # 아키텍처 갭 대응] 표/축 이름 매칭은 전부 통과했지만, 그 표의 실제
    # 작성 목적(조사 대상/범위)이 claim이 전제하는 개념과 다른 경우 전용.
    # UNVERIFIED_UNRESOLVED(개념을 컬럼/분류값으로 확정 못 함)와는 다르다 -
    # 이건 "확정은 했는데, 확정한 그 표 자체가 claim이 원하는 조사가
    # 아니다"라는, 더 구체적이고 더 위험한 사유다(값이 있어서 그럴듯하게
    # VERIFIED/MISMATCH로 보일 수 있다는 게 UNVERIFIED_UNRESOLVED와의 핵심
    # 차이 - 사용자가 "6700원이라는 수치가 없어서 unverified로 나오긴
    # 하겠지만, 그건 정확한 설명이 아니다"라고 정확히 지적한 부분).
    # local_db_agent._attach_purpose_check가 최종 확정된 (org_id, tbl_id)
    # 1건에 한해서만(비용 절감 - 사용자와 합의된 설계) 1회 hcx_purpose_
    # resolver로 검증하고, 그 결과(actual.purpose_mismatch)를
    # _check_purpose_mismatch가 실제 게이트로 사용한다(장식적 RAG 텍스트가
    # 아니라 판정을 실제로 낮춘다 - Decision 003의 derivation_used 강제
    # 패턴과 동일한 설계, 사용자가 명시적으로 요구함).
    UNVERIFIED_PURPOSE_MISMATCH = "UNVERIFIED_PURPOSE_MISMATCH"
    RAW_ONLY = "RAW_ONLY"


# ---------------------------------------------------------------------
# 입력 스키마
# ---------------------------------------------------------------------
@dataclass
class Claim:
    """기사에서 뽑은 주장 하나.

    direction: "increase"|"decrease"|"no_change"|None - "13만 명 감소했다"
    처럼 claim 자체가 두 시점 사이의 증감을 주장하는 경우에만 채워진다.
    원문장의 근사/부등호 표현(hedge, 3절)과는 다른 신호다 - hedge는 "그
    숫자를 얼마나 엄밀하게 주장했는가"를 나타내고, direction은 "그 숫자가
    애초에 증가량인가 감소량인가"를 나타낸다. 이 필드가 있으면
    ActualEvidence도 반드시 두 시점 값(is_comparison=True, values 2개)
    으로 와야 판정이 가능하다 - 단일 시점 절대값과 비교하면 안 된다
    (실측으로 확인된 문제: "13만 명 감소" 주장을 취업자 수 절대값
    2,787만 명과 그대로 비교하면 무의미한 MISMATCH가 나온다).

    "no_change"는 "동결됐다"/"보합세를 이어갔다"처럼 변화가 없다는 주장
    전용이다(README 3장 E - 실제 버그였음). "increase"/"decrease"와 같은
    자리에서 구분해야 하는 이유: 실제 diff가 어느 방향이든 0이 아니기만
    하면 "increase"도 "decrease"도 아니라서, 세 번째 값 없이는 방향이
    항상 강제로 틀렸다고 처리돼버린다. claimed_value는 보통 0(또는 0에
    준하는 값)으로 채워져 온다고 가정한다 - 실제 "변화 없음" 여부는
    이 필드가 아니라 아래 magnitude 비교(허용 오차) 단계에서 가려진다.

    claimed_unit 표기 규칙(2026-08-10 정리): unit_category가 PERCENT이고
    지표 자체가 절대량(예: 재배면적 ha)일 때 "그 값이 X% 변했다"는 주장은
    claimed_unit="%"로 - 이때는 두 시점 값으로 상대적 변화율(pct_change)을
    계산해서 비교한다. 반대로 지표 자체가 이미 비율/rate(예: 기준금리,
    실업률)일 때 "그 비율이 X%p 움직였다/동결됐다"는 주장은
    claimed_unit="%p"로 명시해야 한다 - 이때는 diff를 다시 나누지 않고
    원래 단위(퍼센트포인트) 그대로 비교한다. 이 구분을 claimed_unit으로
    안 하면(예: "%p" 대신 "%"로 잘못 표기) 기준금리류 claim이 퍼센트의
    퍼센트로 잘못 계산될 수 있다.
    """

    raw_sentence: str
    claimed_value: float
    claimed_unit: Optional[str] = None
    claimed_period: Optional[str] = None
    unit_category: str = UnitCategory.OTHER
    direction: Optional[str] = None


@dataclass
class EvidencePoint:
    """다중 시점 증거의 값 한 점(예: 기준시점 또는 비교시점 하나)."""

    period: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None


@dataclass
class ActualEvidence:
    """이전 단계(검색/해석 - 4번 팀원)가 실제로 찾은 값.

    is_comparison: 4번 팀원이 "이 claim은 여러 시점 값이 필요하다"고
    명시하는 플래그. False(기본값)면 value/unit 단일 값을 그대로 쓰고,
    True면 values(EvidencePoint 리스트)를 쓴다 - 두 표현 방식을 섞어
    쓰지 않는다. "몇 시점이 필요한지는 claim이 말해주니, 4번이 그만큼
    담아서 주면 된다"는 원칙 - 이 모듈은 몇 개의 시점이 오든 values
    리스트 하나로 받는다(정확히 2개면 증감 비교, 3개 이상은 아직 자동
    계산하지 않고 UNVERIFIED_DERIVED_NEEDED로 넘긴다 - Decision Log의
    #47[파생·복합 claim 평가]이 그대로 다루는 영역이라 여기서 새로
    풀지 않는다).
    """

    value: Optional[float] = None
    unit: Optional[str] = None
    table_org_id: Optional[str] = None
    table_tbl_id: Optional[str] = None
    table_nm: Optional[str] = None
    table_purpose: Optional[str] = None
    is_comparison: bool = False
    values: Optional[List[EvidencePoint]] = None

    # [2026-08-24 신규 - "역대 최고/최저" claim 배선] 4번(local_db_agent.py)이
    # kosis_warehouse.records 테이블(전체 기간 최댓값/최솟값 요약, 2026-08-17
    # 적재됨)을 이미 확정된 (org_id,tbl_id,itm_id,axis_codes)로 조회해서
    # 실어 보내는 필드들. records에 해당 계열이 없으면(그 표가 아직 records
    # 계산 대상으로 적재되지 않은 경우) 전부 None으로 온다 - 그러면
    # _check_record_claim이 기존처럼 UNVERIFIED_RECORD_CLAIM으로 declining한다
    # (폴백 원칙, 다른 신규 필드들과 동일). claim_period_matches_max/min의
    # 시점 형식 정규화(period_digits 비교)는 4번 쪽(local_db_agent.py)이 이미
    # 끝낸 상태로 온다 - 이 모듈은 KOSIS 시점 포맷을 모른 채로 남겨두기 위해
    # (독립성 원칙, 모듈 docstring 참고) 원본 문자열 비교를 여기서 새로 하지
    # 않는다.
    record_max_value: Optional[float] = None
    record_max_period: Optional[str] = None
    record_min_value: Optional[float] = None
    record_min_period: Optional[str] = None
    record_period_matches_max: Optional[bool] = None
    record_period_matches_min: Optional[bool] = None
    record_coverage_strt: Optional[str] = None
    record_coverage_end: Optional[str] = None

    # [2026-08-28 신규 - 목적 검증(purpose verification) 게이트] 4번
    # (local_db_agent._attach_purpose_check)이 최종 확정된 표 1건에 한해
    # hcx_purpose_resolver로 "이 표의 작성 목적이 claim의 의도와 맞는가"를
    # 1회 검증한 결과. None이면(검증을 아예 시도 안 했거나 - 예:
    # get_stat_explanation 실패 - kosis_client 자체가 없어 애초에 시도할
    # 수 없는 호출부인 경우) 기존처럼 이 게이트를 건너뛴다(폴백 원칙 -
    # table_purpose와 마찬가지로 다른 신규 필드들과 동일한 관용).
    # True면 _check_purpose_mismatch가 판정을 UNVERIFIED_PURPOSE_MISMATCH로
    # 강제한다.
    purpose_mismatch: Optional[bool] = None
    purpose_mismatch_note: Optional[str] = None


@dataclass
class SearchLog:
    """이전 단계의 탐색 과정 기록 - 판단불가 설명의 핵심 재료.

    retrieval_status: "RESOLVED" | "UNRESOLVED" | "NOT_FOUND".
    NOT_FOUND는 후보 표 자체가 하나도 안 나온 경우(리콜 실패),
    UNRESOLVED는 후보 표는 있었지만 그 안에서 개념을 컬럼/분류값으로
    확정 못 한 경우(해석 실패) - 2주차 챗봇의 검색/해석 구분을 그대로
    가져온 것이다.

    derivation_used: 최종 값이 KOSIS가 그대로 내려준 원본 값이 아니라,
    이전 단계가 두 시점 값을 빼거나 나누는 등 2차 가공(파생 계산)을 해서
    만든 값인지 여부. True면 모드와 무관하게 항상
    UNVERIFIED_DERIVED_NEEDED로 분리한다(Decision 003 원칙: 파생값을
    수동 계산해서 검증했다고 우기지 않는다).
    """

    retrieval_status: str = "RESOLVED"
    confident: bool = True
    candidates_tried: List[str] = field(default_factory=list)
    derivation_used: bool = False
    derivation_note: Optional[str] = None
    # [2026-08-19 신규 - 설명 문구 정확성 버그 수정] "표/항목을 왜 확정 못
    # 했는지"(동점 후보 나열) 또는 "확정은 했는데 왜 값이 없는지"(해당
    # 시점 데이터 미보유)처럼, retrieval_status만으로는 구분 안 되는 구체적
    # 사유를 4번이 실어 보낼 수 있는 선택 필드. 없으면(기존 4번/구 데이터)
    # 기존 문구 그대로 - 폴백 원칙은 이 프로젝트의 다른 신규 필드와 동일.
    detail_note: Optional[str] = None


@dataclass
class VerdictResult:
    verdict: Verdict
    explanation: str
    claimed_value: Optional[float] = None
    actual_value: Optional[float] = None
    hedge_type: Optional[str] = None
    mode: Optional[Mode] = None
    # [신규 - 2026-08-10, README 3장 A/B/C] AI가 문장 재해석에 관여했는지
    # 투명하게 남긴다 - derivation_used/derivation_note와 같은 disclosure
    # 원칙(이 프로젝트에서 이미 확립된 패턴)을 판정 단계 AI 호출에도 그대로
    # 적용한 것이다.
    ai_used: bool = False
    ai_note: Optional[str] = None


# ---------------------------------------------------------------------
# 3절: 근사 표현(Hedge) 사전 - 규칙 기반, Decision 001 하이브리드 원칙.
# 표기가 유한한 문제(정해진 표현 목록)는 코드로 결정론적으로 처리하고,
# 사전에 없는 새 표현이 실측으로 나오면 그때 목록을 넓힌다.
#
# 우선순위 순서로 검사한다 - 방향성 표현(at_least/approach_below/
# at_most)이 "약"류의 대칭 근사(approx)보다 문장의 의도를 더 구체적으로
# 알려주므로 먼저 검사한다. 아무 것도 안 걸리면 "exact"(정확한 수치를
# 주장했다)로 취급한다 - 이게 가장 엄격한 기본값이다.
# ---------------------------------------------------------------------
# [실측으로 발견 - 데모 케이스 "65세 이상 고령인구 비율이 20.3%로..."]
# "이상"/"이하"/"미만"은 부등호 주장(돌파/육박류)뿐 아니라, "65세 이상"/
# "300인 이상"처럼 나이·규모 구간을 정의하는 관용구로도 극히 흔하게
# 쓰인다. 이 관용구는 claimed_value(20.3%)와 아무 관계 없이 그냥 "몇
# 세부터를 고령인구로 볼지"를 정의하는 말인데, 문장 전체를 훑는 단순
# 매칭으로는 이걸 "20.3% 이상이라고 주장했다"로 잘못 읽어버린다(실제로
# 이 버그가 데모 실행 중 재현됨 - strict 모드에서 20.2가 20.3 이상이 아니라는
# 이유로 MISMATCH가 나서 발견). kosis_text_utils.py가 "대비"/"동월"/
# "동기"류 단위 오탐을 사전에 strip하는 것과 동일한 방식으로, 숫자+
# (세|인|명|개|년|살) 뒤에 바로 붙는 "이상/이하/미만"은 구간 정의
# 관용구로 보고 hedge 매칭 전에 제거한다.
_HEDGE_FALSE_POSITIVE_STRIP_RE = re.compile(
    r"\d+\s*(?:세|인|명|개|년|살)\s*(?:이상|이하|미만)"
)

_HEDGE_PATTERNS = [
    ("at_least", re.compile(r"(돌파|넘어서|웃돌|초과|상회|이상)")),
    ("approach_below", re.compile(r"(육박|근접|다가서|채\s*못\s*미)")),
    ("at_most", re.compile(r"(밑돌|하회|이하|미만)")),
    # [2026-08-21 신규 - Task #80 대화 중 실측 발견] "1000조원대"/"20%대"
    # 처럼 숫자 뒤에 단위+"대"가 붙는 구간형 어림값 관용구가 사전에 없었다
    # - kosis_local_search.disambiguate_by_value로 실 KOSIS 표(184/
    # DT_102006_001)를 재검증하다가 "2022년 1000조원대로 불어난 국가
    # 채무"(A272c31f6-C010) claim에서 처음 발견했다. 이 값(1000)과 실제
    # KOSIS 값(1067.4)의 상대오차가 6.74%인데, 기존 사전(약/대략/가량/
    # 정도/무렵/안팎)에도, AI 재해석 트리거 그물(_SOFT_SIGNAL_RE)에도
    # "-대"가 없어서 hedge_type이 "exact"로 떨어지고 AI에게 물어볼
    # 기회조차 없이 정확한 값처럼 취급되고 있었다(8가지 케이스 노트의
    # Case C "사전에 없는 비슷한 표현"의 실제 사례).
    #
    # "20대"/"30대"(나이 관용구)·"1980년대"(연대 관용구)와 안 헷갈리게,
    # 숫자와 "대" 사이에 실제 단위 낱말(원/달러/엔/위안/%/퍼센트/명/건/개)
    # 이 있을 때만 걸리도록 좁혔다 - "세/살/년" 단위는 일부러 뺐다(나이/
    # 연대 표현과 겹치는 진짜 관용구라 별도 처리가 필요, 이번엔 안 건드림).
    # [주의] "대" 뒤에 word boundary(\b)를 걸면 안 된다 - Python 정규식의
    # \b는 한글을 전부 \w로 취급해서 "대로"/"대를"처럼 "대" 바로 뒤에
    # 조사가 붙는(실제 뉴스 문장 대부분이 이 형태) 매우 흔한 경우에 경계가
    # 안 생겨 매칭이 실패한다(실측으로 확인 - "1000조원대로", "3%대를"
    # 둘 다 \b를 걸었을 때 매칭 안 됨).
    ("approx", re.compile(r"(약|대략|가량|정도|무렵|안팎)")),
    # [실측 발견 - 위 주석 계속] 처음엔 이것도 "approx"로 묶었는데,
    # "약"/"대략" 같은 hedge 단어는 보통 오차가 작고("약 20%"는 보통
    # ±1~2%p 수준), "-대"는 자릿수 하나를 통째로 어림잡는 관용구라
    # 실측(1000 vs 1067.4, 6.74% 차이)해보니 기존 approx 허용폭
    # (_APPROX_WIDEN_FACTOR)으로도 부족했다(테스트로 확인 - approx로
    # 묶었을 때 6.74% 오차가 허용폭 밖으로 나옴). 그래서 별도 hedge_type
    # "approx_range"로 분리하고 더 넓은 허용폭(_RANGE_WIDEN_FACTOR)을
    # 준다 - 두 hedge 단어군이 실제로 나타내는 근사의 "정도"가 다르다는
    # 걸 그대로 반영한 것.
    ("approx_range", re.compile(
        r"\d+(?:[.,]\d+)?\s*(?:조|억|천|백|만)?\s*(?:원|달러|엔|위안|%|퍼센트|명|건|개)\s*대"
    )),
]

# [버그 수정 - 2026-08-10] UnitCategory.PERCENT는 원래 서로 다른 두 상황을
# 구분 없이 같은 값으로 취급하고 있었다:
#   (1) 절대량 지표의 "X% 변화" - 재배면적(ha)이 "1.0% 감소"처럼, 퍼센트
#       자체가 두 시점 값의 상대적 변화율을 나타냄 -> pct_change =
#       diff/reference*100 변환이 필요하다.
#   (2) 이미 비율/rate인 지표의 "X%p 변화" - 기준금리(%)가 "동결"이나
#       "0.25%p 인상"처럼, 지표 자체가 퍼센트인데 그 값이 얼마나 움직였는지
#       원래 단위(퍼센트포인트) 그대로 말하는 경우 -> diff를 다시 reference로
#       나누면(퍼센트의 퍼센트) 의미가 왜곡된다. 원래 diff를 그대로 써야 한다.
# unit_category만으로는 이 둘을 구분할 수 없으므로, claimed_unit이 "%p"
# (퍼센트포인트)로 명시된 경우만 (2)로 보고 pct_change 변환을 건너뛴다.
# [2026-08-21 실측 버그 수정 - Task #26, 90개 claim 배치] 원래 "%p"/
# "퍼센트포인트"류만 잡았는데, 실측 claim 데이터(run01_result.jsonl)의
# 실제 unit 표기는 "%포인트"("%"기호 + 한글 "포인트")였다 - "%\s*p"는
# "%" 뒤에 로마자 p를 요구해서 "%포인트"(뒤가 한글 "포"로 시작)와 전혀
# 안 겹쳤다. "%\s*포인트"를 추가해 실제 표기를 커버한다.
_PERCENTAGE_POINT_UNIT_RE = re.compile(r"%\s*p|%\s*포인트|퍼센트\s*포인트|퍼센트포인트", re.IGNORECASE)


def _is_percentage_point_claim(claim: "Claim") -> bool:
    """claimed_unit이 %p(퍼센트포인트)로 명시된 claim인지 - 이미 비율인
    지표의 절대적 변화폭을 뜻하므로 pct_change 변환 대상이 아니다."""
    if not claim.claimed_unit:
        return False
    return bool(_PERCENTAGE_POINT_UNIT_RE.search(claim.claimed_unit))

_HEDGE_DESCRIPTIONS = {
    "exact": "정확한 수치를 주장",
    "approx": "대략적인 근사치를 주장(대칭 오차 허용폭 확대)",
    "approx_range": "\"-대\"류 구간형 어림값 주장(자릿수 하나를 통째로 어림잡음 - approx보다 더 넓은 오차 허용폭)",
    "at_least": "이 값 이상이라고 주장(이상/초과 판정)",
    "approach_below": "이 값에 근접했다고 주장(이하이면서 근접해야 인정)",
    "at_most": "이 값 이하라고 주장(이하/미만 판정)",
}


def extract_hedge(raw_sentence: str) -> str:
    """원문장에서 근사/방향성 표현을 찾아 hedge 유형을 반환한다.

    모드와 완전히 독립적이다 - Strict 모드라고 해서 "돌파"라는 부등호
    주장을 등호 비교로 바꿔버리면 안 된다(문장을 잘못 읽은 것이 된다).
    모드가 달라지는 건 오차를 얼마나 허용할지(카테고리별 tolerance)이지,
    문장이 애초에 등호를 주장했는지 부등호를 주장했는지는 원문장 자체가
    정하는 사실이다.
    """
    if not raw_sentence:
        return "exact"
    scan_text = _HEDGE_FALSE_POSITIVE_STRIP_RE.sub("", raw_sentence)
    for hedge_type, pattern in _HEDGE_PATTERNS:
        if pattern.search(scan_text):
            return hedge_type
    return "exact"


# ---------------------------------------------------------------------
# 3.5절: [신규 - 2026-08-10, README 3장 H] "역대 최고/최저" 기록 주장
# 사전 필터. 5장 표에서 H는 "AI로 뜻을 파악하는 것과 별개로, 확인 불가로
# 분류하는 규칙이 먼저 필요"라고 결론 낸 항목이다 - 문장을 잘못 읽는
# 문제가 아니라, "역대"(과거 모든 기록)를 검증하려면 이 모듈이 갖고
# 있지 않은 전체 시계열이 필요해서 애초에 이 모듈의 판단 범위 밖이기
# 때문이다. 그래서 여기는 AI 호출 없이 규칙만으로 처리한다 - 표현이
# "역대"/"사상 최고"류로 사실상 유한한 관용구 집합이라 하이브리드 원칙의
# "표현이 유한하면 규칙으로" 쪽에 해당한다.
# ---------------------------------------------------------------------
_RECORD_CLAIM_RE = re.compile(
    r"역대|사상\s*최(?:고|저)|(?:최고|최저|최대|최소)(?:치|기록)"
)


def _is_record_claim(raw_sentence: Optional[str]) -> bool:
    if not raw_sentence:
        return False
    return bool(_RECORD_CLAIM_RE.search(raw_sentence))


# [2026-08-24 신규 - records 테이블 배선] "역대"가 최고 쪽인지 최저 쪽인지
# 문장에서 구분한다. get_record()가 max/min을 둘 다 조회해서 주므로, 어느
# 쪽과 대조해야 하는지는 여기서 문장을 보고 결정해야 한다 - "역대" 단어
# 자체는 방향을 안 알려준다("역대 최고"/"역대 최저" 둘 다 "역대"를 공유).
_RECORD_MAX_RE = re.compile(r"최(?:고|대)(?:치|기록)?")
_RECORD_MIN_RE = re.compile(r"최(?:저|소)(?:치|기록)?")


def _record_claim_polarity(raw_sentence: Optional[str]) -> Optional[str]:
    """"max"|"min"|None(판별 불가 - 예: "역대급"처럼 최고/최저 단어 없이
    "역대"만 있거나, 한 문장에 최고/최저 표현이 둘 다 있는 드문 경우)."""
    if not raw_sentence:
        return None
    is_max = bool(_RECORD_MAX_RE.search(raw_sentence))
    is_min = bool(_RECORD_MIN_RE.search(raw_sentence))
    if is_max and not is_min:
        return "max"
    if is_min and not is_max:
        return "min"
    return None


# ---------------------------------------------------------------------
# 3.6절: [신규 - 2026-08-10, README 3장 A/B/C] AI 보조 문장 재해석.
#
# 5장에서 정한 하이브리드 원칙을 그대로 따른다: 규칙(extract_hedge)이
# 먼저 훑고, 아래 "위험 신호" 중 하나라도 걸릴 때만 AI를 부른다.
#   - A(부정문 반전): hedge 신호 단어가 있는데 그 근처에 부정 표현
#     ("~못했다"/"~않았다" 등)도 같이 있으면, 부정문이 뜻을 뒤집었을
#     위험이 있다("9%를 넘어서지 못했다" -> 실제로는 "9% 미만"인데
#     규칙은 "돌파/넘어서"만 보고 at_least로 오판).
#   - B("이상"의 다른 뜻): 규칙이 찾은 신호가 "이상"/"이하" 하나뿐이면
#     ("이상 기후"처럼 부등호와 무관한 관용구일 위험이 상대적으로 더
#     크다 - 다른 신호 단어(돌파/웃돌/육박 등)는 이런 다의성이 없다).
#   - C(사전에 없는 표현): 규칙이 "exact"(아무 신호도 못 찾음)로
#     떨어졌는데, 그럼에도 근사/부등호를 뜻할 만한 "느슨한" 신호(아래
#     _SOFT_SIGNAL_RE - 활용형 변화까지 넓게 잡는 트리거 전용 패턴이라
#     정밀하지 않아도 된다. 최종 분류는 AI가 한다)가 남아있으면 새
#     표현일 가능성이 있다.
#
# AI는 자유 서술이 아니라 README 5장 3번이 정한 6개 고정 선택지 중
# 하나만 고르게 강제한다(hcx_client가 없거나 호출/파싱이 실패하면
# 조용히 규칙 기반 결과로 되돌아간다 - 하이브리드 원칙 4번).
# ---------------------------------------------------------------------
_NEGATION_RE = re.compile(
    r"(?:못\s*했|않았|아니었|아니라|넘지\s*못|밑돌지\s*않|채\s*못|미치지\s*못)"
)

# hedge 패턴보다 훨씬 느슨한 "AI 호출 트리거 전용" 신호 - 정확한 분류가
# 목적이 아니라, extract_hedge가 놓쳤을 가능성이 있는 문장을 걸러내는
# 그물이다(활용형 변화까지 잡기 위해 어간만 사용 - 예: "웃돌"이 아니라
# "웃도"까지 넓혀서 "웃도는"도 잡음).
_SOFT_SIGNAL_RE = re.compile(
    r"(웃도|밑도|미치|가까|비슷|다소|엇비슷|근사|달했|기록했|해당)"
)

_AI_SENTENCE_CHOICES = {
    "at_least": "이상이다",
    "at_most": "이하다",
    "approx": "거의 같다",
    "multiplier": "배수로 변했다",
    "no_change": "변화 없다",
    "exact": "정확한 숫자다",
}


def _needs_ai_reinterpretation(raw_sentence: str, rule_hedge_type: str) -> bool:
    """A/B/C 위험 신호 중 하나라도 걸리면 True - 이때만 AI를 부른다."""
    if not raw_sentence:
        return False
    has_hedge_signal = rule_hedge_type != "exact"
    # A: 부정 표현이 hedge 신호와 같이 있으면 반전 위험.
    if has_hedge_signal and _NEGATION_RE.search(raw_sentence):
        return True
    # B: "이상"/"이하" 자체가 유일한 신호면 다의어 위험(다른 신호 단어는
    # extract_hedge가 이미 더 구체적인 hedge_type을 반환했을 것이므로
    # rule_hedge_type이 정확히 at_least/at_most이면서 그 신호가 "이상"/
    # "이하" 글자에서 나왔는지까지는 구분하지 않고, 보수적으로 이 두
    # hedge_type 전체를 위험군으로 취급한다 - 놓치는 것보다 한 번 더
    # 확인하는 쪽이 안전하다).
    if rule_hedge_type in ("at_least", "at_most"):
        return True
    # C: 아무 신호도 못 찾았는데(exact) 느슨한 신호는 남아있으면 새 표현
    # 후보.
    if not has_hedge_signal and _SOFT_SIGNAL_RE.search(raw_sentence):
        return True
    return False


def _strip_korean_copula(s: str) -> str:
    """실제 HCX 응답이 "이하다" 대신 "이하"처럼 서술어 어미("다"/"이다")를
    생략해서 오는 경우를 안전하게 흡수하기 위한 정규화. 의미가 다른 답을
    같다고 취급하지는 않는다 - 어미 한 겹만 벗겨내며, 벗겨낸 결과가 사전
    선택지 집합 안에서 서로 겹치지 않음을 확인했다(Decision 003: 의미
    판단 자체를 느슨하게 하지 않는다)."""
    for suffix in ("이다", "다"):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def _match_ai_sentence_choice(choice_text: Optional[str]) -> Optional[str]:
    """choice_text를 _AI_SENTENCE_CHOICES와 대조해 키를 반환한다. 정확히
    일치하면 바로 반환하고, 아니면 서술어 어미 차이만 있는 경우까지만
    허용해서 한 번 더 시도한다(그 이상의 표현 차이는 여전히 매칭하지
    않고 None을 반환 - 확실하지 않으면 추측하지 않는다)."""
    if not choice_text:
        return None
    normalized = choice_text.strip()
    for key, text in _AI_SENTENCE_CHOICES.items():
        if normalized == text:
            return key
    core = _strip_korean_copula(normalized)
    for key, text in _AI_SENTENCE_CHOICES.items():
        if core == _strip_korean_copula(text):
            return key
    return None


def _ai_reinterpret_sentence(
    raw_sentence: str, claimed_value: float, hcx_client
) -> Optional[str]:
    """hcx_client.generate_completion(messages, temperature=...)을 호출해
    _AI_SENTENCE_CHOICES 중 하나의 키("at_least" 등)를 반환한다.

    hcx_client가 None이거나 호출/파싱이 실패하면 None을 반환한다 -
    호출부가 조용히 규칙 기반 결과로 폴백해야 한다(하이브리드 원칙 4번).
    이 함수는 실제 HCXClient든, 테스트/데모용 목이든 `generate_completion
    (messages, temperature=...) -> str` 인터페이스만 맞으면 그대로
    동작한다 - judgment.py는 client.py를 직접 import하지 않는다(모듈
    독립성 유지, 파일 헤더 참고).
    """
    if hcx_client is None:
        return None
    options_text = "\n".join(
        f"- {v}" for v in _AI_SENTENCE_CHOICES.values()
    )
    system_instruction = (
        "당신은 뉴스 문장 하나가 숫자 주장을 어떤 식으로 하고 있는지"
        " 분류하는 역할입니다. 부정문("
        "\"~하지 못했다\", \"~하지 않았다\" 등)이 있으면 뜻이 반대로"
        " 뒤집힌다는 점에 특히 주의하세요(예: \"9%를 넘어서지 못했다\"는"
        " \"9% 이상이다\"가 아니라 \"9% 이하다\"라는 뜻입니다). 또한"
        " \"이상 기후\", \"이상 징후\"처럼 \"이상\"이 숫자 크기와 무관하게"
        " \"비정상적인\"이라는 뜻으로 쓰인 경우는 숫자 크기 주장이 전혀"
        " 아니므로 \"정확한 숫자다\"로 분류하세요.\n\n"
        f"아래 선택지 중 정확히 하나만 고르세요:\n{options_text}\n\n"
        '{"choice": "<선택지 문구 그대로>"} 형태의 순수 JSON으로만'
        " 응답하세요."
    )
    messages = [
        {"role": "system", "content": system_instruction},
        {
            "role": "user",
            "content": f'문장: "{raw_sentence}"\n주장하는 숫자: {claimed_value}',
        },
    ]
    raw = None
    try:
        raw = hcx_client.generate_completion(messages, temperature=0.0)
        clean = re.sub(r"```json|```", "", raw or "").strip()
        parsed = json.loads(clean)
        choice_text = parsed.get("choice")
        matched_key = _match_ai_sentence_choice(choice_text)
        if matched_key is not None:
            return matched_key
        if _AI_REINTERPRET_DEBUG:
            print(
                "[AI_REINTERPRET_DEBUG] 선택지 불일치 - HCX가 고른 문구가"
                f" 사전 정의된 선택지와 정확히 일치하지 않음: choice={choice_text!r}"
                f" raw_response={raw!r}",
                file=sys.stderr,
            )
        return None
    except Exception as exc:
        if _AI_REINTERPRET_DEBUG:
            print(
                f"[AI_REINTERPRET_DEBUG] 예외 발생: {exc!r} raw_response={raw!r}",
                file=sys.stderr,
            )
        return None


# ---------------------------------------------------------------------
# 3.7절: [신규 - 2026-08-10, README 3장 D] 배수/분수 표현("두 배로 뛰었다",
# "절반으로 줄었다") 파싱. 사전에 단어를 추가해서 될 문제가 아니라 계산
# 방식 자체(비율 비교, 5.1절)가 새로 필요하다고 README에 이미 정리돼
# 있다. 규칙 기반 한국어 배수/분수 사전이 흔한 표현(두 배~열 배, 절반,
# N분의 1)을 우선 처리하고, 트리거 신호(_MULTIPLIER_TRIGGER_RE)는 있는데
# 사전에 없는 표현만 AI로 넘긴다.
# ---------------------------------------------------------------------
_KOREAN_MULTIPLIER_WORDS = {
    "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_MULTIPLIER_WORD_RE = re.compile(
    r"(두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*배"
)
_MULTIPLIER_DIGIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*배")
_MULTIPLIER_FRACTION_RE = re.compile(r"(\d+)\s*분의\s*1")
_MULTIPLIER_HALF_RE = re.compile(r"절반")
_MULTIPLIER_TRIGGER_RE = re.compile(r"배로|배\s*(?:늘|줄|뛰|증가|감소)|절반|곱절|분의\s*1")


def _parse_multiplier_rule_based(raw_sentence: str) -> Optional[float]:
    """규칙으로 배수/분수 표현을 찾아 숫자 비율(예: 2.0, 0.5)로 변환한다.
    못 찾으면 None - 호출부가 AI 폴백 또는 미해석으로 처리한다."""
    if not raw_sentence:
        return None
    m = _MULTIPLIER_DIGIT_RE.search(raw_sentence)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = _MULTIPLIER_WORD_RE.search(raw_sentence)
    if m:
        return float(_KOREAN_MULTIPLIER_WORDS[m.group(1)])
    if _MULTIPLIER_HALF_RE.search(raw_sentence):
        return 0.5
    m = _MULTIPLIER_FRACTION_RE.search(raw_sentence)
    if m:
        try:
            n = float(m.group(1))
            return 1.0 / n if n else None
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _is_multiplier_claim_candidate(raw_sentence: str) -> bool:
    if not raw_sentence:
        return False
    return bool(_MULTIPLIER_TRIGGER_RE.search(raw_sentence))


def _ai_parse_multiplier(raw_sentence: str, hcx_client) -> Optional[float]:
    """규칙 기반 파서가 못 찾은 배수/분수 표현을 AI로 추출한다. 실패하면
    None(호출부가 UNVERIFIED_UNRESOLVED로 처리)."""
    if hcx_client is None:
        return None
    system_instruction = (
        "문장에서 배수/분수로 표현된 변화 비율을 숫자로 추출하는 역할입니다."
        ' 예: "세 배로 늘었다" -> 3.0, "3분의 1로 줄었다" -> 0.333,'
        ' "네 배 가까이" -> 4.0. 배수/분수 표현이 실제로 없으면 null을'
        ' 반환하세요. {"multiplier": <숫자 또는 null>} 형태의 순수 JSON'
        "으로만 응답하세요."
    )
    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f'문장: "{raw_sentence}"'},
    ]
    try:
        raw = hcx_client.generate_completion(messages, temperature=0.0)
        clean = re.sub(r"```json|```", "", raw or "").strip()
        parsed = json.loads(clean)
        value = parsed.get("multiplier")
        return float(value) if value is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------
# 5절: 카테고리별 오차 허용 기준.
#
# percent는 절대오차(%p)를 쓴다 - 값 자체가 이미 비율이라 상대오차로
# 재면 왜곡된다(예: 0.1%p 차이가 20%에서는 상대오차 0.5%지만 2%에서는
# 5%가 되어버려 같은 절대 오차인데 판정이 크게 달라진다).
# money/person/count는 상대오차를 쓴다 - 규모가 표마다 크게 달라서
# 절대오차 하나로는 대응이 안 된다.
#
# [주의] "tolerance" 열은 실제 KOSIS 표준오차/신뢰구간 필드가 아니라,
# 팀이 합의한 고정 상대오차율을 "95% CI 근사치"로 대신 쓰는 것이다.
# 이유: 대부분의 KOSIS 집계표는 표본조사 표준오차 필드를 API로 안
# 준다(실측 확인 전이라 아직 모르는 표도 많음) - 표마다 있는지 없는지
# 확인하는 걸 전제로 설계하면 그 확인이 끝날 때까지 이 모듈 전체가
# 막히므로, 우선 결정론적으로 쓸 수 있는 고정값으로 시작한다. 나중에
# 실제 표준오차 필드를 제공하는 표를 발견하면, 그 표에 한해 아래
# _category_tolerance()가 고정값 대신 실제 CI를 계산해 반환하도록
# 확장하면 된다(이 함수 하나만 바꾸면 되는 확장 포인트로 설계함).
# ---------------------------------------------------------------------
_TOLERANCE_TABLE = {
    UnitCategory.PERCENT: {"kind": "absolute", Mode.STRICT: 0.05, Mode.TOLERANCE: 0.3},
    UnitCategory.MONEY: {"kind": "relative", Mode.STRICT: 0.005, Mode.TOLERANCE: 0.02},
    UnitCategory.PERSON: {"kind": "relative", Mode.STRICT: 0.005, Mode.TOLERANCE: 0.02},
    UnitCategory.COUNT: {"kind": "relative", Mode.STRICT: 0.005, Mode.TOLERANCE: 0.02},
    UnitCategory.OTHER: {"kind": "relative", Mode.STRICT: 0.01, Mode.TOLERANCE: 0.03},
    # [신규 - 2026-08-10] 배수 claim은 claimed_value 자체가 비율(예:
    # "두 배"=2.0)이라 절대오차/상대오차 구분이 money/person과 다르게
    # 적용된다 - 비율값끼리의 상대오차로 취급한다("두 배"라고 했는데
    # 실제로 1.9배였으면 5%는 벗어난 것으로 볼지 등을 상대오차로 판단).
    # "N배"류 표현은 원래 대략적인 서술(정확히 2.000배를 재는 경우는
    # 거의 없음)이라 money/person보다 허용폭을 넉넉하게 잡는다.
    UnitCategory.MULTIPLIER: {"kind": "relative", Mode.STRICT: 0.1, Mode.TOLERANCE: 0.25},
}

# approx(근사치 주장) 문장은 이 배수를 한 번 더 곱해 오차 허용폭을
# 넓힌다 - "정확한 값이라고 주장한 적 없다"는 문장 자체의 신호를
# 반영하기 위함이다.
_APPROX_WIDEN_FACTOR = {Mode.STRICT: 2.0, Mode.TOLERANCE: 1.5}

# [2026-08-21 신규 - "-대" 실측 발견] approx_range는 approx보다 더 큰
# 배수를 쓴다. [주의 - 실측 우선 원칙] 정확한 값은 아니다 - "1000조원대"
# 가 실제로 얼마나 넓은 구간을 뜻하는지(예: [1000,1099]처럼 자릿수 하나
# 전체인지, 화자마다 다른지)는 실제 뉴스 코퍼스로 검증된 적이 없고, 지금은
# 딱 하나의 실측 사례(1000 vs 1067.4, 6.74% 차이)를 여유 있게 통과시키는
# 수준으로만 정한 공학적 추정치다(max_cells를 처음 200으로 잡았던 것과
# 같은 성격 - 나중에 더 많은 "-대" claim이 실측되면 재조정 필요).
# percent(절대오차 %p 기준) 카테고리에는 이 배수를 그대로 곱해도 "20%대"
# 같은 진짜 10%p 폭 구간까지는 못 미칠 수 있다는 것도 알려진 한계로 남긴다.
_RANGE_WIDEN_FACTOR = {Mode.STRICT: 5.0, Mode.TOLERANCE: 4.0}


def _category_tolerance(unit_category: str, mode: Mode, hedge_type: str) -> tuple:
    """(오차 종류("absolute"|"relative"), 오차 크기) 튜플을 반환한다."""
    row = _TOLERANCE_TABLE.get(unit_category, _TOLERANCE_TABLE[UnitCategory.OTHER])
    kind = row["kind"]
    base = row[mode]
    if hedge_type == "approx":
        base *= _APPROX_WIDEN_FACTOR.get(mode, 1.0)
    elif hedge_type == "approx_range":
        base *= _RANGE_WIDEN_FACTOR.get(mode, 1.0)
    return kind, base


def _within_tolerance(claimed: float, actual: float, kind: str, epsilon: float) -> bool:
    if kind == "absolute":
        return abs(claimed - actual) <= epsilon
    # relative - actual이 0에 가까우면 상대오차가 무한대로 발산하니
    # 분모를 claimed와 actual 중 더 큰 절대값으로 잡아 방어한다.
    denom = max(abs(actual), abs(claimed), 1e-9)
    return abs(claimed - actual) / denom <= epsilon


def _compare_with_hedge(
    claimed: float, actual: float, hedge_type: str, kind: str, epsilon: float
) -> bool:
    """hedge 유형에 따라 등호/부등호 판정을 나눠서 적용한다."""
    if hedge_type in ("exact", "approx", "approx_range"):
        return _within_tolerance(claimed, actual, kind, epsilon)

    if hedge_type == "at_least":
        # "이 값 이상"이라고 주장 - 실제값이 주장값보다 살짝 낮아도
        # 반올림/표기 오차 범위 안이면 인정한다(완전히 엄격한 부등호만
        # 쓰면 "9.999는 10 이상이 아니다"처럼 지나치게 깐깐해진다).
        margin = epsilon if kind == "absolute" else abs(claimed) * epsilon
        return actual >= claimed - margin

    if hedge_type == "at_most":
        margin = epsilon if kind == "absolute" else abs(claimed) * epsilon
        return actual <= claimed + margin

    if hedge_type == "approach_below":
        # "이 값에 근접했다(아직 못 미침)" - 실제값이 주장값을 넘었다면
        # "근접"보다 강한 사실(이미 도달/초과)이므로 그 자체로 인정하고,
        # 못 미쳤다면 오차 허용폭 안에서 근접한 경우만 인정한다.
        if actual >= claimed:
            return True
        return _within_tolerance(claimed, actual, kind, epsilon)

    return _within_tolerance(claimed, actual, kind, epsilon)


# ---------------------------------------------------------------------
# 6절: UNVERIFIED 세분화. 우선순위: NOT_FOUND -> UNRESOLVED ->
# DERIVED_NEEDED 순으로 검사한다(표를 아예 못 찾았는데 파생 여부를
# 따지는 건 의미가 없으므로).
# ---------------------------------------------------------------------
def _check_unverified(search_log: SearchLog) -> Optional[VerdictResult]:
    if search_log.retrieval_status == "NOT_FOUND":
        tried = ", ".join(search_log.candidates_tried) or "(후보 없음)"
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_NOT_FOUND,
            explanation=(
                f"{len(search_log.candidates_tried)}개 후보 표를 넓혀서 찾아봤지만"
                f"({tried}), 이 개념과 일치하는 통계표 자체를 찾지 못했습니다."
                " KOSIS 외 다른 데이터 소스가 필요할 수 있습니다."
            ),
        )
    if search_log.retrieval_status == "UNRESOLVED" or not search_log.confident:
        tried = ", ".join(search_log.candidates_tried) or "(후보 없음)"
        # [2026-08-19 신규 - 설명 문구 정확성 버그 수정] detail_note가 있으면
        # (4번이 "동점 후보 N개" 또는 "확정은 했는데 그 시점 데이터가 없음"
        # 같은 구체적 사유를 실어 보낸 경우) 뭉뚱그린 "표 이름/설명만 보고
        # 고른 추정" 문구 대신 그 사유를 그대로 보여준다 - 실측
        # (A82ae9f41-C001 등)에서 표/항목까지 이미 확정된 경우조차 이
        # 문구 때문에 "아예 못 찾은 것"처럼 보였던 문제를 고친다.
        if search_log.detail_note:
            explanation = f"관련 통계표는 찾았지만({tried}), 확정하지 못했습니다: {search_log.detail_note}"
        else:
            explanation = (
                f"관련 통계표는 찾았지만({tried}), 이 개념이 정확히 어느"
                " 컬럼/분류값인지 확인하지 못했습니다(표 이름/설명만 보고"
                " 고른 추정)."
            )
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_UNRESOLVED,
            explanation=explanation,
        )
    if search_log.derivation_used:
        note = search_log.derivation_note or "직접 계산"
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_DERIVED_NEEDED,
            explanation=(
                "이 값은 KOSIS가 그대로 제공한 원본 수치가 아니라, 저희가"
                f" 두 시점 값으로 직접 계산({note})한 참고값입니다. 공식"
                " 확인된 값이 아니므로 판정에 포함하지 않습니다."
            ),
        )
    return None


# ---------------------------------------------------------------------
# 6.4절: [신규 - 2026-08-28] "목적 불일치(purpose mismatch)" 전용 분기.
# search_log의 RESOLVED/UNRESOLVED와는 독립적인 사유다 - 표/축 이름
# 매칭까지는 전부 성공해서 search_log.retrieval_status == "RESOLVED"로
# 넘어온 뒤에도, 그 표의 실제 작성 목적이 claim의 의도와 다를 수 있다
# (예: "배추 소매가" claim이 "외식업 식재료 사입가" 표에 걸리는 경우 -
# 사용자가 실제 KOSIS URL로 확인해 지적한 사례, 2026-08-28). 값이
# 존재하므로 UNVERIFIED_UNRESOLVED보다 더 그럴듯해 보이지만 실제로는 더
# 위험한 오탐이라, _check_unverified/_check_record_claim과 별도 우선순위
# 단계로 분리한다. RAW_ONLY 모드는 원자료만 보여주는 모드라 이 검사에서
# 제외한다(judge_claim에서 RAW_ONLY 분기 이후, _check_record_claim보다도
# 먼저 호출하도록 배치 - 목적이 안 맞는 표라면 "역대 기록"류 판정까지 갈
# 이유가 없으므로).
# ---------------------------------------------------------------------
def _check_purpose_mismatch(actual: ActualEvidence) -> Optional[VerdictResult]:
    """actual.purpose_mismatch가 True일 때만 UNVERIFIED_PURPOSE_MISMATCH를
    반환한다. None/False면(검증을 안 했거나, 검증했는데 일치) 이 게이트를
    통과시킨다(None 반환 - judge_claim이 나머지 로직을 계속 진행)."""
    if actual.purpose_mismatch is not True:
        return None
    reason = actual.purpose_mismatch_note or "표의 작성 목적이 이 주장의 의도와 다른 것으로 보입니다."
    table_desc = f"[{actual.table_nm}] " if actual.table_nm else ""
    return VerdictResult(
        verdict=Verdict.UNVERIFIED_PURPOSE_MISMATCH,
        explanation=(
            f"{table_desc}표/분류 이름은 이 주장과 일치했지만, 통계표의 실제"
            f" 작성 목적을 대조한 결과 다른 조사로 판단됐습니다: {reason}"
            " 값이 조회되더라도 이 주장을 뒷받침하는 근거로 쓰지 않습니다."
        ),
    )


# ---------------------------------------------------------------------
# 6.5절: [신규 - 2026-08-10, README 3장 H] "역대 최고/최저" 기록 주장
# 전용 분기. search_log와 무관한 raw_sentence 자체의 성질이라
# _check_unverified와 분리했다 - 표를 잘 찾았고 값도 정확히 조회됐어도,
# "역대" 여부는 이 모듈이 갖지 않은 전체 과거 시계열이 있어야만 검증
# 가능하므로 항상 UNVERIFIED_RECORD_CLAIM으로 분리한다. RAW_ONLY 모드는
# 애초에 판정을 안 하고 원자료만 보여주는 모드라 이 검사에서 제외한다
# (원자료 자체는 보여줘도 무방함 - judge_claim에서 RAW_ONLY 분기 이후에
# 호출하도록 배치).
# ---------------------------------------------------------------------
def _check_record_claim(
    claim: Claim, actual: Optional[ActualEvidence] = None
) -> Optional[VerdictResult]:
    """[2026-08-24 갱신 - records 테이블 배선] 반환값 셋:

    - VerdictResult(UNVERIFIED_RECORD_CLAIM): 방향(최고/최저) 판별 불가
      이거나, records 테이블에 이 계열 데이터가 아직 없어 대조 자체를
      못 함 - 여전히 판단불가로 정직하게 남긴다.
    - VerdictResult(MISMATCH): records 테이블 조회 결과, 진짜 역대
      최고/최저는 claim이 주장한 시점이 아닌 다른 시점에 났다 - 방향/
      오차 허용과 무관하게 그 자체로 확정 MISMATCH다(_resolve_comparison_
      evidence의 방향 반전 즉시 MISMATCH 패턴과 동일한 설계).
    - None: 방향도 판별됐고 시점도 일치(또는 claim에 시점이 없어 대조
      대상이 아님) - 값 자체의 일치 여부는 이 함수가 판정하지 않고,
      judge_claim의 나머지 일반 허용오차 로직에 그대로 맡긴다(actual.value가
      이미 이 claim의 claimed_period 시점 값이므로, 시점이 일치한다면
      그 값이 곧 records의 max/min과 같아야 한다 - 별도 재비교 불필요).
    """
    if not _is_record_claim(claim.raw_sentence):
        return None

    fallback_explanation = (
        "이 주장은 \"역대/사상 최고·최저\"처럼 과거 전체 기록과의"
        " 비교를 전제로 합니다."
    )

    polarity = _record_claim_polarity(claim.raw_sentence)
    if polarity is None:
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_RECORD_CLAIM,
            explanation=(
                fallback_explanation
                + " 문장에서 \"최고/최대\"인지 \"최저/최소\"인지 방향을"
                " 판별하지 못해 대조를 건너뜁니다."
            ),
            claimed_value=claim.claimed_value,
        )

    if actual is None:
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_RECORD_CLAIM,
            explanation=(
                fallback_explanation
                + " 대조할 원자료 자체가 없습니다."
            ),
            claimed_value=claim.claimed_value,
        )

    if polarity == "max":
        target_value = actual.record_max_value
        target_period = actual.record_max_period
        period_matches = actual.record_period_matches_max
        label = "최고"
    else:
        target_value = actual.record_min_value
        target_period = actual.record_min_period
        period_matches = actual.record_period_matches_min
        label = "최저"

    if target_value is None:
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_RECORD_CLAIM,
            explanation=(
                fallback_explanation
                + f" 이 계열의 전체 기간 {label} 기록 요약이 로컬 웨어하우스"
                " (records 테이블)에 아직 없어(그 표/항목이 역대 기록 계산"
                " 대상으로 적재되지 않았을 수 있음) 대조하지 못했습니다."
            ),
            claimed_value=claim.claimed_value,
        )

    # 진짜 역대 기록이 claim이 주장한 시점과 다른 시점에 났다면, 그 값이
    # 우연히 비슷하더라도 "이 시점이 역대 기록"이라는 주장 자체가
    # 틀렸으므로 오차 허용과 무관하게 즉시 MISMATCH.
    if period_matches is False:
        coverage = None
        if actual.record_coverage_strt or actual.record_coverage_end:
            coverage = f"(웨어하우스 수집 범위 {actual.record_coverage_strt}~{actual.record_coverage_end})"
        return VerdictResult(
            verdict=Verdict.MISMATCH,
            explanation=(
                f"실제 역대 {label} 기록은 {target_period}에 {target_value}"
                f"(으)로 났습니다 - 주장한 시점({claim.claimed_period})과"
                f" 다릅니다{(' ' + coverage) if coverage else ''}."
            ),
            claimed_value=claim.claimed_value,
            actual_value=target_value,
        )

    # 방향 판별됨 + 시점 일치(또는 대조 불가라 통과) - 값 비교는 일반
    # 허용오차 로직에 위임한다.
    return None


# ---------------------------------------------------------------------
# 7절: 진입 함수
# ---------------------------------------------------------------------
def _resolve_comparison_evidence(
    claim: Claim, actual: ActualEvidence, mode: Mode
) -> "tuple":
    """actual.is_comparison=True일 때, 두 시점 값을 하나의 (부호 있는
    diff, 설명용 문구) 로 압축한다. 실패하면 VerdictResult를 반환하고,
    성공하면 (diff, note) 튜플을 반환한다 - 호출부가 타입으로 구분한다.
    """
    points = actual.values or []
    if len(points) < 2:
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_UNRESOLVED,
            explanation=(
                "이 주장은 두 시점 비교가 필요한데, 비교에 쓸 시점 값이"
                f" {len(points)}개만 제공됐습니다(최소 2개 필요)."
            ),
            claimed_value=claim.claimed_value,
            mode=mode,
        )
    if len(points) > 2:
        # 3개 이상(예: "N분기 연속 증가"류)은 Decision Log #47(파생·복합
        # claim 평가)이 다루기로 이미 후순위로 미뤄둔 영역이다 - 여기서
        # 섣불리 다중 시점 추세를 자동 판정하지 않는다.
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_DERIVED_NEEDED,
            explanation=(
                f"{len(points)}개 시점에 걸친 추세 주장은 아직 자동으로"
                " 계산하지 않습니다(다중 시점 계산 아키텍처는 별도 트랙)."
            ),
            claimed_value=claim.claimed_value,
            mode=mode,
        )

    # 정확히 2개 - claimed_period와 일치하는 쪽을 "기준시점"으로,
    # 나머지를 "비교시점"으로 삼는다. 매칭 안 되면(period 정보가 없거나
    # 형식이 다르면) 4번 팀원이 준 순서를 그대로 신뢰한다(첫 번째=기준,
    # 두 번째=비교) - 이 순서 규칙은 어댑터/4번 팀원과 미리 합의해둬야
    # 하는 지점이다.
    base, reference = points[0], points[1]
    if claim.claimed_period:
        for i, p in enumerate(points):
            if p.period == claim.claimed_period:
                base = p
                reference = points[1 - i]
                break

    if base.value is None or reference.value is None:
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_UNRESOLVED,
            explanation="비교에 필요한 두 시점 값 중 일부가 비어있습니다.",
            claimed_value=claim.claimed_value,
            mode=mode,
        )

    diff = base.value - reference.value

    # [버그 수정 - 2026-08-10, 검색 단계 entrypoint를 실제로 이어서
    # 돌려보다가 발견] claim이 퍼센트로 표현된 증감(예: "1.0% 감소")이면,
    # 두 시점의 "절대값 차이"(diff, 예: -1016ha)를 그대로 claimed_value
    # (1.0)와 비교하면 단위가 안 맞는다 - ha 단위 차이를 %와 비교하는
    # 셈이라 항상 터무니없이 큰 diff가 나와서 거의 무조건 MISMATCH로
    # 오판된다. claim.unit_category가 PERCENT면 diff를 reference.value로
    # 나눠 실제 증감률로 환산한 뒤, 그 증감률을 claimed_value와 비교해야
    # 한다. 방향(증가/감소) 판정은 원래 diff의 부호로 하고(퍼센트든
    # 절대값이든 부호가 같으므로), 크기 비교만 카테고리에 맞는 값으로
    # 바꾼다.
    # [버그 수정 - 2026-08-10] claimed_unit이 "%p"면 이미 비율인 지표의
    # 절대적 변화폭이므로 pct_change 변환을 건너뛴다(위
    # _is_percentage_point_claim 주석 참고) - 그렇지 않으면 기준금리
    # "동결"류 claim에서 diff를 reference로 한 번 더 나눠 퍼센트의
    # 퍼센트를 만들어버리는 오류가 생긴다.
    pct_change: Optional[float] = None
    if (
        claim.unit_category == UnitCategory.PERCENT
        and not _is_percentage_point_claim(claim)
        and reference.value not in (None, 0)
    ):
        pct_change = diff / reference.value * 100

    # [신규 - 2026-08-10, README 3장 D] "두 배로 뛰었다"/"절반으로
    # 줄었다"류 claim(unit_category=MULTIPLIER)은 diff도 pct_change도
    # 아니라 두 시점 값의 비율(base/reference)로 비교해야 한다 - claimed_
    # value 자체가 비율(예: 2.0)이기 때문이다. 절대값 차이나 상대적
    # 변화율(%)로 재면 애초에 단위가 안 맞는다.
    ratio: Optional[float] = None
    if (
        claim.unit_category == UnitCategory.MULTIPLIER
        and reference.value not in (None, 0)
    ):
        ratio = base.value / reference.value

    if ratio is not None:
        note = (
            f"기준({base.period}) {base.value}{base.unit or ''} vs"
            f" 비교({reference.period}) {reference.value}{reference.unit or ''}"
            f" - 비율 {ratio:.3f}배"
        )
    elif pct_change is not None:
        note = (
            f"기준({base.period}) {base.value}{base.unit or ''} vs"
            f" 비교({reference.period}) {reference.value}{reference.unit or ''}"
            f" - 두 시점 사이 증감률 {pct_change:+.2f}%"
        )
    else:
        note = (
            f"기준({base.period}) {base.value}{base.unit or ''} vs"
            f" 비교({reference.period}) {reference.value}{reference.unit or ''}"
        )

    # [팀 문서 예시 그대로 반영] "공식 통계는 증가이나 기사는 감소로
    # 표현" - 방향이 정반대면 오차 허용 여부와 무관하게 그 자체로
    # MISMATCH다. 방향 신호가 없는 claim(순수 절대값 주장)에는 이 검사를
    # 적용하지 않는다. 방향은 diff의 부호로 판단한다 - 퍼센트 환산 여부와
    # 무관하게 diff와 pct_change는 항상 같은 부호다.
    #
    # [버그 수정 - 2026-08-10, README 3장 E] "동결됐다"/"보합세를 이어
    # 갔다"처럼 변화 없음을 주장하는 claim(direction="no_change")은 실제
    # diff가 딱 0이 아닌 이상(거의 항상 그렇다 - 완전히 같은 값이 두 번
    # 나오는 경우는 드묾) increase도 decrease도 아니어서 여기서 무조건
    # MISMATCH로 튕겨나가는 버그가 있었다. "no_change"는 increase/decrease
    # 둘 중 하나와 정반대라고 판단할 수 있는 신호가 아니므로 이 조기
    # 판정에서는 아예 제외하고, 실제로 유의미하게 변했는지는 아래(judge_
    # claim의 hedge/tolerance 비교, claimed_value가 보통 0으로 옴)에
    # 맡긴다 - 방향 신호만으로 성급하게 MISMATCH를 확정하지 않는다.
    if claim.direction and claim.direction != "no_change" and diff != 0:
        actual_direction = "increase" if diff > 0 else "decrease"
        if claim.direction != actual_direction:
            return VerdictResult(
                verdict=Verdict.MISMATCH,
                explanation=(
                    f"{note} - 실제로는 {actual_direction}(이)나 주장은"
                    f" {claim.direction}이라고 해서 방향 자체가 반대입니다."
                ),
                claimed_value=claim.claimed_value,
                actual_value=abs(
                    ratio if ratio is not None
                    else (pct_change if pct_change is not None else diff)
                ),
                mode=mode,
            )

    if ratio is not None:
        compare_value = ratio
    elif pct_change is not None:
        compare_value = pct_change
    else:
        compare_value = diff
    return abs(compare_value), note


def judge_claim(
    claim: Claim,
    actual: ActualEvidence,
    search_log: SearchLog,
    mode: Mode = Mode.TOLERANCE,
    hcx_client=None,
) -> VerdictResult:
    """claim과 actual을 놓고 최종 판정을 확정한다.

    이 함수는 검색/해석을 다시 하지 않는다 - search_log가 이미 그 과정을
    끝낸 결과라고 신뢰하고, 여기서는 오직 "판정"과 "설명"만 만든다.

    hcx_client: [신규 - 2026-08-10, README 3장 A/B/C/D] 선택 인자.
    `generate_completion(messages, temperature=...) -> str` 인터페이스를
    가진 객체(client.HCXClient 또는 테스트/데모용 목)를 넘기면 하이브리드
    원칙(5장)에 따라 규칙이 애매한 경우에만 AI를 보조로 부른다. None이면
    (기본값) 순수 규칙 기반으로만 동작한다 - 기존 호출부는 아무것도 바꿀
    필요가 없다(하위 호환).
    """
    unverified = _check_unverified(search_log)
    if unverified is not None:
        unverified.claimed_value = claim.claimed_value
        unverified.actual_value = actual.value
        unverified.mode = mode
        return unverified

    if mode == Mode.RAW_ONLY:
        if actual.is_comparison:
            points_desc = "; ".join(
                f"{p.period}: {p.value}{p.unit or ''}" for p in (actual.values or [])
            )
            explanation = (
                f"[{actual.table_nm}] 주장값 {claim.claimed_value}{claim.claimed_unit or ''}"
                f" / 조회된 시점들({points_desc}) - 판정 없이 원자료를 그대로"
                " 제공합니다. 최종 판단은 사용자에게 맡깁니다."
            )
        else:
            explanation = (
                f"[{actual.table_nm}] 주장값 {claim.claimed_value}{claim.claimed_unit or ''}"
                f" / 조회값 {actual.value}{actual.unit or ''} - 판정 없이 원자료를"
                " 그대로 제공합니다. 최종 판단은 사용자에게 맡깁니다."
            )
        return VerdictResult(
            verdict=Verdict.RAW_ONLY,
            explanation=explanation,
            claimed_value=claim.claimed_value,
            actual_value=actual.value,
            mode=mode,
        )

    # [신규 - 2026-08-28] RAW_ONLY 이후, _check_record_claim보다도 먼저
    # 배치 - 표의 작성 목적이 애초에 claim의 의도와 다르다면, "역대
    # 기록"류 대조나 값 비교로 넘어갈 이유가 없다(장식적 텍스트가 아니라
    # 실제 게이트여야 한다는 사용자 요구 - Decision 003 강제 패턴과 동일).
    purpose_result = _check_purpose_mismatch(actual)
    if purpose_result is not None:
        purpose_result.mode = mode
        purpose_result.claimed_value = claim.claimed_value
        purpose_result.actual_value = actual.value
        return purpose_result

    # [신규 - 2026-08-10, README 3장 H] RAW_ONLY 이후, 나머지 판정 로직
    # 이전에 배치 - 원자료 노출은 막지 않되, VERIFIED/MISMATCH 판정은
    # 절대 내리지 않는다.
    record_result = _check_record_claim(claim, actual)
    if record_result is not None:
        record_result.mode = mode
        return record_result

    # [신규 - 2026-08-10, README 3장 D] 배수/분수 표현 자동 감지. claim을
    # 만든 호출부(claim 추출 단계)가 unit_category=MULTIPLIER를 미리
    # 세팅해주지 않았더라도, raw_sentence에 "두 배로 뛰었다"류 트리거가
    # 있으면 이 모듈이 스스로 알아채서 비율 비교 모드로 전환한다.
    # claimed_value 자체는 호출부가 이미 추출한 값을 그대로 신뢰하고 여기서
    # 덮어쓰지 않는다(이 모듈이 문장에서 다시 파싱한 배수는 감지·검증
    # 용도로만 쓴다) - 만약 파싱 결과가 claimed_value와 크게 다르면 그건
    # claim 추출 단계의 문제일 가능성이 높으므로 여기서 조용히 고치지
    # 않고 있는 그대로 비교한다.
    effective_claim = claim
    multiplier_ai_used = False
    multiplier_note = None
    if (
        actual.is_comparison
        and claim.unit_category != UnitCategory.MULTIPLIER
        and _is_multiplier_claim_candidate(claim.raw_sentence)
    ):
        detected = _parse_multiplier_rule_based(claim.raw_sentence)
        if detected is None:
            detected = _ai_parse_multiplier(claim.raw_sentence, hcx_client)
            multiplier_ai_used = detected is not None
        if detected is not None:
            effective_claim = replace(claim, unit_category=UnitCategory.MULTIPLIER)
            multiplier_note = (
                "배수 표현 감지"
                + (" (AI 보조)" if multiplier_ai_used else " (규칙 기반)")
                + f" - 문장에서 추출한 배수 참고값 {detected:.3f}"
            )

    comparison_note = None
    if actual.is_comparison:
        resolved = _resolve_comparison_evidence(effective_claim, actual, mode)
        if isinstance(resolved, VerdictResult):
            if multiplier_note:
                resolved.ai_used = resolved.ai_used or multiplier_ai_used
                resolved.ai_note = multiplier_note
            return resolved
        actual_value, comparison_note = resolved
    elif actual.value is None:
        # 방어적 가드 - search_log가 RESOLVED라고 했는데 실제 값이 없는
        # 경우는 이 함수 스펙 밖의 상황이지만, 조용히 죽지 않고 판단불가로
        # 처리한다.
        return VerdictResult(
            verdict=Verdict.UNVERIFIED_UNRESOLVED,
            explanation="표는 확정됐지만 실제 값을 조회하지 못했습니다.",
            claimed_value=claim.claimed_value,
            mode=mode,
        )
    else:
        actual_value = actual.value

    # [2026-08-28 신규 - 실측 발견, 팀원 110차 DB확장 보고 §3-4번 유형]
    # is_comparison=False(단일 시점) change_amount/rate claim에서, KOSIS
    # 원본 컬럼이 이미 부호 있는 증감값으로 적재된 경우(예: "자연증감" -
    # 감소는 음수로 저장)와 뉴스 claim이 부호 없이 "9124명 감소"처럼
    # 방향은 별도 단어로, 크기는 항상 양수로 표현하는 관례가 충돌해서
    # 허위 MISMATCH가 났다(실측: Aeb3233ab-C019 "자연 감소 9124명" vs
    # 조회값 -9149 - 부호까지 다른 값으로 착각해 18273 차이로 MISMATCH,
    # 실제로는 절댓값 기준 25명(0.3%) 차이로 사실상 일치).
    #
    # is_comparison=True 경로(_resolve_comparison_evidence)는 diff의 부호로
    # 스스로 방향을 계산하므로 이 문제가 원래 없다 - 이 문제는 "이미 부호가
    # 실려서 오는 단일 값"에서만 생긴다.
    #
    # claim.direction이 있으면(1번 스키마 - change_amount/change_rate에서만
    # 채워짐, level claim에는 없음) 먼저 부호 자체가 진짜로 반대인지부터
    # 확인한다 - 방향 반전은 오차 허용과 무관하게 그 자체로 즉시 MISMATCH
    # 다(_resolve_comparison_evidence의 기존 "방향이 정반대면 무조건
    # MISMATCH" 패턴과 동일). 부호가 일치하면(또는 actual_value가 0이라
    # 방향 판단 근거가 없으면) 이후 비교는 절댓값 기준으로 통일한다 -
    # claimed_value는 추출 관례상 이미 양수로 온다는 전제이므로, 사실상
    # actual_value 쪽만 부호가 뒤집혀 있던 걸 바로잡는 효과다.
    if not actual.is_comparison and claim.direction in ("increase", "decrease") and actual_value != 0:
        actual_sign_direction = "increase" if actual_value > 0 else "decrease"
        if actual_sign_direction != claim.direction:
            claimed_desc = "증가" if claim.direction == "increase" else "감소"
            actual_desc = "증가" if actual_sign_direction == "increase" else "감소"
            return VerdictResult(
                verdict=Verdict.MISMATCH,
                explanation=(
                    f"[{actual.table_nm}] 실제 조회값({actual_value}{actual.unit or ''})은"
                    f" {actual_desc} 방향인데, 주장은 \"{claimed_desc}\"라고 해서 방향 자체가"
                    " 반대입니다."
                ),
                claimed_value=claim.claimed_value,
                actual_value=abs(actual_value),
                mode=mode,
            )
        # 부호 일치 - claimed_value(이미 양수)와 절댓값 기준으로 맞춰서
        # 비교한다. effective_claim을 통해 흘려보내 이후 비교/설명 코드가
        # 전부 이 값을 쓰게 한다(멀티플라이어 감지가 이미 쓰는 것과 같은
        # "정규화는 effective_claim에, 원본 claim은 안 건드림" 패턴).
        effective_claim = replace(effective_claim, claimed_value=abs(claim.claimed_value))
        actual_value = abs(actual_value)

    # [신규 - 2026-08-10, README 3장 A/B/C] 규칙 기반 hedge_type을 먼저
    # 구하고, 위험 신호(_needs_ai_reinterpretation)가 있을 때만 AI에게
    # 고정 선택지로 재해석을 맡긴다. hcx_client가 없거나 호출/파싱이
    # 실패하면 ai_choice가 None이 되어 조용히 규칙 기반 결과를 그대로
    # 쓴다(하이브리드 원칙 4번 - 에러가 나도 모듈 전체가 멈추지 않음).
    hedge_type = extract_hedge(claim.raw_sentence)
    ai_used = multiplier_ai_used
    ai_note = multiplier_note
    if _needs_ai_reinterpretation(claim.raw_sentence, hedge_type):
        ai_choice = _ai_reinterpret_sentence(
            claim.raw_sentence, claim.claimed_value, hcx_client
        )
        if ai_choice is not None and ai_choice != hedge_type:
            reinterpret_note = (
                f"AI 재해석: 규칙 기반으로는 \"{_HEDGE_DESCRIPTIONS.get(hedge_type, hedge_type)}\""
                f"로 읽었으나, 부정문/다의어 등 위험 신호가 있어 AI에게 재확인한 결과"
                f" \"{_AI_SENTENCE_CHOICES.get(ai_choice, ai_choice)}\"로 재해석됨"
            )
            ai_used = True
            ai_note = (
                f"{ai_note}; {reinterpret_note}" if ai_note else reinterpret_note
            )
            if ai_choice in ("at_least", "at_most", "approx", "exact"):
                hedge_type = ai_choice
            elif ai_choice == "no_change":
                # hedge_type 체계에는 "변화 없음" 개념이 없다(E번은
                # claim.direction으로 표현) - 여기서 claim.direction까지
                # 덮어쓰는 건 호출부 입력을 임의로 바꾸는 것이라 하지
                # 않는다. 대신 오차 허용폭을 넓히는 approx로 완화해
                # "정확히 딱 그 값이 아니어도 된다"는 뜻을 최대한 반영한다.
                hedge_type = "approx"
            elif ai_choice == "multiplier":
                # 이미 위에서 effective_claim이 MULTIPLIER로 전환됐다면
                # 중복이니 건드리지 않는다. 아직 전환 안 됐다면(D 트리거
                # 정규식이 못 잡은 표현을 AI가 여기서 새로 알아챈 경우)
                # hedge_type만으로는 비율 비교로 바꿀 수 없으므로(이미
                # actual_value 계산이 끝난 뒤라 재계산이 필요) approx로
                # 완화하고 한계를 설명에 남긴다.
                if effective_claim.unit_category != UnitCategory.MULTIPLIER:
                    hedge_type = "approx"
                    ai_note += " (배수 비교로 전환은 다음 판정에 반영 필요 - 이번 호출은 근사치로 완화 처리)"
        elif ai_choice is not None:
            ai_used = True
            confirm_note = (
                f"AI 재확인: 위험 신호가 있었으나 규칙 기반 해석"
                f" \"{_HEDGE_DESCRIPTIONS.get(hedge_type, hedge_type)}\"과 동일하게 판단됨"
            )
            ai_note = f"{ai_note}; {confirm_note}" if ai_note else confirm_note

    kind, epsilon = _category_tolerance(effective_claim.unit_category, mode, hedge_type)
    # [2026-08-28 갱신 - 방향 부호 정규화] claim.claimed_value가 아니라
    # effective_claim.claimed_value를 쓴다 - 대부분의 경우 둘은 같지만,
    # 위에서 방향 부호 정규화가 적용된 claim(단일 시점 change_amount/rate +
    # direction 일치)이면 effective_claim.claimed_value가 abs() 적용된
    # 값이다(원본 claim 객체 자체는 그대로 보존 - 멀티플라이어 감지와
    # 동일한 "정규화는 effective_claim에" 원칙).
    matched = _compare_with_hedge(
        effective_claim.claimed_value, actual_value, hedge_type, kind, epsilon
    )

    hedge_desc = _HEDGE_DESCRIPTIONS.get(hedge_type, hedge_type)
    diff = actual_value - effective_claim.claimed_value
    value_desc = (
        comparison_note
        if comparison_note
        else f"조회값 {actual_value}{actual.unit or ''}"
    )
    # [2026-08-24 신규 - records 테이블 배선] 역대 claim이 여기까지 왔다는
    # 건 _check_record_claim에서 방향 판별 + 시점 일치까지 이미 확인됐다는
    # 뜻이다(불일치였으면 위에서 이미 MISMATCH로 반환됨) - "역대" 주장의
    # 시점 근거도 검증됐음을 설명에 남긴다(그냥 조용히 일반 claim처럼
    # 보이면 이 claim이 records 테이블까지 대조됐다는 사실이 안 드러난다).
    record_note = ""
    if _is_record_claim(claim.raw_sentence):
        polarity = _record_claim_polarity(claim.raw_sentence)
        if polarity == "max" and actual.record_max_value is not None:
            record_note = (
                f" (records 테이블 기준 전체 기간 최댓값 {actual.record_max_value}"
                f"과 시점 일치 확인됨 - \"역대\" 주장의 시점 근거도 검증됨.)"
            )
        elif polarity == "min" and actual.record_min_value is not None:
            record_note = (
                f" (records 테이블 기준 전체 기간 최솟값 {actual.record_min_value}"
                f"과 시점 일치 확인됨 - \"역대\" 주장의 시점 근거도 검증됨.)"
            )
    if matched:
        explanation = (
            f"[{actual.table_nm}] 원문장은 \"{hedge_desc}\"으로 해석됩니다."
            f" 주장값 {effective_claim.claimed_value}{claim.claimed_unit or ''} vs"
            f" {value_desc}"
            f" (차이 {diff:+.3f}) - {mode.value} 기준 허용 오차 이내로 일치합니다."
            f"{record_note}"
        )
        verdict = Verdict.VERIFIED
    else:
        explanation = (
            f"[{actual.table_nm}] 원문장은 \"{hedge_desc}\"으로 해석됩니다."
            f" 주장값 {effective_claim.claimed_value}{claim.claimed_unit or ''} vs"
            f" {value_desc}"
            f" (차이 {diff:+.3f}) - {mode.value} 기준 허용 오차를 벗어났습니다."
            f"{record_note}"
        )
        verdict = Verdict.MISMATCH

    return VerdictResult(
        verdict=verdict,
        explanation=explanation,
        claimed_value=effective_claim.claimed_value,
        actual_value=actual_value,
        hedge_type=hedge_type,
        mode=mode,
        ai_used=ai_used,
        ai_note=ai_note,
    )


# ---------------------------------------------------------------------
# [신규 - 2026-08-10] A/B/C/D 데모 전용 목 HCX 클라이언트.
#
# 이 세션에는 실제 HCX API 키가 없다(config.py가 비어있음, 이전 세션부터
# 계속된 환경 제약 - README 참고). 그래서 실제 CLOVA Studio 호출 대신,
# generate_completion(messages, temperature=...) 인터페이스만 동일하게
# 맞춘 목으로 "하이브리드 경로가 실제로 배선되어 정확한 답을 낼 수
# 있는가"를 검증한다 - 사람이 미리 정답을 채워 넣은 문자열 매칭이라 실제
# LLM 추론이 아니다. 실제 NCP_CLOVASTUDIO_API_KEY가 확보되면
# client.HCXClient()로 그대로 교체해 재검증해야 한다(judge_claim은
# generate_completion 인터페이스만 요구하므로 교체 비용은 0).
# ---------------------------------------------------------------------
class _DemoHCXClient:
    def __init__(self, answers: dict):
        self.answers = answers

    def generate_completion(self, messages, temperature=0.0, **kwargs):
        user_content = messages[-1]["content"] if messages else ""
        for key, response in self.answers.items():
            if key in user_content:
                return response
        return '{"choice": "exact"}'


# ---------------------------------------------------------------------
# 데모 - 2주차 발표/서비스 목업에서 썼던 것과 동일한 6개 사례.
# ---------------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        (
            "최저임금(VERIFIED - exact)",
            Claim("내년도 최저임금은 시간당 9,860원으로 결정됐다", 9860, "원", "2026", UnitCategory.MONEY),
            ActualEvidence(9860, "원", "101", "DT_2OEEM1012", "지방자치단체 외 최저임금 및 영향률", None),
            SearchLog("RESOLVED", True, ["지방자치단체 외 최저임금 및 영향률"]),
        ),
        (
            "소비자물가지수(MISMATCH)",
            Claim("6월 소비자물가지수가 전년 동월 대비 3.5% 급등했다", 3.5, "%", "202606", UnitCategory.PERCENT),
            ActualEvidence(2.4, "%", "101", "DT_1J17009", "소비자물가지수(등락률)", None),
            SearchLog("RESOLVED", True, ["소비자물가지수(등락률)"]),
        ),
        (
            "배추가격(UNVERIFIED_NOT_FOUND)",
            Claim("배추 한 포기 가격이 3,000원에 육박하며 밥상물가에 비상", 3000, "원", "202607", UnitCategory.MONEY),
            ActualEvidence(),
            SearchLog("NOT_FOUND", False, ["농가판매가격지수", "채소류 소득조사", "식재료 구매 행태"]),
        ),
        (
            "고령인구비율(VERIFIED - tolerance, exact hedge)",
            Claim("65세 이상 고령인구 비율이 20.3%로 집계되며 초고령사회 진입", 20.3, "%", "202606", UnitCategory.PERCENT),
            ActualEvidence(20.2, "%", "101", "DT_1B040A3", "주민등록인구 및 세대현황(연령별)", None),
            SearchLog("RESOLVED", True, ["주민등록인구 및 세대현황(연령별)"]),
        ),
        (
            "전세가율(VERIFIED)",
            Claim("서울 아파트 평균 전세가율이 65%를 넘어섰다", 65.0, "%", "202606", UnitCategory.PERCENT),
            ActualEvidence(65.2, "%", "101", "DT_1YL13502E", "주택매매가격 및 전세가격 동향조사", None),
            SearchLog("RESOLVED", True, ["주택매매가격 및 전세가격 동향조사"]),
        ),
        (
            "청년실업률(MISMATCH)",
            Claim("6월 청년(15~29세) 실업률이 8.1%로 집계됐다", 8.1, "%", "202606", UnitCategory.PERCENT),
            ActualEvidence(6.8, "%", "101", "DT_1DA7002S", "연령별 경제활동인구 총괄", None),
            SearchLog("RESOLVED", True, ["연령별 경제활동인구 총괄"]),
        ),
        (
            "취업자 수 감소(VERIFIED - 증감 비교, 2시점)",
            Claim(
                "2025년 1월 취업자 수는 13만 명 감소했다", 130000, "명", "2025-01",
                UnitCategory.PERSON, direction="decrease",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_1DA7001S",
                table_nm="성별 경제활동인구 총괄", is_comparison=True,
                values=[
                    EvidencePoint("2025-01", 27748000, "명"),
                    EvidencePoint("2024-01", 27878000, "명"),
                ],
            ),
            SearchLog("RESOLVED", True, ["성별 경제활동인구 총괄"]),
        ),
        (
            "취업자 수 방향 반대(MISMATCH - 방향 모순)",
            Claim(
                "2025년 1월 취업자 수는 13만 명 감소했다", 130000, "명", "2025-01",
                UnitCategory.PERSON, direction="decrease",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_1DA7001S",
                table_nm="성별 경제활동인구 총괄", is_comparison=True,
                values=[
                    EvidencePoint("2025-01", 28008000, "명"),
                    EvidencePoint("2024-01", 27878000, "명"),
                ],
            ),
            SearchLog("RESOLVED", True, ["성별 경제활동인구 총괄"]),
        ),
        (
            "재배면적 1.0% 감소(VERIFIED - 퍼센트 증감 비교, 2026-08-10 버그 수정)",
            Claim(
                "재배면적이 10만4943㏊로 작년 10만5959㏊보다 1.0% 감소했다.",
                1.0, "%", None, UnitCategory.PERCENT, direction="decrease",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_CROP", table_nm="재배면적",
                is_comparison=True,
                values=[
                    EvidencePoint("2025", 104943.0, "ha"),
                    EvidencePoint("2024", 105959.0, "ha"),
                ],
            ),
            SearchLog("RESOLVED", True, ["재배면적"]),
        ),
        (
            "재배면적 5% 감소 과장(MISMATCH - 퍼센트 증감 비교)",
            Claim(
                "재배면적이 작년보다 5% 감소했다",
                5.0, "%", None, UnitCategory.PERCENT, direction="decrease",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_CROP", table_nm="재배면적",
                is_comparison=True,
                values=[
                    EvidencePoint("2025", 104943.0, "ha"),
                    EvidencePoint("2024", 105959.0, "ha"),
                ],
            ),
            SearchLog("RESOLVED", True, ["재배면적"]),
        ),
        (
            "기준금리 동결(VERIFIED - no_change 방향, 2026-08-10 버그 수정)",
            Claim(
                "한국은행이 기준금리를 이번 달에도 동결했다",
                0.0, "%p", None, UnitCategory.PERCENT, direction="no_change",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_BOK_BASE", table_nm="한국은행 기준금리",
                is_comparison=True,
                values=[
                    EvidencePoint("2026-08", 3.50, "%"),
                    EvidencePoint("2026-07", 3.50, "%"),
                ],
            ),
            SearchLog("RESOLVED", True, ["한국은행 기준금리"]),
        ),
        (
            "기준금리 동결 오보(MISMATCH - no_change인데 실제로 크게 변함)",
            Claim(
                "한국은행이 기준금리를 이번 달에도 동결했다",
                0.0, "%p", None, UnitCategory.PERCENT, direction="no_change",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_BOK_BASE", table_nm="한국은행 기준금리",
                is_comparison=True,
                values=[
                    EvidencePoint("2026-08", 2.75, "%"),
                    EvidencePoint("2026-07", 3.50, "%"),
                ],
            ),
            SearchLog("RESOLVED", True, ["한국은행 기준금리"]),
        ),
        (
            "기준금리 0.25%p 인상(VERIFIED - %p는 pct_change 변환 안 함, 2026-08-10 버그 수정)",
            Claim(
                "한국은행이 기준금리를 0.25%p 인상했다",
                0.25, "%p", None, UnitCategory.PERCENT, direction="increase",
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_BOK_BASE", table_nm="한국은행 기준금리",
                is_comparison=True,
                values=[
                    EvidencePoint("2026-08", 3.50, "%"),
                    EvidencePoint("2026-07", 3.25, "%"),
                ],
            ),
            SearchLog("RESOLVED", True, ["한국은행 기준금리"]),
            # 수정 전이었다면: diff=0.25를 reference(3.25)로 나눠
            # pct_change≈7.69%를 계산해서 주장값 0.25(%p)와 비교 -
            # 단위가 안 맞아 거의 항상 MISMATCH가 났을 것.
            # 수정 후: claimed_unit="%p" -> pct_change 변환 건너뛰고
            # diff=0.25를 그대로 비교 -> 정확히 일치.
        ),
        # ---- 아래부터 README 3장 A/B/C/D/H 하이브리드 AI 호출 데모 ----
        (
            "A. 부정문 반전, AI 없음(실제로는 VERIFIED가 맞는데 잘못 MISMATCH)",
            Claim("실업률이 9%를 넘어서지 못했다", 9.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(8.5, "%", "101", "DT_UNEMP", "실업률", None),
            SearchLog("RESOLVED", True, ["실업률"]),
            None,  # hcx_client 없음 - "넘어서"만 보고 at_least로 오판(8.5가
            # 9-margin보다 작다고 나와 false MISMATCH - "9%를 못 넘었다"는
            # "9% 미만"이 맞다는 뜻인데, 부정문을 못 읽어 정반대로 해석)
        ),
        (
            "A. 부정문 반전, AI 있음(VERIFIED로 정정 - 실제로는 '9% 미만'이 맞음)",
            Claim("실업률이 9%를 넘어서지 못했다", 9.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(8.5, "%", "101", "DT_UNEMP", "실업률", None),
            SearchLog("RESOLVED", True, ["실업률"]),
            _DemoHCXClient({'문장: "실업률이 9%를 넘어서지 못했다"': '{"choice": "이하다"}'}),
        ),
        (
            "B. '이상 기후'의 다른 뜻, AI 없음(MISMATCH여야 하는데 잘못 VERIFIED)",
            Claim("이상 기후로 인해 배추 가격이 30% 폭등했다", 30.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(55.0, "%", "101", "DT_CABBAGE", "농산물 가격동향", None),
            SearchLog("RESOLVED", True, ["농산물 가격동향"]),
            None,  # hcx_client 없음 - "이상"만 보고 at_least로 오판(55>=30이라
            # "이상"이면 만족해버림 - 실제로는 30%라고 했는데 55%라 완전히 다름)
        ),
        (
            "B. '이상 기후'의 다른 뜻, AI 있음(MISMATCH로 정정)",
            Claim("이상 기후로 인해 배추 가격이 30% 폭등했다", 30.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(55.0, "%", "101", "DT_CABBAGE", "농산물 가격동향", None),
            SearchLog("RESOLVED", True, ["농산물 가격동향"]),
            _DemoHCXClient({
                '문장: "이상 기후로 인해 배추 가격이 30% 폭등했다"': '{"choice": "정확한 숫자다"}'
            }),
        ),
        (
            "C. 사전에 없는 근사 표현('~에 가까운'), AI 없음(MISMATCH가 과하게 나옴)",
            Claim("실업률이 8%에 가까운 수준을 기록했다", 8.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(7.65, "%", "101", "DT_UNEMP", "실업률", None),
            SearchLog("RESOLVED", True, ["실업률"]),
            None,  # hcx_client 없음 - "가깝다"가 사전(_HEDGE_PATTERNS)에 없어
            # exact로 취급, tolerance 모드 절대오차 0.3보다 diff(0.35)가 커서 MISMATCH
        ),
        (
            "C. 사전에 없는 근사 표현('~에 가까운'), AI 있음(VERIFIED로 정정)",
            Claim("실업률이 8%에 가까운 수준을 기록했다", 8.0, "%", None, UnitCategory.PERCENT),
            ActualEvidence(7.65, "%", "101", "DT_UNEMP", "실업률", None),
            SearchLog("RESOLVED", True, ["실업률"]),
            _DemoHCXClient({
                '문장: "실업률이 8%에 가까운 수준을 기록했다"': '{"choice": "거의 같다"}'
            }),
        ),
        (
            "D. 배수 표현('두 배'), 규칙 기반으로 바로 해결(AI 불필요)",
            Claim(
                "전세가율이 작년보다 두 배로 뛰었다", 2.0, "배", None,
                UnitCategory.OTHER,  # 호출부가 MULTIPLIER를 안 채워도 감지됨
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_JEONSE", table_nm="전세가율 동향",
                is_comparison=True,
                values=[EvidencePoint("2026", 130.0, "%"), EvidencePoint("2025", 65.0, "%")],
            ),
            SearchLog("RESOLVED", True, ["전세가율 동향"]),
            None,
        ),
        (
            "D. 배수 표현('곱절'), 규칙 사전에 없어 AI 없으면 오판(MISMATCH)",
            Claim(
                "전세가율이 작년보다 곱절로 뛰었다", 2.0, "배", None,
                UnitCategory.OTHER,
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_JEONSE", table_nm="전세가율 동향",
                is_comparison=True,
                values=[EvidencePoint("2026", 130.0, "%"), EvidencePoint("2025", 65.0, "%")],
            ),
            SearchLog("RESOLVED", True, ["전세가율 동향"]),
            None,  # "곱절"은 _parse_multiplier_rule_based 사전에 없음 -> 규칙
            # 기반 감지 실패 -> MULTIPLIER 전환 안 됨 -> diff(65)를 claimed_
            # value(2.0)와 그대로 비교(OTHER, 상대오차) -> 터무니없는 MISMATCH
        ),
        (
            "D. 배수 표현('곱절'), AI로 감지해 정정(VERIFIED)",
            Claim(
                "전세가율이 작년보다 곱절로 뛰었다", 2.0, "배", None,
                UnitCategory.OTHER,
            ),
            ActualEvidence(
                table_org_id="101", table_tbl_id="DT_JEONSE", table_nm="전세가율 동향",
                is_comparison=True,
                values=[EvidencePoint("2026", 130.0, "%"), EvidencePoint("2025", 65.0, "%")],
            ),
            SearchLog("RESOLVED", True, ["전세가율 동향"]),
            _DemoHCXClient({
                '문장: "전세가율이 작년보다 곱절로 뛰었다"': '{"multiplier": 2.0}'
            }),
        ),
        (
            "H. 역대 최저 기록 주장(UNVERIFIED_RECORD_CLAIM - 규칙 기반, AI 불필요)",
            Claim("출산율이 역대 최저치를 기록했다", 0.72, "명", "2025", UnitCategory.OTHER),
            ActualEvidence(0.72, "명", "101", "DT_1B81A17", "합계출산율", None),
            SearchLog("RESOLVED", True, ["합계출산율"]),
            None,
            # RAW_ONLY에서는 원자료가 정상적으로 그대로 나오고, STRICT/
            # TOLERANCE에서만 "역대 여부는 확인 불가"로 분리됨을 아래 실행
            # 결과에서 확인할 수 있다.
        ),
    ]

    for case in cases:
        name, claim, actual, log = case[0], case[1], case[2], case[3]
        hcx_client = case[4] if len(case) > 4 else None
        print(f"\n=== {name} ===")
        for mode in (Mode.STRICT, Mode.TOLERANCE, Mode.RAW_ONLY):
            result = judge_claim(claim, actual, log, mode=mode, hcx_client=hcx_client)
            ai_tag = " [AI 사용]" if result.ai_used else ""
            print(f"[{mode.value:9s}] {result.verdict.value}{ai_tag}: {result.explanation}")
            if result.ai_note:
                print(f"            ai_note: {result.ai_note}")

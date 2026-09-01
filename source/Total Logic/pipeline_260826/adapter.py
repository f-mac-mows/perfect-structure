"""팀원 1(Claim)·4(KOSIS Evidence)가 넘겨주는 원본 JSON을 판정 모듈
(judgment.py)의 내부 타입으로 변환하는 어댑터 - 5번(나) 파트 전용.

[설계 확정 - 팀 논의 결과] 5번이 실제로 받는 입력은 결국 두 가지뿐이다:
(1) 1번 팀원의 Claim, (2) 4번 팀원이 찾아서 넘겨준 값. 2·3번 팀원이
내부적으로 어떻게 후보를 찾고 랭킹하는지는 5번이 알 필요가 없다 - 그
과정의 결과(찾았는지/못 찾았는지/확신하는지)는 전부 4번의 출력에
반영되어 나온다고 가정한다. 그래서 이전에 고려했던 "3번 table_candidate
JSON도 별도로 받기"는 버리고, 입력을 claim_payload + evidence_payload
두 개로 단순화했다.

이 파일이 존재하는지 judgment.py는 전혀 모른다(반대로도 마찬가지) -
판정 로직과 파싱 로직은 완전히 분리돼 있고, 팀원들이 필드명을 바꾸면
이 파일만 고치면 된다.
"""

import json
import re
from typing import Any, Dict, List, Optional, Union

from judgment import ActualEvidence, Claim, EvidencePoint, SearchLog, UnitCategory
from kosis_text_utils import TextUtilsMixin

JsonLike = Union[str, Dict[str, Any]]


def _as_dict(payload: JsonLike) -> Dict[str, Any]:
    if isinstance(payload, str):
        return json.loads(payload)
    return payload or {}


def _first_present(d: Dict[str, Any], keys, default=None):
    """d에서 keys를 순서대로 시도해 처음 존재하는(None이 아닌) 값을
    반환한다. 팀원이 필드명을 조금씩 다르게 부를 가능성에 대비한
    안전판 - kosis_resolution.py의 _first_present와 같은 발상."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _to_float(value: Any) -> Optional[float]:
    """실제 KOSIS API는 수치를 JSON 문자열로 준다(예: "677421.7146").
    judgment.py는 float 연산(뺄셈/나눗셈)을 전제로 하므로 4번 출력이
    단일 시점이든(비교 아님) 다중 시점(비교)이든 이 함수를 거쳐 값을
    통일한다. 예전엔 단일 시점 분기에서만 float() 변환이 있고 비교
    분기는 문자열을 그대로 넘겨서, 실제 데이터로 비교 판정을 돌릴 때만
    'str - str' TypeError가 났다(모킹 테스트는 항상 float를 썼기 때문에
    안 잡혔음) - 2026-08-10 실사용 테스트에서 발견."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# [2026-08-18 - 48개 claim 재검증 실측에서 발견] KOSIS는 표에 따라 원본
# 값을 절대 단위(명/원/건)가 아니라 "천명"/"십억원"처럼 축척(scale)이 붙은
# 단위로 내려준다(예: DT_1DA7E33S_NEW의 UNIT_NM="천명" - raw 값 3233.9는
# 실제로 3,233,900명을 뜻한다). claim은 기사 원문에서 항상 절대값으로
# 추출되므로("323만4000명" -> 3,234,000), 후보 값도 같은 절대 단위로
# 맞추지 않으면 실제로는 거의 일치하는데도(3,233,900 vs 3,234,000)
# judgment.py가 숫자만 그대로 비교해 항상 MISMATCH를 낸다(실측:
# A82ae9f41-C004, 차이 -3,230,766로 보고됐지만 실제로는 100명 차이).
#
# 이 배율 계산 로직 자체는 새로 만들 필요가 없었다 - kosis_text_utils.
# TextUtilsMixin._unit_scale_multiplier가 이미 legacy 라이브 파이프라인
# (지금은 backup/20260815_kosis_refactor/kosis_resolution.py)에서
# GDP(십억원)/외환보유액(100만 USD) 표 대상 실측 스트레스 테스트로
# 검증됐던 함수다. 문제는 이 함수가 new_kosis_resolution.py/
# new_kosis_agent.py로 리팩터링되면서 호출부 없이 클래스에만 남아
# 있었다는 것 - 그래서 라이브 API 경로(NewKosisAgent)도 LocalDbAgent도
# 지금까지 이 배율을 전혀 적용하지 않고 있었다(둘 다 이 버그의 영향권).
# adapter.py는 두 agent가 공통으로 거치는 지점이라 여기 한 곳만 고치면
# 양쪽 다 해결된다.
# ---------------------------------------------------------------------
def _scale_to_absolute(value: Any, unit: Optional[str]) -> Optional[float]:
    """evidence(4번/LocalDbAgent 출력)의 값을 unit(KOSIS UNIT_NM 원본,
    예: "천명"/"십억원")에 실린 배율까지 반영해 절대 단위로 변환한다.
    claim 쪽 _parse_claimed_value/_to_float와 별도인 이유: 이건 KOSIS가
    표에 매겨둔 축척이지 사람이 쓴 "조/억/만" 축약 표기가 아니다."""
    numeric = _to_float(value)
    if numeric is None:
        return None
    return numeric * TextUtilsMixin._unit_scale_multiplier(unit)


def _strip_unit_scale(unit: Optional[str]) -> Optional[str]:
    """_scale_to_absolute로 값을 이미 절대 단위로 바꿨다면, 화면에 보여줄
    단위 라벨도 배율 접두어("천"/"십억" 등)를 뗀 기본단위로 맞춰야 한다 -
    안 떼면 "3233900.0천명"처럼 값과 라벨이 서로 어긋나서, 이미 절대값으로
    바꿔둔 숫자를 다시 33억 명으로 보이게 하는 표시 오류가 난다(judgment.py
    설명 문구 f"{actual.value}{actual.unit}"에서 실측 확인)."""
    if not unit:
        return unit
    m = TextUtilsMixin._UNIT_SCALE_RE.match(unit.strip())
    if not m:
        return unit
    numeral_str, scale_word, base = m.groups()
    if not numeral_str and not scale_word:
        return unit
    return base


# ---------------------------------------------------------------------
# [2026-08-14 - 90건 실사용 테스트에서 발견] run01_result.jsonl의 value는
# KOSIS 원본 수치("677421.7146")와 달리, 1번 팀원이 원문장에서 사람이 쓰는
# 한국어 축약 표기를 그대로 뽑아서 준다("1200조", "2909만1000",
# "323만4000" 등) - float()를 바로 쓰면 이런 표기에서 전부 예외가 난다
# (90건 중 14건이 이렇게 죽었음). 조/억/만/천 단위를 실제 숫자로 환산해
# 합산한다.
# ---------------------------------------------------------------------
_KOREAN_SCALE_UNITS = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3}
_KOREAN_SCALED_NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만|천)?")
# "2020년 0.5%, 2021년 2.5%, ..."처럼 연도 라벨이 2개 이상 붙은 값은 애초에
# 단일 숫자가 아니라 여러 시점 값이 문자열 하나에 뭉쳐 들어온 것이다 -
# 이런 값을 조/억/만 파서에 그대로 태우면 서로 다른 시점의 숫자들을 멋대로
# 더해 의미 없는 값을 만들 위험이 있다(Decision 003: 확실하지 않으면
# 추측하지 않는다). 그래서 이 모양이면 파싱을 포기하고 이유를 명확히
# 남긴다 - 1번 팀원 쪽에서 claim을 연도별로 쪼개서 다시 보내야 하는
# 케이스로 본다(다른 비교형 claim들처럼 claim_id가 연도별로 분리돼 있어야
# route_claim_group의 derived_comparison 경로를 탈 수 있다).
_MULTI_PERIOD_VALUE_RE = re.compile(r"\d{4}\s*년")


def _parse_claimed_value(value: Any) -> float:
    """claim payload의 value(주장 수치, 문자열/숫자 모두 가능)를 float로
    변환한다. parse_evidence_and_log가 쓰는 _to_float와 별도 함수인 이유:
    _to_float는 KOSIS API가 내려주는 이미 정제된 숫자 문자열만 상대하고,
    이 함수는 1번 팀원이 원문장에서 그대로 뽑아온 한국어 축약 표기까지
    상대해야 해서 처리 범위가 다르다."""
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()

    if len(_MULTI_PERIOD_VALUE_RE.findall(s)) >= 2:
        raise ValueError(
            f"value에 연도 라벨이 여러 개 섞인 시계열 문자열입니다"
            f"(단일 수치로 파싱 불가 - claim_id를 연도별로 분리해야 함): {s!r}"
        )

    # 1차: 쉼표만 제거한 순수 숫자(가장 흔한 케이스)
    try:
        return float(s.replace(",", ""))
    except ValueError:
        pass

    # 2차: 조/억/만/천 단위 환산(단위가 여러 번 나오면 합산 - "1267조2000억")
    total = 0.0
    matched_any = False
    for m in _KOREAN_SCALED_NUMBER_RE.finditer(s):
        num_str = m.group(1)
        if not num_str:
            continue
        try:
            num = float(num_str.replace(",", ""))
        except ValueError:
            continue
        total += num * _KOREAN_SCALE_UNITS.get(m.group(2), 1)
        matched_any = True

    if not matched_any:
        raise ValueError(f"value를 숫자로 변환할 수 없습니다: {s!r}")
    return total


# ---------------------------------------------------------------------
# 단위 -> 카테고리 추론(judgment.py 밖 파일을 import하지 않는다는 원칙을
# 지키기 위해 독립적으로 둔다).
# ---------------------------------------------------------------------
_UNIT_CATEGORY_MARKERS = (
    (UnitCategory.PERCENT, ("%", "퍼센트", "％")),
    (UnitCategory.MONEY, ("원", "달러", "USD", "KRW", "$")),
    (UnitCategory.PERSON, ("명", "인")),
    (UnitCategory.COUNT, ("개", "건", "곳", "대")),
)


def _infer_unit_category(unit: Optional[str]) -> str:
    if not unit:
        return UnitCategory.OTHER
    for category, markers in _UNIT_CATEGORY_MARKERS:
        if any(m in unit for m in markers):
            return category
    return UnitCategory.OTHER


# ---------------------------------------------------------------------
# 1번 팀원 출력 -> Claim
#
# direction: "13만 명 감소했다"류 claim에서 1번이 이미 뽑아준 방향
# 신호("increase"/"decrease"). 원문장의 근사/부등호 표현(hedge)은 여기서
# 뽑지 않는다 - 그건 judgment.py의 extract_hedge()가 raw_sentence를 보고
# 직접 처리한다(문장 해석은 1번이 아니라 5번이 한다고 확정됨).
# ---------------------------------------------------------------------
def parse_claim(payload: JsonLike) -> Claim:
    data = _as_dict(payload)
    value = _first_present(data, ("value", "claimed_value", "claim_value"))
    if value is None:
        raise ValueError(f"claim payload에 value(주장 수치)가 없습니다: {data}")
    unit = _first_present(data, ("unit", "claimed_unit"))
    return Claim(
        raw_sentence=_first_present(data, ("claim", "raw_sentence", "sentence"), ""),
        claimed_value=_parse_claimed_value(value),
        claimed_unit=unit,
        claimed_period=_first_present(data, ("period", "claimed_period")),
        unit_category=_infer_unit_category(unit),
        direction=_first_present(data, ("direction",)),
    )


# ---------------------------------------------------------------------
# 4번 팀원 출력 -> (ActualEvidence, SearchLog)
#
# 4번이 값을 어떻게 표현하는지 두 가지 모양을 모두 지원한다:
#   (a) 단일 시점: {"value"/"normalized_value": ..., "unit": ...}
#   (b) 다중 시점(비교 필요): {"is_comparison": true,
#        "values": [{"period","value","unit"}, ...]}
# 어느 쪽인지는 "is_comparison" 플래그로 명시적으로 구분한다 - claim이
# 몇 시점을 요구하는지는 4번이 이미 claim을 보고 판단해서 그만큼
# values에 채워 보낸다는 전제.
#
# 판단불가(NOT_FOUND/UNRESOLVED) 여부도 이제 4번 출력 하나에서 전부
# 읽는다 - status류 필드가 있으면 우선 쓰고, 없으면 "값이 있는지"
# 자체를 신호로 삼는다.
# ---------------------------------------------------------------------
def parse_evidence_and_log(payload: JsonLike) -> "tuple":
    data = _as_dict(payload)

    is_comparison = bool(_first_present(data, ("is_comparison",), False))
    raw_points = _first_present(data, ("values", "points"))

    if is_comparison or raw_points:
        points = [
            EvidencePoint(
                period=_first_present(p, ("period",)),
                value=_scale_to_absolute(
                    _first_present(p, ("value", "normalized_value")),
                    _first_present(p, ("unit", "normalized_unit")),
                ),
                unit=_strip_unit_scale(_first_present(p, ("unit", "normalized_unit"))),
            )
            for p in (raw_points or [])
        ]
        evidence = ActualEvidence(
            table_org_id=_first_present(data, ("org_id", "table_org_id")),
            table_tbl_id=_first_present(data, ("table_id", "tbl_id")),
            table_nm=_first_present(data, ("table_name", "table_nm", "item_name")),
            table_purpose=_first_present(data, ("table_purpose", "purpose")),
            is_comparison=True,
            values=points,
        )
    else:
        value = _first_present(data, ("normalized_value", "value", "raw_value"))
        unit = _first_present(data, ("normalized_unit", "unit", "raw_unit"))
        # [2026-08-24 신규 - "역대 최고/최저" claim 배선] local_db_agent.py의
        # _attach_record_extremes가 붙인 "record" 서브딕(있으면)을 그대로
        # ActualEvidence의 record_* 필드로 펼친다 - 없으면(대부분의 claim,
        # 또는 records 테이블에 해당 계열이 없는 경우) 전부 None으로 남아
        # judgment.py가 기존처럼 안전하게 폴백한다.
        record_info = _first_present(data, ("record",)) or {}
        evidence = ActualEvidence(
            value=_scale_to_absolute(value, unit),
            unit=_strip_unit_scale(unit),
            table_org_id=_first_present(data, ("org_id", "table_org_id")),
            table_tbl_id=_first_present(data, ("table_id", "tbl_id")),
            table_nm=_first_present(data, ("table_name", "table_nm", "item_name")),
            table_purpose=_first_present(data, ("table_purpose", "purpose")),
            record_max_value=record_info.get("max_value"),
            record_max_period=record_info.get("max_prd_de"),
            record_min_value=record_info.get("min_value"),
            record_min_period=record_info.get("min_prd_de"),
            record_period_matches_max=record_info.get("claim_period_matches_max"),
            record_period_matches_min=record_info.get("claim_period_matches_min"),
            record_coverage_strt=record_info.get("coverage_strt_prd_de"),
            record_coverage_end=record_info.get("coverage_end_prd_de"),
        )

    # 판단불가 신호 - status류 필드가 명시적으로 있으면 그걸 우선한다.
    # 없으면 "값이 하나도 없다"는 사실 자체를 NOT_FOUND로 본다(4번까지
    # 왔는데 값이 정말 하나도 없다면, 그 이전 어딘가에서 표/컬럼을 아예
    # 못 찾았다는 뜻일 가능성이 높다 - 다만 이건 잠정 추론이라, 4번이
    # 명시적 status를 함께 주는 쪽이 항상 더 정확하다).
    status = _first_present(
        data, ("retrieval_status", "status", "query_status")
    )
    if status in ("success", "resolved", "RESOLVED"):
        retrieval_status = "RESOLVED"
    elif status in ("no_data", "error", "unresolved", "UNRESOLVED"):
        retrieval_status = "UNRESOLVED"
    elif status in ("not_found", "NOT_FOUND", "table_not_found"):
        retrieval_status = "NOT_FOUND"
    elif evidence.value is None and not (evidence.values or []):
        retrieval_status = "NOT_FOUND"
    else:
        retrieval_status = "RESOLVED"

    confident = bool(_first_present(data, ("confident", "selection_confident"), True))
    candidates = _first_present(data, ("candidates_tried", "candidates"), [])
    candidate_names: List[str] = [
        (c.get("table_name") or c.get("table_nm") or c.get("name") or str(c))
        if isinstance(c, dict) else str(c)
        for c in candidates
    ]
    # [2026-08-19 신규 - 설명 문구 정확성 버그 수정] 4번(local_db_agent)의
    # no_data/tie-unconfident success 경로는 candidates_tried 리스트를
    # 명시적으로 안 채우고 table_name 하나만 준다 - 이전엔 그 경우
    # candidate_names가 빈 채로 남아 judgment.py가 "(후보 없음)"이라고
    # 잘못 표시했다(실측: A82ae9f41-C001/A93bfa851-C001 등 - 표/항목을
    # 실제로 찾았는데도 후보가 하나도 없었던 것처럼 설명됨). candidates_tried가
    # 비어 있고 table_name은 있으면 그거라도 보여준다.
    if not candidate_names and evidence.table_nm:
        candidate_names = [evidence.table_nm]
    derivation = _first_present(data, ("derivation",), {}) or {}
    # [2026-08-19 신규 - 위와 같은 버그의 2차 원인] local_db_agent가 왜
    # confident=False인지(동점 후보 나열)/왜 no_data인지(항목은 확정, 시점
    # 데이터만 없음) 설명하는 error_message/confidence_note를 이전엔 아예
    # 안 읽어와서 judgment.py에 뭉뚱그린 문구만 전달됐다.
    detail_note = _first_present(data, ("error_message", "confidence_note"))

    search_log = SearchLog(
        retrieval_status=retrieval_status,
        confident=confident,
        candidates_tried=candidate_names,
        derivation_used=bool(derivation.get("used", False)),
        derivation_note=derivation.get("note"),
        detail_note=detail_note,
    )
    return evidence, search_log


def build_inputs(claim_payload: JsonLike, evidence_payload: JsonLike):
    """claim(1번)과 evidence(4번) 두 조각만 받아 (Claim, ActualEvidence,
    SearchLog)로 변환한다 - 5번이 실제로 받기로 확정한 입력 형태."""
    claim = parse_claim(claim_payload)
    actual, search_log = parse_evidence_and_log(evidence_payload)
    return claim, actual, search_log


# ---------------------------------------------------------------------
# [종합 프로젝트 - 2.5절] claim 라우팅: KOSIS에서 직접 검색해야 하는
# claim과, 이미 찾은 다른 claim들의 조합으로만 검증 가능한 파생 비교값
# claim을 구분한다.
#
# 1번 Task 출력은 같은 원문장에서 여러 claim_id로 쪼개져 나온다(예:
# "재배면적이 10만4943㏊로 작년 10만5959㏊보다 1.0% 감소했다"가 2025년
# 값/2024년 값/1.0% 감소율 3개의 claim_id로 분리됨). 이 중 증감률
# claim("1.0% 감소")은 KOSIS에 그런 컬럼이 애초에 없는 경우가 많아서,
# 검색을 시도하는 대신 이미 찾은 절대값 claim 2개를 judgment.py의
# is_comparison/EvidencePoint 경로로 넘기는 게 맞다 - 검색 자체를
# 아예 하지 말아야 하는 claim을 미리 걸러내는 게 이 함수의 역할이다.
# ---------------------------------------------------------------------
def route_claim_group(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """claim 목록을 "직접 검색 대상"과 "파생 비교값"으로 분류한다.

    판별 기준(추측이 아니라 claim 자체의 필드로 결정론적으로 판별 -
    Decision 003 원칙: 확실하지 않으면 추측하지 않는다):
    - period가 없고(None/빈 값) unit이 "%"인 claim은 "증감률"류 파생값
      후보다.
    - 같은 원문장(claim 텍스트가 동일) 안에, period가 있고 metric이 같은
      절대값 claim이 2개 이상 있으면, 그 파생값 claim은 그 절대값들의
      비교로 검증 가능하다고 보고 "파생 비교값"으로 분류한다.
    - 짝이 되는 절대값 claim이 부족하면(원문장이 다르거나 형제 claim이
      1개 이하) "직접 검색 대상"으로 분류한다 - KOSIS가 등락률 자체를
      공식 컬럼으로 제공하는 지표도 있으므로(예: 소비자물가 상승률),
      "%"에 period가 없다는 사실만으로 무조건 파생값이라고 단정하지
      않는다. 그런 경우는 검색해봐야 알 수 있으므로 안전한 기본값(직접
      검색)으로 둔다.
    - kosis_eligible이 명시적으로 False인 claim은 애초에 검색 대상이
      아니므로 두 버킷 어디에도 넣지 않고 "excluded"로 따로 뺀다(1번
      Task가 이미 KOSIS로 확인 불가능하다고 판단한 claim - 예: 예측치,
      의견성 문장 등을 재추측하지 않는다).

    반환:
        {
          "direct": [claim, ...],
          "derived_comparison": [
              {"claim": claim, "sources": [claim_a, claim_b]}, ...
          ],
          "excluded": [claim, ...],
        }
    """
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for c in claims:
        if c.get("kosis_eligible") is False:
            excluded.append(c)
        else:
            eligible.append(c)

    by_sentence: Dict[str, List[Dict[str, Any]]] = {}
    for c in eligible:
        by_sentence.setdefault(c.get("claim", ""), []).append(c)

    direct: List[Dict[str, Any]] = []
    derived: List[Dict[str, Any]] = []

    for group in by_sentence.values():
        absolute_by_metric: Dict[Any, List[Dict[str, Any]]] = {}
        for c in group:
            period = c.get("period")
            if period not in (None, "null", ""):
                absolute_by_metric.setdefault(c.get("metric"), []).append(c)

        for c in group:
            period = c.get("period")
            unit = str(c.get("unit", "")).strip()
            is_rate_shaped = period in (None, "null", "") and unit == "%"
            siblings = [
                s
                for s in absolute_by_metric.get(c.get("metric"), [])
                if s.get("claim_id") != c.get("claim_id")
            ]
            if is_rate_shaped and len(siblings) >= 2:
                derived.append({"claim": c, "sources": siblings[:2]})
            else:
                direct.append(c)

    return {"direct": direct, "derived_comparison": derived, "excluded": excluded}


# ---------------------------------------------------------------------
# [2026-08-14 - 팀 구조 변경] "5번(나)이 4번 역할(실제 값 조회)도 겸한다"
# 로 결정됨에 따라, 이 어댑터가 두 산출물 파일을 직접 받아 KOSIS 검색/
# 조회(kosis_agent) -> 최종 판정(judgment.judge_claim)까지 한 번에 잇는
# 진입점을 추가한다.
#
# run01_result.jsonl: claim 추출 결과(JSON Lines, claim_id/claim/metric/
#   metric_normalized/value/unit/period/kosis_eligible) - 필드명이
#   parse_claim/route_claim_group이 이미 기대하는 이름과 그대로 맞아서
#   변환 없이 바로 쓴다.
# run03_result.json: 키워드 검색 결과({"claims": [{"claim_id",
#   "matched_keywords", ...}, ...]}) - 실제 수치는 없고 "어떤 phrase가
#   KOSIS에 뭔가 걸렸는지"만 있다. 이 matched_keywords를
#   kosis_agent.KosisInteractiveAgent.process_claim_group_keywords의
#   keywords_by_claim_id 입력으로 그대로 넘겨서, 표 확정 -> 컬럼 확정 ->
#   실제 값 조회를 실제 KOSIS API로 수행한다(README에 이미 있고 실측
#   검증된 경로를 재사용하는 것 - 조회 로직을 새로 만들지 않는다).
# ---------------------------------------------------------------------


def load_claims_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """run01_result.jsonl(JSON Lines, 한 줄에 claim 하나)을 읽어
    claim 딕셔너리 리스트로 반환한다."""
    claims: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                claims.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{file_path} {line_num}번째 줄이 올바른 JSON이 아닙니다: {e}"
                ) from e
    return claims


def load_search_results_json(file_path: str) -> Dict[str, Any]:
    """run03_result.json(claim_id별 키워드 검색 결과)을 읽어 그대로
    반환한다."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_keywords_by_claim_id(search_data: Dict[str, Any]) -> Dict[str, List[str]]:
    """run03_result.json의 claims 배열에서 claim_id -> matched_keywords
    (KOSIS에 실제로 뭔가 걸린 phrase만)를 뽑는다.

    exists=False로 아무것도 안 걸린 키워드까지 넘기면 kosis_agent 쪽
    검색만 불필요하게 늘어나고 결과는 어차피 다시 실패이므로, run03이
    이미 걸러준 matched_keywords만 쓴다.
    """
    by_id: Dict[str, List[str]] = {}
    for claim_result in search_data.get("claims", []):
        claim_id = claim_result.get("claim_id")
        if not claim_id:
            continue
        by_id[claim_id] = list(claim_result.get("matched_keywords") or [])
    return by_id


def run_search_and_judge(
    claims: List[Dict[str, Any]],
    keywords_by_claim_id: Dict[str, List[str]],
    category_hint: Optional[Union[str, List[str]]] = None,
    mode: Optional[Any] = None,
    agent: Optional[Any] = None,
    all_modes: bool = False,
) -> List[Dict[str, Any]]:
    """claim 목록 + claim_id별 검색 키워드로 KOSIS 검색(agent)부터 최종
    판정(judgment.judge_claim)까지 한 번에 수행한다.

    all_modes: [2026-08-24 신규 - 프론트 요구사항: "STRICT/TOLERANCE/
    RAW_ONLY 세 mode 결과를 claim 하나에서 동시에 보여줘야 한다"] 기본값
    False면 기존과 완전히 동일하게 동작한다(mode 파라미터로 지정한 딱
    하나의 mode 결과만 담은 평평한(flat) dict를 claim마다 하나씩 반환 -
    하위 호환, 기존 호출부/테스트 전부 안 바뀜).

    True면 검색/증거 조회(`agent.process_claim_group_keywords`, `build_
    inputs`)는 claim당 여전히 한 번만 하되(이게 비용이 큰 부분 - KOSIS
    검색/HCX 호출), 그 위에서 `judge_claim`을 Mode.STRICT/TOLERANCE/
    RAW_ONLY 세 번 순수 계산만 다시 돌려서(judge_claim 자체는 DB/네트워크
    호출이 없다 - hcx_client도 이 함수는 안 넘기므로 AI 재해석도 안 걸림)
    한 claim의 결과를 `{"claim_id":..., "claim":..., "evidence":{...},
    "modes": {"strict":{...}, "tolerance":{...}, "raw_only":{...}}}`
    구조로 반환한다 - `mode` 파라미터는 all_modes=True일 때는 무시된다.

    NOT_ELIGIBLE/ERROR는 all_modes와 무관하게 항상 평평한 구조 그대로다 -
    둘 다 judge_claim(따라서 mode)에 도달하기 전에 이미 결정되는 상태라서
    (NOT_ELIGIBLE은 1번이 이미 판단, ERROR는 mode와 무관한 예외) "mode별로
    다른 3가지 결과"라는 개념 자체가 성립하지 않는다.

    agent를 인자로 받는 이유: 테스트/모킹 시 실제 API를 호출하지 않는 가짜
    agent를 주입할 수 있게 하기 위함(test_national_derivation_mock.py에서
    쓴 것과 같은 monkeypatch 패턴을 그대로 적용 가능).

    [2026-08-22 변경 - 전체 점검 후 사용자 결정] 기본값(None)이면 예전엔
    라이브 API 기반 `new_kosis_agent.NewKosisAgent()`를 새로 만들었는데,
    그 경로(new_kosis_agent.py/new_kosis_resolution.py/kosis_fetch.py/
    kosis_config.py) 전체가 이 세션의 로컬 DB 기반 접근(kosis_local_
    search.py/local_db_agent.py/hcx_stage1·2·tree_resolver.py)으로
    완전히 대체됐다고 판단해 삭제했다 - 이제 기본값은 `LocalDbAgent()`다
    (db_path 기본값 "kosis_warehouse.db", HCX 폴백/축 리졸버는 전부 꺼진
    literal-only 최소 구성 - HCX까지 켜려면 호출부가 run04_local.py처럼
    agent를 직접 만들어 넘겨야 한다).

    local_db_agent.py가 이미 이 파일의 route_claim_group/_parse_claimed_
    value를 import해서 쓰므로(local_db_agent -> adapter), 여기서
    local_db_agent를 모듈 최상단에서 import하면 순환 import가 된다
    (adapter -> local_db_agent -> adapter). 그래서 함수 안에서만 지연
    import한다(예전 kosis_agent.py 때와 같은 이유, 대상만 바뀜).
    """
    from judgment import Mode, judge_claim

    if agent is None:
        from local_db_agent import LocalDbAgent

        agent = LocalDbAgent()

    if mode is None:
        mode = Mode.TOLERANCE

    claims_by_id = {c["claim_id"]: c for c in claims}

    # process_claim_group_keywords가 내부적으로 route_claim_group을 다시
    # 돌려서 excluded/direct/derived_comparison을 나누고, excluded는
    # {"query_status": "not_eligible"}로, 나머지는 실제 조회/파생 결과로
    # 채운 evidence_by_claim_id를 돌려준다 - 세 버킷 모두 여기서 한 번에
    # 받는다.
    evidence_by_claim_id = agent.process_claim_group_keywords(
        claims, keywords_by_claim_id, category_hint=category_hint
    )

    results: List[Dict[str, Any]] = []
    for claim_id, evidence_payload in evidence_by_claim_id.items():
        c = claims_by_id.get(claim_id)
        if c is None:
            continue

        # "not_eligible"은 parse_evidence_and_log의 상태표에 없는 값이라
        # (value가 없으니) NOT_FOUND로 잘못 뭉개질 수 있다 - 검색을 아예
        # 안 한 것과 검색해서 못 찾은 것은 다른 사유이므로, judgment.py를
        # 거치지 않고 여기서 바로 분리한다.
        if evidence_payload.get("query_status") == "not_eligible":
            # [2026-08-19 신규 - 1번 확정 스키마] exclusion_code가 있으면
            # ("FORECAST"/"PARTIAL_PERIOD"/"AMBIGUOUS_METRIC") 왜 제외됐는지
            # 설명에 그대로 덧붙인다 - 없으면(구 포맷) 기존 문구 그대로.
            # claims_schema_1번_v2.md 참고.
            exclusion_code = (c.get("exclusion_code") or "").strip()
            explanation = (
                "1번 Task가 KOSIS 검증 대상이 아니라고 이미 판단한 "
                "claim(kosis_eligible=False) - 재추측하지 않음."
            )
            if exclusion_code:
                explanation += f" 사유: {exclusion_code}."
            results.append(
                {
                    "claim_id": claim_id,
                    "claim": c.get("claim"),
                    "verdict": "NOT_ELIGIBLE",
                    "explanation": explanation,
                }
            )
            continue

        try:
            claim, actual, search_log = build_inputs(c, evidence_payload)
            evidence_block = {
                "table_org_id": actual.table_org_id,
                "table_tbl_id": actual.table_tbl_id,
                "table_nm": actual.table_nm,
                "retrieval_status": search_log.retrieval_status,
            }
            if all_modes:
                # [2026-08-24 신규] 검색/증거 조회는 위에서 이미 끝났다 -
                # 여기서부터는 judge_claim 순수 계산만 세 번 반복(비용
                # 무시할 만함, DB/네트워크 재호출 없음).
                modes_block = {}
                for m in (Mode.STRICT, Mode.TOLERANCE, Mode.RAW_ONLY):
                    vr = judge_claim(claim, actual, search_log, mode=m)
                    modes_block[m.value] = {
                        "verdict": vr.verdict.value,
                        "explanation": vr.explanation,
                        "claimed_value": vr.claimed_value,
                        "actual_value": vr.actual_value,
                        "hedge_type": vr.hedge_type,
                        "ai_used": vr.ai_used,
                        "ai_note": vr.ai_note,
                    }
                results.append(
                    {
                        "claim_id": claim_id,
                        "claim": c.get("claim"),
                        "modes": modes_block,
                        "evidence": evidence_block,
                    }
                )
            else:
                verdict_result = judge_claim(claim, actual, search_log, mode=mode)
                results.append(
                    {
                        "claim_id": claim_id,
                        "claim": c.get("claim"),
                        "verdict": verdict_result.verdict.value,
                        "explanation": verdict_result.explanation,
                        "claimed_value": verdict_result.claimed_value,
                        "actual_value": verdict_result.actual_value,
                        "hedge_type": verdict_result.hedge_type,
                        "mode": mode.value if hasattr(mode, "value") else str(mode),
                        "ai_used": verdict_result.ai_used,
                        "ai_note": verdict_result.ai_note,
                        "evidence": evidence_block,
                    }
                )
        except Exception as e:
            # Decision 003: 판정 중 뭔가 예외가 나면 조용히 삼키지 않고
            # ERROR로 명시해서 남긴다(추측으로 다른 verdict를 채우지 않음).
            # all_modes 여부와 무관하게 항상 평평한 구조 - 예외는 mode별로
            # 다르게 나는 게 아니라 claim 하나 처리 자체가 실패한 것이다.
            results.append(
                {
                    "claim_id": claim_id,
                    "claim": c.get("claim"),
                    "verdict": "ERROR",
                    "explanation": f"판정 중 예외 발생: {e}",
                }
            )

    return results


def run_pipeline_from_files(
    claims_jsonl_path: str,
    search_json_path: Optional[str] = None,
    output_path: Optional[str] = None,
    category_hint: Optional[Union[str, List[str]]] = None,
    mode: Optional[Any] = None,
    agent: Optional[Any] = None,
    all_modes: bool = False,
) -> List[Dict[str, Any]]:
    """run01_result.jsonl(+ run03_result.json) 파일 경로를 받아 검색
    (4번 역할) + 판정(5번 역할)까지 전부 수행한다. output_path가 있으면
    결과를 JSON으로 저장까지 한다.

    all_modes: run_search_and_judge에 그대로 전달(설명은 그쪽 docstring
    참고) - True면 output_path에 저장되는 JSON도 claim당 "modes" 중첩
    구조로 저장된다.

    [2026-08-24 변경 - 담당 범위 정정("run02/03도 우리 소관") 후 run03
    자체를 대체하는 stage1_keywords="llm_table_select"/"metric_normalized"
    경로가 자리잡으면서, run03_result.json 없이도 파이프라인 전체가
    동작해야 한다는 게 실제로 확인됨] search_json_path를 Optional로
    바꿨다 - None이면 run03 기반 matched_keywords 없이(빈 dict) 진행한다.
    이 값은 LocalDbAgent가 stage1_keywords="run03"일 때만 실제로 읽으므로,
    그 모드를 안 쓰는 호출부(run04_local.py 등)는 이제 run03_result.json
    자체가 필요 없다. 기존 호출부(문자열 경로를 넘기는 곳)는 동작 그대로."""
    claims = load_claims_jsonl(claims_jsonl_path)
    if search_json_path:
        search_data = load_search_results_json(search_json_path)
        keywords_by_claim_id = build_keywords_by_claim_id(search_data)
    else:
        keywords_by_claim_id = {}

    results = run_search_and_judge(
        claims,
        keywords_by_claim_id,
        category_hint=category_hint,
        mode=mode,
        agent=agent,
        all_modes=all_modes,
    )

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    return results


if __name__ == "__main__":
    import sys

    # interface.py는 e2e 쪽에만 있다(test/는 독립된 core-engine 사본이라
    # interface.py 없이도 그대로 동작해야 한다) - 있으면 기본 경로로 쓰고,
    # 없으면 조용히 넘어간다.
    try:
        import interface as _interface
    except ImportError:
        _interface = None

    claims_path = search_path = out_path = None
    if len(sys.argv) >= 3:
        # 실사용: python3 adapter.py run01_result.jsonl run03_result.json [output.json]
        # 실제 KOSIS/HCX API 키가 .env에 있어야 한다.
        claims_path = sys.argv[1]
        search_path = sys.argv[2]
        out_path = sys.argv[3] if len(sys.argv) > 3 else (
            str(_interface.PIPELINE04_PATH) if _interface else "verification_result.json"
        )
    elif len(sys.argv) == 1 and _interface is not None:
        # 인수 없이 실행 - interface.py 기본 경로(루트에 모은 산출물) 사용
        # (2026-08-14 결정: "일단 루트에 모아줘")
        claims_path = str(_interface.PIPELINE01_PATH)
        # [2026-08-24 변경 - 담당 범위 정정 반영] interface.PIPELINE03_PATH
        # 제거됨(interface.py 2026-08-24 항목 참고) - 이 bare 진입점은 여전히
        # agent=None -> LocalDbAgent() 기본값(stage1_keywords="run03", HCX
        # 없음, run04_local.py 문서가 말하는 "최소 구성(literal-only)")이라
        # run03_result.json이 실제로 필요하다. 리터럴 문자열로 직접 씀.
        search_path = "run03_result.json"
        out_path = str(_interface.PIPELINE04_PATH)

    if claims_path:
        pipeline_results = run_pipeline_from_files(
            claims_path, search_path, output_path=out_path
        )
        verdict_counts: Dict[str, int] = {}
        for r in pipeline_results:
            verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1
        print(f"총 {len(pipeline_results)}건 처리 - {verdict_counts}")
        print(f"결과 저장: {out_path}")
        for r in pipeline_results:
            print(f"  [{r['verdict']}] {r['claim_id']} - {(r.get('explanation') or '')[:60]}")
        raise SystemExit(0)

    from judgment import Mode, judge_claim

    # (1) 단일 시점 케이스 - 최저임금류
    claim1 = {
        "claim": "내년도 최저임금은 시간당 9,860원으로 결정됐다",
        "value": 9860,
        "unit": "원",
        "period": "2026",
    }
    evidence1 = {
        "table_id": "DT_2OEEM1012",
        "table_name": "지방자치단체 외 최저임금 및 영향률",
        "normalized_value": 9860,
        "normalized_unit": "원",
        "query_status": "success",
    }
    c, a, s = build_inputs(claim1, evidence1)
    r = judge_claim(c, a, s, mode=Mode.TOLERANCE)
    print("[단일 시점]", r.verdict.value, "|", r.explanation)

    # (2) 다중 시점(증감) 케이스 - 취업자 수 감소, 4번이 is_comparison
    #     플래그와 values 2개를 채워 보낸 경우
    claim2 = {
        "claim": "2025년 1월 취업자 수는 13만 명 감소했다",
        "value": 130000,
        "unit": "명",
        "period": "2025-01",
        "direction": "decrease",
    }
    evidence2 = {
        "table_id": "DT_1DA7001S",
        "table_name": "성별 경제활동인구 총괄",
        "is_comparison": True,
        "values": [
            {"period": "2025-01", "value": 27748000, "unit": "명"},
            {"period": "2024-01", "value": 27878000, "unit": "명"},
        ],
        "query_status": "success",
    }
    c2, a2, s2 = build_inputs(claim2, evidence2)
    r2 = judge_claim(c2, a2, s2, mode=Mode.TOLERANCE)
    print("[증감 비교]", r2.verdict.value, "|", r2.explanation)

    # (3) 4번이 아예 못 찾은 케이스
    evidence3 = {"query_status": "error"}
    c3, a3, s3 = build_inputs(claim1, evidence3)
    r3 = judge_claim(c3, a3, s3, mode=Mode.TOLERANCE)
    print("[조회 실패]", r3.verdict.value, "|", r3.explanation)
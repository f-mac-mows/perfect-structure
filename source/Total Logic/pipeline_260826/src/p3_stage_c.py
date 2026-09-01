# -*- coding: utf-8 -*-
"""P3 Stage C — 룰 검증기 (§5.6): 역검증·metric 실존·교차검증·감사 플래그·패스스루 스모크.

역할 구분:
- 파괴적 검사(destructive_issues): 위반 시 해당 item을 수리/폐기 경로로 보낸다
  (역검증 실패 = 환각 값, metric 창작 어휘, 스키마 형식 위반)
- 감사 플래그(audit_flags): 파괴하지 않고 trace에 남긴다
  (forecast 사전 히트↔N 불일치 — 자동 승격은 골든 역행이라 금지(§5.6),
   value_type·direction 룰 교차검증 불일치)

패스스루 스모크(§5.6 구현 순서 ③): 골든 508 Claim을 이 검증기에 통과시켜
룰이 정답을 파괴하지 않는지 HCX 0콜로 확인 — `python -m src.p3_stage_c --passthrough`.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from src.p3_schemas import ClaimRecord

RE_DIGIT = re.compile(r"\d")

# 비통계 주장 의심 신호(59차 도입 · 61차 정밀도 실측으로 축소) — **비파괴 플래그 전용**.
# 룰로 통계/비통계를 가르는 것은 불가능하다(골든 NON_STAT 293문장 중 167건이 표면 패턴
# 무신호, '%'·'억원'이 양쪽에 공존). 그래서 "거른다"가 아니라 "표시만 한다".
#
# ★ 61차 정밀도 실측(test 43)으로 신호 2종을 **제거**했다 — Recall이 우선이라
#   오탐이 많은 신호는 정상 Claim의 검증 기회를 빼앗는다:
#     statutory 45% — '법인세율'(제도)과 '법인세 세수'(통계)를 어휘로 가를 수 없다
#     quoted    20% — "통계청은 …라고 밝혔다"가 걸린다. **공식 통계 발표 인용이
#                     가장 신뢰할 Claim인데** 그걸 의심 대상으로 만든다(최악의 오탐)
NON_STAT_SIGNALS = (
    # 상거래·요금 — 개별 상품/서비스 가격은 공식 통계 대상이 아니다(유튜브 구독료 8500원)
    # 정밀도 82% (11건 중 9건이 골든 비통계)
    ("commerce", ("구독료", "이용료", "수수료", "보상금", "위약금", "할인율", "판매가", "정가")),
    # 개별 기업 재무 — 기관 생산 통계가 아니다(CJ건설 이자 비용 31억원). 정밀도 100%
    ("corporate", ("영업이익", "당기순이익", "이자 비용", "부채비율", "시가총액", "지분율")),
)

# 전망 표현 사전(26차 판례 + §5.6) — 감사 플래그 전용. 자동 승격 금지.
# (리뷰 실측: 플래그 25건 중 골든 오류 0 — 형제 Claim 반응·명사 '계획' 오탐이 대부분.
#  '~겠다'류 추가는 소음 증가 트레이드오프라 보류, '게 됐'·'앞두'만 보강)
FORECAST_LEXICON = (
    "전망", "예상", "예측", "추산", "계획", "목표", "할 것", "될 것", "넘어설 것",
    "가능성", "우려", "변수", "유력", "달할 것", "이를 것", "게 됐", "앞두",
)
_INCREASE = ("증가", "늘어", "늘었", "상승", "올랐", "오르", "오른", "올라", "높아",
             "확대", "급증", "뛰", "불어나", "커졌")
_DECREASE = ("감소", "줄어", "줄었", "하락", "떨어", "내렸", "낮아", "축소", "급감",
             "밑돌", "위축")
_DIR_WORDS = "|".join(_INCREASE + _DECREASE)


def _value_pattern(value: str, unit: str) -> re.Pattern:
    """value+unit 실존 매칭 패턴 — 역검증과 원문 위치 탐색이 공유."""
    v = (value or "").replace(" ", "")
    u = (unit or "").replace(" ", "")
    vpat = r"\s*".join(re.escape(ch) for ch in v)
    # 나열 압축 표기(89차): "아이슬란드·벨기에의 46·64% 수준" = 46%와 64%.
    # 단위를 뒤에 한 번만 쓰는 한국 기사 관례라, value와 unit 사이에 가운뎃점으로
    # 이어진 다른 값이 낄 수 있다. 이걸 막으면 정당한 Claim이 환각으로 폐기된다.
    listpat = r"(?:[·ㆍ]\s*[\d.,]+)*" if u else ""
    # 값과 단위 사이에 낀 근사 접미사('84만여 개'·'3만남짓 대')를 건너뛴다. 근사어는
    # 규약상 value에 넣지 않으므로(§4.1) 검증이 막으면 정당한 Claim이 폐기된다.
    approxpat = r"(?:여|남짓|가량|여|째)?" if u else ""
    upat = (listpat + approxpat + r"\s?" +
            r"\s*".join(re.escape(ch) for ch in u)) if u else ""
    guard_l = r"(?<![\d.,만억조천])"
    guard_r = r"(?![\d만억조천])" if not u else ""   # 문장부호는 정당한 우측 경계
    return re.compile(guard_l + vpat + upat + guard_r)


def value_in_sentence(value: str, unit: str, sentence: str) -> bool:
    """역검증(§4.1) — value+unit 결합(공백 무시)이 문장에 실존해야 한다.

    좌측 숫자 경계 필수: '3%'⊂'8.3%', '300조원'⊂'1300조원' 같은 절단 환각이
    부분 문자열로 통과하면 역검증이 무력화된다(리뷰 실측 반례). unit이 없으면
    우측도 수사 경계('13만'⊂'13만4000', '1300'⊂'1300조원' 차단).
    원문 위에서 매칭한다 — 공백을 지우고 비교하면 나열 쉼표("21.8%, 9.2%")가
    천 단위 쉼표("1,300조")와 구분되지 않아 정당한 값이 차단된다.
    """
    if not value:
        return False
    return bool(_value_pattern(value, unit).search(sentence or ""))


def value_position(value: str, unit: str, sentence: str, start: int = 0) -> int | None:
    """value+unit의 원문 등장 위치(§5.6 출력 순서 계약 검사용). start 이후 첫 매치."""
    if not value:
        return None
    m = _value_pattern(value, unit).search(sentence or "", start)
    return m.start() if m else None


def _word_exists(word: str, text: str) -> bool:
    # 숫자로 시작하는 어휘는 단순 substring이 '12·3등급'⊃'2등급' 오허용을 만들므로
    # 경계 있는 나열 패턴만 사용(판례 4 준용: "2·3·4등급" = 각 등급 실존)
    m = re.fullmatch(r"(\d+)(\D+)", word)
    if m:
        num, suf = m.groups()
        pat = re.compile(
            rf"(?<![\d.])(?:\d+\s*[·,~/／]\s*)*{re.escape(num)}(?:\s*[·,~/／]\s*\d+)*\s*{re.escape(suf)}")
        return bool(pat.search(text))
    return word in text


# 지표 문법 접미사 — '무엇을 세는가'를 나타내는 문법 요소다. 사실 주장이 아니므로
# 기사 실존을 요구할 이유가 없다(61차 실측: 창작 판정 35건 중 24건이 이 유형).
METRIC_SUFFIX = ("수", "율", "률", "액", "량", "비율", "성장률", "증가율", "감소율", "증감률",
                 "점유율", "금액", "건수", "규모", "지수", "가격", "단가", "인원", "대수",
                 "증가액", "감소액", "총액", "평균", "합계", "비중", "물량", "수량",
                 "증가분", "감소분")
# 기능어 — 조사·접속사. 지표의 내용이 아니다('자동차 및 부품 소매'의 '및').
METRIC_STOPWORDS = ("및", "등", "의", "과", "와", "를", "을", "은", "는", "이", "가", "중")
# 활용 어미 — '정상화된' = '정상화' + '된'
METRIC_ENDINGS = ("된", "한", "할", "하는", "인", "되는", "적", "별", "당")


def _metric_word_ok(word: str, article_text: str) -> bool:
    """어휘가 기사에 실존하거나, 지표 문법 요소로 설명되는가."""
    if _word_exists(word, article_text) or word in METRIC_SUFFIX or word in METRIC_STOPWORDS:
        return True
    for s in sorted(METRIC_SUFFIX, key=len, reverse=True):     # 접미사 떼면 어간이 실존
        if word.endswith(s) and len(word) > len(s):
            stem = word[:-len(s)]
            if _word_exists(stem, article_text) or stem in METRIC_SUFFIX:
                return True
    for e in sorted(METRIC_ENDINGS, key=len, reverse=True):    # 활용 어미 절단
        if word.endswith(e) and len(word) > len(e) and _word_exists(word[:-len(e)], article_text):
            return True
    return False


# 인용부호는 어휘의 일부가 아니다. 기사는 통계 항목명을 따옴표로 감싸는 일이 잦고
# (‘식료품 및 비주류 음료’ 물가지수), LLM은 그것을 곧은 따옴표로 바꿔 낸다. 종전 분리자는
# 따옴표를 안 잘라서 "'식료품"이 통째로 한 어휘가 되고, 기사에는 ‘식료품이 있으니 실존
# 검사가 실패해 **정상 어휘가 창작으로 몰려 잘려 나갔다**(실측: '식료품 및 비주류 음료 물가'
# → '및 비주류 물가지수'). 어휘 양끝의 인용·괄호류를 벗기고 센다.
_METRIC_SPLIT = re.compile(r"[\s()·,]+")
_METRIC_EDGE = "‘’“”'\"「」『』〈〉《》[]<>"


def _metric_tokens(metric: str) -> list[str]:
    return [t for t in (w.strip(_METRIC_EDGE) for w in _METRIC_SPLIT.split(metric or "")) if t]


# ── 제도 기준값 판정(ⓑ · 105차) ─────────────────────────────────────────
# test 40 블라인드에서 드러난 최대 결함: 오검출 101건 중 **제도가 정한 기준값**이 49건
# (법인세율 22% · 보험료율 9% · 소득대체율 43% · 출국 납부금 7000원 · 대출 한도 6억원).
# 프롬프트 rule 1이 이미 명시한 범주인데 모델이 지키지 않는다.
#
# 62차에 `statutory` 신호를 **문장 기준**으로 재고 정밀도 45%로 기각했었다. 여기서는
# **metric 기준**으로 본다 — 문장에는 제도와 실적이 함께 나오지만("법인세율을 올려
# 세수를 늘린다") metric은 둘 중 하나를 가리키기 때문이다.
# 걷힌 실적('법인세 세수'·'관세 수입')은 통계이므로 명시적으로 되돌린다.
_STATUTORY_METRIC = re.compile(
    r"(세율|요율|보험료율|소득대체율|수수료율|할인율|관세율|공제율|공제 한도"
    r"|대출\s?한도|한도액|납부금|구독료|이용료|수수료|기준소득월액)")
_STATUTORY_EXEMPT = re.compile(r"(세수|징수|수입액|납부액|판매액|매출|거래액|결제액)")


def is_statutory_value(metric: str, sentence: str = "") -> bool:
    """제도·계약이 정한 기준값인가(= 세상 상태에 대한 통계 주장이 아니다)."""
    m = (metric or "").strip()
    if not m or not _STATUTORY_METRIC.search(m):
        return False
    return not _STATUTORY_EXEMPT.search(m)


# ── 모집단 한정어 보강(R5 · 102차) ─────────────────────────────────────────
# metric 불일치 173건을 전수 판정한 결과 **위험 58건의 대부분이 한 유형**이었다:
# 문장에 있는 모집단 한정어가 metric에서 빠져 더 넓은 값을 조회하게 되는 것
# ('서울 외국인주민 45만888명' → '외국인 주민 수'로 조회하면 전국 258만과 대조된다).
#
# 프롬프트 축(v2.3/v2.4)은 목표는 맞혔지만(한정어 20/58 복구) period·eligible·cb·재현율을
# 함께 떨어뜨려 되돌렸다. 룰로 이룬다 — 붙이는 어휘가 **문장에 실존**하므로 §4.4의
# '조합 허용·창작 금지'를 위반하지 않는다.
#
# ★ 게이트는 **인접성**이다: 한정어가 그 값 **바로 앞**(30자 이내)에 있을 때만 붙인다.
#   골든의 실제 모양이 '서울(45만888명)'·'중소기업은 298억달러'라 이 조건이 곧 정답 신호다.
#   문장 단위 유일성만으로 걸렀을 때는 골든 9건을 훼손했다 —
#     · '폐암은 매년 3만명 … 서울 대형 병원이'의 '서울'을 모집단으로 오인
#     · 문장 어디에 있든 잡히니 여러 모집단 중 엉뚱한 것이 붙음
#   인접 게이트는 둘 다 없앤다(dev 실측: 발동 15 · +6 / **−0**, 골든 패스스루 파괴 0).
# metric에 이미 한정어가 있으면 손대지 않는다('10~19세 …'에 '10대'를 덧붙이던 버그 차단).
# 시·도 중 '경기'·'광주'는 동음이의('건설 경기 불황')라 목록에서 뺐다.
_SIDO = ("서울", "부산", "대구", "인천", "대전", "울산", "세종", "강원",
         "충북", "충남", "전북", "전남", "경북", "경남", "제주")
_POP_PATTERNS = (
    re.compile(r"(대기업|중견기업|중소기업)"),
    re.compile(r"(상위\s?\d+대(?:\s?기업)?)"),
    re.compile(r"(\d+대\s?은행)"),
    re.compile(r"(\d+\s?~\s?\d+세)"),
    re.compile(r"(\d+세\s?(?:미만|이상))"),
    re.compile(r"(?<![\d])(\d0대)(?![\d])"),
    re.compile(r"(?:^|[\s(,])(" + "|".join(_SIDO) + r")(?=[\s(]|은|는|이|가|의|도)"),
)
POP_WINDOW = 30


def population_qualifiers(text: str, with_pos: bool = False):
    """모집단 한정어 목록(등장 순서·중복 제거). with_pos면 (어휘, 시작 위치)."""
    out: list = []
    seen: set[str] = set()
    for pat in _POP_PATTERNS:
        for m in pat.finditer(text or ""):
            q = m.group(1).strip()
            if q in seen:
                continue
            seen.add(q)
            out.append((q, m.start(1)) if with_pos else q)
    return out


def add_population_qualifier(metric: str, sentence: str, value: str, unit: str) -> str:
    """값 바로 앞의 모집단 한정어를 metric에 붙인다. 조건 미충족이면 그대로 둔다."""
    m = (metric or "").strip()
    if not m or population_qualifiers(m):
        return metric
    pos = value_position(value, unit, sentence)
    if pos is None:
        return metric
    near = [q for q, qp in population_qualifiers(sentence, with_pos=True)
            if 0 <= pos - (qp + len(q)) <= POP_WINDOW]
    return f"{near[0]} {m}" if len(near) == 1 else metric


def metric_missing_words(metric: str, article_text: str) -> list[str]:
    """구성 어휘 실존(§4.4) — metric의 각 어휘가 기사 정제본에 있어야 한다(조합 허용·창작 금지).

    **지표 문법 요소는 면제한다(61차)**: `성장률`·`감소액`·`비율`·`건수` 같은 접미사는
    지표를 만드는 문법이지 기사가 주장하는 사실이 아니다. 이걸 창작으로 보면
    `외국인 주민 비율`·`데이터센터 산업 성장률` 같은 정상 metric이 폐기된다(실측 24/35).
    **대상·개체를 가리키는 어휘의 창작은 여전히 금지**된다 — 실존 검증의 본래 목적이
    '기사에 없는 대상을 지어내지 않는다'이기 때문이다.
    골든 508건은 면제 전후 모두 전건 통과 = 완화는 통과 범위를 넓히기만 한다(회귀 위험 0).
    """
    return [w for w in _metric_tokens(metric) if not _metric_word_ok(w, article_text or "")]


def strip_coined_words(metric: str, article_text: str) -> str:
    """창작 어휘를 제거한 metric. 남는 게 없으면 빈 문자열.

    사용자 결정(61차): 창작 어휘는 허용하지 않는다 — metric의 기사 실존 검증이
    '재검토'의 의미를 갖기 때문이다. 맥락 보강은 `metric_normalized`가 담당한다.
    원표기는 `claims_trace.jsonl`에 보존해 5번이 복구할 수 있게 한다.
    """
    return " ".join(w for w in _metric_tokens(metric)
                    if _metric_word_ok(w, article_text or ""))


def destructive_issues(claim: ClaimRecord, sentence: str, article_text: str) -> list[str]:
    """위반 시 수리/폐기 대상이 되는 검사 — 골든 패스스루에서 0건이어야 한다."""
    issues = list(claim.schema_issues())
    # value는 §4.1상 '수치 표현부'다. 역검증은 "문장에 실존하는가"만 보므로
    # '크게'·'급증'·'작년보다 증가' 같은 서술어도 통과해 버린다(test 43 실측 5건, 1건은
    # eligible=true로 유출). 골든 508건은 전건이 숫자를 포함하므로 회귀 위험이 없다.
    # 무수치 방향·최상급 주장은 ver1 범위 밖이라는 정책(§4.7.5)과도 일치한다.
    if not RE_DIGIT.search(claim.value or ""):
        issues.append(f"value에 수치 없음: {claim.value!r} (수치 표현부 필수 — §4.1)")
    if not value_in_sentence(claim.value, claim.unit, sentence):
        issues.append(f"역검증 실패: '{claim.value}{claim.unit}' 문장 미실존")
    missing = metric_missing_words(claim.metric, article_text)
    if missing:
        issues.append(f"metric 창작 어휘: {missing}")
    return issues


# ── 룰 1차 분류기 (교차검증용 — LLM 판정과 대조, 불일치는 플래그) ──────────
def rule_direction(sentence: str) -> str | None:
    """어휘 기반 증감 방향. 양쪽 신호 혼재·무신호면 None(판정 보류)."""
    inc = any(k in sentence for k in _INCREASE)
    dec = any(k in sentence for k in _DECREASE)
    if inc and not dec:
        return "increase"
    if dec and not inc:
        return "decrease"
    return None


def rule_value_type(sentence: str, value: str, unit: str) -> str | None:
    """어휘·unit 패턴 기반 1차 분류. 확신 없으면 None.

    리뷰 계통 오류 반영: ① 증감 창은 소수점('(3.8%) 증가')을 통과하되 문장 경계는 막음
    ② %의 change_rate 판정은 값 '근접' 방향어만(사이에 다른 숫자가 끼면 보류 —
      '45.6%로 1%포인트 하락'의 45.6을 change_rate로 오발하던 결함)
    ③ '(으)로' 도달 구문은 증감액 배제.

    **62차 관례 변경**: "시점값 비율은 `level`"(사용자 결정) — 구성비·점유율도 그 시점의
    수준값이므로 `share_ratio`를 쓰지 않는다(골든 60행을 level로 일괄 정정, 잔여 0건).
    비율 문맥은 이제 `level`로 분류한다 — 룰이 골든에 없는 값을 예측하면 교차검증이
    영구 불일치가 되기 때문이다.
    """
    u = (unit or "").strip()
    v = (value or "").strip()
    if not v:
        return None
    v_esc = re.escape(v)
    win = r"(?:[^.]|\.(?=\d)){0,15}"          # 소수점만 통과하는 창(문장 종결 '.'은 차단)
    if u in ("%", "％"):
        if re.search(rf"(전체|GDP|국내총생산)(의|에서)\s*{v_esc}\s*%", sentence):
            return "level"
        if re.search(rf"{v_esc}\s*%[^\d.]{{0,10}}(비중|비율|점유율|차지)", sentence) \
                or re.search(rf"(비중|비율|점유율)[^\d.]{{0,10}}{v_esc}\s*%", sentence):
            return "level"
        if re.search(rf"{v_esc}\s*%[^\d.]{{0,10}}(?:{_DIR_WORDS})", sentence):
            return "change_rate"              # 값 바로 뒤 방향어(사이 숫자 없음)만
        return None
    if u in ("%p", "%P", "%포인트", "포인트") \
            and re.search(rf"{v_esc}\s*{re.escape(u)}{win}(?:{_DIR_WORDS})", sentence):
        return "change_amount"
    if u and rule_direction(sentence):
        if re.search(rf"{v_esc}\s*{re.escape(u)}(?!\s*[으]?로(?![\d]))" + win + rf"(?:{_DIR_WORDS})",
                     sentence):
            return "change_amount"
        return "level"
    return None


def audit_flags(claim: ClaimRecord, sentence: str) -> list[str]:
    """비파괴 감사 — trace 기록용."""
    flags = []
    if (claim.forecast or "N").upper() == "N" and any(k in sentence for k in FORECAST_LEXICON):
        flags.append("forecast_lexicon_hit_but_N")
    rd = rule_direction(sentence)
    if rd and claim.direction and rd != claim.direction:
        flags.append(f"direction_rule_mismatch:rule={rd},llm={claim.direction}")
    rv = rule_value_type(sentence, claim.value, claim.unit)
    if rv and claim.value_type and rv != claim.value_type:
        flags.append(f"value_type_rule_mismatch:rule={rv},llm={claim.value_type}")
    flags += non_stat_suspect_flags(claim, sentence)
    return flags


def non_stat_suspect_flags(claim: ClaimRecord, sentence: str) -> list[str]:
    """비통계 주장 의심 — 플래그만 남긴다(폐기·eligible 변경 없음).

    Stage B의 LLM이 NON_STAT_NUMBER 판정을 단독으로 하고 아무도 재검증하지 않는데,
    실측(test 43)에서 골든이 비통계로 뺀 41건이 Claim으로 유출됐고 그중 9건이
    `eligible=true`로 KOSIS 조회 대상까지 갔다. 룰로 가를 수는 없으므로 신호만 남겨
    5번의 '판별 보류' 근거와 프롬프트 개선 표본으로 쓴다.
    """
    hits = [name for name, kws in NON_STAT_SIGNALS
            if any(k in sentence or k in (claim.metric or "") for k in kws)]
    return [f"non_stat_suspect:{','.join(hits)}"] if hits else []


# ── 골든 패스스루 스모크 (§5.6 구현 순서 ③) ───────────────────────────────
def run_passthrough(golden_path=None, articles_path=None):
    from src import config
    from src.p3_golden import load_golden, GOLDEN_DEFAULT

    articles_path = articles_path or (config.data_dir() / "articles_clean.jsonl")
    gold = load_golden(golden_path or GOLDEN_DEFAULT)
    arts = {}
    with open(articles_path, encoding="utf-8") as f:
        for line in f:
            a = json.loads(line)
            arts[a["article_id"]] = a["text"]

    destroyed: list[tuple[str, list[str]]] = []
    flags_count: dict[str, int] = {}
    dir_agree = dir_total = vt_agree = vt_total = 0
    for c in gold.claims:
        issues = destructive_issues(c, c.claim, arts.get(c.article_id, ""))
        if issues:
            destroyed.append((c.claim_id, issues))
        for fl in audit_flags(c, c.claim):
            flags_count[fl.split(":")[0]] = flags_count.get(fl.split(":")[0], 0) + 1
        rd = rule_direction(c.claim)
        if c.direction and rd:
            dir_total += 1
            dir_agree += (rd == c.direction)
        rv = rule_value_type(c.claim, c.value, c.unit)
        if c.value_type and rv:
            vt_total += 1
            vt_agree += (rv == c.value_type)

    # E 사영까지 — 골든 전건이 계약 위반 없이 7필드로 나가는지 + eligible 총계
    handoffs = [c.to_handoff() for c in gold.claims]
    n_eligible = sum(1 for h in handoffs if h["kosis_eligible"])
    excluded_bad = [e for e in gold.excluded if e.schema_issues()]
    return {
        "claims": len(gold.claims), "excluded": len(gold.excluded),
        "destroyed": destroyed, "excluded_bad": excluded_bad,
        "handoff_ok": len(handoffs), "eligible_true": n_eligible,
        "audit_flags": flags_count,
        "direction_rule_agreement": (dir_agree, dir_total),
        "value_type_rule_agreement": (vt_agree, vt_total),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage C 룰 — 골든 패스스루 스모크")
    ap.add_argument("--passthrough", action="store_true")
    ap.add_argument("--golden", type=Path, default=None)
    args = ap.parse_args()
    if not args.passthrough:
        ap.error("--passthrough 를 지정하세요")
    r = run_passthrough(args.golden)
    print(f"골든 Claim {r['claims']} · 제외 {r['excluded']}")
    print(f"파괴된 Claim: {len(r['destroyed'])}건")
    for cid, issues in r["destroyed"][:20]:
        print(f"  ✗ {cid}: {issues}")
    print(f"제외 코드 위반: {len(r['excluded_bad'])}건")
    print(f"7필드 사영: {r['handoff_ok']}건 전부 성공 · eligible TRUE {r['eligible_true']}")
    print(f"감사 플래그 분포: {r['audit_flags']}")
    da, dt_ = r["direction_rule_agreement"]
    va, vt_ = r["value_type_rule_agreement"]
    print(f"direction 룰↔골든 합치: {da}/{dt_} ({da / dt_:.1%})" if dt_ else "direction 표본 없음")
    print(f"value_type 룰↔골든 합치: {va}/{vt_} ({va / vt_:.1%})" if vt_ else "value_type 표본 없음")
    ok = not r["destroyed"] and not r["excluded_bad"]
    print(f"\n패스스루 {'통과' if ok else '실패'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

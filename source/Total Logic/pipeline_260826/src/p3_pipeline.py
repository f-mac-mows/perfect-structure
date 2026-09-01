# -*- coding: utf-8 -*-
"""P3 파이프라인 오케스트레이터 — A(필터) → B(추출기 주입) → C(룰 검증·해소) → E(산출).

Stage B는 `extractor` 콜러블로 주입한다:
    extractor(cand: SentenceCandidate) -> list[dict]   # 문장당 raw item 목록
raw item 필드: kind(claim|excluded) · exclusion_code · forecast(Y/N — claim 필수) · metric ·
value · unit · period(표면형 period_expr 또는 이미 해소된 표기) · value_type · direction ·
comparison_basis · note · metric_normalized(선택)

라우팅 규약(리뷰 반영):
- kind ∉ {claim, excluded} → errors (미지 kind가 eligible=true Claim으로 폴스루 금지)
- kind=claim인데 exclusion_code가 제외 코드 → errors (kind×code 모순 = 저신뢰 신호, 수리 대상)
- kind=claim인데 forecast ∉ {Y,N} → errors (§5.6 필수 2값 — 기본값 부여는 위험 방향)
- kind=excluded + PARTIAL_PERIOD → errors (§4.8: 제외 코드 아님 — 5단계 수리 규칙 1순위 예약)
- PARTIAL_PERIOD 마킹은 LLM 값을 쓰지 않고 resolver의 partial에서 재계산(골든 전건 일치 실증)
- 앵커 시프트("전년동기")는 원문 순서상 직전의 해소된 형제 period(없으면 첫 후행값) 기준,
  partial(부분기간) 앵커 허용 — "올 들어 20일까지 … 전년 동기 대비"가 골든 실사례(s010)
- claim_id 일련번호는 claim-kind item 슬롯이 소모한다 — 한 item이 검증 실패로 빠져도
  뒤 Claim들의 id가 밀리지 않는다(재실행 안정성, §4.1 조인 키 보호)
- 서킷브레이커: 오류 문장이 배치의 3% 초과 → 파이프라인 실패(§5.6 — 계통 결함 신호)

실 HCX 백엔드·수리 루프·record-replay 캐시 래퍼는 5단계에서 extractor 바깥에 씌운다.
지금은 stub(골든 라벨 반환)으로 A→E 전 구간을 검증한다(§5.6 무HCX 스모크 ③).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from src.p3_schemas import ClaimRecord, ExcludedRecord, PIPELINE_VERSION, \
    CONTRACT_EXCLUSION_CODES, CLAIM_ALLOWED_CODES, is_std_period, is_valid_period_form
from src.p3_stage_a import SentenceCandidate, collect_candidates
from src.p3_period import (resolve_period, resolve_comparison, yoy_of, prev_slot,
                           ANCHOR_SHIFT_EXPRS, SAME_PERIOD_EXPRS, Resolved)
from src.p3_stage_c import (destructive_issues, audit_flags, value_position, rule_direction,
                            metric_missing_words, strip_coined_words, value_in_sentence,
                            add_population_qualifier, is_statutory_value)
from src.p3_emit import emit_all, AccountingError

Extractor = Callable[[SentenceCandidate], list[dict]]
CIRCUIT_BREAKER_RATE = 0.03
# cb 백필용 — 비교 기준은 비교 조사와 **인접 결합**된 형태만("전년 동월 대비"·"작년보다"·
# "1년 전보다"·"전월 대비"). '지난달'·'작년 7월(…)부터' 같은 대상 시점 표현을 배제한다(91차).
_CB_BACKFILL_YOY = re.compile(
    r"(?:전년|(?<!재)작년|지난해|1년\s?전)[^,.·]{0,4}?(?:대비|보다|비해|동기|동월|동분기|같은\s?(?:기간|달))")
_CB_BACKFILL_PREV = re.compile(r"(?:(?<!전)전월|전\s?분기|직전\s?분기|(?<!전)전기)\s?(?:대비|보다|비해)")
CARRY_MAX_DISTANCE = 3     # 문장 간 시점 상속 상한 — 먼 앞 문장 오상속 방지(실측 18문장 사례)


def _carry_value(carry: dict, article_id: str, sent_idx: dict) -> str | None:
    """거리 상한 안에서만 직전 문장의 해소 시점을 물려준다."""
    hit = carry.get(article_id)
    if not hit:
        return None
    period, idx = hit
    return period if sent_idx.get(article_id, 0) - idx < CARRY_MAX_DISTANCE else None


def _get(item: dict, key: str) -> str:
    v = item.get(key, "")
    return str(v).strip() if v is not None else ""


# 비교 기준 값 행 판별(92차) — 이 행의 값이 '전년 값 자체'인가, '변화량'인가.
# 가르는 신호는 위치가 아니라 **비교 조사**다:
#   "전년 동기에는 200억달러"   → 사이에 대비/보다 없음 → 값이 곧 전년 값  → 시프트
#   "전년(2만1336원) 대비"      → 괄호 안 값도 전년 값               → 시프트
#   "전년 동기 대비 32.6% 늘어" → 대비가 앞서면 뒤는 변화량           → 대상 시점
#   "작년 6월보다 16.0% 감소"   → 보다가 앞서면 뒤는 변화량           → 대상 시점
_CMP_WORD = re.compile(r"(전년|작년|지난해|전월|전분기|1년\s?전|전기)")


def _is_comparison_value_row(value: str, unit: str, sentence: str) -> bool:
    if not value or not sentence:
        return False
    pos = value_position(value, unit, sentence)
    if pos is None:
        return False
    lead = sentence[max(0, pos - 14):pos]
    m = None
    for m in _CMP_WORD.finditer(lead):
        pass                      # 값에 가장 가까운 비교 어휘
    if m is None:
        return False
    return not re.search(r"대비|보다|비해", lead[m.end():])


_APPROX_SUFFIX = re.compile(r"\s*(이상|이하|이내|넘게|초과|미만|가량|안팎|남짓|가까이)$")
# 구간 표기 '~원대/조원대' — 수사·화폐 뒤의 '대'만 제거(자동차 '3대'의 단위 '대'는 보존)
# 구간 표기 '~원대' — **화폐 뒤의 '대'만** 제거한다(98차). 종전 [원조억만천] 룩비하인드는
# '432만대'(자동차 대수)의 '대'까지 지워 단위를 소실시켰다(test 실행 산출에 실피해 기록).
_RANGE_DAE = re.compile(r"(?<=원)대$")
# 선행 시점 표기 — 뒤에 실제 수치가 이어질 때만 절단('2020년 0.5'→'0.5', '2020'은 그대로)
_LEADING_PERIOD = re.compile(r"^\d{1,4}\s*(?:년|년도|월|일|분기)\s*(?=\d)")
# 단위 인벤토리 단일화(98차 Phase 4) — 종전 _UNIT_TAIL·_UNIT_AFTER가 서로 다른 목록을
# 써서 t·대·개·가구·배·위·곳·포인트가 어느 한쪽에서 빠져 있었다(골든 unit 18종 중 9종 미커버).
# 실피해: '2만5228t'+'톤' → 단위 소실·역검증 실패로 Claim 폐기(test 실행 5건).
# 긴 표기가 먼저 와야 부분 매치되지 않는다(%포인트(p) > %포인트 > %p > %).
KNOWN_UNITS = ("%포인트(p)", "%포인트", "%p", "%", "달러", "원", "명", "톤", "t",
               "건", "가구", "개", "배", "위", "곳", "대", "포인트", "㏊", "ha")
_UNIT_ALT = "|".join(re.escape(u) for u in KNOWN_UNITS)
_UNIT_TAIL = re.compile(rf"({_UNIT_ALT})$")


def normalize_value_unit(value: str, unit: str) -> tuple[str, str]:
    """LLM 출력 관행의 결정적 정규화(§5.1 — 룰이 할 수 있는 일). dev 실측 오류의 최빈 3종:

    ① value에 단위 중복('441만4000명'+unit '명' → 역검증 '명명' 실패)
    ② value에 경계·근사어('8% 이상' — 29차 사전상 value 미포함, 정보는 claim 원문 보존)
    ③ unit 내부 공백('% p') · unit 누락 시 value 꼬리의 단위 분리('3만원' → '3만'+'원')
    ④ **선행 시점 표기**('2020년 0.5' → '0.5') — 나열 문장에서 연도가 값에 붙어 나온다.
       프롬프트로 두 번 고쳐도 재발했고(v2.1·v2.2 실측 각 5건), 시점은 period 소관이라
       결정적 룰이 맞다. 골든 508건 중 선행 시점이 붙은 value는 0건이라 회귀 위험도 없다.
       단 값 자체가 연도인 경우(`2020`)는 뒤에 다른 수치가 없으므로 절단되지 않는다.
    정규화 후에도 역검증(value+unit 문장 실존)은 그대로 돌므로 환각 차단은 약화되지 않는다.
    """
    u = re.sub(r"\s+", "", unit or "")
    v = (value or "").strip()
    v = _LEADING_PERIOD.sub("", v).strip()
    v = re.sub(r"^약\s*", "", v)
    v = _APPROX_SUFFIX.sub("", v).strip()
    v = _RANGE_DAE.sub("", v).strip()      # '1000조원대' → '1000조원'(구간 표기 — 29차 사전)
    if u and v.endswith(u):
        v = v[: -len(u)].strip()
        v = _APPROX_SUFFIX.sub("", v).strip()
    m = _UNIT_TAIL.search(v)
    if m and len(v) > len(m.group(1)):
        # value 꼬리에 단위 표기가 남아 있으면 분리 — value 쪽 표기가 기사 verbatim일
        # 개연성이 높으므로 unit이 있어도 교체(dev 실측: v='7%포인트'+u='%p' → '7%포인트%p')
        u = m.group(1)
        v = v[: -len(u)].strip()
    # 수사 이동(91차 — HCX-007 실측 최빈 표기 변형): unit 선두의 수사는 value 소속이다.
    # '1300'+'조원' → '1300조'+'원' · '196'+'만명' → '196만'+'명' (§4.3 분리 기준 그대로)
    sm = _SCALE_PREFIX.match(u)
    if sm and v and v[-1].isdigit():
        v, u = v + sm.group(0), u[sm.end():]
    return v, u


def restore_unit_notation(value: str, unit: str, sentence: str) -> str:
    """단위 축약의 verbatim 복원 — LLM이 '%포인트'를 '%p'로 줄이면 기사 표기로 되돌린다.

    §4.1: 단위는 기사 표기 그대로. 복원 후보가 문장에 실존할 때만 교체(결정적·안전).
    """
    if unit in ("%p", "%P") and value:
        for cand_u in ("%포인트", "%p"):
            if (value + cand_u).replace(" ", "") in re.sub(r"\s", "", sentence):
                return cand_u
    # 단위 창작 차단 — '고용률은 61로'에 unit '%'를 붙이는 상식 보정(실측). value+unit이
    # 문장에 없는데 value만 독립 수치로 있으면 단위를 비운다(§4.1: 단위 없는 수치는 null).
    # 단, 나열 압축 표기('46·64%' = 46%와 64%)는 단위가 뒤에 한 번만 쓰인 정당한 경우라
    # 역검증(value_in_sentence)이 인정하는 형태면 지우지 않는다(89차).
    if unit and value:
        flat = re.sub(r"\s", "", sentence or "")
        if (value + unit).replace(" ", "") not in flat and \
                not value_in_sentence(value, unit, sentence) and \
                re.search(rf"{re.escape(value)}(?![\d.])", flat):
            return ""
    return unit


# verbatim 스냅(91차) — HCX-007 실측: 표기 상이 41건이 전부 결정적 변형이었다
# ('1300'+'조원'↔'1300조'+'원' · '5만5000'↔'5만 5000' · 무단위 값 뒤 문장 실존 단위).
# 역검증 패턴이 문장에 매치하면 그 **실제 스팬의 표기**로 value·unit을 되씌운다 —
# 문장 실존 기반이라 창작 경로가 없고, §4.1 verbatim 원칙을 오히려 강화한다.
_UNIT_AFTER = re.compile(rf"^\s?({_UNIT_ALT})")
_SCALE_PREFIX = re.compile(r"^[조억만천]+")


def snap_value_to_sentence(value: str, unit: str, sentence: str) -> tuple[str, str]:
    from src.p3_stage_c import _value_pattern
    if not value:
        return value, unit
    m = _value_pattern(value, unit).search(sentence or "")
    if not m:
        return value, unit
    span = m.group(0).strip()
    if unit:
        # 스팬 = value+unit 결합 — 꼬리에서 unit의 **비공백 문자 수**만큼 소비해
        # value의 문장 실표기를 얻는다('5만5000'+'명' ↔ 문장 '5만 5000명' → '5만 5000').
        # 단위 종류를 열거하지 않으므로 미등록 단위(t·위·곳)에도 안전하다.
        need, cut = len(re.sub(r"\s+", "", unit)), len(span)
        while need and cut:
            cut -= 1
            if not span[cut].isspace():
                need -= 1
        snapped = span[:cut].rstrip()
        # 스냅은 **공백 표기 복원만** — 비공백이 달라지면(나열 허용 패턴이 '46·64'를
        # 삼키는 경우 등) 원값 유지. 값의 실체를 바꾸는 룰이 아니다.
        if re.sub(r"\s+", "", snapped) != re.sub(r"\s+", "", value):
            return value, unit
        return (snapped or value), unit
    ma = _UNIT_AFTER.match(sentence[m.end():m.end() + 5])
    if ma:
        return span, ma.group(1)     # 문장 실존 단위 흡수(창작 아님)
    return (span or value), unit


_DECIMAL_UNIT = re.compile(r"^(\d+)\.(\d+)(조|억|만)$")
_SCALE_SUB = {"조": "억", "억": "만", "만": ""}


def restore_value_notation(value: str, unit: str, sentence: str) -> str:
    """소수 축약의 verbatim 복원 — '1217.8조' → '1217조8000억'(문장 표기).

    §4.1은 value를 기사 표기 그대로 요구하는데, LLM은 큰 수를 소수로 줄이는 습관이 있다
    (실측: dev 역검증 거부 5문장 중 2문장이 이 형태, 프롬프트 금지 규칙이 있는데도 재발).
    소수부를 아래 단위로 환산하는 것은 결정적 변환이고, **복원형이 문장에 실존할 때만**
    교체하므로 안전하다(존재하지 않으면 원본 유지 → 역검증이 그대로 거부).
    """
    m = _DECIMAL_UNIT.match((value or "").strip())
    if not m:
        return value
    head, frac, scale = m.group(1), m.group(2), m.group(3)
    sub = _SCALE_SUB.get(scale)
    if not sub:
        return value
    expanded = f"{head}{scale}{frac.ljust(4, '0')}{sub}"
    flat = re.sub(r"\s", "", sentence or "")
    if (expanded + (unit or "")).replace(" ", "") in flat or expanded in flat:
        return expanded
    return value


def _err(cand: SentenceCandidate, stage: str, reason: str, item, item_index=None) -> dict:
    return {"article_id": cand.article_id, "sent_id": cand.sent_id, "sentence": cand.text,
            "stage": stage, "reason": reason, "item": item, "item_index": item_index}


# ⓑ 제도 기준값 룰 — 106차 블라인드에서 채택(기본 켬).
#   세율·요율·한도처럼 **제도가 정한 기준값**은 세상의 상태를 집계한 값이 아니라
#   비통계다(§5.3 NON_STAT_NUMBER). 실행 전 해악 상한을 골든 741건(블라인드 174 +
#   ver3 567)에 대고 재서 **죽는 Claim 0건**을 확인했고, 블라인드 실측도 같았다 —
#   오검출 11건 제거·참 손실 0·F1 +0.018·비용 0원.
#   ⚠ 지운 11건은 전부 eligible=false였다 = 점수는 오르지만 다운스트림 피해는 안 준다.
STATUTORY_RULE = True
# Phase 5-3 비통계 2차 게이트를 **모든 Claim**에 적용(게이트 단독 모드).
# 106차 실측으로 미채택 — 오검출 32건을 지우는 대가로 경상수지·무역수지 흑자·
# 정부 총지출 같은 핵심 통계 17건을 죽였다. 맥락(제목·리드·앞뒤 문장)을 줘도
# 참 손실이 15건으로 2건 줄 뿐 오검출이 늘어 F1은 되레 하락(0.809→0.802).
# 실험 재현용으로 남긴다 — run(gate_fn=..., gate_all=True).
GATE_ALL = False


def process_sentence(cand: SentenceCandidate, items: list[dict], article_text: str,
                     carry_anchor: str | None = None, article_anchor: str | None = None,
                     gate_fn=None
                     ) -> tuple[list[ClaimRecord], list[ExcludedRecord], list[dict], list[dict]]:
    """한 문장의 raw item들 → (claims, excluded, errors, traces).

    claims와 traces는 같은 순서·같은 길이(쌍). trace에는 item_index(안정 키)가 실린다.
    carry_anchor: 같은 기사 직전 문장의 해소 period — "이 기간"·"전년 동기"가 문장 경계를
    넘어 앞 문장을 가리키는 경우를 위한 상속 씨앗(dev 실측 최빈 오류).
    """
    claims: list[ClaimRecord] = []
    excluded: list[ExcludedRecord] = []
    errors: list[dict] = []
    traces: list[dict] = []
    ANCHORED = ANCHOR_SHIFT_EXPRS | SAME_PERIOD_EXPRS

    # period 해소 2패스 — ①비앵커 먼저, ②앵커 표현은 원문 순서상 직전 형제
    #   (문장 내 형제가 없으면 직전 문장의 carry_anchor로 폴백)
    resolved: list = [None] * len(items)
    for i, it in enumerate(items):
        expr = _get(it, "period")
        if expr not in ANCHORED:
            resolved[i] = resolve_period(expr, cand.posted_date)
    # pass1 결과 스냅샷 — 앵커는 반드시 '비앵커 항목의 해소값'에서만 온다.
    # 스냅샷 없이 resolved를 그대로 읽으면 앵커 항목이 직전 앵커 항목의 *이미 시프트된*
    # 값을 다시 앵커로 잡아 -1년이 누적된다(실측: 한 문장의 3분기 3개가
    # 2024-Q3·2023-Q3·2022-Q3로 흩어짐). 스냅샷으로 1회 시프트를 보장한다.
    base = list(resolved)
    for i, it in enumerate(items):
        if resolved[i] is not None:
            continue
        anchor = None
        for j in range(i - 1, -1, -1):     # 직전 형제 우선(리뷰: 첫 값 고정은 오앵커)
            if base[j] is not None and base[j].period:
                anchor = base[j].period
                break
        if anchor is None:
            for j in range(i + 1, len(items)):
                if base[j] is not None and base[j].period:
                    anchor = base[j].period
                    break
        if anchor is None:
            anchor = carry_anchor          # 문장 간 상속(거리 제한)
        expr_i = _get(it, "period")
        # 앵커 시프트는 **이 행의 값이 비교 기준 값일 때만**(92차).
        # 설계 의도는 "수박 2만9115원으로 전년(2만1336원)"의 2만1336 행처럼 값 자체가
        # 전년 값인 경우다. 그런데 LLM은 change_rate·현재값 행에도 period_expr='전년 동기'를
        # 붙이고(비교는 수식어일 뿐), 그때 시프트하면 대상 시점이 1년 밀린다 —
        # dev 실측 anchor_shift 13건 중 11건이 이 오류였다. 값이 문장에서 비교 어휘
        # **직후**(괄호 포함 12자 이내)에 나올 때만 비교 기준 값 행으로 본다.
        if expr_i in ANCHOR_SHIFT_EXPRS and not _is_comparison_value_row(
                _get(it, "value"), _get(it, "unit"), cand.text):
            resolved[i] = (Resolved(anchor, not is_std_period(anchor), "comparison_expr_target")
                           if anchor else Resolved(None, False, "anchor_missing"))
            continue
        r_i = resolve_period(expr_i, cand.posted_date, anchor=anchor)
        resolved[i] = r_i

    # pass2b — **YOY 착지 교정**(99차). ANCHOR_SHIFT 집합에 없는 형태('전년 8월'처럼
    # 한정 월)라도 해소 결과가 **문서 앵커의 전년**이고 이 행이 비교 기준 값 행이
    # 아니면, 비교 수식어를 period 자리에 낸 것이다 → 대상 시점으로 되돌린다.
    #  · 별도 패스인 이유: pass2는 pass1이 못 푼 항목만 돈다. '전년 8월'은 앵커 없이도
    #    풀려서 pass1에서 확정되므로 pass2 안에 두면 영원히 발동하지 않는다(실측 3건).
    #  · 기준을 형제가 아니라 **문서 앵커**(문장 간 carry → 기사 기준 시점)로 잡는 이유:
    #    한 문장의 항목이 전부 같은 표면형을 내면 형제 앵커 자체가 이미 1년 밀린 값이다.
    #  · 절대 표기('2024년 8월')는 제외 — 그건 진짜로 그 시점을 말한 것이다.
    doc_anchor = carry_anchor or article_anchor
    if doc_anchor and is_valid_period_form(doc_anchor):
        yoy_doc = yoy_of(doc_anchor)
        for i, it in enumerate(items):
            r = resolved[i]
            if not r or not r.period or not yoy_doc or r.period != yoy_doc:
                continue
            if not re.search(r"(전년|작년|지난해|1년\s*전)", _get(it, "period") or ""):
                continue
            if _is_comparison_value_row(_get(it, "value"), _get(it, "unit"), cand.text):
                continue
            resolved[i] = Resolved(doc_anchor, not is_std_period(doc_anchor),
                                   "comparison_expr_target")

    # pass3 — 기간 길이 표현("지난 5년")의 종점 상속.
    # 통계 기사는 리드에서 대상 시점을 확립하고 이후 문장이 그것을 공유하므로,
    # 문장 내 형제 → 기사 기준 시점(거리 무제한) 순으로 종점을 찾는다(실측 18건).
    sibling = next((r.period for r in resolved if r and r.period and not r.partial), None)
    for i, it in enumerate(items):
        r = resolved[i]
        if r is None or r.period is not None or r.method != "duration_no_anchor":
            continue
        for anc in (sibling, carry_anchor, article_anchor):
            if not anc:
                continue
            retry = resolve_period(_get(it, "period"), cand.posted_date, anchor=anc)
            if retry.period:
                resolved[i] = retry
                break

    # pass4 — 비교 표현이 period 자리에 온 항목의 대상 시점 폴백(92차).
    # 실측: HCX-007 미해소 72건 중 21건이 '전년 동기'·'전년 대비'·'5년 전 대비'처럼
    # **비교 기준 수식어**를 period로 낸 것이고, 골든은 전부 **대상 시점**(기사 기준 시점)을
    # 원한다. 형제가 있으면 pass2가 시프트로 처리하므로(비교 기준 값 행의 정당한 경로)
    # 여기는 **형제도 carry도 없어 anchor_missing으로 죽은 경우**만 받는다 — 그때는
    # 그 표현이 값의 시점이 아니라 수식어라는 뜻이므로 기사 기준 시점을 시프트 없이 쓴다.
    for i, it in enumerate(items):
        r = resolved[i]
        if r is None or r.period is not None or r.method != "anchor_missing":
            continue
        for anc in (sibling, carry_anchor, article_anchor):
            if anc:
                resolved[i] = Resolved(anc, not is_std_period(anc), "comparison_expr_target")
                break

    prev_pos = -1
    value_occurrence: dict[tuple[str, str], int] = {}
    order_violated_idx: set[int] = set()
    for idx, (it, r) in enumerate(zip(items, resolved)):
        kind = _get(it, "kind")
        if kind == "excluded":
            code = _get(it, "exclusion_code")
            if code not in CONTRACT_EXCLUSION_CODES:
                # PARTIAL_PERIOD 포함(§4.8: 제외 코드 아님) — 수리 대상으로 격리
                errors.append(_err(cand, "C", f"excluded인데 계약 밖 코드: {code!r}", it, idx))
                continue
            excluded.append(ExcludedRecord(article_id=cand.article_id, sent_id=cand.sent_id,
                                           sentence=cand.text, exclusion_code=code,
                                           note=_get(it, "note")))
            continue
        if kind != "claim":
            errors.append(_err(cand, "C", f"계약 밖 kind: {kind!r}", it, idx))
            continue

        raw_code = _get(it, "exclusion_code")
        if raw_code not in CLAIM_ALLOWED_CODES:
            errors.append(_err(cand, "C", f"kind=claim인데 제외 코드 {raw_code!r} — kind×code 모순", it, idx))
            continue
        forecast = _get(it, "forecast").upper()
        if forecast not in ("Y", "N"):
            errors.append(_err(cand, "C", f"forecast 누락/이탈: {forecast!r} (Y/N 필수)", it, idx))
            continue

        nv, nu = normalize_value_unit(_get(it, "value"), _get(it, "unit"))
        nv = restore_value_notation(nv, nu, cand.text)
        # 98차 순서 교체: restore_unit(단위 창작 차단·표기 복원) → snap(문장 실표기).
        # 종전 snap→restore_unit 순서는 '원/통'·'%포인트'처럼 결합형이 문장에 없는 단위를
        # 창작으로 오인해 **지워 버렸다**(실측 4건 — '0.9%포인트'가 unit 공백으로 출하).
        # snap이 뒤에 오면 문장 실존 단위를 다시 흡수하므로 그 손실이 복구된다.
        nu = restore_unit_notation(nv, nu, cand.text)
        nv, nu = snap_value_to_sentence(nv, nu, cand.text)   # 문장 실표기 스냅(91차)
        # direction은 룰 우선(§5.6) — 단, **증감형(change_rate·change_amount)에서만** 채운다.
        # 골든 실측: direction이 있는 265건은 전부 증감형이고 비증감형은 100% 공백이며,
        # 증감형에서 양쪽 값이 있을 때 룰↔골든 209/209 일치(불일치 0). 조건 없이 룰을 적용하면
        # 문장에 증감 어휘가 있다는 이유로 수준값(level)에까지 방향이 붙어 골든을 파괴한다.
        vt = _get(it, "value_type")
        # share_ratio는 62차에 폐지(시점값 비율 = level)됐지만 프롬프트 스키마 라인에
        # 남아 있어 LLM이 계속 낸다(90차 실측 dev 24건 유출 — value_type 0.880의 주범).
        # 프롬프트는 동결 통제라 결정적 치환으로 흡수한다 — 62차 rule_value_type과 같은 방향.
        if vt == "share_ratio":
            vt = "level"
        # %p·%포인트는 **정의상 두 시점의 차이**라 예외가 없다(72차 규약) — LLM이
        # change_rate로 내면 결정적으로 교정한다. 98차 Phase 4 실측: 룰 정밀도 6/6,
        # 골든 변형 0, eligible=true Claim 7건 즉시 교정.
        if vt == "change_rate" and re.sub(r"\s", "", _get(it, "unit")) in ("%p", "%포인트", "%포인트(p)"):
            vt = "change_amount"
        direction = _get(it, "direction")
        if vt in ("change_rate", "change_amount"):
            direction = rule_direction(cand.text) or direction
        else:
            direction = ""
        # v0.5(80차): comparison_basis 표면형 → enum + 절대시점. ClaimRecord에는 enum만
        # 두고(79차 내부 표준) 표면형은 trace에 보존한다. comparison_period는 기사 단위
        # period 후처리(_backfill_duration 등) 뒤 run()에서 최종 period 기준으로 재파생된다.
        # ※ '구간 변화 주장의 period는 종점'(71차 규약)을 룰로 강제하는 안은 **기각**했다.
        #   골든 실측 이득 1건('1955~1960년 16% 늘었다' → 1960) vs 손실 2건
        #   ('2.3%에서 1.4%로 하락' — 구간이 변화 구간이 아니라 **평균 창**이라 골든이
        #   범위를 유지한다). 문장만으로는 두 용법을 못 가른다 — 범위를 그대로 넘긴다.
        period_val, partial_val = r.period or "", r.partial
        cb_expr = _get(it, "comparison_basis")
        # period 자리에 온 비교 표현은 **비교 기준 정보**다(92차) — 대상 시점으로 되돌릴 때
        # 그 표현을 cb로 승계한다. LLM이 필드를 잘못 골랐을 뿐 정보 자체는 정확하다.
        # 증시의 '전장 대비'도 같다(99차): 대상 시점은 그날, 표현은 직전 거래일 비교.
        if not cb_expr and r.method in ("comparison_expr_target", "as_of_posted_prev_session"):
            cb_expr = _get(it, "period")
        cb_enum, cb_period = resolve_comparison(cb_expr, period_val, cand.posted_date)
        # SPECIFIC의 절대시점은 재구성 불가라 item이 직접 실어 오면 수용한다
        # (골든 stub 패스스루 경로 — LLM 경로는 이 키를 출력하지 않는다)
        if cb_enum == "SPECIFIC" and not cb_period:
            cb_period = _get(it, "comparison_period")
        c = ClaimRecord(
            claim_id=f"{cand.article_id}-C000",  # 임시 — run()에서 슬롯 기반 정식 부여
            article_id=cand.article_id, sent_id=cand.sent_id,
            posted_date=cand.posted_date, claim=cand.text,
            metric=_clean_metric(_get(it, "metric")),
            metric_normalized=_clean_metric(_get(it, "metric_normalized")),
            value=nv, unit=nu,
            value_type=vt, direction=direction,
            period=period_val, comparison_basis=cb_enum, comparison_period=cb_period,
            forecast=forecast,
            exclusion_code="PARTIAL_PERIOD" if partial_val else "",
            note=_get(it, "note"),
        ).finalize()

        # metric 창작 어휘 → **Claim을 버리지 않고 어휘만 제거**(61차 사용자 결정).
        # metric은 value+unit이 무엇에 대한 값인지 정하는 판정 기준이자 한 문장 내
        # 다중 주장의 구분 수단이라 비어서도, 그것 때문에 Claim이 사라져서도 안 된다.
        # 창작은 허용하지 않는다(실존 검증이 재검토의 의미를 갖는다) — 대신 절단한다.
        # 원표기는 trace에 보존해 5번이 복구할 수 있게 한다.
        coined = metric_missing_words(c.metric, article_text)
        metric_original = ""
        if coined:
            stripped = strip_coined_words(c.metric, article_text)
            if stripped:
                metric_original, c.metric = c.metric, stripped

        # 모집단 한정어 보강(R5) — 파괴적 검사 **전에** 해야 보강된 metric도 실존 검증을 받는다
        c.metric = add_population_qualifier(c.metric, cand.text, c.value, c.unit)

        # ⓑ 제도 기준값 룰 + Phase 5-3 비통계 2차 게이트(105차).
        # 룰은 재현율이 높고 정밀도가 낮다 — 게이트가 있으면 룰은 **후보만 지목**하고
        # 판정은 LLM이 한다. GATE_ALL이면 룰과 무관하게 모든 Claim에 묻는다(게이트 단독).
        statutory = STATUTORY_RULE and is_statutory_value(c.metric, cand.text)
        drop, why = False, ""
        if gate_fn is not None and (GATE_ALL or statutory):
            try:
                drop = not gate_fn(cand.text, c.metric, c.value, c.unit, cand)
                why = "게이트"
            except Exception:
                drop = False                    # 게이트 실패 시 막지 않는다(Recall 우선)
        elif statutory:
            drop, why = True, "룰"
        if drop:
            excluded.append(ExcludedRecord(
                article_id=cand.article_id, sent_id=cand.sent_id, sentence=cand.text,
                exclusion_code="NON_STAT_NUMBER",
                note=f"비통계 판정({why}): {c.metric}"))
            continue

        issues = destructive_issues(c, cand.text, article_text)
        if issues:
            errors.append(_err(cand, "C", "; ".join(issues), it, idx))
            continue

        # §5.6 출력 순서 계약 검사(비파괴 — 감사 플래그)
        pos = value_position(c.value, c.unit, cand.text, start=prev_pos + 1)
        if pos is None and prev_pos >= 0:
            order_violated_idx.add(idx)
        elif pos is not None:
            prev_pos = pos

        # 같은 문장에 같은 (value, unit)이 여러 번 나올 때의 **등장 순번**(101차 R1).
        # 항목 출력 순서 = 원문 등장 순서라는 계약(§5.6) 위에서만 성립하는 결정적 식별자다.
        # metric은 패스마다 표기가 달라져 키가 될 수 없고(FP 7→39 실측), 순번은 문장이라는
        # 불변 입력에서 나오므로 재추출본과 안정적으로 대응된다.
        occ_key = (c.value, c.unit)
        value_occurrence[occ_key] = value_occurrence.get(occ_key, 0) + 1

        flags = audit_flags(c, cand.text)
        if coined:
            flags.append(f"metric_coinage:{','.join(coined)}")
        if idx in order_violated_idx:
            flags.append("item_order_violation")
        claims.append(c)
        traces.append({
            "metric_original": metric_original,   # 절단 전 원표기(없으면 "")
            "article_id": cand.article_id, "sent_id": cand.sent_id, "item_index": idx,
            "offsets": [cand.start, cand.end],
            "comparison_basis_expr": cb_expr,     # enum 전환 전 표면형(v0.5 — 5번 복구용)
            "period_expr": _get(it, "period"), "period_resolved": r.period,
            "period_method": r.method, "partial": r.partial,
            "value_occurrence": value_occurrence[occ_key],
            "audit_flags": flags,
            "pipeline_version": PIPELINE_VERSION,
        })
    return claims, excluded, errors, traces


# ★ 이 정규식은 **파서가 아니라 트리거**다 — 정확한 토큰화를 목표로 하지 않는다.
#
# 67차 실측: 한글 수사를 통째로 잡도록 "고쳤더니"('2만9115원'을 한 토큰으로) 힌트
# 적중률은 32%→90%로 올랐는데 **재현율이 0.888→0.872, 분리 완전성이 0.775→0.700으로
# 떨어졌다.** 틀린 힌트('9115원')도 그 문장을 다시 보게 만드는 효과가 있었기 때문이다.
# 즉 여기서 중요한 것은 힌트의 정확성이 아니라 **재검토가 발동하는 범위**다.
# 잘게 쪼개는 쪽이 의도된 동작이므로 되돌린다(수리 결과는 어차피 Stage C가 재검증한다).
# 수치 토큰 — 한국 수사는 **스케일이 연쇄**한다('13조8000억원'·'5180만5547명').
# 스케일 하나만 받던 종전 패턴은 이것을 '13조'+'8000억원' 두 조각으로 잘라
# 힌트를 약하게 만들었다(dev 실측: A272c31f6 s001).
_SCALE = "조|억|만|천"
_NUM_TOKEN = re.compile(
    rf"\d[\d,.]*(?:\s*(?:{_SCALE})\s*\d[\d,.]*)*\s*(?:{_SCALE})?\s*"
    r"(?:%p|%포인트|%|원|명|톤|개|가구|달러|건)?")
_TIME_TOKEN = re.compile(r"\d{1,4}\s*(?:년|년도|월|일|분기|시|분|호|차|위|기|세)(?![\d가-힣])")

# 재검토 트리거(99차) — Claim이 하나도 안 나온 문장을 다시 볼지 정한다.
# 그냥 열면 dev 109문장 중 5개만 쓸모 있어 정밀도 0.05다(호출 104회 낭비).
# 아래 두 신호로 좁히면 **발동 7 · 유용 5 · 회수 가능 8건 · 정밀도 0.71**(dev 실측).
# 근거: 비통계로 판정된 문장이라도 ①증감 서술이 붙은 비율 표현이나 ②큰 화폐 금액을
# 담고 있으면 통계 주장일 개연성이 높다(실제로 '1000조원대'·'0.1% 증가'가 이 유형).
_SL_RATE = re.compile(r"\d[\d.,]*\s*(?:%|퍼센트)")
_SL_BIG = re.compile(r"\d[\d,.]*\s*(?:조|억)\s*(?:\d[\d,.]*\s*(?:억|만))?\s*(?:원|달러)")
_SL_MOVE = re.compile(r"(증가|감소|상승|하락|늘|줄|올라|내려|웃도|밑도|기록|달성|전망|예상|집계|나타났)")


def _second_look(sentence: str) -> bool:
    s = sentence or ""
    return bool(_SL_BIG.search(s) or (_SL_RATE.search(s) and _SL_MOVE.search(s)))


def _covered_by_taken(core: str, taken: set[str]) -> bool:
    """추출값이 이 토큰을 이미 담고 있나 — **숫자 경계**를 지켜 판정한다.

    종전의 단순 접두 매칭은 값 '5' 하나가 '5180만5547명'을 덮어 버렸다(dev 실측
    A1193d6ae s006: 총인구 Claim이 통째로 사라짐). 값이 토큰 안에 나타나되 **바로 뒤에
    숫자가 이어지지 않을 때**만 덮은 것으로 본다.
    """
    for t in taken:
        if not t:
            continue
        for m in re.finditer(re.escape(t), core):
            if not core[m.end():m.end() + 1].isdigit():
                return True
        if t.startswith(core):        # 추출값이 더 긴 경우(토큰이 잘려 나온 상황)
            return True
    return False


def missing_value_hints(sentence: str, claims: list[ClaimRecord],
                        excluded: list) -> list[str]:
    """문장에 있는데 어느 항목에도 안 담긴 수치를 찾는다 — **조용한 누락**의 탐지기.

    수리 루프는 원래 Stage C 파괴적 실패가 있을 때만 돌았다. 그런데 실측 손실의 다수는
    '아무 오류 없이 그냥 덜 뽑은' 경우라 기계에 보이지 않았다(dev 손실 36건 중 22문장).
    여기서 신호를 만들어 수리를 발동시킨다.

    게이트(99차 개정):
      · Claim이 하나라도 있으면 **excluded가 함께 있어도 발동**한다. 종전에는
        excluded가 하나만 있어도 통째로 껐는데, Claim이 있다는 것 자체가 그 문장이
        통계 문장이라는 증거라 '제외 항목이 모든 수치를 대표 회계한다'는 근거가 없다.
      · Claim이 0건이면 _second_look()이 참일 때만 발동한다(위 주석의 정밀도 근거).

    **거짓 신호를 줄이는 쪽으로 보수적으로 짠다** — 발동 1회가 곧 LLM 호출 1회(약 6원).
    다만 유형별 적중률로 거르는 안(%·큰 수만)은 **기각**했다 — 발동 43→33회로 비용은
    66% 줄었지만 재현율이 0.888→0.824로 같이 떨어졌다. 힌트 하나가 틀려도 그 문장을
    다시 보게 만드는 효과가 있어 **힌트 정밀도 ≠ 수리 생산성**이다(66차 실측).
    """
    if not claims and not _second_look(sentence or ""):
        return []
    taken = set()
    for c in claims:
        v = re.sub(r"\s", "", c.value or "")
        if v:
            taken.add(v)
    hints = []
    for m in _NUM_TOKEN.finditer(sentence or ""):
        tok = m.group(0).strip()
        if not tok or not re.search(r"\d", tok):
            continue
        if _TIME_TOKEN.fullmatch(tok):          # 연·월·일·분기 → period 소관
            continue
        # 맨 숫자 뒤에 연·월·일이 이어지면 그것도 시점이다('2022년'의 '2022' —
        # 토큰이 단위를 못 물고 끊긴 경우라 fullmatch로는 안 걸린다)
        if sentence[m.end():m.end() + 1] in ("년", "월", "일") and tok.isdigit():
            continue
        core = re.sub(r"\s", "", tok)
        if _covered_by_taken(core, taken):
            continue
        hints.append(tok)
    return hints[:4]


# 기사는 통계 항목명을 따옴표로 감싸는 일이 잦다(‘식료품 및 비주류 음료’ 물가지수).
# 따옴표는 기사의 표기 장치이지 지표명의 일부가 아니고, 골든 567건 중 따옴표를 포함한
# metric은 **0건**이다. 검색 씨앗에도 노이즈이므로 벗긴다(값·단위와 달리 verbatim 보존
# 대상이 아님 — metric은 원래 조합·보충이 허용되는 필드다, §4.4).
_METRIC_QUOTES = re.compile(r"[‘’“”'\"「」『』]")


def _clean_metric(m: str) -> str:
    return re.sub(r"\s{2,}", " ", _METRIC_QUOTES.sub("", m or "")).strip()


def _merge_repair(cl, ex, er, tr, cl2, ex2, er2, tr2):
    """원본 + 재추출본을 병합. 원본 통과분은 보존하고 새로 회수된 수치만 더한다.

    반환: (claims, excluded, errors, traces) — claims와 traces는 쌍을 유지한다.

    오류 쪽은 두 경우를 나눈다.
    ① 수리가 파괴적 실패를 **줄였으면** 재추출본의 오류로 갈아탄다 — 원본 오류는
       그 시도가 교정됐다는 뜻이라 남기면 같은 수치가 Claim이자 오류로 이중 계상된다
       (값 자체를 잘못 뽑은 경우 value가 달라져 키로는 대응을 못 찾는다).
    ② 줄지 않았으면 원본 오류를 유지하되, 회수된 수치의 오류만 뺀다.
    어느 쪽이든 최종 Claim에 존재하는 수치의 오류는 남기지 않는다.
    """
    # 병합 키(99차): (value, unit)의 **다중집합**.
    #   종전은 같은 키의 집합이라 한 문장에 같은 값·단위가 둘이면 하나가 사라졌다
    #   — 골든 실측 6문장(브라질/태국 수입 전기차 86% · 연도별 5.9% · 시도별 8.8% 등).
    #   개수로 대조하면 해결된다: 재추출본이 K개 냈는데 원본이 J개만 담고 있으면 K−J개를
    #   더한다. 재추출본이 낸 것 이상은 절대 안 늘어난다.
    #   ⚠ metric은 키에 넣지 않는다 — 재추출본이 같은 수치에 다른 metric 표기를 붙이면
    #   ('기업 실적 상회 비율' ↔ '시장 예상치 상회 비율') 같은 Claim이 둘로 출하된다.
    #   실측(dev): metric을 키에 넣자 FP 7 → 39로 폭증했다.
    base_slot = {(c.value, c.unit, tt.get("value_occurrence")): i
                 for i, (c, tt) in enumerate(zip(cl, tr))}
    seen = {(c.value, c.unit) for c in cl}      # 오류 제거용
    # 재추출본의 item_index는 **자기 패스 기준 0부터** 다시 매겨진다 — 그대로 두면 원본
    # 인덱스와 충돌해 run()의 슬롯 딕셔너리(`{item_index: claim}`)에서 한쪽이 덮인다.
    # 덮인 Claim은 번호를 못 받아 임시값 C000으로 남고, 한 기사에 C000이 여럿이면
    # 조인 키까지 깨진다(97차 다운스트림 신고 — 실측 16건). 원본 뒤로 밀어 충돌을 없앤다.
    next_idx = max((t.get("item_index", -1) for t in tr), default=-1) + 1
    added_c, added_t = [], []
    replaced = 0
    for c2, t2 in zip(cl2, tr2):
        k = (c2.value, c2.unit, t2.get("value_occurrence"))
        if k in base_slot:
            # 같은 수치의 같은 등장 위치 = **같은 Claim**이다. 재추출본이 정보를 더 담고
            # 있을 때만 갈아탄다 — ① 원본 metric이 창작 어휘로 잘렸는데 재추출본은
            # 멀쩡하다 ② 원본은 시점을 못 채웠는데 재추출본은 채웠다.
            # 두 조건 다 '정보가 늘어난' 방향이라 교체가 손해가 될 수 없다.
            i = base_slot[k]
            c0, t0 = cl[i], tr[i]
            coin0 = any(str(f).startswith("metric_coinage") for f in t0.get("audit_flags", []))
            coin2 = any(str(f).startswith("metric_coinage") for f in t2.get("audit_flags", []))
            better = (coin0 and not coin2) or (not c0.period and bool(c2.period))
            if better:
                t2 = dict(t2)
                t2["repaired"] = True
                t2["replaced_original"] = True
                t2["item_index"] = t0.get("item_index")   # 슬롯(claim_id 번호) 유지
                cl[i], tr[i] = c2, t2
                replaced += 1
            continue
        t2 = dict(t2)
        t2["repaired"] = True                   # 리니지: 이 Claim은 수리 경로에서 왔다
        t2["item_index_original"] = t2.get("item_index")
        t2["item_index"] = next_idx
        next_idx += 1
        seen.add((c2.value, c2.unit))
        added_c.append(c2)
        added_t.append(t2)

    def _dfails(errs):
        return [e for e in errs if e["stage"] == "C" and e.get("item") is not None]

    improved = len(_dfails(er2)) < len(_dfails(er))
    base_er = er2 if improved else er
    kept_er = [e for e in base_er
               if not (e.get("item") and (_get(e["item"], "value"), _get(e["item"], "unit"))
                       in seen)]
    # 제외 항목도 새로 나온 것만 추가(문장·코드가 같으면 중복)
    seen_ex = {(e.sent_id, e.exclusion_code) for e in ex}
    added_ex = [e for e in ex2 if (e.sent_id, e.exclusion_code) not in seen_ex]
    return cl + added_c, ex + added_ex, kept_er, tr + added_t


def _backfill_duration(claims: list[ClaimRecord], traces: list[dict],
                       article_base: dict[str, str]) -> int:
    """기간 길이 표현의 종점을 기사 기준 시점으로 후보정. 반환: 보정 건수.

    순차 처리 중에는 기사 기준 시점이 아직 확립되지 않아 **첫 문장의 duration이 항상
    미해소**로 남는다(실측: "5년간 먹거리 물가가 20% 넘게 상승" — 기준 시점은 다음 문장의
    '지난달'). 전 문장 처리가 끝난 뒤 한 번 더 해소해 이 순서 의존성을 없앤다.
    미해소로 남는 경우는 그대로 둔다(§8-6 억지 추정 금지).
    """
    fixed = 0
    for c, t in zip(claims, traces):
        if c.period or t.get("period_method") != "duration_no_anchor":
            continue
        base = article_base.get(c.article_id)
        if not base:
            continue
        r = resolve_period(t.get("period_expr", ""), c.posted_date, anchor=base)
        if not r.period:
            continue
        c.period = r.period
        c.exclusion_code = "PARTIAL_PERIOD" if r.partial else ""
        c.kosis_eligible = None          # period가 바뀌었으므로 재파생
        c.finalize()
        t.update(period_resolved=r.period, period_method=r.method, partial=r.partial)
        fixed += 1
    return fixed


def _backfill_no_expr(claims: list[ClaimRecord], traces: list[dict],
                      article_base: dict[str, str],
                      para_of: dict[tuple[str, str], int] | None = None,
                      min_share: float = 0.5, min_n: int = 8,
                      unanimous_n: int = 4, para_min_n: int = 5) -> int:
    """LLM이 **시점 표면형을 아예 안 낸** Claim에 기사 기준 시점을 상속한다.

    근거(dev 실측 99차): period 불일치 92건 중 표면형 미출력이 39건으로 최대 버킷인데,
    그 **36건(92%)의 골든 period가 기사 기준 시점과 정확히 같다.** 통계 기사는 리드에서
    대상 시점을 확립하고 이후 문장들이 그것을 공유하기 때문이고, 골든 저작자도 같은
    관례로 채웠다. 즉 억지 추정이 아니라 **골든이 쓰는 문서 단위 규약의 재현**이다.
    (대안은 전부 열등했다 — 직전 Claim 상속은 21/39, '부분기간 우선' 보정은 −1.)

    ★ 단일 시점 기사에만 적용한다. 골든 stub 검증이 무조건 상속의 위험을 드러냈다:
    골든이 **일부러 비워 둔** period 31건 중 29건이 채워졌다(의학 일반 통계 '폐암의
    80~85%는 비소세포폐암', 제도값 '수입차 관세 70~100%', 인터뷰 속 시점 미상 수치 —
    전부 시점이 없는 것이 정답이다). 그래서 **그 기사의 최빈 period 점유율**을 게이트로
    둔다: 점유율 > {min_share}, 표본 ≥ {min_n}. 점유율이 흩어진 기사는 문장마다 시점이
    다르다는 뜻이라 상속 근거가 없다.
    실측 효과 — dev +36/−3 → **+35/−0**, 골든 빈칸 오채움 29건 → **10건**.

    상속분은 trace의 period_method로 표시해 감사·되돌리기가 가능하게 둔다(§8-6 꼬리표).
    """
    from collections import Counter
    para_of = para_of or {}
    periods: dict[str, list[str]] = {}
    para_periods: dict[tuple[str, int], list[str]] = {}
    for c in claims:
        if c.period:
            periods.setdefault(c.article_id, []).append(c.period)
            pno = para_of.get((c.article_id, c.sent_id))
            if pno is not None:
                para_periods.setdefault((c.article_id, pno), []).append(c.period)
    # 문단 앵커(101차 R3) — 기사가 여러 통계를 나눠 다루면 기사 단위 앵커 하나로는
    # 뒷부분을 못 따라간다(A1a2e60a7: 앞은 '8월 인구동향', 뒤는 '9월 인구이동 통계').
    # 문단은 기자가 묶은 의미 단위라 같은 문단의 형제 시점이 더 가깝다.
    # 사전 측정(dev): 표면형 없는 Claim에만 적용 시 +3/−0. 표면형이 있는데 못 푼 건까지
    # 넓히면 +4/−7, 이미 해소된 값을 덮어쓰면 +0/−5라 **둘 다 기각**했다.
    para_anchor: dict[tuple[str, int], str] = {}
    for key, per in para_periods.items():
        mode, n = Counter(per).most_common(1)[0]
        if len(per) >= para_min_n and n / len(per) > min_share:
            para_anchor[key] = mode
    eligible_articles = set()
    for aid, base in article_base.items():
        per = periods.get(aid, [])
        if not per:
            continue
        share = Counter(per)[base] / len(per)
        # 2단 게이트: ① 표본이 충분하고 최빈 점유율이 과반이거나,
        #             ② **해소된 시점이 전부 같고**(만장일치) 표본이 최소한은 될 때.
        # ②가 필요한 이유 — 문장 대부분이 시점 어휘를 생략하는 기사는 해소된 Claim
        # 자체가 적어서 ①의 표본 하한에 걸린다(실측 A1193d6ae: 해소 5건뿐인데 전부
        # 2024, 상속하면 +14/−0인데 ①만으로는 통째로 막혔다).
        # 만장일치에도 하한을 두는 이유 — 해소가 2건뿐인 기사까지 열면 골든이 일부러
        # 비워 둔 칸을 채운다(Ab0baee3a 4건 실측).
        if (len(per) >= min_n and share > min_share) or \
                (share >= 0.999 and len(per) >= unanimous_n):
            eligible_articles.add(aid)

    fixed = 0
    for c, t in zip(claims, traces):
        if c.period or t.get("period_expr"):
            continue
        pno = para_of.get((c.article_id, c.sent_id))
        base = para_anchor.get((c.article_id, pno)) if pno is not None else None
        method = "para_anchor_no_expr"
        if base is None:                          # 문단 앵커가 없으면 기사 앵커로 폴백
            if c.article_id not in eligible_articles:
                continue
            base = article_base[c.article_id]
            method = "article_base_no_expr"
        c.period = base
        c.exclusion_code = "PARTIAL_PERIOD" if not is_std_period(base) else ""
        c.kosis_eligible = None          # period가 바뀌었으므로 재파생
        c.finalize()
        t.update(period_resolved=base, period_method=method,
                 partial=not is_std_period(base))
        fixed += 1
    return fixed


def _backfill_comparison(claims: list[ClaimRecord], traces: list[dict],
                         article_base: dict[str, str]) -> int:
    """기사 단위 **비교 기준(SPECIFIC)**을 증감형 Claim에 상속한다.

    근거(dev 실측 99차): cb 불일치 69건 중 35건이 '골든 SPECIFIC ↔ 파이프 공백'이고,
    그 대부분이 한 기사(5년 전 대비 물가 기사)에 몰려 있다. 기사가 "2020년 9월 대비"라는
    비교 틀을 리드에서 한 번 세우고 이후 품목별 문장은 그것을 되풀이하지 않기 때문이다.
    골든 저작자는 모든 품목 행에 같은 기준을 적어 넣었다.

    ★ 게이트 3개 — 전부 실측으로 좁혀졌다(무게이트 +32.5/−29 → 최종 **+26/−1**):
      ① SPECIFIC 앵커만. YOY 앵커까지 상속하면 골든이 비워 둔 칸 6개를 깬다(이득 0).
         SPECIFIC은 그 기사 특유의 비교 틀이라 저작자가 매 행에 적지만, YOY는 기본값이라
         적기도 하고 비우기도 한다.
      ② 기사 안에서 SPECIFIC (기준, 시점) 쌍이 **유일**할 것.
      ③ **Claim의 period가 기사 기준 시점과 같을 것.** 이것이 결정적이었다 — '연도별로
         2020년 4.4%, 2021년 5.9%…' 같은 나열 문장의 각 값은 그 해의 전년 대비라
         기사 비교 틀과 무관한데, 이 게이트가 그 11건을 정확히 걸러낸다.
    """
    from collections import defaultdict
    grouped: dict[str, list[ClaimRecord]] = defaultdict(list)
    for c in claims:
        grouped[c.article_id].append(c)
    anchors: dict[str, tuple[str, str]] = {}
    for aid, cs in grouped.items():
        pairs = {(c.comparison_basis, c.comparison_period) for c in cs
                 if c.comparison_basis == "SPECIFIC" and c.comparison_period}
        others = {c.comparison_basis for c in cs if c.comparison_basis} - {"SPECIFIC"}
        # ④ 기사에 SPECIFIC 말고 다른 비교 기준(YOY 등)이 섞여 있으면 상속하지 않는다.
        #    한 건뿐인 우발적 SPECIFIC이 기사 전체로 번지는 경로다 — 골든 stub 실측:
        #    이 게이트 없이는 고용 기사(대부분 전년 동월 대비)의 취업자 증감 4건에
        #    엉뚱한 'SPECIFIC/2022'가 붙었다.
        if len(pairs) == 1 and not others:
            anchors[aid] = next(iter(pairs))

    fixed = 0
    for c, t in zip(claims, traces):
        if c.comparison_basis or c.value_type not in ("change_rate", "change_amount"):
            continue
        a = anchors.get(c.article_id)
        if not a or not c.period or c.period != article_base.get(c.article_id):
            continue
        c.comparison_basis, c.comparison_period = a
        t.update(comparison_source="article_anchor")
        fixed += 1
    return fixed


def run(extractor: Extractor, outdir: Path | str,
        sentences_path=None, articles_path=None, normalizer=None,
        article_filter: set | None = None,
        breaker_rate: float = CIRCUIT_BREAKER_RATE,
        statutory_rule: bool = True, gate_fn=None, gate_all: bool = False) -> dict:
    """A→B(주입)→C→D(사전)→E 전 구간 실행. 반환: emit 요약(회계 수치·경로).

    normalizer(선택): src.p3_stage_d.MetricNormalizer — metric_normalized가 빈 Claim을
    사전으로 채운다(v0.4 계약 필드). 추출기가 이미 채운 값은 우선.
    article_filter(선택): 기사 ID 집합 — dev 8 부분 실행 등. 회계도 그 부분집합 기준.
    """
    global STATUTORY_RULE, GATE_ALL
    STATUTORY_RULE = statutory_rule
    GATE_ALL = gate_all
    kw = {}
    if sentences_path:
        kw["sentences_path"] = sentences_path
    if articles_path:
        kw["articles_path"] = articles_path
    candidates, _non_numeric, arts = collect_candidates(**kw)
    if article_filter:
        candidates = [c for c in candidates if c.article_id in article_filter]

    all_claims: list[ClaimRecord] = []
    all_excluded: list[ExcludedRecord] = []
    all_errors: list[dict] = []
    all_traces: list[dict] = []
    seq: dict[str, int] = {}          # 기사별 claim_id 슬롯 카운터
    error_sentences: set = set()
    carry: dict[str, tuple[str, int]] = {}   # 기사별 (해소 period, 문장 인덱스)
    sent_idx: dict[str, int] = {}            # 기사별 처리 문장 순번(거리 계산용)
    article_base: dict[str, str] = {}        # 기사에서 처음 확립된 표준형 시점(거리 무제한)

    for cand in candidates:
        sent_idx.setdefault(cand.article_id, 0)
        try:
            items = extractor(cand)
        except Exception as exc:      # LLM 호출 실패 등 — 한 콜이 전체를 죽이지 않게
            all_errors.append(_err(cand, "B", f"EXTRACTION_ERROR: {exc}", None))
            error_sentences.add(cand.key)
            continue
        if not items:
            all_errors.append(_err(cand, "B", "EXTRACTION_ERROR: 추출 결과 없음", None))
            error_sentences.add(cand.key)
            continue

        cl, ex, er, tr = process_sentence(cand, items, arts[cand.article_id]["text"],
                                          carry_anchor=_carry_value(carry, cand.article_id,
                                                                     sent_idx),
                                          article_anchor=article_base.get(cand.article_id),
                                          gate_fn=gate_fn)

        # Stage C 실패 → 수리 루프 1회(§5.6): 실패 사유를 피드백으로 재추출.
        # **병합 채택(59차)**: 원본에서 통과한 Claim은 그대로 두고, 재추출본에서 통과한
        # 것 중 **원본에 없던 수치**만 더한다(§5.6의 partial-accept를 슬롯 매칭 없이 구현).
        #   이전 방식은 "파괴적 실패 수가 엄격히 감소"할 때만 문장을 통째로 교체했는데,
        #   실측(test 43)에서 metric 창작 하나 때문에 역검증을 통과한 value·unit·period까지
        #   같이 버려졌다(오류 79건 중 35건). 재현율 손실의 주요 경로다.
        # 키는 (value, unit) — 둘 다 기사 표기 그대로라 재추출본과 안정적으로 대조된다.
        repair_fn = getattr(extractor, "repair", None)
        c_fails = [e for e in er if e["stage"] == "C" and e.get("item") is not None]
        # **조용한 누락도 수리 대상**(65차): 파괴적 실패가 없어도, 문장에 있는 수치가
        # 어느 항목에도 안 담겼으면 재추출한다. 종전에는 이 경우가 기계에 보이지 않아
        # 손실의 다수(dev 36건 중 22문장)가 그대로 확정됐다.
        hints = missing_value_hints(cand.text, cl, ex)
        if (c_fails or hints) and repair_fn is not None:
            parts = [e["reason"][:120] for e in c_fails[:4]]
            if hints:      # 파괴적 실패와 조용한 누락은 함께 알린다(수리 1회로 둘 다 겨냥)
                parts.append(
                    f"이 문장의 수치 {', '.join(hints)} 이(가) 어느 항목에도 없다. "
                    "통계 주장의 값이면 항목을 추가하라(수준값·비교 기준 시점 값·"
                    "증감량·증감률은 각각 별개 항목). 주장이 아니면 그대로 두라.")
            feedback = " | ".join(parts)
            try:
                items2 = repair_fn(cand, feedback)
            except Exception:
                items2 = None
            if items2:
                cl2, ex2, er2, tr2 = process_sentence(
                    cand, items2, arts[cand.article_id]["text"],
                    carry_anchor=_carry_value(carry, cand.article_id, sent_idx),
                    article_anchor=article_base.get(cand.article_id),
                    gate_fn=gate_fn)
                cl, ex, er, tr = _merge_repair(cl, ex, er, tr, cl2, ex2, er2, tr2)

        # 다음 문장으로 넘길 앵커 — 이 문장에서 해소된 마지막 period + 문장 인덱스.
        # 거리 제한(CARRY_MAX_DISTANCE)이 없으면 18문장 떨어진 값까지 상속돼
        # "당시"가 엉뚱한 연도를 물려받는다(실측). 인덱스를 함께 저장해 상한을 건다.
        sent_idx[cand.article_id] = sent_idx.get(cand.article_id, 0) + 1
        for c in cl:
            if c.period:
                carry[cand.article_id] = (c.period, sent_idx[cand.article_id])
                # 기사 기준 시점 — 처음 확립된 표준 4형식 하나만 고정(덮어쓰지 않는다)
                if cand.article_id not in article_base and is_std_period(c.period):
                    article_base[cand.article_id] = c.period

        # claim_id 부여 — claim-kind item 슬롯이 번호를 소모(실패 item도 소모 → 재실행 안정)
        aid = cand.article_id
        ok_by_idx = {t["item_index"]: c for c, t in zip(cl, tr)}
        claim_slots = sorted(set(ok_by_idx) |
                             {e["item_index"] for e in er
                              if e["item_index"] is not None
                              and _get(e["item"] or {}, "kind") not in ("excluded",)})
        for slot in claim_slots:
            seq[aid] = seq.get(aid, 0) + 1
            if slot in ok_by_idx:
                ok_by_idx[slot].claim_id = f"{aid}-C{seq[aid]:03d}"
        for c, t in zip(cl, tr):
            t["claim_id"] = c.claim_id

        all_claims += cl
        all_excluded += ex
        all_errors += er
        all_traces += tr
        if er:
            error_sentences.add(cand.key)

    _backfill_duration(all_claims, all_traces, article_base)
    _backfill_no_expr(all_claims, all_traces, article_base,
                      para_of={c.key: c.para for c in candidates
                               if c.para is not None})

    # v0.5(80차): comparison_period 재파생 — 기사 단위 period 후처리(앵커 상속·후보정)로
    # period가 바뀐 Claim의 YOY/PREV_PERIOD 절대시점을 최종 period 기준으로 다시 계산.
    # SPECIFIC은 표면형 자체가 절대시점이라 period 변화와 무관(재파생 불요).
    #
    # cb 백필(91차): LLM이 비교 표면형을 아예 안 내는 유형이 cb 공백의 전부였다
    # (dev 실측: 골든 YOY ↔ 공백 16건 중 표면형 미출력 16 · 룰 미해소 0).
    # ⚠ 단순 어휘 매칭(_CB_YOY/_CB_PREV)은 기각 — '지난달 수출은 1.3% 감소'의 '지난달'은
    # **대상 시점**이지 비교 기준이 아닌데 오백필됐다(골든 stub 실측 10건 중 7건이 이 유형).
    # 비교 기준은 **비교 조사와 결합된 형태**로만 식별한다: "전년 (동월) 대비"·"작년보다"·
    # "전월 대비" — 사이 간극 4자 이내("작년 11월 보고서보다" 같은 원거리 결합은 배제).
    for c in all_claims:
        if (not c.comparison_basis and c.value_type in ("change_rate", "change_amount")):
            has_yoy = bool(_CB_BACKFILL_YOY.search(c.claim))
            has_prev = bool(_CB_BACKFILL_PREV.search(c.claim))
            if has_yoy and not has_prev:
                c.comparison_basis = "YOY"
            elif has_prev and not has_yoy:
                c.comparison_basis = "PREV_PERIOD"
    for c in all_claims:
        if c.comparison_basis == "YOY":
            c.comparison_period = yoy_of(c.period)
        elif c.comparison_basis == "PREV_PERIOD":
            c.comparison_period = prev_slot(c.period)
    _backfill_comparison(all_claims, all_traces, article_base)

    # 서킷브레이커(§5.6): 오류 문장 비율이 임계 초과 = 개별 문제가 아니라 계통 결함.
    # 기본 3%(전량 실행). dev 튜닝 런은 완주해 성적표를 내는 것이 목적이고 dev 8에
    # 최고난도 음성 기사가 의도적으로 포함돼 있어(정당한 거부가 오류로 계상) 상향 허용.
    if candidates and len(error_sentences) / len(candidates) > breaker_rate:
        # 진단 가시성: 중단하더라도 오류 목록은 남긴다 — 무엇이 계통 결함인지 봐야 고친다
        dump = Path(outdir) / "errors_breaker.jsonl"
        dump.parent.mkdir(parents=True, exist_ok=True)
        with open(dump, "w", encoding="utf-8") as f:
            import json as _json
            for e in all_errors:
                f.write(_json.dumps(e, ensure_ascii=False) + "\n")
        raise AccountingError(
            f"서킷브레이커 발동 — 오류 문장 {len(error_sentences)}/{len(candidates)}"
            f" ({len(error_sentences) / len(candidates):.1%}) > {breaker_rate:.0%}"
            f" : 프롬프트/파서 계통 결함으로 간주(§5.6). 오류 목록: {dump}")

    # Stage D — 표준명 사전 적용(v0.4: metric_normalized는 계약 필드 = 크리티컬 패스).
    # 기본값(None)이면 사전 파일에서 자동 로드 — 배선 누락으로 신규 계약 필드가
    # 전건 null인 파일이 무경고 산출되는 함정 차단(리뷰 high). 명시적 False로만 생략.
    # ※ 사전 파일이 **없어도** normalizer를 만든다 — 55차 정책상 기본 동작은
    #   "verbatim metric 복사"이고 그건 빈 사전으로도 성립한다. 파일 존재를 조건으로
    #   걸면 새로 클론한 환경(data/는 저장소 미포함)에서 Stage D가 통째로 건너뛰어져
    #   계약 필드 metric_normalized가 전건 null로 나간다(실측 경로).
    dictionary_version = None
    if normalizer is None:
        from src.p3_stage_d import DICTIONARY_DEFAULT, load_dictionary, MetricNormalizer
        normalizer = MetricNormalizer(load_dictionary())
        if Path(DICTIONARY_DEFAULT).exists():
            import hashlib
            dictionary_version = hashlib.sha1(Path(DICTIONARY_DEFAULT).read_bytes()).hexdigest()[:8]
    stage_d_stats = None
    if normalizer:
        stage_d_stats = normalizer.apply(all_claims, all_traces)

    summary = emit_all(outdir, candidates, all_claims, all_excluded, all_errors, all_traces)
    summary["dictionary_version"] = dictionary_version   # 재현 추적(리니지 4튜플)
    summary["stage_d"] = stage_d_stats or "skipped"
    return summary


def make_golden_stub_extractor(golden_path=None) -> Extractor:
    """골든 라벨을 그대로 돌려주는 stub Stage B — 무HCX E2E 스모크(§5.6 ③)용."""
    from src.p3_golden import load_golden, GOLDEN_DEFAULT

    gold = load_golden(golden_path or GOLDEN_DEFAULT)
    by_sentence: dict[tuple[str, str], list[dict]] = {}
    for c in gold.claims:
        by_sentence.setdefault((c.article_id, c.sent_id), []).append({
            "kind": "claim", "forecast": c.forecast, "metric": c.metric,
            "metric_normalized": c.metric_normalized, "value": c.value, "unit": c.unit,
            "value_type": c.value_type, "direction": c.direction, "period": c.period,
            "comparison_basis": c.comparison_basis,
            "comparison_period": c.comparison_period, "note": c.note,
        })
    for e in gold.excluded:
        by_sentence.setdefault((e.article_id, e.sent_id), []).append({
            "kind": "excluded", "exclusion_code": e.exclusion_code, "note": e.note,
        })

    def extractor(cand: SentenceCandidate) -> list[dict]:
        return by_sentence.get(cand.key, [])

    return extractor

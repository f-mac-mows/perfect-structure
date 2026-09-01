# -*- coding: utf-8 -*-
"""P3 Claim 추출 — 스키마·period 문법·eligible 파생·v0.5 15필드 사영 (CLAUDE.md §5.6).

내부 표준 = 골든셋(claim_silver_set_ver2) 18필드(79차: comparison_period 신설).
공식 인수인계는 §4.1의 **v0.5 15필드** 사영(80차 팀장 승인) —
v0.4 8필드 + exclusion_code(75차) + approx(78차) + value_num·value_type·direction·
comparison_basis(enum)·comparison_period(71차 확정, 80차 구현).
- kosis_eligible = not(period가 표준 4형식이 아님 or forecast=Y)  — §4.8 파생식
- 사영 시 period는 표준 4형식만 통과, 확장형(부분기간·월범위·연범위)은 null화(원본은 full/trace 보존)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

PIPELINE_VERSION = "p3_v1"

# ── period 문법 ──────────────────────────────────────────────
# 표준 4형식 (KOSIS 조회 가능 · 7필드 계약 허용)
RE_PERIOD_STD = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])|-Q[1-4]|-H[12])?$")
# 확장형 (내부 보존용 — 사영 시 null)
RE_MONTH_RANGE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])~\d{4}-(0[1-9]|1[0-2])$")
RE_YEAR_RANGE = re.compile(r"^\d{4}~\d{4}$")
_DAY = r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])"
RE_DAY_FORM = re.compile(rf"^{_DAY}(~{_DAY})?$")

# ── enum (계약·내부) ─────────────────────────────────────────
VALUE_TYPES = frozenset({"level", "change_rate", "change_amount", "share_ratio"})
DIRECTIONS = frozenset({"increase", "decrease"})
# 계약 제외 코드 — §5.3 현행 5종 전체. excluded 행 전용(PARTIAL_PERIOD는 제외가 아니라
# Claim 행의 eligible=false 신호이므로 여기 넣지 않는다 — §4.8)
CONTRACT_EXCLUSION_CODES = frozenset({
    "NON_STAT_NUMBER", "METAPHOR_COMPARISON", "AMBIGUOUS_METRIC",
    "RELATIVE_NO_BASE", "DUPLICATE",
})
# 내부 전용 코드 — errors.jsonl 소속. excluded.jsonl(계약 파일)에는 절대 넣지 않는다
INTERNAL_ERROR_CODE = "EXTRACTION_ERROR"
CLAIM_ALLOWED_CODES = frozenset({"", "PARTIAL_PERIOD"})  # kind=claim 행의 code 정합(§5.6 kind×code)


def is_std_period(period: str | None) -> bool:
    return bool(period) and bool(RE_PERIOD_STD.match(period.strip()))


# 월범위·연범위 — 시작·끝이 표준 주기라 각 시점 조회가 가능하다
_RE_MONTH_RANGE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])~(\d{4})-(0[1-9]|1[0-2])$")
_RE_YEAR_RANGE = re.compile(r"^(\d{4})~(\d{4})$")


def is_verifiable_period(period: str | None) -> bool:
    """검증 시도 가능한 period — 표준 4형식 + **월범위·연범위**(74차 사용자 결정).

    구간 평균·누계 주장("9~10월 일평균 수출액 3.2% 증가")도 시작·끝이 KOSIS 표준
    주기(월/연)면 다운스트림이 구간 값들을 조회해 계산할 수 있으므로 넘긴다 —
    계산 시도 여부는 그쪽 판단(판단불가면 UNVERIFIED — 정직 원칙 그대로).
    **일(DD) 단위는 계속 제외**(62차 — KOSIS에 일 단위 데이터는 예외적).
    ※ 계약 관점에선 period 문법 확장 = v0.5 협의 사항(팀장 고지 필요).
    """
    if is_std_period(period):
        return True
    if not period:
        return False
    p = period.strip()
    m = _RE_MONTH_RANGE.match(p)
    if m:
        return (m.group(1), m.group(2)) <= (m.group(3), m.group(4))
    m = _RE_YEAR_RANGE.match(p)
    if m:
        return m.group(1) <= m.group(2)
    return False


def is_valid_period_form(period: str | None) -> bool:
    """내부 스키마에서 허용하는 period 표기 전체(빈값 포함).

    형식뿐 아니라 실재성도 본다 — 달력에 없는 날짜(2025-02-30)와
    역전 범위(시작>끝)는 반려(47차 "불가능 날짜 반려"의 완성).
    """
    if not period:
        return True
    p = period.strip()
    if RE_PERIOD_STD.match(p):
        return True
    if RE_MONTH_RANGE.match(p):
        a, b = p.split("~")
        return a <= b   # YYYY-MM 사전순 = 시간순
    if RE_YEAR_RANGE.match(p):
        a, b = p.split("~")
        return int(a) <= int(b)
    if RE_DAY_FORM.match(p):
        import datetime as _dt
        try:
            parts = [_dt.date.fromisoformat(x) for x in p.split("~")]
        except ValueError:
            return False   # 2025-02-30 등 달력 밖 날짜
        return len(parts) == 1 or parts[0] <= parts[1]
    return False


def derive_kosis_eligible(period: str | None, forecast: str | None) -> bool:
    """§4.8 파생식. forecast는 'Y'만 참으로 본다(빈값·'N'은 비전망).

    74차: 판정 기준이 is_std_period → is_verifiable_period로 확장(월범위·연범위 포함).
    """
    return is_verifiable_period(period) and (forecast or "").strip().upper() != "Y"


# ── comparison_basis enum (v0.5 — 70차 확정) ─────────────────
# 다운스트림 분기(resolve_yoy_change vs resolve_period_change)와 1:1.
# YOY = 1년 전 같은 시점 · PREV_PERIOD = 발표 주기상 바로 직전 칸 ·
# SPECIFIC = 특정 절대 시점(comparison_period 병기) · "" = 비교 기준 없음/미상
COMPARISON_ENUMS = frozenset({"YOY", "PREV_PERIOD", "SPECIFIC"})


# ── value_num 파생 (v0.5 — 71차 사용자 결정: "기술 문서에 없다면 만들자") ──
# 수사 문자열 → 숫자. 결정적 룰(§8-4 — LLM 미사용), 실패 시 None(억지 추정 금지).
# 팀장 파이프라인의 disambiguate_by_value가 숫자 대조를 하므로 변환 책임을 1번이 진다.
_VN_SEG = re.compile(r"(\d+(?:\.\d+)?)(조|억|만|천)?")
_VN_SCALE = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3}
# 스케일 없는 마지막 세그먼트의 자릿값 = **일의 자리(리터럴)** — 80차 사용자 결정.
# "27억6000"은 27억+6000이지 27억6000만이 아니다. 축약 관례("12억5000"=12억5000만)를
# 추정 해석하는 것은 verbatim 원칙 위반 — 기사 표기가 의도와 다르면(만 생략 등)
# 그 불일치는 다운스트림 값 대조에서 드러나는 것이 맞다.


def parse_value_num(value: str | None) -> int | float | None:
    """value(기사 표기 그대로) → 숫자. "13만"→130000 · "10만4943"→104943 ·
    "1조2000억"→1200000000000 · "4만5014.04"→45014.04 · "1.0"→1.0 · "1,234"→1234 ·
    "27억6000"→2700006000(무단위 꼬리 = 일의 자리 **리터럴** — 80차 사용자 결정,
    축약 관례 추정 금지).

    문법: [-]?(숫자[.소수부]?[조|억|만|천]?)+ — 스케일은 큰 단위부터 **내림차순만**,
    소수부는 마지막 세그먼트에만, 스케일 없는 세그먼트(일의 자리)는 마지막에만,
    각 세그먼트 값은 직전 스케일 미만(자리 넘침 "1조20000억" 반려).
    세그먼트 사이 공백("5만 5000")과 콤마는 표기 변이로 흡수, 음수("-3382")는 부호 유지.
    문법 밖 문자열(범위 "70~100"·서술어)은 None — 파생 실패를 0 등으로 뭉개지 않는다(§8-6).
    """
    if not value:
        return None
    s = value.strip().replace(",", "").replace(" ", "")
    sign = 1
    if s.startswith("-"):
        sign, s = -1, s[1:]
    if not s or not s[0].isdigit():
        return None
    # 정수 세그먼트는 int 산술로 누적 — float 누적은 2^53 근처에서 '틀린 숫자'를
    # 조용히 낸다(적대 검증 실측: '9007199254740993'→…992). 소수는 마지막 세그먼트만
    # 허용되므로 그 하나만 float로 합산한다.
    total, pos, prev_scale, frac = 0, 0, None, None
    while pos < len(s):
        m = _VN_SEG.match(s, pos)
        if not m:
            return None
        if m.group(2) is None:
            if m.end() != len(s):
                return None                  # 스케일 없는 세그먼트는 마지막만(일의 자리)
            scale = 1
        else:
            scale = _VN_SCALE[m.group(2)]
        if "." in m.group(1):
            if m.end() != len(s):
                return None                  # 소수부는 마지막 세그먼트만
            seg = float(m.group(1)) * scale
            frac = seg
        else:
            seg = int(m.group(1)) * scale
            total += seg
        if prev_scale is not None and (scale >= prev_scale or seg >= prev_scale):
            return None                      # 스케일 역순("2000억1조")·자리 넘침
        prev_scale = scale
        pos = m.end()
    if frac is None:
        return sign * total                  # int 정확 — 크기 제한 불필요
    # 원표기에 소수점이 있으면 float("1.0"→1.0 — 표기 정밀도 보존)
    return sign * (total + frac)


# ── approx 탐지 (78차 — 계약 파생 필드) ──────────────────────
# 29차 근사·경계 표현 사전을 의미별로 분류. value에는 안 담고(62차) 계약 사영 시 파생한다.
# 팀장 파이프라인의 claim 확인 오류 보고로 추가 — "8% 넘게"(실제 8.3%)를 숫자만 대조하면
# 허위 MISMATCH가 나는 경로를 구조화 필드로 차단한다.
# 98차 Phase 4: 어간 누락 보강 — '웃돌았다/밑돌았다'(활용형)와 '돌파'가 미등재였다.
# '달하다'는 정확 보고이므로 넣지 않는다(경계 주장이 아님).
_APPROX_GTE = ("넘게", "넘는", "넘어", "이상", "초과", "남짓", "웃도", "웃돌", "돌파")
_APPROX_LTE = ("이하", "미만", "이내", "가까이", "육박", "밑도", "밑돌")
_APPROX_NEAR = ("가량", "안팎")                                          # ±근사
_APPROX_AFTER = re.compile(
    r"^[\s을를이가은는에도]{0,2}(" + "|".join(_APPROX_GTE + _APPROX_LTE + _APPROX_NEAR) + ")")
_APPROX_PREFIX = re.compile(r"약\s*$")


def detect_approx(value: str, unit: str, sentence: str) -> str | None:
    """value+unit 주변의 근사·경계어 → 'GTE' | 'LTE' | 'APPROX' | None.

    결정적 룰 — value는 역검증으로 문장 실존이 보장되므로 그 등장 위치의 앞("약")과
    뒤(조사 0~2자 + 경계어)만 본다. 경계어가 앞뒤 모두면 뒤(경계)가 우선 —
    "약 1200조원 이상"은 하한 주장이다. 같은 값이 문장에 두 번이면 첫 등장 기준(드묾).
    """
    if not value or not sentence:
        return None
    m = re.search(re.escape(value) + r"\s*" + re.escape(unit or ""), sentence)
    if not m:
        return None
    after = _APPROX_AFTER.match(sentence[m.end():m.end() + 8])
    if after:
        w = after.group(1)
        if w in _APPROX_GTE:
            return "GTE"
        if w in _APPROX_LTE:
            return "LTE"
        return "APPROX"
    if _APPROX_PREFIX.search(sentence[max(0, m.start() - 4):m.start()]):
        return "APPROX"
    return None


# ── 레코드 ───────────────────────────────────────────────────
@dataclass
class ClaimRecord:
    """내부 표준 17필드 — 골든셋 스키마와 1:1."""

    claim_id: str
    article_id: str
    sent_id: str
    posted_date: str
    claim: str                      # 문장 원문 그대로 (재작성 금지)
    metric: str                     # verbatim — 구성 어휘가 기사에 실존해야 함
    metric_normalized: str = ""     # Stage D 산출(자유 합성 허용 열)
    value: str = ""                 # 기사 표기 그대로
    unit: str = ""
    value_type: str = ""            # VALUE_TYPES
    direction: str = ""             # DIRECTIONS
    period: str = ""                # period 문법 참조
    comparison_basis: str = ""      # 79차: enum(YOY|PREV_PERIOD|SPECIFIC|"") — 표면형에서 전환
    comparison_period: str = ""     # 79차: 비교 기준의 절대 시점(알 수 있으면 항상)
    forecast: str = "N"             # 'Y' | 'N'
    kosis_eligible: bool | None = None  # None이면 finalize()에서 파생
    exclusion_code: str = ""        # CLAIM_ALLOWED_CODES
    note: str = ""

    def finalize(self) -> "ClaimRecord":
        if self.kosis_eligible is None:
            self.kosis_eligible = derive_kosis_eligible(self.period, self.forecast)
        return self

    def to_handoff(self) -> dict:
        """§4.1 **v0.5 15필드** 사영(80차 팀장 승인) — 계약 파일(claims.jsonl) 행.

        v0.4(50차): `metric_normalized` 승격 — 2번의 확장 씨앗. 미정규화(사전 미스·미승인)는
        null로 나가고 2번은 verbatim metric으로 폴백한다.
        v0.5(80차): +value_num(수사→숫자, 결정적 파생) · value_type · direction ·
        comparison_basis(enum) · comparison_period — 팀장 통합 파이프라인(69차 기술문서)의
        disambiguate_by_value·스코어링 등락률 의도·파생 계산 분기가 요구하는 정보.
        finalize()를 강제(멱등)해 eligible 미파생(None) 묵살을 차단하고,
        '기간 null ∧ eligible=true' 같은 §4.1 위반 조합은 예외로 막는다(무경고 계약 위반 생산 금지).
        """
        self.finalize()
        # 74차: 월범위·연범위는 그대로 내보낸다(구간 평균·누계 주장 — 다운스트림이 계산 판단).
        # DD 단위 등 나머지 확장형만 null화(§4.8). period 문법 확장은 v0.5 협의 사항.
        period_out = self.period if is_verifiable_period(self.period) else None
        eligible = bool(self.kosis_eligible)
        if eligible and period_out is None:
            raise ValueError(
                f"{self.claim_id}: kosis_eligible=true인데 period가 비표준({self.period!r}) — §4.1 위반 조합"
            )
        # exclusion_code(75차 사용자 결정): 최종 리포트가 "왜 검증 안 했는지"를 쓸 수 있게
        # eligible=false의 사유를 계약에 싣는다. 내부 코드(PARTIAL_PERIOD)가 있으면 그것,
        # 없으면 사영 시점에 파생 — FORECAST(전망·추산) / PERIOD_UNRESOLVED(시점 미상).
        # eligible=true면 null. 우선순위: 내부 코드 > FORECAST > PERIOD_UNRESOLVED.
        # 사유 코드명은 다운스트림 합의 문서(96차)를 따른다 — 시점 미상은 `AMBIGUOUS_METRIC`.
        # ⚠ §5.3의 excluded.jsonl 코드 `AMBIGUOUS_METRIC`(지표 특정 불가)과 **이름이 같고
        #   뜻이 다르다**. 두 파일은 소비 지점이 달라 충돌하지는 않지만, 합의 문서의 명명이라
        #   그대로 쓰고 이 주석으로 남긴다(정리하려면 팀 합의 필요).
        reason = None
        if not eligible:
            reason = (self.exclusion_code or None
                      or ("FORECAST" if (self.forecast or "").upper() == "Y" else None)
                      or "AMBIGUOUS_METRIC")
        # 필드 순서·표기는 다운스트림 합의 문서(claims_jsonl_출력_예시.md, 96차)를 따른다:
        # ① article_id·sent_id 포함(조인 키를 파싱하지 않고 바로 쓰게) ② approx는
        # metric_normalized 뒤 ③ **빈 값은 null이 아니라 빈 문자열**(value_num만 숫자라 null).
        return {
            "claim_id": self.claim_id,
            "article_id": self.article_id,
            "sent_id": self.sent_id,
            "claim": self.claim,
            "metric": self.metric,
            "metric_normalized": self.metric_normalized or "",
            # 78차: 근사·경계 의미 — GTE("8% 넘게"→실제≥8) / LTE / APPROX("약") / ""(정확값).
            # value_num 대조 시 허용 오차 방향을 알려준다(팀장 claim 확인 오류 대응).
            "approx": detect_approx(self.value, self.unit, self.claim) or "",
            "value": self.value,
            # v0.5: 수사 문자열의 숫자 변환 — 파생 실패는 null(다운스트림은 value 원문 폴백)
            "value_num": parse_value_num(self.value),
            "unit": self.unit or "",
            "value_type": self.value_type or "",
            "direction": self.direction or "",
            "period": period_out or "",
            # v0.5: 비교 기준 — enum(YOY/PREV_PERIOD/SPECIFIC) + 절대 시점(파생 가능 시).
            # comparison_period도 period와 같은 사영 규칙: 검증 가능 형식(표준+월/연범위)만
            # 통과, 일 단위 등 확장형은 빈값(원본은 full/trace 보존 — 적대 검증 F7)
            "comparison_basis": self.comparison_basis or "",
            "comparison_period": (self.comparison_period
                                  if is_verifiable_period(self.comparison_period) else ""),
            "kosis_eligible": eligible,
            "exclusion_code": reason or "",
        }

    def schema_issues(self) -> list[str]:
        """형식 수준 검사(의미 검증은 Stage C 소관)."""
        issues = []
        if not self.claim_id:
            issues.append("claim_id 없음")
        if not self.claim:
            issues.append("claim(문장) 없음")
        if not self.metric:
            issues.append("metric 없음")
        if not self.value:
            issues.append("value 없음")
        if self.value_type and self.value_type not in VALUE_TYPES:
            issues.append(f"value_type 이탈: {self.value_type!r}")
        if self.direction and self.direction not in DIRECTIONS:
            issues.append(f"direction 이탈: {self.direction!r}")
        if self.comparison_basis and self.comparison_basis not in COMPARISON_ENUMS:
            # 79차부터 내부 표준도 enum — 표면형은 ClaimRecord에 두지 않는다(trace 보존)
            issues.append(f"comparison_basis 이탈: {self.comparison_basis!r}")
        if (self.forecast or "").upper() not in ("Y", "N", ""):
            issues.append(f"forecast 이탈: {self.forecast!r}")
        if self.exclusion_code not in CLAIM_ALLOWED_CODES:
            issues.append(f"claim 행에 부적합한 code: {self.exclusion_code!r}")
        if not is_valid_period_form(self.period):
            issues.append(f"period 형식 이탈: {self.period!r}")
        if is_std_period(self.period) and self.exclusion_code == "PARTIAL_PERIOD":
            issues.append("표준형 period에 PARTIAL_PERIOD 마킹")
        if not is_std_period(self.period) and self.period and self.exclusion_code != "PARTIAL_PERIOD" \
                and not RE_MONTH_RANGE.match(self.period.strip()):
            # 월범위는 코드 불요(사용자 규약) — 그 외 확장형·연범위는 PARTIAL_PERIOD 필수
            issues.append(f"비표준 period인데 PARTIAL_PERIOD 아님: {self.period!r}")
        return issues

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExcludedRecord:
    """제외 대장 행 — kind=excluded 문장(계약 코드만)."""

    article_id: str
    sent_id: str
    sentence: str
    exclusion_code: str
    note: str = ""

    def schema_issues(self) -> list[str]:
        if self.exclusion_code not in CONTRACT_EXCLUSION_CODES:
            return [f"계약 밖 제외 코드: {self.exclusion_code!r}"]
        return []

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentSet:
    """한 실행분(또는 골든)의 Claim·제외 전체 + 메타."""

    claims: list[ClaimRecord] = field(default_factory=list)
    excluded: list[ExcludedRecord] = field(default_factory=list)
    version: str = ""               # 골든 파일 해시 또는 pipeline_version

    def sentence_keys(self) -> set[tuple[str, str]]:
        keys = {(c.article_id, c.sent_id) for c in self.claims}
        keys |= {(e.article_id, e.sent_id) for e in self.excluded}
        return keys

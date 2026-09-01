# -*- coding: utf-8 -*-
"""P3 Stage B — HCX 구조화 추출 (§5.6): 프롬프트 조립 · HCX 클라이언트 · 파싱/수리 · 스파이크.

계약(§5.6): 모델 HCX-005 고정 · 입력 = 제목+posted_date+대상 문장+맥락(앞2·뒤2+리드)+시점 앵커 ·
재시도 = ①수리 대화(오류 피드백) ②temp 0.3~0.5 재샘플 ③실패 시 예외(파이프라인이
EXTRACTION_ERROR로 회계) · 모든 호출은 ReplayCache 경유(중단-재개·회귀·비용).

실 HCX 호출은 `.env` 필요: NCP_CLOVASTUDIO_API_KEY (필수) · HCX_ENDPOINT·HCX_MODEL (선택).
저장소 루트의 `.env.example`을 복사해 `.env`를 만든다 — 값 관리는 src/config.py 소관.
스파이크(§5.6 착수 조건): python -m src.p3_stage_b --spike 50  → 파싱 성공률·enum 이탈률 실측.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

from src import config, llm_meter
from src.p3_schemas import VALUE_TYPES, DIRECTIONS, CONTRACT_EXCLUSION_CODES
from src.p3_stage_a import SentenceCandidate
from src.p3_cache import ReplayCache

PROMPT_V1 = Path(__file__).parent / "prompts" / "extract_v1.txt"
REPAIR_PROMPT = Path(__file__).parent / "prompts" / "repair_v1.txt"
PROMPT_VERSION = "extract_v2.2"   # v2.1 + period 결정 절차 5단계(94차). v2.3/v2.4는 102차 실측으로 되돌림
# 맥락 윈도우는 캐시 키에 포함된다(params) — 창을 바꾸면 입력이 달라지므로 재생하면 안 된다.
CONTEXT_WINDOW = (2, 2)           # (앞, 뒤) 문장 수 — window 모드·para 없는 구 데이터용
# C-3 A/B 채택(90차): dev 18 실측 — paragraph가 10개 지표 중 9개 win/tie
# (F1 +0.008 · P +0.016 · 완전 일치 +0.031 · 위험 116→101 · FP 11→6), 호출당 토큰 중립.
# 개별 폭은 노이즈 대역 안이지만 방향이 일관돼 채택. para 없는 입력은 자동 window 폴백.
CONTEXT_MODE_DEFAULT = "paragraph"
# 동결 근거(dev 8, 프롬프트 v1.3~v1.7 5회 실행):
#   v1.5가 metric 0.740·direction 1.000·value 0.974·unit 0.987로 최고. metric은 2번의
#   유일한 입력(§2)이라 가중치가 크다. v1.6의 "수치 하나당 항목 하나" 강조는 재현율을
#   0.616→0.584로 떨어뜨렸고, v1.7(선두 forecast 한 줄 추가)은 metric을 0.689로 되돌렸다.
#   같은 본문에 한 줄 차이로 ±0.05가 흔들린다 = 노이즈 바닥. 추가 튜닝은 dev 과적합이다.
MODEL_DEFAULT = "HCX-005"
ENDPOINT_DEFAULT = "https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}"
# dev 18 = 46차 확정 8 + 70차 확장 10(사용자 승인 D1).
# 확장 근거: dev 8의 다중 주장 문장이 45개뿐이라 문장 1개 = 2.2pp — 분리 완전성 0.95를
# 판별할 수 없었다. test 43에서 다중 밀도 상위 10기사를 옮겨 45→120개(0.8pp)로 확대.
# ⚠ 이 10기사는 test 블라인드에서 영구 제외(소각)됐다 — test는 33기사로 재정의.
DEV_ARTICLES = ("Ae21581c3", "A272c31f6", "Ae4300e50", "Afab9aae1",
                "Af46d3c1a", "A82ae9f41", "A93bfa851", "A6d70d480",   # 46차 확정 8
                "Ae0fb5872", "A1a2e60a7", "Af715d863", "A9c10c595",   # 70차 확장 10
                "A97c411df", "Aa84160d5", "A1193d6ae", "Aa918ebe1",
                "A35f066d9", "A139753e7")
# few-shot에 쓰인 문장(dev 채점 제외 — §5.6 규율)
# 89차 새 체인 이관으로 sent_id 재맵핑(s010→s011 · s018→s020 · s004→s004+s005 분할)
FEW_SHOT_KEYS = {("Ae21581c3", "s002"), ("Ae21581c3", "s004"), ("Ae4300e50", "s002"),
                 ("A272c31f6", "s011"), ("A272c31f6", "s020"),
                 ("A6d70d480", "s004"), ("A6d70d480", "s005"),
                 ("Afab9aae1", "s005")}

ITEM_KEYS = ("kind", "exclusion_code", "forecast", "metric", "value", "unit",
             "value_type", "direction", "period", "comparison_basis", "note")

# Structured Outputs 스키마(95차 — HCX-007 전용, thinking과 동시 사용 불가).
# 우리 item은 평면 객체 배열이라 스키마가 그대로 대응한다. enum을 서버가 강제하므로
# 파서의 3단 폴백·enum 이탈 검사가 필요 없어지는 것이 기대 효과.
STRUCTURED_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["claim", "excluded"]},
            "exclusion_code": {"type": "string", "enum": [""] + sorted(CONTRACT_EXCLUSION_CODES)},
            "forecast": {"type": "string", "enum": ["Y", "N", ""]},
            "metric": {"type": "string"},
            "value": {"type": "string"},
            "unit": {"type": "string"},
            "value_type": {"type": "string",
                           "enum": ["", "level", "change_rate", "change_amount"]},
            "direction": {"type": "string", "enum": ["", "increase", "decrease"]},
            "period": {"type": "string"},
            "comparison_basis": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["kind", "metric", "value", "unit", "period"],
    },
}
RE_ANCHOR_SENT = re.compile(r"(조사|집계|실시|기준|발표|현재)")


# ── 입력 조립 ────────────────────────────────────────────────
def build_sentence_index(sentences_path=None) -> dict:
    sentences_path = sentences_path or (config.data_dir() / "sentences.jsonl")
    idx: dict[str, list[dict]] = {}
    with open(sentences_path, encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            idx.setdefault(s["article_id"], []).append(s)
    return idx


def extract_time_anchors(article_sents: list[dict], exclude_sid: str, cap: int = 2) -> list[str]:
    """기사 단위 시점 앵커 선추출(§5.6) — 조사 기간·기준일 문장(골든 period의 29%가 맥락 유래)."""
    out = []
    for s in article_sents:
        t = s.get("text") or ""
        if s["sent_id"] != exclude_sid and RE_ANCHOR_SENT.search(t) and re.search(r"\d", t):
            out.append(t)
        if len(out) >= cap:
            break
    return out


def build_user_message(cand: SentenceCandidate, sent_index: dict,
                       n_prev: int | None = None, n_next: int | None = None,
                       context_mode: str | None = None) -> str:
    """맥락 조립 — 두 모드(C-3 A/B, 90차):

    window(현행): 앞 n_prev·뒤 n_next 문장. 63차 실측 근거 —
      골든에서 정답 근거의 위치는 metric 앞 39.9%/뒤 12.6%, period 앞 24.1%/뒤 19.0%.
    paragraph(신규): **같은 문단의 형제 문장 전부**(순서 유지). 재크롤로 문단 경계가
      실물이 되면서 가능해졌다 — 기자가 묶은 의미 단위가 고정 창보다 정확한 경계라는
      가설. 문단이 대상 문장뿐이면 맥락 없음(이웃 문단 미차용 — 순수 A/B 통제).
    두 모드 모두 리드·시점 앵커는 동일하게 첨부. 캐시 키에 모드가 들어가야 한다.
    """
    context_mode = context_mode or CONTEXT_MODE_DEFAULT   # None → 채택 기본(90차)
    n_prev = CONTEXT_WINDOW[0] if n_prev is None else n_prev
    n_next = CONTEXT_WINDOW[1] if n_next is None else n_next
    sents = sent_index.get(cand.article_id, [])
    ids = [s["sent_id"] for s in sents]
    i = ids.index(cand.sent_id) if cand.sent_id in ids else -1
    if context_mode == "paragraph" and i >= 0 and sents[i].get("para") is not None:
        para = sents[i]["para"]
        prev2 = [s["text"] for s in sents[:i] if s.get("para") == para]
        next2 = [s["text"] for s in sents[i + 1:] if s.get("para") == para]
    else:
        prev2 = [s["text"] for s in sents[max(0, i - n_prev):i]] if i >= 0 else []
        next2 = [s["text"] for s in sents[i + 1:i + 1 + n_next]] if i >= 0 else []
    lead = [s["text"] for s in sents[:2] if s["sent_id"] != cand.sent_id][:2]
    anchors = extract_time_anchors(sents, cand.sent_id)
    parts = [f"제목: {cand.title}", f"작성일: {cand.posted_date}"]
    if lead:
        parts.append("기사 리드: " + " / ".join(lead))
    if prev2:
        parts.append("앞 문장: " + " / ".join(prev2))
    if next2:
        parts.append("뒤 문장: " + " / ".join(next2))
    if anchors:
        parts.append("시점 앵커 후보: " + " / ".join(anchors))
    parts.append(f"대상 문장: {cand.text}")
    parts.append("위 '대상 문장'만 추출하라. JSON 배열만 출력.")
    return "\n".join(parts)


# ── 응답 파싱·검증 ───────────────────────────────────────────
def _repair_flat_json(body: str) -> str:
    """평면 스키마 전제의 결정적 수리 — HCX-005 실측 기형 대응.

    스파이크 실측: 50건 중 10건이 객체 닫힘 중복('..."note":"..."}}]')으로 파싱 실패.
    우리 item은 중첩 없는 평면 객체라 '}}'는 정상 출력에 존재할 수 없다 —
    1차 파싱 실패 시에만 시도하므로 정상 중첩 JSON을 훼손할 경로도 없다.
    """
    fixed = re.sub(r"\}\s*\}(\s*[,\]])", r"}\1", body)   # 닫힘 중복
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)          # 트레일링 콤마
    return fixed


def _scan_flat_objects(body: str) -> list[dict] | None:
    """3차 폴백 — 고정 키 스키마 전제의 관용 스캐너.

    스파이크 실측 잔여 기형: note 문자열 '안'의 이스케이프 안 된 큰따옴표
    ('…상속."1300조원대"는…')는 JSON으로는 복구 불가. 대신 알려진 키들의 위치로
    필드 경계를 잡으면 내부 따옴표가 값 안에 그대로 보존된다. 값은 전부 문자열
    강제라 우리 item 계약과 동형.
    """
    inner = body.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return None
    parts = re.split(r"\}\s*,\s*\{", inner[1:-1].strip().lstrip("{").rstrip("}"))
    items = []
    for part in parts:
        positions = []
        for k in ITEM_KEYS:
            m = re.search(rf'"{k}"\s*:\s*', part)
            if m:
                positions.append((m.start(), m.end(), k))
        if not positions:
            continue
        positions.sort()
        obj = {}
        for i, (s, e, k) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(part)
            raw = part[e:end].strip().rstrip(",").strip()
            if raw.startswith('"'):
                raw = raw[1:]
            if raw.endswith('"'):
                raw = raw[:-1]
            obj[k] = raw.strip()
        items.append({k: obj.get(k, "") for k in ITEM_KEYS})
    return items or None


def parse_items(text: str) -> tuple[list[dict] | None, str]:
    """LLM 응답 → items. 실패 시 (None, 오류 메시지 — 수리 대화 피드백용).

    3단 파싱: ①엄격 JSON ②평면 스키마 결정적 수리(닫힘 중복·트레일링 콤마)
    ③고정 키 관용 스캐너(문자열 내 비이스케이프 따옴표) — 전부 HCX 0콜.
    """
    if not text or not text.strip():
        return None, "빈 응답"
    body = re.sub(r"```(json)?", "", text).strip()
    m = re.search(r"\[.*\]", body, re.S)
    if not m:
        return None, "JSON 배열을 찾을 수 없음"
    data = None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        try:
            data = json.loads(_repair_flat_json(m.group(0)))
        except json.JSONDecodeError:
            scanned = _scan_flat_objects(m.group(0))
            if scanned is not None:
                return scanned, ""
            return None, f"JSON 파싱 실패: {e}"
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        return None, "배열 원소가 객체가 아님"
    items = []
    for x in data:
        items.append({k: str(x.get(k, "") or "").strip() for k in ITEM_KEYS})
    return items, ""


def enum_deviations(items: list[dict]) -> list[str]:
    """스파이크 계측·수리 피드백용 — 파이프라인 게이트와 같은 기준."""
    dev = []
    if not items:   # 숫자 문장은 최소 1개 item(claim 또는 excluded)이 정답 — 빈 배열은 계약 위반
        dev.append("빈 배열 — 숫자 문장은 최소 1개 item(claim 또는 excluded) 필수")
    for i, it in enumerate(items):
        if it["kind"] not in ("claim", "excluded"):
            dev.append(f"item{i}.kind={it['kind']!r}")
        if it["kind"] == "claim" and it["forecast"] not in ("Y", "N"):
            dev.append(f"item{i}.forecast={it['forecast']!r}")
        if it["kind"] == "claim" and it["exclusion_code"] in CONTRACT_EXCLUSION_CODES:
            dev.append(f"item{i}: kind=claim인데 제외 코드 {it['exclusion_code']!r} — kind×code 모순")
        if it["kind"] == "excluded" and it["exclusion_code"] not in CONTRACT_EXCLUSION_CODES:
            dev.append(f"item{i}.exclusion_code={it['exclusion_code']!r}")
        if it["value_type"] and it["value_type"] not in VALUE_TYPES:
            dev.append(f"item{i}.value_type={it['value_type']!r}")
        if it["direction"] and it["direction"] not in DIRECTIONS:
            dev.append(f"item{i}.direction={it['direction']!r}")
    return dev


def load_system_prompt(path: Path | None = None) -> str:
    """시스템 프롬프트를 읽되 **선두 개발 메타 주석은 떼고** 보낸다.

    프롬프트 파일 맨 위의 `#` 블록은 사람용 기록(버전 이력·few-shot 문장 목록)이라
    모델에게는 잡음인데, 시스템 프롬프트는 **매 호출 통째로 전송**되므로 그대로 두면
    150토큰이 호출 수만큼 곱해져 과금된다(전량 2,695건 기준 약 8,156원).
    파일에는 남겨 두고 전송분에서만 뺀다 — 기록은 유지하고 요금만 줄인다.

    본문의 `## 출력 형식` 같은 마크다운 헤딩은 건드리지 않는다 —
    **첫 비주석 줄 이전의 연속된 주석 블록만** 제거한다.
    """
    text = (path or PROMPT_V1).read_text(encoding="utf-8")
    lines = text.splitlines(True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        if lines[i].lstrip().startswith("##"):    # 본문 헤딩을 만나면 중단
            break
        i += 1
    return "".join(lines[i:]).lstrip()


def extract_usage(result: dict) -> tuple[int | None, int | None]:
    """CLOVA 응답에서 (입력 토큰, 출력 토큰). 과금 기준값이라 추정하지 않는다.

    표기가 버전마다 다르다 — **v3 실측은 `usage.promptTokens`·`usage.completionTokens`**이고,
    API 문서(v1 계열)의 `inputLength`·`outputLength`도 아직 통용되므로 둘 다 읽는다.
    어느 쪽도 없으면 None(집계에서 제외) — 0으로 채우면 요금이 조용히 과소 집계된다.
    """
    u = result.get("usage") or {}
    tin = u.get("promptTokens", u.get("inputTokens", result.get("inputLength")))
    tout = u.get("completionTokens", u.get("outputTokens", result.get("outputLength")))
    return (tin if isinstance(tin, int) else None,
            tout if isinstance(tout, int) else None)


# ── HCX 클라이언트 ───────────────────────────────────────────
class HCXClient:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 endpoint: str | None = None, timeout: int = 60, meter=None,
                 thinking: str | None = None, structured: bool = False):
        # 키·엔드포인트는 `.env` 소관(src/config.py). 값은 로그·예외에 싣지 않는다(§7).
        self.api_key = api_key or config.get_hcx_api_key()
        self.model = model or config.get_env(config.ENV_HCX_MODEL) or MODEL_DEFAULT
        self.endpoint = (endpoint or config.get_env(config.ENV_HCX_ENDPOINT)
                         or ENDPOINT_DEFAULT).format(model=self.model)
        self.timeout = timeout
        self.meter = meter            # src.llm_meter.UsageMeter | None — 없으면 계측 생략
        # 추론 모델(HCX-007) 전용: thinking.effort ∈ none|low|medium|high.
        # None이면 필드 자체를 안 보냄(서버 기본 = low). 007 외 모델에는 전송하지 않는다.
        self.thinking = thinking
        # Structured Outputs(95차): responseFormat으로 서버가 JSON 스키마를 강제한다.
        # 문서 명시 제약 — **추론(thinking)과 동시 사용 불가**라 effort가 강제로 none이 된다.
        self.structured = structured
        if structured and thinking and thinking != "none":
            raise ValueError("Structured Outputs는 thinking과 동시 사용 불가(API 제약)")
        self.last_usage: dict | None = None

    @property
    def cache_tag(self) -> str:
        """record-replay 캐시 키의 모델 성분 — thinking 변형이 같은 키로 재생되면
        실험이 거짓말을 한다(모델 스와프 실험의 드리프트 가드)."""
        return (self.model + (f"|thinking:{self.thinking}" if self.thinking else "")
                + ("|structured" if self.structured else ""))

    def _build_payload(self, system: str, messages: list[dict],
                       temperature: float, max_tokens: int) -> dict:
        payload = {
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": max(temperature, 0.01),   # CLOVA는 0 미허용 이력 — 최솟값 근사
            "topP": 0.8, "repetitionPenalty": 1.1,
        }
        if self.structured:
            payload["responseFormat"] = {"type": "json", "schema": STRUCTURED_SCHEMA}
        if self.model.startswith("HCX-007"):
            # 추론 모델 API 차이(추론Thinking.pdf): maxTokens → maxCompletionTokens
            # (추론 토큰 포함 상한), thinking.effort로 추론 깊이 제어
            payload["maxCompletionTokens"] = max_tokens
            # Structured Outputs는 추론과 동시 사용 불가 — effort를 **명시적으로 none**으로
            # 보내야 서버가 받는다(생략하면 서버 기본 low와 충돌해 400: Invalid parameter).
            effort = "none" if self.structured else self.thinking
            if effort:
                payload["thinking"] = {"effort": effort}
        else:
            payload["maxTokens"] = max_tokens
        return payload

    def chat(self, system: str, messages: list[dict], temperature: float = 0.0,
             max_tokens: int = 2000, meta: dict | None = None) -> str:
        """429·5xx는 지수 백오프 재시도(일시 스로틀링을 서킷브레이커의 계통 결함으로
        오판하지 않게 — 리뷰), HTTPError 본문(CLOVA status.code)은 예외 메시지에 보존.

        meta: 계측용 꼬리표({article_id, sent_id, attempt, stage}) — 리소스 산정에서
        "어느 시도가 비용을 쓰는지"를 가르는 축이라 호출부가 넘겨준다.
        토큰 수는 추정하지 않고 응답의 inputLength·outputLength를 그대로 기록한다(과금 기준).
        """
        import time
        import urllib.error
        payload = self._build_payload(system, messages, temperature, max_tokens)
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})
        meta = dict(meta or {})
        delay = 2.0
        retries = 0
        wall0 = time.perf_counter()
        # 429는 분당 쿼터형 스로틀이라 5xx보다 길게 견딘다 — dev 18 실측에서 백오프
        # 4회(최대 16초)로는 75문장이 통째로 죽었다(81차). 서킷브레이커가 계통 결함을
        # 잡는 역할이므로 여기서는 일시 스로틀을 최대한 흡수한다.
        for attempt in range(7):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    status = resp.status
                latency_ms = (time.perf_counter() - t0) * 1000
                result = data.get("result") or {}
                tin, tout = extract_usage(result)
                self.last_usage = {"input_tokens": tin, "output_tokens": tout,
                                   "latency_ms": latency_ms}
                self._meter(meta, ok=True, status=status, retries=retries,
                            latency_ms=latency_ms,
                            wall_ms=(time.perf_counter() - wall0) * 1000,
                            usage=(tin, tout))
                return result["message"]["content"]
            except urllib.error.HTTPError as e:
                latency_ms = (time.perf_counter() - t0) * 1000
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                retryable = ((e.code == 429 and attempt < 6)
                             or (e.code in (500, 502, 503) and attempt < 3))
                if retryable:
                    retries += 1
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)   # 429 최대 대기: 2+4+8+16+32+60 ≈ 2분
                    continue
                # 실패도 기록한다 — 입력 토큰은 이미 소비됐고, 실패율이 곧 운영 리스크다
                self._meter(meta, ok=False, status=e.code, retries=retries,
                            latency_ms=latency_ms,
                            wall_ms=(time.perf_counter() - wall0) * 1000,
                            usage=None, error=f"HTTP {e.code}: {body[:120]}")
                raise RuntimeError(f"HCX HTTP {e.code}: {body}") from e
            except Exception as e:      # 타임아웃·연결 실패 등
                self._meter(meta, ok=False, status=None, retries=retries,
                            latency_ms=(time.perf_counter() - t0) * 1000,
                            wall_ms=(time.perf_counter() - wall0) * 1000,
                            usage=None, error=f"{type(e).__name__}: {e}"[:160])
                raise
        raise RuntimeError("HCX 재시도 소진")   # 도달 불가(위에서 raise) — 방어

    def _meter(self, meta: dict, *, ok: bool, status, retries: int,
               latency_ms: float, wall_ms: float, usage, error: str = "") -> None:
        """계측은 부수효과일 뿐 — 실패해도 파이프라인을 죽이지 않는다."""
        if self.meter is None:
            return
        tin, tout = usage if usage else (None, None)
        try:
            self.meter.record(
                model=self.model,
                stage=meta.get("stage", "stage_b"),
                attempt=meta.get("attempt", "initial"),
                article_id=meta.get("article_id", ""),
                sent_id=meta.get("sent_id", ""),
                ok=ok, http_status=status, http_retries=retries,
                latency_ms=latency_ms, wall_ms=wall_ms,
                input_tokens=tin, output_tokens=tout,
                error=error,
            )
        except Exception:
            pass


# ── 추출기(파이프라인 주입용) ─────────────────────────────────
def make_hcx_extractor(client, cache: ReplayCache, system_prompt: str, sent_index: dict,
                       meter=None, use_cache: bool = True, repair_client=None,
                       context_mode: str | None = None):
    """재시도 정책(§5.6): 원 호출 → 수리 대화 → temp 0.5 재샘플 → 예외.

    client는 .chat(system, messages, temperature=...) 인터페이스면 무엇이든(테스트 fake 포함).
    캐시에는 최종 성공 응답과 수리 체인이 payload 키 아래 저장된다.
    meter: 캐시 재생을 기록하기 위한 UsageMeter(실호출 계측은 client가 한다).
    use_cache=False: 재생을 끄고 전부 실호출 — 사용량 실측(요금·지연) 수집용.
    """
    def _cached(cand: SentenceCandidate) -> None:
        if meter is None:
            return
        try:
            meter.record(model=getattr(client, "model", ""), cached=True, ok=True,
                         attempt="replay", article_id=cand.article_id, sent_id=cand.sent_id)
        except Exception:
            pass

    def _meta(cand: SentenceCandidate, attempt: str) -> dict:
        return {"article_id": cand.article_id, "sent_id": cand.sent_id, "attempt": attempt}

    def extractor(cand: SentenceCandidate) -> list[dict]:
        payload = build_user_message(cand, sent_index, context_mode=context_mode)
        hit = cache.get(payload) if use_cache else None
        if hit is not None:
            items, err = parse_items(hit["response"])
            # 재생 경로도 현행 게이트로 재검증 — 빈 배열·구버전 enum 응답이 우회 재생되면
            # 해당 문장이 영구 오류로 고착된다(리뷰 실증). 미달이면 미스로 강등해 재호출.
            if items and not enum_deviations(items):
                _cached(cand)
                return items   # 재생 — HCX 0콜

        chain: list[dict] = []
        messages = [{"role": "user", "content": payload}]
        resp = client.chat(system_prompt, messages, temperature=0.0,
                           meta=_meta(cand, "initial"))
        chain.append({"attempt": "initial", "temperature": 0.0, "response": resp})
        items, err = parse_items(resp)
        dev = enum_deviations(items) if items is not None else []
        if items and not dev:
            cache.put(payload, resp, chain)
            return items

        # ① 수리 대화 — 오류·위반 필드를 피드백에 포함(같은 프롬프트 재호출 금지)
        feedback = err or ("출력 규칙 위반: " + "; ".join(dev))
        repair_msgs = messages + [
            {"role": "assistant", "content": resp},
            {"role": "user", "content": f"출력에 문제가 있다 — {feedback}. "
             "규칙을 다시 확인하고 JSON 배열만 다시 출력하라."}]
        resp2 = client.chat(system_prompt, repair_msgs, temperature=0.0,
                            meta=_meta(cand, "repair"))
        chain.append({"attempt": "repair", "temperature": 0.0, "response": resp2})
        items2, err2 = parse_items(resp2)
        if items2 and not enum_deviations(items2):
            cache.put(payload, resp2, chain)
            return items2

        # ② temp 재샘플 — 결정적 동일 실패 복제 금지(temp 0 재시도 금지, §5.6)
        resp3 = client.chat(system_prompt, messages, temperature=0.5,
                            meta=_meta(cand, "resample"))
        chain.append({"attempt": "resample", "temperature": 0.5, "response": resp3})
        items3, err3 = parse_items(resp3)
        if items3 and not enum_deviations(items3):
            cache.put(payload, resp3, chain)
            return items3

        raise RuntimeError(f"EXTRACTION_ERROR: 수리·재샘플 실패 — {err or err2 or err3 or '출력 규칙 위반'}")

    def repair(cand: SentenceCandidate, feedback: str) -> list[dict] | None:
        """Stage C 검증 실패·조용한 누락의 수리 재추출 — 전체 프롬프트 1콜.

        ⚠ 66차 실측 — **축약 수리 프롬프트는 기각됐다.** 수리 호출 토큰의 92%가 시스템
        프롬프트라 줄이면 싸질 줄 알았으나(`prompts/repair_v1.txt`, 640토큰), dev 재현율이
        0.888 → 0.736으로 떨어지고 오류가 2 → 8로 늘었다. 규칙·few-shot이 재추출 품질을
        실제로 떠받치고 있다 — 여기서 토큰을 아끼면 회수율을 잃는다.
        """
        base_payload = build_user_message(cand, sent_index, context_mode=context_mode)
        payload = base_payload + f"\n[STAGE_C_REPAIR] {feedback}"   # 원 호출과 캐시 키 분리
        hit = cache.get(payload) if use_cache else None
        if hit is not None:
            items, _ = parse_items(hit["response"])
            if items and not enum_deviations(items):
                _cached(cand)
                return items
        msg = (base_payload + "\n\n이전 추출의 문제: " + feedback +
               "\n위 '대상 문장'을 다시, 빠짐없이 추출해 JSON 배열만 출력하라.")
        resp = (repair_client or client).chat(
            system_prompt, [{"role": "user", "content": msg}], temperature=0.3,
            meta=_meta(cand, "stage_c_repair"))
        items, _ = parse_items(resp)
        if items and not enum_deviations(items):
            cache.put(payload, resp, [{"attempt": "stage_c_repair", "temperature": 0.3, "response": resp}])
            return items
        return None

    extractor.repair = repair
    return extractor


# ── 스파이크 (§5.6 착수 조건 실측) ────────────────────────────
def _filter_docs(ds, article_ids: set, drop_keys: set):
    """DocumentSet을 기사 부분집합으로 — few-shot 문장은 채점 제외(§5.6 규율)."""
    from src.p3_schemas import DocumentSet
    out = DocumentSet(version=ds.version)
    out.claims = [c for c in ds.claims
                  if c.article_id in article_ids and (c.article_id, c.sent_id) not in drop_keys]
    out.excluded = [e for e in ds.excluded
                    if e.article_id in article_ids and (e.article_id, e.sent_id) not in drop_keys]
    return out


# ── Phase 5-3 · 비통계 2차 게이트(105차) ──────────────────────────────────
# 멘토 순서 5번(AI 다중 호출)의 실행. 63차 스파이크 실측: Stage B 통합 판정만으로는
# 비통계 판정 재현율 73%인데 2차 게이트를 **조합**하면 83%로 오른다(+10pp).
# 당시 결론이 "조합 이득이 필요해지면 그때"였고, test 40 블라인드가 그 시점을 만들었다
# (오검출 101건 중 92건이 비통계 승격).
#
# 비용 통제: 모든 Claim에 묻지 않는다. ⓑ 룰이 **후보를 지목**한 것만 묻는다 —
# 룰은 재현율이 높고 정밀도가 낮으므로, 정밀도를 LLM이 보완하는 배치다.
GATE_MODEL_DEFAULT = "HCX-DASH-002"
GATE_PROMPT = """다음 수치가 **국가 통계로 검증할 수 있는 통계적 주장**인지 판정하라.

통계적 주장 = 세상의 상태를 집계한 값(기관·조사기관·기업집단이 집계한 것). → YES
통계적 주장 아님 = 제도가 정한 기준값(세율·요율·한도·공제·납부금),
  개별 상품·서비스 요금, 특정 기업 한 곳의 재무, 표결·조직 수, 순위, 조사 운영 수치. → NO

⚠ 세율은 NO지만 '법인세 세수'처럼 **걷힌 실적**은 YES다.

YES 또는 NO 한 단어만 출력하라."""


def make_nonstat_gate(client, cache=None, meter=None, sent_index=None):
    """(문장, metric, value, unit) → True면 통계 주장. 실패 시 True(막지 않는다)."""
    def build_ctx(cand) -> str:
        """제목 + 리드 + 앞뒤 문장.

        63차 실측: 맥락 없이 판정하면 과잉 차단 27%, 맥락을 주면 7%.
        sent_index가 없으면 빈 문자열 — 무맥락 모드와 캐시 키가 갈린다.
        """
        if not (sent_index and cand is not None):
            return ""
        sents = sent_index.get(getattr(cand, "article_id", ""), [])
        if not sents:
            return ""
        i = next((n for n, s in enumerate(sents) if s["sent_id"] == cand.sent_id), None)
        near = [] if i is None else [s["text"] for s in sents[max(0, i - 1):i + 2]]
        seen, ctx = set(), []
        for x in [getattr(cand, "title", "")] + [s["text"] for s in sents[:2]] + near:
            if x and x not in seen:
                seen.add(x)
                ctx.append(x)
        return "기사 맥락:\n" + "\n".join(f"  {x}" for x in ctx) + "\n\n"

    def gate(sentence: str, metric: str, value: str, unit: str, cand=None) -> bool:
        payload = (f"{build_ctx(cand)}문장: {sentence}\n"
                   f"지표: {metric}\n값: {value}{unit or ''}")
        hit = cache.get(payload) if cache else None
        if hit:
            return not hit["response"].strip().upper().startswith("NO")
        try:
            resp = client.chat(GATE_PROMPT, [{"role": "user", "content": payload}],
                               temperature=0.0, max_tokens=8,
                               meta={"stage": "gate", "attempt": "nonstat_gate"})
        except Exception:
            return True
        if cache:
            cache.put(payload, resp, [{"attempt": "nonstat_gate", "response": resp}])
        return not resp.strip().upper().startswith("NO")
    return gate


def run_dev(outdir: Path, cache_path: Path, meter=None, fresh: bool = False,
            repair_model: str | None = None, model: str | None = None,
            thinking: str | None = None, context_mode: str | None = None,
            structured: bool = False) -> None:
    """6단계 — dev 전체 실행(실 HCX) + 골든 채점 리포트.

    fresh=True: 캐시 재생을 끄고 전부 실호출 — 사용량 실측용(과금 발생).
    이때는 **동결 캐시를 오염시키지 않도록 별도 cache_path를 주는 것**을 권한다.
    model/thinking: 모델 스와프 실험(멘토 순서 1·2번 — 81차)용 오버라이드.
    캐시 키에 model+thinking이 들어가므로 변형 간 교차 재생은 일어나지 않지만,
    실험별로 별도 cache_path를 쓰는 것을 권한다.
    """
    from src.p3_pipeline import run
    from src.p3_emit import load_documents_jsonl
    from src.p3_golden import load_golden
    from src.p3_eval import evaluate

    client = HCXClient(meter=meter, model=model, thinking=thinking, structured=structured)
    cache = ReplayCache(cache_path, PROMPT_VERSION, client.cache_tag)
    sent_index = build_sentence_index()
    system_prompt = load_system_prompt()
    # 수리 전용 모델(선택) — 수리는 '힌트를 받아 다시 뽑는' 제약된 작업이라 값싼 모델로
    # 대체할 여지가 있는지 실측용. DASH-002는 HCX-005의 1/5 단가.
    rc = HCXClient(model=repair_model, meter=meter) if repair_model else None
    extractor = make_hcx_extractor(client, cache, system_prompt, sent_index,
                                   meter=meter, use_cache=not fresh, repair_client=rc,
                                   context_mode=context_mode)
    print(f"맥락 모드: {context_mode or CONTEXT_MODE_DEFAULT}")

    # dev 튜닝 런은 브레이커 해제 — 성적표(오류 목록 포함)를 산출하는 것이 목적이고,
    # 오류는 리포트의 FN·검토 목록으로 드러난다. 전량 실행(7단계)은 기본 3% 유지.
    summary = run(extractor, outdir, article_filter=set(DEV_ARTICLES), breaker_rate=1.0)
    print(f"dev 8 실행 — 문장 {summary['numeric_sentences']} · Claim {summary['claims']} · "
          f"제외 {summary['excluded']} · 오류 {summary['errors']} · eligible {summary['eligible_true']} · "
          f"사전 {summary['dictionary_version']} {summary['stage_d']}")

    gold = _filter_docs(load_golden(), set(DEV_ARTICLES), FEW_SHOT_KEYS)
    pred = _filter_docs(
        load_documents_jsonl(Path(outdir) / "claims_full.jsonl", Path(outdir) / "excluded.jsonl"),
        set(DEV_ARTICLES), FEW_SHOT_KEYS)
    rep = evaluate(gold, pred, prompt_version=PROMPT_VERSION)
    md = rep.to_markdown()
    # 실패율 배너 — dev 런은 브레이커를 꺼 두므로(성적표가 목적) 대량 실패도 리포트가 나온다.
    # 그 리포트를 '프롬프트 성적'으로 오독하지 않도록 머리에 못 박는다(102차 DNS 장애 실측).
    err_rate = summary["errors"] / max(summary["numeric_sentences"], 1)
    if err_rate > 0.03:
        banner = (f"> ⚠ **이 리포트는 신뢰할 수 없다** — 문장 {summary['numeric_sentences']}개 중 "
                  f"**{summary['errors']}개({err_rate:.0%})가 추출 실패**했다. 프롬프트 성적이 아니라\n"
                  f"> 실행 자체가 깨진 것일 수 있다(네트워크·인증·서버). "
                  f"`errors.jsonl`의 사유를 먼저 확인하라.\n\n")
        md = md.split("\n", 1)[0] + "\n\n" + banner + md.split("\n", 1)[1]
        print(f"\n⚠ 추출 실패 {summary['errors']}건({err_rate:.0%}) — 리포트 신뢰 불가. "
              f"errors.jsonl 확인 필요\n")
    report_path = Path(outdir) / "dev_report.md"
    report_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n리포트 저장: {report_path}")


def run_test(outdir: Path, cache_path: Path, meter=None,
             model: str | None = None, thinking: str | None = None,
             context_mode: str | None = None) -> None:
    """7단계 — test 동결 실행(§5.6: 프롬프트 동결 후 버전당 1회, 블라인드 소진).

    dev를 제외한 기사 전부. 튜닝 근거로 쓰지 않는다 — 결과는 보고용이며, 여기서 발견한
    예외는 골든 수정 판단에만 사용하고 프롬프트로 되먹이지 않는다(블라인드 유지).

    ★ model·thinking·context_mode를 **반드시 동결 구성으로 넘겨라.** 인자를 안 주면
    MODEL_DEFAULT(HCX-005)로 돌아 블라인드를 엉뚱한 구성에 태우게 된다(102차 직전 적발).
    """
    from src.p3_pipeline import run
    from src.p3_emit import load_documents_jsonl
    from src.p3_golden import load_golden
    from src.p3_eval import evaluate
    from src.p3_stage_a import collect_candidates

    cands, _, _ = collect_candidates()
    test_ids = {c.article_id for c in cands} - set(DEV_ARTICLES)
    client = HCXClient(meter=meter, model=model, thinking=thinking)
    cache = ReplayCache(cache_path, PROMPT_VERSION, client.cache_tag)
    sent_index = build_sentence_index()
    system_prompt = load_system_prompt()
    extractor = make_hcx_extractor(client, cache, system_prompt, sent_index, meter=meter,
                                   context_mode=context_mode)
    print(f"동결 구성 — 모델 {client.cache_tag} · 프롬프트 {PROMPT_VERSION} · "
          f"맥락 {context_mode or CONTEXT_MODE_DEFAULT}")

    # 채점 실행이므로 브레이커는 해제(성적표 산출이 목적) — 오류율은 리포트에 명시
    summary = run(extractor, outdir, article_filter=test_ids, breaker_rate=1.0)
    n = summary["numeric_sentences"]
    print(f"test {len(test_ids)}기사 — 문장 {n} · Claim {summary['claims']} · 제외 {summary['excluded']} · "
          f"오류 {summary['errors']}({summary['errors'] / n:.1%}) · eligible {summary['eligible_true']}")

    gold = _filter_docs(load_golden(), test_ids, set())
    pred = _filter_docs(
        load_documents_jsonl(Path(outdir) / "claims_full.jsonl", Path(outdir) / "excluded.jsonl"),
        test_ids, set())
    rep = evaluate(gold, pred, prompt_version=PROMPT_VERSION)
    md = rep.to_markdown()
    # dev와 같은 실패율 배너 — 깨진 실행이 성적표처럼 보이지 않게(102차 DNS 장애 실측)
    err_rate = summary["errors"] / max(n, 1)
    if err_rate > 0.03:
        banner = (f"> ⚠ **이 리포트는 신뢰할 수 없다** — 문장 {n}개 중 "
                  f"**{summary['errors']}개({err_rate:.0%})가 추출 실패**했다. "
                  f"`errors.jsonl`의 사유를 먼저 확인하라.\n\n")
        md = md.split("\n", 1)[0] + "\n\n" + banner + md.split("\n", 1)[1]
        print(f"\n⚠ 추출 실패 {summary['errors']}건({err_rate:.0%}) — 리포트 신뢰 불가\n")
    (Path(outdir) / "test_report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\n리포트 저장: {Path(outdir) / 'test_report.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage B — 스파이크/dev/test 실행")
    ap.add_argument("--spike", type=int, default=0)
    ap.add_argument("--dev-run", action="store_true", help="dev 8 전체 실행 + 골든 채점(6단계)")
    ap.add_argument("--test-run", action="store_true", help="test 43 동결 실행(7단계, 버전당 1회)")
    dev_out = config.data_dir() / "dev_run"
    ap.add_argument("--outdir", type=Path, default=dev_out)
    ap.add_argument("--cache", type=Path,
                    default=config.cache_dir() / "replay_extract_v1.jsonl")
    ap.add_argument("--fresh", action="store_true",
                    help="캐시 재생을 끄고 전부 실호출 — 사용량(토큰·요금·지연) 실측용. 과금 발생")
    ap.add_argument("--meter", type=Path, default=None,
                    help=f"사용량 로그 경로 (기본: data/{llm_meter.USAGE_LOG_DEFAULT})")
    ap.add_argument("--no-meter", action="store_true", help="사용량 기록 끄기")
    ap.add_argument("--repair-model", default=None,
                    help="수리 호출만 다른 모델로 (예: HCX-DASH-002) — 비용 실측용")
    ap.add_argument("--model", default=None,
                    help="추출 모델 오버라이드 (예: HCX-007, HCX-003) — 모델 스와프 실험(81차)")
    ap.add_argument("--thinking", default=None,
                    choices=("none", "low", "medium", "high"),
                    help="HCX-007 전용 추론 깊이. 미지정 시 필드 미전송(서버 기본 low)")
    ap.add_argument("--structured", action="store_true",
                    help="Structured Outputs(responseFormat) 사용 — HCX-007 전용, thinking 불가")
    ap.add_argument("--context-mode", default=None, choices=("window", "paragraph"),
                    help="맥락 조립(기본 paragraph — C-3 채택). window=앞2·뒤2 고정 창")
    args = ap.parse_args()

    meter = None if args.no_meter else llm_meter.UsageMeter(
        args.meter, prompt_version=PROMPT_VERSION)

    if args.test_run:
        out = args.outdir if args.outdir != dev_out else config.data_dir() / "test_run"
        run_test(out, args.cache, meter=meter, model=args.model,
                 thinking=args.thinking, context_mode=args.context_mode)
        _print_usage(meter)
        return
    if args.dev_run:
        run_dev(args.outdir, args.cache, meter=meter, fresh=args.fresh,
                repair_model=args.repair_model, model=args.model,
                thinking=args.thinking, context_mode=args.context_mode,
                structured=args.structured)
        _print_usage(meter)
        return
    if not args.spike:
        ap.error("--spike N / --dev-run / --test-run 중 하나를 지정하세요")

    from src.p3_stage_a import collect_candidates
    system_prompt = load_system_prompt()
    sent_index = build_sentence_index()
    candidates, _, _ = collect_candidates()
    dev = [c for c in candidates if c.article_id in DEV_ARTICLES and c.key not in FEW_SHOT_KEYS]
    target = dev[:args.spike]

    client = HCXClient(meter=meter, model=args.model, thinking=args.thinking,
                       structured=args.structured)   # .env의 NCP_CLOVASTUDIO_API_KEY 필요
    cache = ReplayCache(args.cache, PROMPT_VERSION, client.cache_tag)
    n_ok = n_repair = n_fail = n_replay = 0
    enum_dev_total = 0
    for cand in target:
        payload = build_user_message(cand, sent_index, context_mode=args.context_mode)
        # --fresh면 재생하지 않는다 — 토큰·지연 실측이 목적이라 캐시가 있으면 측정이 안 된다
        hit = None if args.fresh else cache.get(payload)
        if hit is not None:
            items, _ = parse_items(hit["response"])
            if items and not enum_deviations(items):
                n_ok += 1
                n_replay += 1
                if meter is not None:
                    meter.record(model=client.model, cached=True, attempt="replay",
                                 article_id=cand.article_id, sent_id=cand.sent_id)
                continue
        try:
            resp = client.chat(system_prompt, [{"role": "user", "content": payload}],
                               meta={"article_id": cand.article_id, "sent_id": cand.sent_id,
                                     "attempt": "initial", "stage": "spike"})
            items, err = parse_items(resp)
            dev_list = enum_deviations(items) if items is not None else []
            if items and not dev_list:
                n_ok += 1
                cache.put(payload, resp, [{"attempt": "initial", "temperature": 0.0, "response": resp}])
            else:
                n_repair += 1
                enum_dev_total += len(dev_list)
                print(f"  △ {cand.article_id}/{cand.sent_id}: {err or dev_list}")
        except Exception as exc:
            n_fail += 1
            print(f"  ✗ {cand.article_id}/{cand.sent_id}: {exc}")
    total = len(target)
    print(f"\n스파이크 {total}건 — 1차 성공 {n_ok}({n_ok / total:.0%}, 재생 {n_replay}) · "
          f"수리 필요 {n_repair} · 호출 실패 {n_fail} · enum 이탈 {enum_dev_total}건")
    print("판정 기준(§5.6): 1차 파싱 성공률·enum 이탈률로 HCX-005 구조화 출력 채택 여부 결정")
    _print_usage(meter)


def _print_usage(meter) -> None:
    """실행 끝에 사용량 한 줄 — 상세 집계는 `python -m src.llm_meter --report`."""
    if meter is None or not meter.records:
        return
    s = meter.summary()
    print(f"\n[사용량] API {s['calls_api']}콜 · 캐시 재생 {s['calls_cached']} · "
          f"토큰 입력 {s['input_tokens']:,}/출력 {s['output_tokens']:,} · "
          f"요금 {s['cost_krw']:,.2f}원(VAT 별도) → 로그 {meter.path}")


if __name__ == "__main__":
    main()

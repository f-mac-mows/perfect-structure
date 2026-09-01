# CLOVA Studio Chat Completions v3 API 공통 클라이언트를 제공합니다.
"""HCX-005와 HCX-007 호출을 위한 공통 클라이언트 모듈."""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


logger = logging.getLogger(__name__)

API_BASE_URL = "https://clovastudio.stream.ntruss.com/v3/chat-completions"
SUPPORTED_MODELS = {"HCX-005", "HCX-007"}

# [2026-08-22 신규 - 실측 확인, probe_national_debt_item_sales_final_check.py]
# 한 스크립트 실행 안에서 HCX-007 호출이 여러 번(Stage 2 갭 폴백 + weak_
# literal_tie 등) 연달아 몰리면 429(rate limit)가 실제로 난다 - claim 사이에
# 3초 delay를 넣어도 재현됨(단일 claim 처리 중 재시도 호출까지 포함하면 더
# 촘촘하게 몰릴 수 있음). 429만 제한적으로 재시도한다 - 다른 4xx/5xx는
# 재시도해도 의미가 없거나(설정 오류 등) 오히려 숨겨진 문제를 감추므로
# 그대로 예외를 올린다.
_MAX_429_RETRIES = 2
_DEFAULT_429_BACKOFF_SEC = 5.0

# [2026-08-24 신규 - 사용자가 실측한 429 응답 헤더 근거]
# x-ratelimit-limit-tokens: '60000', x-ratelimit-remaining-tokens: '0'을
# 실제로 확인 - 분당 토큰 예산 자체가 60000이다. 이 값은 헤더 이름으로
# 추측한 게 아니라 사용자가 429 응답에서 직접 받은 실측치를 그대로 상수화한
# 것이다.
_TOKEN_LIMIT_PER_MINUTE = 60000

# [2026-08-24 신규 - 실측 기반 근사치] 문자 수 -> 토큰 수 변환 비율을
# 추측하지 않고, README "2026-08-24 갱신" 항목에 기록된 실제 관측치를
# 그대로 쓴다: Stage 1 프롬프트 실측 promptTokens ~2550~2560, 같은 시점
# content_chars 실측 ~7,233자 -> 약 2.83자/토큰. 이 비율은 이 프로젝트의
# 실제 프롬프트(한글 위주 통계표 이름/축값) 실측 하나에서 뽑은 근사치라
# 다른 성격의 텍스트에는 안 맞을 수 있다 - 그래서 "정확한 사전 차단"이
# 아니라 "요청 하나가 명백히 예산 전체를 넘는 극단적 경우만 걸러내는
# 안전판"으로만 쓴다(임계값 자체가 60000토큰이라 여유가 크다).
_CHARS_PER_TOKEN_ESTIMATE = 2.83

# [2026-08-22 신규 - 사용자 요청, "미루면 프로덕션에서 못 쓴다"는 문제
# 대응] 429 재시도는 이미 터진 뒤에 대응하는 반응형이라, 배치 실행처럼
# 호출이 몰릴 때는 실패가 몇 건 나고서야 늦춰진다. 여기 추가한 건
# 사전 페이싱(proactive) - 연속 호출 사이 최소 간격을 강제해서 애초에
# 분당 토큰 한도 근처로 몰리는 걸 줄인다. 기본값 0.0(페이싱 없음)이라
# 이 파라미터를 안 넘기는 기존 모든 호출부(프로덕션 단일 기사 처리
# 포함)는 동작이 전혀 안 바뀐다 - 사용자가 지적한 대로 프로덕션은
# 기사 하나당 claim 10~20개라 인위적 지연이 UX를 깎아먹으면 안 되고,
# 이 한도 자체를 피해갈 방법도 없으므로(같은 API 토큰 풀을 쓰는 이상)
# 페이싱은 "배치/평가처럼 호출이 몰리는 상황에서 노이즈 없는 측정"을
# 위한 opt-in 도구로만 쓴다. 정확한 토큰 예산 기반 페이싱은 호출 전엔
# 실제 토큰 수를 모르므로(추측 안 함) 불가능 - 시간 간격만 강제하는
# 근사치다.
_last_call_at: Optional[float] = None


class HCXClientError(RuntimeError):
    """HCX 클라이언트 처리 중 발생하는 기본 예외."""


class HCXConfigurationError(HCXClientError):
    """HCX API 키 또는 모델 설정이 올바르지 않을 때 발생한다."""


class HCXRequestError(HCXClientError):
    """HCX API 네트워크 또는 HTTP 요청이 실패했을 때 발생한다."""


class HCXResponseParseError(HCXClientError):
    """HCX API 응답 형식이 예상과 다를 때 발생한다."""


class HCXTokenBudgetExceededError(HCXRequestError):
    """[2026-08-24 신규 - 사용자 실측 요청] 이 요청 하나의 추정 프롬프트
    토큰 수가 분당 토큰 한도(x-ratelimit-limit-tokens) 자체를 넘을 때
    발생한다. 이런 요청은 몇 번을 재시도하거나 얼마나 기다려도 절대
    성공할 수 없다(요청 하나가 예산 전체보다 크므로) - 429를 실제로
    맞고서 재시도 카운터만 소모한 뒤 실패하는 대신, 네트워크 호출
    자체를 생략하고 그 사유를 명확히 남긴다."""


def _load_api_key() -> str:
    """프로젝트 루트의 .env에서 CLOVA Studio API 키를 읽는다."""

    load_dotenv(".env")
    api_key = os.getenv("NCP_CLOVASTUDIO_API_KEY", "").strip()
    if not api_key:
        raise HCXConfigurationError(
            "NCP_CLOVASTUDIO_API_KEY가 설정되어 있지 않습니다."
        )

    return api_key


def _build_request_body(
    model_name: str,
    messages: List[Dict[str, Any]],
    thinking_effort: Optional[str] = None,
    temperature: Optional[float] = None,
    max_completion_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """모델별 Chat Completions v3 요청 body를 만든다.

    temperature: [2026-08-22 신규 - 사용자 제안, Task #29 item_diff 편차
    대응] 기본값 None이면 기존 하드코딩 값(HCX-005=0.5, HCX-007=0.3)을
    그대로 쓴다 - Stage 1/2는 이미 이 값으로 검증됐으므로 안 건드린다.
    호출부가 명시적으로 넘기면(예: hcx_stage3_resolver가 분류 작업에
    맞게 0.0을 넘김) 그 값으로 덮어쓴다 - "다양한 표현을 유연하게 받아야
    하는 작업"(표/셀 이름 해석)과 "정답이 고정된 분류 작업"(비교 모드
    판단)은 필요한 temperature가 다를 수 있다는 사용자 판단에 따른 opt-in
    파라미터.

    max_completion_tokens: [2026-08-22 신규, 2026-08-24 재구현 - 실측
    문서 확인 완료] 90개 배치 실행에서 Stage 1/3 응답이 지침("설명
    금지")을 어기고 잘리는 사례가 실측됐던 원인을 처음엔 "1000이 부족한
    것 같다"는 추측으로 CLI 스위트 스팟 실험(--max-completion-tokens)
    으로 접근했는데, 사용자가 "예측으로 때려 맞추지 말고 실제 데이터
    구조부터 분석하라"고 정정 - NCP 공식 문서
    (https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-thinking,
    2026-08-24 실측 확인)를 보니 thinking.effort별 maxCompletionTokens
    기본값이 이미 명시돼 있었다: none=512, low=5120, medium=10240,
    high=20480. 이 프로젝트 모든 리졸버(hcx_stage1/2/3_resolver.py,
    hcx_tree_resolver.py)는 예외 없이 effort="low"를 쓰는데, 우리 코드가
    자체적으로 1000이라는(문서 기본값 5120의 1/5에 불과한, 근거 없이
    고른) 값을 매 요청에 강제로 얹어서 API 자체 기본값보다 훨씬 작게
    깎아먹고 있었다 - truncation의 진짜 원인은 "모델이 원래 부족하다"가
    아니라 "우리가 임의로 예산을 5분의 1로 줄여서 보냈다"였다. 그래서
    max_completion_tokens가 None이면 이제 우리가 다른 숫자를 대신
    추측해서 채우지 않고 요청 body에서 이 필드 자체를 아예 뺀다 - 그러면
    API가 실제로 보낸 thinking.effort에 맞는 문서화된 기본값을 스스로
    적용한다(현재 모든 호출부가 low이므로 사실상 5120 적용). 호출부가
    명시적으로 값을 넘기면(예: --max-completion-tokens CLI 실험) 그 값이
    최우선으로 그대로 쓰인다."""

    if model_name == "HCX-005":
        body: Dict[str, Any] = {
            "messages": messages,
            "topP": 0.8,
            "topK": 0,
            "temperature": temperature if temperature is not None else 0.5,
            "repetitionPenalty": 1.1,
        }
        # [2026-08-24 - HCX-005는 thinking이 없어 문서화된 effort별 기본값이
        # 없다 - 이 300은 이번 재구현과 무관한 기존 값이라 그대로 둔다(이
        # 값이 문제라는 실측/신고가 없었음, 손대지 않는다).]
        body["maxTokens"] = max_completion_tokens if max_completion_tokens is not None else 300
        return body

    body = {
        "messages": messages,
        "thinking": {"effort": thinking_effort or "low"},
        "topP": 0.8,
        "topK": 0,
        "temperature": temperature if temperature is not None else 0.3,
        "repetitionPenalty": 1.1,
    }
    if max_completion_tokens is not None:
        body["maxCompletionTokens"] = max_completion_tokens
    # else: 필드 자체를 안 보낸다 - API가 thinking.effort에 따른 문서화된
    # 기본값(low=5120 등)을 스스로 적용하게 둔다.
    return body


# [2026-08-22 신규 - 사용자 실측 발견] hcx_stage1/2/3_resolver.py가 전부
# 이 모듈의 call_hcx를 통해 HCX-007을 호출하는데, 지금까지 이 함수는
# client.py의 _record_hcx_call/api_usage_log.jsonl 시스템과 완전히 분리돼
# 있었다 - Stage 3 배선 직후 사용자가 "api_usage_log에 안 남는 것 같다"고
# 지적해서 확인해보니, Stage 1/2도 처음부터 같은 gap을 갖고 있었다(각
# 리졸버가 api_usage_logger.record_api_usage로 docs/API_USAGE_LOG.md에는
# 이미 기록하고 있었지만, 그건 client.py의 jsonl과는 별개 시스템이라 하나가
# 채워져도 다른 하나는 저절로 안 채워진다). 이 함수 하나에 기록 지점을
# 두면 Stage 1/2/3 전부가 한 번에 소급 적용된다.
def _record_hcx_usage_safely(usage: Dict[str, Any]) -> None:
    """client.py의 _record_hcx_call(모듈 전역 카운터, atexit에
    api_usage_log.jsonl로 flush)에 이번 호출을 기록한다. client 모듈을
    함수 안에서 지연 import하는 이유: hcx_client.py는 HCX 호출 하나만
    책임지는 얇은 모듈로 유지하고(client.py는 KOSIS API까지 포함한 훨씬
    큰 모듈), 순환 임포트 위험도 피하기 위해서다. 기록 자체가 실패해도
    (예: 이 모듈이 client.py 없이 단독으로 쓰이는 다른 컨텍스트, 또는
    client.py의 내부 구조가 나중에 바뀌는 경우) 절대 HCX 호출 자체를
    실패시키면 안 되므로 조용히 삼킨다 - 로깅/계측이 본 기능을 깨뜨리면
    안 된다는 원칙."""
    try:
        import client as _kosis_client_module
        _kosis_client_module._record_hcx_call(usage)
    except Exception:
        pass


def _parse_ratelimit_reset_seconds(raw_value: Optional[str]) -> Optional[float]:
    """[2026-08-24 신규 - 사용자 실측 요청] x-ratelimit-reset-tokens/
    x-ratelimit-reset-requests 헤더값("42s", "-2s" 등 실측 확인된 형식)을
    초 단위 float로 변환한다. 파싱 실패(형식이 다른 경우) 또는 0 이하
    (이미 리셋된 상태라는 뜻이지만 같은 응답의 remaining이 아직 0으로
    보일 수 있어 - 사용자가 실측으로 확인한 'x-ratelimit-reset-tokens':
    '-2s' 사례) 값은 호출부가 신뢰하지 못하도록 None을 반환해 다음
    후보(다른 헤더 또는 기존 기본 backoff)로 넘어가게 한다."""
    if not raw_value:
        return None
    try:
        seconds = float(str(raw_value).strip().rstrip("sS"))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds


def call_hcx(
    model_name: str,
    messages: List[Dict[str, Any]],
    timeout: int = 30,
    thinking_effort: Optional[str] = None,
    temperature: Optional[float] = None,
    max_completion_tokens: Optional[int] = None,
    min_interval_sec: float = 0.0,
) -> Dict[str, Any]:
    """지정한 HCX 모델에 Chat Completions v3 요청을 보내고 JSON을 반환한다.

    temperature: [2026-08-22 신규] 기본값 None이면 기존 하드코딩 값을 그대로
    씀(_build_request_body 문서 참고) - 안 넘기면 기존 호출부(Stage 1/2 등)
    동작이 전혀 안 바뀐다.

    max_completion_tokens: [2026-08-22 신규] _build_request_body 문서 참고 -
    기본값 None이면 기존 하드코딩 값 그대로.

    min_interval_sec: [2026-08-22 신규 - 사용자 요청, opt-in 사전 페이싱]
    기본값 0.0(페이싱 없음) - 안 넘기면 프로덕션 단일 기사 처리를 포함한
    기존 모든 호출부 동작이 전혀 안 바뀐다. 0보다 크면, 직전 HCX 호출
    시작 시각으로부터 이 초만큼 지나지 않았으면 그 차이만큼 sleep한 뒤
    호출한다(모듈 전역 상태라 프로세스 안의 모든 호출자가 같은 페이스를
    공유함 - 배치 스크립트가 90개 claim을 돌릴 때 유효). 프로덕션에는
    안 쓰기로 함(기사 하나당 claim 10~20개인데 인위적 지연을 넣으면
    UX가 떨어지고, 어차피 같은 API 토큰 풀을 쓰는 이상 이 한도 자체를
    피해갈 수는 없다는 게 사용자 판단) - 배치/평가처럼 호출이 몰려서
    노이즈 없는 측정이 필요한 곳에서만 opt-in으로 쓴다."""

    if model_name not in SUPPORTED_MODELS:
        raise HCXConfigurationError(
            "지원하지 않는 model_name입니다. HCX-005 또는 HCX-007을 사용하세요."
        )

    if min_interval_sec > 0:
        global _last_call_at
        now = time.time()
        if _last_call_at is not None:
            wait_sec = min_interval_sec - (now - _last_call_at)
            if wait_sec > 0:
                time.sleep(wait_sec)
        _last_call_at = time.time()

    api_key = _load_api_key()
    url = f"{API_BASE_URL}/{model_name}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = _build_request_body(model_name, messages, thinking_effort, temperature, max_completion_tokens)

    # [2026-08-22 신규 - 사용자 요청, "요청 수 제한이 아니라 token 제한
    # 아니냐"는 의심 확인용] 이 프로젝트가 HCX 자체 토크나이저를 실측/확보한
    # 적이 없어(추측 안 함) 정확한 토큰 수는 못 낸다 - 문자 수만 크기 proxy로
    # 로그에 남긴다. 진짜 토큰 수는 성공 응답의 usage.promptTokens(아래)가
    # 유일한 실측치이고, 429로 실패하면 그 값 자체를 못 받으므로 대신 429
    # 응답 헤더 전체를 그대로 남긴다(어떤 rate-limit 헤더가 실제로 오는지
    # 이름을 미리 추측하지 않고 사용자가 직접 눈으로 확인하게 한다).
    messages_char_len = sum(len(str(m.get("content", ""))) for m in messages)
    logger.info(
        f"[HCX 요청 크기(문자 수 proxy, 실제 토큰 수 아님)] model={model_name} "
        f"messages={len(messages)}개 content_chars={messages_char_len}"
    )

    # [2026-08-24 신규 - 사용자 실측 요청: "입력 토큰이 60000개를 넘어가면
    # 사전에 skip(사유 기록)"] 이 요청 하나의 추정 프롬프트 토큰이 분당
    # 전체 한도(_TOKEN_LIMIT_PER_MINUTE)를 넘으면, 몇 번을 재시도하거나
    # 얼마나 기다려도 이 요청 자체는 절대 성공할 수 없다(요청 하나가
    # 예산 전체보다 큼) - 네트워크 호출도 시도하지 않고 바로 실패시킨다.
    # _CHARS_PER_TOKEN_ESTIMATE 자체가 실측 근사치라 정확한 토큰 수가
    # 아니므로, 임계값에 걸릴 정도로 명백히 큰 극단적 케이스에 대한
    # 안전판일 뿐이다.
    estimated_prompt_tokens = messages_char_len / _CHARS_PER_TOKEN_ESTIMATE
    if estimated_prompt_tokens > _TOKEN_LIMIT_PER_MINUTE:
        reason = (
            f"추정 프롬프트 토큰({estimated_prompt_tokens:.0f}, content_chars="
            f"{messages_char_len} / {_CHARS_PER_TOKEN_ESTIMATE}자/토큰 근사치)이 "
            f"분당 토큰 한도({_TOKEN_LIMIT_PER_MINUTE})를 넘어 이 요청은 재시도해도 "
            f"성공할 수 없습니다 - 네트워크 호출을 생략합니다."
        )
        logger.warning(f"[HCX 토큰 예산 초과 - 사전 차단] model={model_name} {reason}")
        _record_hcx_usage_safely({"error": reason, "elapsed_sec": 0.0})
        raise HCXTokenBudgetExceededError(reason)

    t0 = time.time()
    attempt = 0
    while True:
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as error:
            _record_hcx_usage_safely({"error": str(error), "elapsed_sec": time.time() - t0})
            raise HCXRequestError("HCX API 네트워크 오류가 발생했습니다.") from error

        if response.status_code == 429:
            logger.warning(f"[HCX 429 응답 헤더 전체(원인 확인용)] {dict(response.headers)}")

        if response.status_code == 429 and attempt < _MAX_429_RETRIES:
            # [2026-08-24 변경 - 사용자 실측 요청] 예전엔 Retry-After
            # 헤더(NCP가 실제로는 안 보내는 걸로 실측 확인됨) -> 없으면
            # 고정 backoff였다. 사용자가 실제 429 응답 헤더 전체를 보내줘
            # 확인해보니 NCP는 대신 x-ratelimit-reset-tokens/
            # x-ratelimit-reset-requests(둘 다 "42s" 같은 초 단위 문자열)를
            # 준다 - 이 요청이 왜 429를 맞았는지(x-ratelimit-remaining-
            # tokens='0'였음, 실측) 알려주는 값이므로 고정 backoff보다
            # 훨씬 정확한 재시도 시점이다. reset-tokens를 우선 신뢰하되
            # (토큰 예산이 실제 429 원인), 그 값이 파싱 불가/음수(사용자
            # 실측 사례: 'x-ratelimit-reset-tokens': '-2s' - remaining이
            # 아직 0으로 보이는 상태에서 reset 값 자체가 신뢰 못 할 수
            # 있음을 실측으로 확인)면 reset-requests -> Retry-After ->
            # 기존 고정 backoff 순으로 폴백한다.
            wait_sec = (
                _parse_ratelimit_reset_seconds(response.headers.get("x-ratelimit-reset-tokens"))
                or _parse_ratelimit_reset_seconds(response.headers.get("x-ratelimit-reset-requests"))
                or _parse_ratelimit_reset_seconds(response.headers.get("Retry-After"))
                or _DEFAULT_429_BACKOFF_SEC * (attempt + 1)
            )
            attempt += 1
            logger.warning(
                f"[HCX 429 rate limit - {wait_sec:.1f}초 후 재시도 {attempt}/{_MAX_429_RETRIES}] "
                f"model={model_name} "
                f"(x-ratelimit-reset-tokens={response.headers.get('x-ratelimit-reset-tokens')!r}, "
                f"x-ratelimit-reset-requests={response.headers.get('x-ratelimit-reset-requests')!r})"
            )
            time.sleep(wait_sec)
            continue
        break

    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        _record_hcx_usage_safely({
            "error": f"HTTP {response.status_code}", "elapsed_sec": time.time() - t0,
        })
        raise HCXRequestError(
            f"HCX API HTTP 오류가 발생했습니다. status={response.status_code}"
        ) from error

    try:
        result = response.json()
    except ValueError as error:
        _record_hcx_usage_safely({"error": "JSON 파싱 실패", "elapsed_sec": time.time() - t0})
        raise HCXResponseParseError("HCX API 응답을 JSON으로 읽을 수 없습니다.") from error

    elapsed_sec = time.time() - t0
    usage = {}
    finish_reason = None
    try:
        usage = result.get("result", {}).get("usage") or {}
        finish_reason = result.get("result", {}).get("finishReason")
    except AttributeError:
        usage = {}
    # [2026-08-24 신규 - 실측 문서 확인, 이전엔 존재조차 몰랐던 필드]
    # usage.completionTokensDetails.thinkingTokens - 생성 토큰 중 "추론
    # 내용"(message.thinkingContent, content와 별개 필드)이 실제로 몇
    # 토큰을 썼는지의 진짜 실측치. 이게 없으면 completionTokens 하나만
    # 보고 "잘렸다"만 알 수 있지 "추론이 예산을 얼마나 먹었는지"는 몰라서
    # 원인 진단이 안 됐다 - 이번에 hcx_client.py가 자체적으로 1000이라는
    # 근거 없는 값을 강제해온 게 진짜 원인으로 확인됐지만(위 _build_
    # request_body 문서 참고), 앞으로 다른 튜닝을 할 때도 이 값이 있어야
    # "추론이 늘어난 건지 답변 자체가 길어진 건지"를 구분할 수 있다.
    thinking_tokens = None
    try:
        thinking_tokens = usage.get("completionTokensDetails", {}).get("thinkingTokens")
    except AttributeError:
        thinking_tokens = None
    # [2026-08-22 신규 - 사용자 요청, "출력 토큰 window가 좁은 것 같다"
    # 진단용] finishReason="length"면 maxCompletionTokens 한도에 걸려
    # 답이 중간에 잘렸다는 뜻이다(90개 배치 실행에서 지침을 어기고
    # 설명하다 잘린 응답이 실제로 관찰됨, README "마흔다섯 번째") -
    # 이걸 눈으로 직접 확인해야 "몇 토큰이 적정한가"를 추측 없이 정할
    # 수 있다. finishReason="stop"이면 모델이 스스로 끝낸 정상 종료.
    logger.info(
        f"[HCX 실제 토큰 사용량 - API 응답 실측치] model={model_name} "
        f"promptTokens={usage.get('promptTokens')} completionTokens={usage.get('completionTokens')} "
        f"thinkingTokens={thinking_tokens} totalTokens={usage.get('totalTokens')} finishReason={finish_reason}"
    )
    if finish_reason == "length":
        logger.warning(
            f"[HCX 응답이 maxCompletionTokens 한도에 걸려 잘림] model={model_name} "
            f"completionTokens={usage.get('completionTokens')} thinkingTokens={thinking_tokens} "
            f"- maxCompletionTokens을 명시적으로 낮게 넘겼는지 확인 필요(2026-08-24: "
            f"기본값 None이면 이제 API 자체 문서화된 기본값을 씀, 그래도 잘리면 진짜 부족한 것)"
        )
    _record_hcx_usage_safely({
        "promptTokens": usage.get("promptTokens"),
        "completionTokens": usage.get("completionTokens"),
        "thinkingTokens": thinking_tokens,
        "totalTokens": usage.get("totalTokens"),
        "elapsed_sec": elapsed_sec,
    })
    return result


def extract_hcx_content(response_json: Dict[str, Any]) -> str:
    """정상 HCX 응답에서 모델의 최종 텍스트 content를 반환한다."""

    try:
        content = response_json["result"]["message"]["content"]
    except (KeyError, TypeError) as error:
        raise HCXResponseParseError(
            "HCX API 응답에 result.message.content가 없습니다."
        ) from error

    if not isinstance(content, str):
        raise HCXResponseParseError("HCX API 응답의 content가 문자열이 아닙니다.")

    return content


if __name__ == "__main__":
    selected_model = sys.argv[1] if len(sys.argv) > 1 else "HCX-005"
    sample_messages = [
        {
            "role": "system",
            "content": "당신은 한국어 통계 용어를 이해하는 AI입니다.",
        },
        {
            "role": "user",
            "content": "'재배면적'과 관련된 통계 검색 키워드 5개를 짧게 제시하세요.",
        },
    ]

    try:
        sample_response = call_hcx(selected_model, sample_messages)
        print(extract_hcx_content(sample_response))
    except HCXClientError as error:
        print(f"HCX 호출 오류: {error}")

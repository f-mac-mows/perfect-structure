"""[2026-08-22 신규 - 사용자 실측 발견 대응] hcx_client.call_hcx(Stage 1/2/3
리졸버가 전부 이걸 통해 HCX-007을 호출함)가 client.py의 _record_hcx_call
(api_usage_log.jsonl 시스템)에 실제로 기록하는지 확인한다. Task #29 Step 3
(item_diff)를 로컬에서 검증하던 중 사용자가 "api_usage_log에 안 남는 것
같다"고 지적해서 확인해보니, hcx_client.call_hcx가 처음부터 client.py의
jsonl 카운터와 완전히 분리돼 있었다(Stage 3만의 문제가 아니라 Stage 1/2도
같은 gap) - hcx_client.py에 기록 지점을 추가했으니, 여기 하나만 확인하면
세 Stage 모두 소급 검증된다.

requests.post/client._record_hcx_call/hcx_client._load_api_key를 직접
바꿔치기해서(이 프로젝트는 unittest.mock을 안 쓰고 손으로 주입/복원하는
관례) 네트워크 없이 배관만 확인한다.

사용법: python test_hcx_client_usage_logging.py (종료 코드 0 = 전체 PASS)
"""

import sys

import requests

import hcx_client
import client as kosis_client_module

_failures = []


def _check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


class _FakeResponse:
    def __init__(self, json_data, status_code=200, raise_http_error=False, headers=None):
        self._json_data = json_data
        self.status_code = status_code
        self._raise_http_error = raise_http_error
        self.headers = headers or {}

    def raise_for_status(self):
        if self._raise_http_error:
            raise requests.HTTPError("가짜 HTTP 오류")

    def json(self):
        return self._json_data


def test_call_hcx_success_records_usage_to_client_counters():
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    recorded = []

    def _fake_record(usage):
        recorded.append(usage)

    requests.post = lambda *a, **k: _FakeResponse(
        {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 10, "completionTokens": 2, "totalTokens": 12}}}
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = _fake_record
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("정상 응답이면 client._record_hcx_call이 정확히 1번 호출됨", len(recorded) == 1, str(recorded))
    if recorded:
        _check("promptTokens가 그대로 전달됨", recorded[0].get("promptTokens") == 10, str(recorded[0]))
        _check("completionTokens가 그대로 전달됨", recorded[0].get("completionTokens") == 2, str(recorded[0]))
        _check("elapsed_sec이 채워짐(0 이상)", recorded[0].get("elapsed_sec") is not None and recorded[0]["elapsed_sec"] >= 0, str(recorded[0]))


def test_call_hcx_http_error_still_records_usage_with_error():
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    recorded = []

    def _fake_record(usage):
        recorded.append(usage)

    requests.post = lambda *a, **k: _FakeResponse({}, status_code=500, raise_http_error=True)
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = _fake_record
    try:
        raised = False
        try:
            hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        except hcx_client.HCXRequestError:
            raised = True
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("HTTP 오류는 그대로 전파됨(기존 동작 유지)", raised)
    _check("HTTP 오류여도 usage 기록이 시도됨(error 필드 포함)", len(recorded) == 1 and "error" in recorded[0], str(recorded))


def test_call_hcx_network_error_still_records_usage_with_error():
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    recorded = []

    def _fake_record(usage):
        recorded.append(usage)

    def _raising_post(*a, **k):
        raise requests.ConnectionError("가짜 네트워크 오류")

    requests.post = _raising_post
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = _fake_record
    try:
        raised = False
        try:
            hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        except hcx_client.HCXRequestError:
            raised = True
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("네트워크 오류는 그대로 전파됨(기존 동작 유지)", raised)
    _check("네트워크 오류여도 usage 기록이 시도됨(error 필드 포함)", len(recorded) == 1 and "error" in recorded[0], str(recorded))


def test_call_hcx_recording_failure_does_not_break_call():
    """client._record_hcx_call 자체가 예외를 던져도(예: client.py가 없거나
    구조가 바뀐 경우) call_hcx는 정상적으로 응답을 반환해야 한다 - 로깅이
    본 기능을 깨뜨리면 안 된다는 원칙."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call

    def _raising_record(usage):
        raise RuntimeError("기록 자체가 실패하는 상황(재현용 가짜)")

    requests.post = lambda *a, **k: _FakeResponse(
        {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = _raising_record
    try:
        result = hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        raised = False
    except Exception:
        result = None
        raised = True
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("기록 자체가 예외를 던져도 call_hcx는 예외 없이 정상 반환", not raised, str(raised))
    _check("응답 content가 정상적으로 회수됨", result is not None and hcx_client.extract_hcx_content(result) == "3", str(result))


def test_call_hcx_429_retries_then_succeeds():
    """[2026-08-22 신규 - 실측 확인, probe_national_debt_item_sales_final_
    check.py에서 429가 실제로 재현됨] 429가 뜨면 바로 예외를 던지지 않고
    _MAX_429_RETRIES(2)번까지 재시도한 뒤 성공 응답을 그대로 반환해야 한다.
    time.sleep을 가짜로 바꿔치기해서 테스트가 실제로 몇 초씩 안 기다리게
    한다."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    original_sleep = hcx_client.time.sleep
    sleeps = []
    call_count = {"n": 0}

    def _fake_post(*a, **k):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return _FakeResponse({}, status_code=429, headers={"Retry-After": "1"})
        return _FakeResponse(
            {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
        )

    requests.post = _fake_post
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: None
    hcx_client.time.sleep = lambda sec: sleeps.append(sec)
    try:
        result = hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record
        hcx_client.time.sleep = original_sleep

    _check("429가 2번 나도 3번째 시도에서 성공 응답을 그대로 반환", hcx_client.extract_hcx_content(result) == "3")
    _check("총 요청 횟수 = 재시도 포함 3번", call_count["n"] == 3, str(call_count))
    _check("Retry-After 헤더값(1초)만큼 대기함", sleeps == [1.0, 1.0], str(sleeps))


def test_call_hcx_max_completion_tokens_override_reaches_request_body():
    """[2026-08-22 신규, 2026-08-24 재구현 - 실측 문서 확인 후 정정]
    max_completion_tokens를 넘기면 실제 요청 body의 maxCompletionTokens에
    그대로 실려야 한다. 안 넘기면(기존 모든 호출부) - 예전엔 우리가 고른
    1000을 강제로 채워 보냈는데, NCP 공식 문서 확인 결과 그게 thinking.
    effort="low"의 문서화된 기본값(5120)보다 훨씬 작은, 근거 없는 값이었다
    (truncation의 실제 원인). 이제는 이 필드 자체를 아예 안 보내서 API가
    thinking.effort에 맞는 자기 기본값을 쓰게 한다 - 그러니 "안 넘기면"의
    올바른 기대는 "1000이 실린다"가 아니라 "필드 자체가 body에 없다"."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    sent_bodies = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        sent_bodies.append(json)
        return _FakeResponse(
            {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
        )

    requests.post = _fake_post
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: None
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}], max_completion_tokens=2000)
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check(
        "기본값(안 넘기면) maxCompletionTokens 필드 자체가 없음(API 자체 기본값에 위임)",
        "maxCompletionTokens" not in sent_bodies[0], str(sent_bodies[0]),
    )
    _check("명시적으로 넘기면 그 값이 그대로 실림", sent_bodies[1]["maxCompletionTokens"] == 2000, str(sent_bodies[1]))


def test_call_hcx_min_interval_sec_paces_consecutive_calls():
    """[2026-08-22 신규 - 사용자 요청, "페이싱 도입하자"] min_interval_sec을
    넘기면, 직전 호출로부터 그만큼 안 지났을 때 그 차이만큼 sleep해야
    한다. 기본값(0.0, 안 넘기면)은 이 로직 자체를 안 타서 기존 모든
    호출부(프로덕션 포함) 동작이 그대로여야 한다."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    original_sleep = hcx_client.time.sleep
    original_time = hcx_client.time.time
    original_last_call_at = hcx_client._last_call_at
    hcx_client._last_call_at = None
    sleeps = []
    # 호출 1번당 time.time()이 4번 불린다(페이싱 체크, 페이싱 마커 갱신,
    # 요청 시작 t0, 경과시간 계산) - 두 번 호출하니 8개 값을 순서대로
    # 소비하는 가짜 시계를 만든다. 두 번째 호출은 첫 번째로부터 0.5초
    # 뒤라고 가정 - min_interval_sec=2.0이면 1.5초 sleep을 기대한다.
    clock_values = [1000.0, 1000.0, 1000.0, 1000.0, 1000.5, 1000.5, 1000.5, 1000.5]

    def _fake_time():
        return clock_values.pop(0)

    requests.post = lambda *a, **k: _FakeResponse(
        {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: None
    hcx_client.time.sleep = lambda sec: sleeps.append(sec)
    hcx_client.time.time = _fake_time
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}], min_interval_sec=2.0)
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}], min_interval_sec=2.0)
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record
        hcx_client.time.sleep = original_sleep
        hcx_client.time.time = original_time
        hcx_client._last_call_at = original_last_call_at

    _check("첫 호출은 직전 호출 기록이 없어 페이싱 sleep 없음 + 두 번째 호출에서 1.5초 sleep", sleeps == [1.5], str(sleeps))


def test_call_hcx_min_interval_sec_default_zero_skips_pacing_entirely():
    """min_interval_sec을 아예 안 넘기면(기존 모든 호출부) 페이싱 코드
    경로 자체를 안 타야 한다 - time.sleep이 429 재시도 목적 외에는
    절대 안 불려야 함(회귀 방지)."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    original_sleep = hcx_client.time.sleep
    original_last_call_at = hcx_client._last_call_at
    hcx_client._last_call_at = None
    sleeps = []

    requests.post = lambda *a, **k: _FakeResponse(
        {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: None
    hcx_client.time.sleep = lambda sec: sleeps.append(sec)
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record
        hcx_client.time.sleep = original_sleep
        hcx_client._last_call_at = original_last_call_at

    _check("min_interval_sec 기본값(0.0)이면 sleep이 전혀 안 불림(기존 호출부 회귀 없음)", sleeps == [], str(sleeps))


def test_call_hcx_extracts_thinking_tokens_from_completion_tokens_details():
    """[2026-08-24 신규 - 실측 문서 확인] usage.completionTokensDetails.
    thinkingTokens(추론 토큰 수 실측치, 예전엔 존재도 몰랐던 필드)가
    응답에 있으면 뽑아서 client._record_hcx_call에 함께 넘겨야 한다 -
    이게 있어야 "추론이 예산을 얼마나 먹었는지"를 completionTokens
    하나만 볼 때보다 정확히 진단할 수 있다."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    recorded = []

    requests.post = lambda *a, **k: _FakeResponse(
        {
            "result": {
                "message": {"content": "3"},
                "usage": {
                    "promptTokens": 58, "completionTokens": 631, "totalTokens": 689,
                    "completionTokensDetails": {"thinkingTokens": 366},
                },
            }
        }
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: recorded.append(usage)
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("thinkingTokens가 그대로 추출되어 기록됨", recorded and recorded[0].get("thinkingTokens") == 366, str(recorded))


def test_call_hcx_missing_completion_tokens_details_does_not_crash():
    """completionTokensDetails가 없는 응답(예: HCX-005, 또는 thinking을
    아예 안 쓴 호출)에서도 에러 없이 thinkingTokens=None으로 처리돼야
    한다 - 실측되기 전(예전) 응답 형태와의 하위 호환."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    recorded = []

    requests.post = lambda *a, **k: _FakeResponse(
        {"result": {"message": {"content": "3"}, "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2}}}
    )
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: recorded.append(usage)
    try:
        hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        crashed = False
    except Exception:
        crashed = True
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record

    _check("completionTokensDetails 없어도 크래시 안 함", not crashed)
    _check("thinkingTokens는 None으로 기록됨", recorded and recorded[0].get("thinkingTokens") is None, str(recorded))


def test_call_hcx_429_exceeds_max_retries_raises():
    """재시도 횟수를 넘어서도 계속 429면 결국 HCXRequestError를 던져야
    한다 - 무한 대기/무한 재시도로 빠지면 안 된다."""
    original_post = requests.post
    original_load_key = hcx_client._load_api_key
    original_record = kosis_client_module._record_hcx_call
    original_sleep = hcx_client.time.sleep
    call_count = {"n": 0}

    def _fake_post(*a, **k):
        call_count["n"] += 1
        return _FakeResponse({}, status_code=429, raise_http_error=True)

    requests.post = _fake_post
    hcx_client._load_api_key = lambda: "test-key"
    kosis_client_module._record_hcx_call = lambda usage: None
    hcx_client.time.sleep = lambda sec: None
    try:
        raised = False
        try:
            hcx_client.call_hcx("HCX-007", [{"role": "user", "content": "질의"}])
        except hcx_client.HCXRequestError:
            raised = True
    finally:
        requests.post = original_post
        hcx_client._load_api_key = original_load_key
        kosis_client_module._record_hcx_call = original_record
        hcx_client.time.sleep = original_sleep

    _check("재시도 한도(2번)를 넘기면 결국 HCXRequestError를 던짐", raised)
    _check("최초 시도 + 재시도 2번 = 총 3번만 요청함(무한 재시도 아님)", call_count["n"] == 3, str(call_count))


if __name__ == "__main__":
    test_call_hcx_success_records_usage_to_client_counters()
    test_call_hcx_http_error_still_records_usage_with_error()
    test_call_hcx_network_error_still_records_usage_with_error()
    test_call_hcx_recording_failure_does_not_break_call()
    test_call_hcx_429_retries_then_succeeds()
    test_call_hcx_max_completion_tokens_override_reaches_request_body()
    test_call_hcx_min_interval_sec_paces_consecutive_calls()
    test_call_hcx_min_interval_sec_default_zero_skips_pacing_entirely()
    test_call_hcx_extracts_thinking_tokens_from_completion_tokens_details()
    test_call_hcx_missing_completion_tokens_details_does_not_crash()
    test_call_hcx_429_exceeds_max_retries_raises()

    print()
    if _failures:
        print(f"{len(_failures)}건 FAIL: {_failures}")
        sys.exit(1)
    else:
        print("전체 PASS")
        sys.exit(0)

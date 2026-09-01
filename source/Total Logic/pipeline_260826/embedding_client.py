# CLOVA Studio Embedding v2 API의 독립 클라이언트를 제공합니다.
"""텍스트를 CLOVA Studio Embedding v2 벡터로 변환한다."""

import os
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import requests
from dotenv import load_dotenv


EMBEDDING_V2_URL = (
    "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
)
# [2026-08-19 버그 수정] 원래 .parent.parent였는데, 그러면 이 파일 기준
# 한 디렉터리 더 위(e2e/의 부모, 예: /Users/mows/)에서 .env를 찾는다 -
# 실제 .env는 이 파일과 같은 e2e/ 폴더에 있어서(client.py/config.py 등
# 다른 스크립트도 전부 이 위치를 씀) 이전엔 항상 조용히 못 찾고
# EmbeddingConfigurationError만 냈다(실측: python3 embedding_client.py
# 직접 실행해서 확인함). embedding_ranker.rank_keywords는 이 실패를
# 잡아서 "기존 순서 유지"로 조용히 폴백하므로 눈에 띄는 에러 없이
# embedding 기반 순위 매기기 자체가 계속 무력화돼 있었을 수 있다.
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TIMEOUT = 30


class EmbeddingClientError(RuntimeError):
    """Embedding 클라이언트 처리 중 발생하는 기본 예외."""


class EmbeddingConfigurationError(EmbeddingClientError):
    """Embedding API 설정이 올바르지 않을 때 발생한다."""


class EmbeddingInputError(EmbeddingClientError):
    """Embedding 입력 텍스트가 올바르지 않을 때 발생한다."""


class EmbeddingRequestError(EmbeddingClientError):
    """Embedding API 네트워크 또는 HTTP 요청이 실패했을 때 발생한다."""


class EmbeddingResponseParseError(EmbeddingClientError):
    """Embedding API 응답 형식이 예상과 다를 때 발생한다."""


def _load_api_key() -> str:
    """프로젝트 루트의 .env에서 CLOVA Studio API 키를 읽는다."""

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("NCP_CLOVASTUDIO_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingConfigurationError(
            "NCP_CLOVASTUDIO_API_KEY가 설정되어 있지 않습니다."
        )
    return api_key


def _normalize_text(text: str) -> str:
    """입력 텍스트를 검증하고 앞뒤 공백을 제거한다."""

    if not isinstance(text, str):
        raise EmbeddingInputError("Embedding 입력은 비어 있지 않은 문자열이어야 합니다.")

    normalized_text = text.strip()
    if not normalized_text:
        raise EmbeddingInputError("Embedding 입력은 비어 있지 않은 문자열이어야 합니다.")
    return normalized_text


def _extract_embedding(response_json: Dict[str, Any]) -> List[float]:
    """공식 응답의 result.embedding을 실수 리스트로 반환한다."""

    try:
        embedding = response_json["result"]["embedding"]
    except (KeyError, TypeError) as error:
        raise EmbeddingResponseParseError(
            "Embedding API 응답에 result.embedding이 없습니다."
        ) from error

    if not isinstance(embedding, list) or not embedding:
        raise EmbeddingResponseParseError(
            "Embedding API 응답의 embedding이 유효한 배열이 아닙니다."
        )
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in embedding
    ):
        raise EmbeddingResponseParseError(
            "Embedding API 응답의 embedding에 숫자가 아닌 값이 있습니다."
        )

    return [float(value) for value in embedding]


def _post_embedding_request(normalized_text: str) -> Dict[str, Any]:
    """실제 HTTP 요청 1회를 보내고 파싱된 JSON 응답 전체를 그대로
    돌려준다 - get_embedding()과 get_embedding_with_meta() 둘 다 이걸
    공유한다(중복 방지). 429 등 HTTP 에러거나 응답이 JSON이 아니면 여기서
    바로 예외를 던진다 - 호출부가 상태코드/재시도 판단에 쓰는
    EmbeddingRequestError(str(e)에 "429" 포함) 계약은 그대로 유지한다."""
    api_key = _load_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid4()),
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            EMBEDDING_V2_URL,
            headers=headers,
            json={"text": normalized_text},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as error:
        raise EmbeddingRequestError(
            "Embedding API 네트워크 오류가 발생했습니다."
        ) from error

    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        raise EmbeddingRequestError(
            f"Embedding API HTTP 오류가 발생했습니다. status={response.status_code}"
        ) from error

    try:
        response_json = response.json()
    except ValueError as error:
        raise EmbeddingResponseParseError(
            "Embedding API 응답을 JSON으로 읽을 수 없습니다."
        ) from error

    if not isinstance(response_json, dict):
        raise EmbeddingResponseParseError(
            "Embedding API 응답이 JSON 객체가 아닙니다."
        )
    return response_json


def get_embedding(text: str) -> List[float]:
    """text를 Embedding v2로 벡터화해서 실수 리스트로 반환한다."""

    normalized_text = _normalize_text(text)
    response_json = _post_embedding_request(normalized_text)
    return _extract_embedding(response_json)


def get_embedding_with_meta(text: str) -> "tuple[List[float], Dict[str, Any]]":
    """[2026-08-20 신규 - 사용자 요청, rate limit 진단용] get_embedding과
    똑같이 벡터를 계산하지만, CLOVA가 실제로 돌려준 응답 JSON 전체도 같이
    반환한다 - "이 API가 응답에 토큰 사용량 필드를 주는지" 자체가 아직
    미확정이라(공식 문서를 이 세션에서 직접 검증 못 했고, 이 샌드박스는
    네트워크가 막혀 있어 실제 응답을 볼 수 없다 - CLAUDE.md 실측 우선
    원칙) 필드명을 추측해서 파싱하는 대신 응답 전체를 그대로 넘긴다.
    사용자가 로컬에서 실행해서 response_json의 실제 키(예: result 안에
    inputTokens류 필드가 있는지)를 직접 확인하면 된다 - 있으면 다음부터
    그 필드를 정식으로 파싱하도록 고칠 수 있다."""

    normalized_text = _normalize_text(text)
    response_json = _post_embedding_request(normalized_text)
    embedding = _extract_embedding(response_json)
    return embedding, response_json


if __name__ == "__main__":
    sample_text = "국가채무 증가율"

    try:
        sample_embedding = get_embedding(sample_text)
        print("Embedding API 호출 성공")
        print(f"text: {sample_text}")
        print(f"dimension: {len(sample_embedding)}")
        print(f"first_values: {sample_embedding[:5]}")
    except EmbeddingClientError as error:
        print(f"Embedding API 호출 오류: {error}")

# 고정된 Claim JSON 입력의 유효성을 확인하고 공백을 정리합니다.
"""고정 Claim JSON 인터페이스를 정규화한다."""

from typing import Any, Dict


class InvalidClaimInputError(ValueError):
    """고정 입력 인터페이스 검증에 실패했을 때 발생하는 예외."""


def normalize_claim_input(input_data: Any) -> Dict[str, str]:
    """고정 JSON 객체에서 claim_id와 original_claim을 정규화한다."""

    if not isinstance(input_data, dict):
        raise InvalidClaimInputError("입력은 JSON 객체여야 합니다.")

    claim_id = input_data.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise InvalidClaimInputError("claim_id를 찾을 수 없습니다.")

    original_claim = input_data.get("original_claim")
    if not isinstance(original_claim, str):
        raise InvalidClaimInputError("Claim 문장을 찾을 수 없습니다.")

    normalized_claim = " ".join(original_claim.split())
    if not normalized_claim:
        raise InvalidClaimInputError("Claim 문장을 찾을 수 없습니다.")

    return {
        "claim_id": claim_id.strip(),
        "original_claim": normalized_claim,
    }

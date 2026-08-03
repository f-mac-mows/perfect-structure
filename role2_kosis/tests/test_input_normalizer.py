# 고정 Claim JSON 입력을 정규화하는 기능을 검증합니다.
import pytest

from src.input_normalizer import InvalidClaimInputError, normalize_claim_input


def test_normalizes_a_valid_fixed_json_input():
    input_data = {
        "claim_id": "C001",
        "original_claim": "지난달 취업자 수는 2804만1000명으로 감소했다.",
    }

    assert normalize_claim_input(input_data) == input_data


def test_trims_outer_whitespace_from_original_claim():
    assert normalize_claim_input(
        {
            "claim_id": "C001",
            "original_claim": "  지난달 취업자 수는 감소했다.  ",
        }
    ) == {
        "claim_id": "C001",
        "original_claim": "지난달 취업자 수는 감소했다.",
    }


def test_normalizes_repeated_whitespace_in_original_claim():
    assert normalize_claim_input(
        {
            "claim_id": "NEWS_0202",
            "original_claim": "지난달   취업자 수는   감소했다.",
        }
    ) == {
        "claim_id": "NEWS_0202",
        "original_claim": "지난달 취업자 수는 감소했다.",
    }


@pytest.mark.parametrize("input_data", [None, [], "Claim 문장", 100])
def test_rejects_an_input_that_is_not_a_json_object(input_data):
    with pytest.raises(InvalidClaimInputError, match="입력은 JSON 객체여야 합니다."):
        normalize_claim_input(input_data)


@pytest.mark.parametrize(
    "input_data",
    [
        {},
        {"claim_id": "", "original_claim": "Claim 문장"},
        {"claim_id": "   ", "original_claim": "Claim 문장"},
        {"original_claim": "Claim 문장"},
    ],
)
def test_rejects_a_missing_or_blank_claim_id(input_data):
    with pytest.raises(InvalidClaimInputError, match="claim_id를 찾을 수 없습니다."):
        normalize_claim_input(input_data)


@pytest.mark.parametrize(
    "input_data",
    [
        {"claim_id": "C001"},
        {"claim_id": "C001", "original_claim": ""},
        {"claim_id": "C001", "original_claim": "   "},
        {"claim_id": "C001", "original_claim": None},
        {"claim_id": "C001", "original_claim": 123},
    ],
)
def test_rejects_a_missing_blank_or_non_string_claim(input_data):
    with pytest.raises(InvalidClaimInputError, match="Claim 문장을 찾을 수 없습니다."):
        normalize_claim_input(input_data)

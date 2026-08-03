# Claim 문장에서 초기 KOSIS 검색 키워드를 추출하는 실험 기능을 검증합니다.
import csv
import json

from src.initial_keyword_extractor import (
    clean_claim_text,
    extract_initial_keywords,
    extract_keywords_from_csv,
)


def test_clean_claim_text_removes_numbers_time_and_change_expressions():
    cleaned = clean_claim_text("지난달 전국 취업자 수는 28만 명 증가했다.")

    assert "28만" not in cleaned
    assert "지난달" not in cleaned
    assert "증가" not in cleaned
    assert "전국 취업자 수" in cleaned


def test_clean_claim_text_preserves_condition_expressions():
    cleaned = clean_claim_text("15세 이상 청년 비정규직 취업자 수는 2만 명 늘었다.")

    assert "15세 이상" in cleaned
    assert "청년" in cleaned
    assert "비정규직" in cleaned


def test_extracts_primary_and_expanded_keywords_from_claim_text():
    result = extract_initial_keywords("지난달 전국 취업자 수는 28만 명 증가했다.")

    assert result == {
        "primary_keyword": "취업자 수",
        "expanded_keywords": ["취업자", "전국 취업자 수"],
    }


def test_prefers_a_complete_statistical_indicator_phrase():
    result = extract_initial_keywords("소비자물가지수는 전년 동월 대비 2.1% 상승했다.")

    assert result["primary_keyword"] == "소비자물가지수"
    assert "소비자물가지수" not in result["expanded_keywords"]


def test_ignores_reporting_context_before_the_indicator():
    result = extract_initial_keywords(
        "통계청이 발표한 고용 동향에 따르면, 지난달 취업자 수는 2804만명으로 감소했다."
    )

    assert result["primary_keyword"] == "취업자 수"


def test_extracts_a_claim_phrase_instead_of_a_generic_word_from_a_report_title():
    result = extract_initial_keywords(
        "국가데이터처가 발표한 경제활동인구조사에 따르면, 비정규직 근로자는 856만명이다."
    )

    assert result["primary_keyword"] == "근로자"
    assert "비정규직 근로자" in result["expanded_keywords"]


def test_does_not_invent_a_keyword_that_is_not_in_claim():
    claim = "지난달 혼인 건수는 22만2422건으로 늘었다."
    result = extract_initial_keywords(claim)

    assert result["primary_keyword"] == "혼인 건수"
    assert all(keyword in claim for keyword in [result["primary_keyword"], *result["expanded_keywords"]])
    assert "경제활동인구" not in result["expanded_keywords"]


def test_returns_empty_keywords_when_no_indicator_candidate_exists():
    result = extract_initial_keywords("관련 수치는 지난해보다 늘었다.")

    assert result == {"primary_keyword": "", "expanded_keywords": []}


def test_extract_keywords_from_csv_preserves_columns_and_writes_outputs(tmp_path):
    input_path = tmp_path / "claims.csv"
    json_path = tmp_path / "initial_keyword_extraction.json"
    output_path = tmp_path / "claims_with_keywords.csv"
    rows = [
        {"candidate_id": "KC0001", "original_claim": "지난달 전국 취업자 수는 28만 명 증가했다.", "human_review": ""},
        {"candidate_id": "KC0002", "original_claim": "관련 수치는 지난해보다 늘었다.", "human_review": ""},
    ]
    with input_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    results = extract_keywords_from_csv(input_path, json_path, output_path)

    assert results[0]["candidate_id"] == "KC0001"
    assert results[0]["primary_keyword"] == "취업자 수"
    assert results[1]["primary_keyword"] == ""

    with json_path.open(encoding="utf-8") as file:
        assert json.load(file) == results
    with output_path.open(encoding="utf-8-sig", newline="") as file:
        saved_rows = list(csv.DictReader(file))
    assert saved_rows[0]["human_review"] == ""
    assert saved_rows[0]["code_primary_keyword"] == "취업자 수"
    assert saved_rows[0]["code_expanded_keywords"] == "취업자|전국 취업자 수"
    assert saved_rows[0]["extraction_status"] == "success"
    assert saved_rows[1]["extraction_status"] == "empty"


def test_extract_keywords_from_csv_requires_claim_columns(tmp_path):
    input_path = tmp_path / "invalid.csv"
    json_path = tmp_path / "result.json"
    output_path = tmp_path / "result.csv"
    input_path.write_text("candidate_id\nKC0001\n", encoding="utf-8")

    try:
        extract_keywords_from_csv(input_path, json_path, output_path)
    except ValueError as error:
        assert "필수 CSV 컬럼이 없습니다" in str(error)
    else:
        raise AssertionError("필수 컬럼 누락 오류가 발생해야 합니다.")

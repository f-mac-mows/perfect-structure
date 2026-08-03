# 뉴스 원문에서 Claim 후보를 추출하는 스크립트의 동작을 검증합니다.
import csv

import pytest

from scripts.build_claim_candidates import (
    build_claim_candidates,
    contains_numeric_claim,
    deduplicate_candidates,
    extract_article_candidates,
    normalize_sentence,
    score_candidate,
    select_diverse_candidates,
    split_sentences,
)


def make_article(body: str, article_id: str = "NEWS_001"):
    return {
        "기사ID": article_id,
        "기사제목": "테스트 기사",
        "작성일": "2025-01-01",
        "URL": "https://example.com/news/1",
        "정제된_본문": body,
    }


def write_source_csv(path, rows, fieldnames=None):
    fieldnames = fieldnames or ["기사ID", "기사제목", "작성일", "URL", "정제된_본문"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_normalizes_sentence_whitespace():
    assert normalize_sentence("  취업자   수는  2804만1000명이다.  ") == "취업자 수는 2804만1000명이다."


def test_splits_sentences_without_breaking_decimals():
    text = "물가 상승률은 1.7%였다. 합계출산율은 0.75명이었다."

    assert split_sentences(text) == [
        "물가 상승률은 1.7%였다.",
        "합계출산율은 0.75명이었다.",
    ]


def test_detects_a_sentence_with_a_number_and_unit():
    assert contains_numeric_claim("취업자 수는 2804만1000명으로 감소했다.")


def test_excludes_a_sentence_without_a_number():
    assert not contains_numeric_claim("고용 상황은 좋지 않았다.")


def test_excludes_a_sentence_with_only_a_year():
    assert not contains_numeric_claim("2025년부터 제도가 시행된다.")


def test_extracts_only_numeric_candidates_from_an_article():
    article = make_article(
        "고용 상황은 좋지 않았다. 취업자 수는 2804만1000명으로 감소했다. 물가 상승률은 1.7%였다."
    )

    candidates = extract_article_candidates(article)

    assert [candidate["original_claim"] for candidate in candidates] == [
        "취업자 수는 2804만1000명으로 감소했다.",
        "물가 상승률은 1.7%였다.",
    ]


def test_limits_candidates_to_three_per_article():
    article = make_article(
        "첫 번째 통계는 1명으로 집계됐다. 두 번째 통계는 2명으로 집계됐다. 세 번째 통계는 3명으로 집계됐다. 네 번째 통계는 4명으로 집계됐다."
    )

    assert len(extract_article_candidates(article)) == 3


def test_deduplicates_the_same_claim_sentence():
    candidates = [
        {"original_claim": "취업자 수는 100명이다."},
        {"original_claim": "취업자 수는 100명이다."},
        {"original_claim": "실업률은 3.5%다."},
    ]

    assert deduplicate_candidates(candidates) == [
        {"original_claim": "취업자 수는 100명이다."},
        {"original_claim": "실업률은 3.5%다."},
    ]


def test_builds_review_csv_with_ids_metadata_and_blank_review_columns(tmp_path):
    input_path = tmp_path / "news.csv"
    output_path = tmp_path / "claim_candidates_review.csv"
    write_source_csv(
        input_path,
        [make_article("취업자 수는 2804만1000명으로 감소했다.")],
    )

    candidates = build_claim_candidates(input_path, output_path)

    assert candidates[0]["candidate_id"] == "KC0001"
    assert candidates[0]["article_id"] == "NEWS_001"
    assert candidates[0]["article_title"] == "테스트 기사"
    assert candidates[0]["article_date"] == "2025-01-01"
    assert candidates[0]["article_url"] == "https://example.com/news/1"
    assert candidates[0]["original_claim"] == "취업자 수는 2804만1000명으로 감소했다."
    assert candidates[0]["category_hint"] == "고용"
    assert candidates[0]["human_review"] == ""
    assert candidates[0]["primary_keyword"] == ""
    assert candidates[0]["expanded_keywords"] == ""
    assert candidates[0]["review_note"] == ""

    with output_path.open(encoding="utf-8-sig", newline="") as file:
        saved_rows = list(csv.DictReader(file))
    assert saved_rows[0]["candidate_id"] == "KC0001"
    assert saved_rows[0]["original_claim"] == "취업자 수는 2804만1000명으로 감소했다."
    assert saved_rows[0]["human_review"] == ""
    assert saved_rows[0]["primary_keyword"] == ""


def test_raises_an_error_when_a_required_column_is_missing(tmp_path):
    input_path = tmp_path / "invalid_news.csv"
    output_path = tmp_path / "claim_candidates_review.csv"
    write_source_csv(
        input_path,
        [{"기사ID": "NEWS_001", "기사제목": "제목", "작성일": "2025-01-01", "URL": "url"}],
        fieldnames=["기사ID", "기사제목", "작성일", "URL"],
    )

    with pytest.raises(ValueError, match="필수 CSV 컬럼이 없습니다"):
        build_claim_candidates(input_path, output_path)


@pytest.mark.parametrize(
    "sentence",
    [
        "지난달 취업자 수는 2804만1000명으로 전년 동월 대비 감소했다.",
        "소비자물가지수는 전년 대비 2.1% 상승했다.",
        "합계출산율은 0.75명으로 집계됐다.",
    ],
)
def test_statistical_indicator_sentences_receive_high_scores(sentence):
    assert score_candidate(sentence)["claim_candidate_score"] >= 60


@pytest.mark.parametrize(
    "sentence",
    [
        "김씨는 올해 78세로 가족 9명과 살았다.",
        "사고 희생자 9명이 발생해 구조 작업이 진행됐다.",
        "폴크스바겐 전기차 80만대의 데이터가 유출됐다.",
    ],
)
def test_clear_non_statistical_sentences_are_excluded(sentence):
    assert score_candidate(sentence)["is_excluded"]


def test_national_vehicle_registration_statistic_is_retained():
    result = score_candidate("지난해 국내 전기차 등록 대수는 80만대를 기록했다.")

    assert not result["is_excluded"]
    assert result["claim_candidate_score"] > 0


def test_policy_period_guidance_receives_a_low_score():
    result = score_candidate("육아휴직 기간이 기존 1년에서 1년 6개월로 연장된다.")

    assert not result["is_excluded"]
    assert result["claim_candidate_score"] < 30


def test_comparison_and_statistical_context_increase_a_score():
    plain_score = score_candidate("취업자 수는 2804만1000명이다.")["claim_candidate_score"]
    contextual_score = score_candidate(
        "통계청 조사 결과 취업자 수는 전년 대비 13만명 증가했다."
    )["claim_candidate_score"]

    assert contextual_score > plain_score


def test_final_candidates_are_sorted_by_score_descending(tmp_path):
    input_path = tmp_path / "news.csv"
    output_path = tmp_path / "claim_candidates_review.csv"
    write_source_csv(
        input_path,
        [
            make_article("육아휴직 기간은 1년에서 1년 6개월로 연장된다.", "NEWS_001"),
            make_article("통계청 조사 결과 취업자 수는 전년 대비 13만명 증가했다.", "NEWS_002"),
        ],
    )

    candidates = build_claim_candidates(input_path, output_path)

    assert [candidate["claim_candidate_score"] for candidate in candidates] == sorted(
        (candidate["claim_candidate_score"] for candidate in candidates), reverse=True
    )


def test_diversity_selection_limits_a_category_to_thirty_percent():
    candidates = [
        {"category_hint": "고용", "claim_candidate_score": 90 - index}
        for index in range(5)
    ] + [
        {"category_hint": "물가", "claim_candidate_score": 85 - index}
        for index in range(3)
    ] + [
        {"category_hint": "인구", "claim_candidate_score": 80 - index}
        for index in range(2)
    ]

    selected = select_diverse_candidates(candidates, limit=10)
    category_counts = {}
    for candidate in selected:
        category = candidate["category_hint"]
        category_counts[category] = category_counts.get(category, 0) + 1

    assert all(count <= 3 for count in category_counts.values())

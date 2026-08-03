# 뉴스 원문에서 사람이 검토할 수치 Claim 후보 CSV를 생성합니다.
"""뉴스 원문에서 사람이 검토할 수치 Claim 후보를 추출한다."""

import csv
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Union


REQUIRED_COLUMNS = ("기사ID", "기사제목", "작성일", "URL", "정제된_본문")
OUTPUT_COLUMNS = (
    "candidate_id",
    "article_id",
    "article_title",
    "article_date",
    "article_url",
    "original_claim",
    "category_hint",
    "claim_candidate_score",
    "candidate_reason",
    "exclusion_hint",
    "human_review",
    "primary_keyword",
    "expanded_keywords",
    "review_note",
)
MIN_SENTENCE_LENGTH = 15
MAX_CANDIDATES_PER_ARTICLE = 3

SCORE_NUMERIC_WITH_UNIT = 15
SCORE_STATISTICAL_INDICATOR = 30
SCORE_COMPARISON = 15
SCORE_STATISTICAL_CONTEXT = 15
SCORE_SCOPE = 10
PENALTY_PERSONAL_AGE = 40
PENALTY_EVENT = 40
PENALTY_COMPANY_PRODUCT = 35
PENALTY_POLICY_PERIOD = 30
PENALTY_PRICE_OR_SIMPLE_PAY = 25
PENALTY_PRODUCT_SPEC = 35
MAX_CATEGORY_SHARE = 0.30

CATEGORY_INDICATORS = {
    "고용": ("취업자", "실업자", "실업률", "고용률", "경제활동인구", "비경제활동인구", "임금", "근로시간", "최저임금"),
    "물가": ("소비자물가지수", "생산자물가지수", "물가상승률"),
    "인구": ("총인구", "인구수", "고령인구"),
    "출생·사망": ("출생아", "출산율", "합계출산율", "사망자", "사망률", "혼인", "이혼"),
    "가구": ("가구 수", "가구원 수"),
    "무역": ("수출", "수입", "무역수지"),
    "산업": ("경제성장률", "국내총생산", "GDP", "생산지수", "판매액지수", "등록 대수"),
    "교육": ("학생 수", "학교 수"),
    "보건": ("병상 수", "의료기관 수"),
    "주택": ("주택 수", "주택보급률"),
}
COMPARISON_TERMS = ("전년 대비", "전월 대비", "전년 동월 대비", "비율", "증가율", "감소율", "평균")
STATISTICAL_CONTEXT_TERMS = ("통계청", "국가데이터처", "KOSIS", "조사 결과", "통계에 따르면", "집계됐다", "집계한 결과")
SCOPE_TERMS = ("전국", "전체", "시도별", "연령별", "국내", "국가")
EVENT_TERMS = ("참사", "사고", "희생자", "피해자", "사망했다", "구조됐다", "경찰", "검찰", "화재", "추락", "충돌")
COMPANY_TERMS = ("회사", "기업", "업체", "브랜드", "폴크스바겐", "모델", "판매했다", "출시", "매출", "영업이익", "데이터 유출", "소유주")
POLICY_TERMS = ("시행", "기간", "육아휴직", "계약", "연장된다", "지원금")
PRICE_PAY_TERMS = ("월급", "급여", "가격", "할인", "벌금", "형량", "주식")
PRODUCT_SPEC_TERMS = ("정확도", "사양", "로봇", "기기", "장비")
NUMERIC_UNIT_PATTERN = re.compile(r"\d[\d,.]*\s*(?:명|%|퍼센트|원|만원|억원|조원|건|가구|대|개|톤|달러|지수|배|세)")


def normalize_sentence(text: str) -> str:
    """앞뒤와 연속 공백을 정리한다."""

    return " ".join(text.split())


def split_sentences(text: str) -> List[str]:
    """소수점 내부의 마침표를 유지하면서 문장을 분리한다."""

    sentences: List[str] = []
    buffer: List[str] = []

    for index, character in enumerate(text):
        buffer.append(character)
        if character not in ".!?":
            continue

        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        is_decimal_point = character == "." and previous.isdigit() and following.isdigit()
        if is_decimal_point:
            continue

        sentence = normalize_sentence("".join(buffer))
        if sentence:
            sentences.append(sentence)
        buffer = []

    remainder = normalize_sentence("".join(buffer))
    if remainder:
        sentences.append(remainder)
    return sentences


def contains_numeric_claim(sentence: str) -> bool:
    """연도·날짜만 있는 문장을 제외한 수치 문장인지 판별한다."""

    if not re.search(r"\d", sentence):
        return False

    without_dates = re.sub(r"\d{4}\s*년", "", sentence)
    without_dates = re.sub(r"\d{1,2}\s*월", "", without_dates)
    without_dates = re.sub(r"\d{1,2}\s*일", "", without_dates)
    return bool(re.search(r"\d", without_dates))


def score_candidate(sentence: str) -> Dict[str, object]:
    """공식 통계 Claim 가능성을 0~100 범위로 점수화한다."""

    category_hint = _find_category(sentence)
    has_indicator = category_hint != "기타"
    has_scope = _contains_any(sentence, SCOPE_TERMS)
    score = 0
    reasons: List[str] = []
    exclusion_hints: List[str] = []
    is_excluded = False

    if NUMERIC_UNIT_PATTERN.search(sentence):
        score += SCORE_NUMERIC_WITH_UNIT
        reasons.append("숫자와 단위 포함")
    if has_indicator:
        score += SCORE_STATISTICAL_INDICATOR
        reasons.append(f"핵심 통계 지표: {category_hint}")
    if _contains_any(sentence, COMPARISON_TERMS):
        score += SCORE_COMPARISON
        reasons.append("증감·비율 표현")
    if _contains_any(sentence, STATISTICAL_CONTEXT_TERMS):
        score += SCORE_STATISTICAL_CONTEXT
        reasons.append("통계·조사 문맥")
    if has_scope:
        score += SCORE_SCOPE
        reasons.append("국가·지역 범위")

    if re.search(r"\(?\d{1,3}\)?\s*세", sentence) and not has_indicator:
        score -= PENALTY_PERSONAL_AGE
        exclusion_hints.append("개인 나이 문맥")
        is_excluded = True
    if _contains_any(sentence, EVENT_TERMS) and not has_indicator:
        score -= PENALTY_EVENT
        exclusion_hints.append("사건·희생자 문맥")
        is_excluded = True
    if _contains_any(sentence, COMPANY_TERMS) and not has_indicator and not has_scope:
        score -= PENALTY_COMPANY_PRODUCT
        exclusion_hints.append("기업·제품 개별 수치")
        is_excluded = True
    if _contains_any(sentence, PRODUCT_SPEC_TERMS) and not has_indicator and not has_scope:
        score -= PENALTY_PRODUCT_SPEC
        exclusion_hints.append("제품·장비 사양 문맥")
        is_excluded = True
    if _contains_any(sentence, POLICY_TERMS) and not has_indicator:
        score -= PENALTY_POLICY_PERIOD
        exclusion_hints.append("제도·기간 안내")
    if _contains_any(sentence, PRICE_PAY_TERMS) and not has_indicator:
        score -= PENALTY_PRICE_OR_SIMPLE_PAY
        exclusion_hints.append("가격·급여 단순 안내")

    return {
        "category_hint": category_hint,
        "claim_candidate_score": max(0, min(100, score)),
        "candidate_reason": "; ".join(reasons),
        "exclusion_hint": "; ".join(exclusion_hints),
        "is_excluded": is_excluded,
    }


def extract_article_candidates(article: Mapping[str, str]) -> List[Dict[str, str]]:
    """기사 한 건에서 최대 세 개의 수치 Claim 후보를 원문 그대로 추출한다."""

    body = article.get("정제된_본문") or ""
    candidates: List[Dict[str, object]] = []

    for sentence in split_sentences(body):
        if len(sentence) < MIN_SENTENCE_LENGTH or not contains_numeric_claim(sentence):
            continue
        assessment = score_candidate(sentence)
        if assessment["is_excluded"]:
            continue
        candidate = _candidate_from_article(article, sentence)
        candidate.update({key: value for key, value in assessment.items() if key != "is_excluded"})
        candidates.append(candidate)

    return sorted(
        candidates,
        key=lambda candidate: int(candidate["claim_candidate_score"]),
        reverse=True,
    )[:MAX_CANDIDATES_PER_ARTICLE]


def deduplicate_candidates(candidates: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """같은 원문 문장은 첫 번째 후보만 유지한다."""

    unique_candidates: List[Dict[str, str]] = []
    seen_claims = set()
    for candidate in candidates:
        claim = candidate["original_claim"]
        if claim in seen_claims:
            continue
        seen_claims.add(claim)
        unique_candidates.append(candidate)
    return unique_candidates


def build_claim_candidates(
    input_path: Union[str, Path], output_path: Union[str, Path], limit: int = 100
) -> List[Dict[str, str]]:
    """원본 뉴스 CSV에서 최대 ``limit``개의 검토용 후보 CSV를 생성한다."""

    input_file = Path(input_path)
    output_file = Path(output_path)
    articles = _read_articles(input_file)

    extracted = [
        candidate
        for article in articles
        for candidate in extract_article_candidates(article)
    ]
    unique_candidates = deduplicate_candidates(extracted)
    selected_candidates = select_diverse_candidates(unique_candidates, limit=limit)

    for index, candidate in enumerate(selected_candidates, start=1):
        candidate["candidate_id"] = f"KC{index:04d}"

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(selected_candidates)

    return selected_candidates


def select_diverse_candidates(
    candidates: Iterable[Dict[str, object]], limit: int = 100
) -> List[Dict[str, object]]:
    """점수 순서를 유지하면서 한 분야가 전체의 30%를 넘지 않게 선택한다."""

    category_limit = max(1, int(limit * MAX_CATEGORY_SHARE))
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: int(candidate["claim_candidate_score"]),
        reverse=True,
    )
    selected: List[Dict[str, object]] = []
    category_counts: Counter = Counter()
    for candidate in sorted_candidates:
        category = str(candidate["category_hint"])
        if category_counts[category] >= category_limit:
            continue
        selected.append(candidate)
        category_counts[category] += 1
        if len(selected) == limit:
            break
    return selected


def _read_articles(input_path: Path) -> List[Dict[str, str]]:
    with input_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames: Sequence[str] = reader.fieldnames or []
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing_columns:
            raise ValueError(f"필수 CSV 컬럼이 없습니다: {', '.join(missing_columns)}")
        return list(reader)


def _candidate_from_article(article: Mapping[str, str], sentence: str) -> Dict[str, object]:
    return {
        "candidate_id": "",
        "article_id": article["기사ID"],
        "article_title": article["기사제목"],
        "article_date": article["작성일"],
        "article_url": article["URL"],
        "original_claim": sentence,
        "category_hint": "기타",
        "claim_candidate_score": 0,
        "candidate_reason": "",
        "exclusion_hint": "",
        "human_review": "",
        "primary_keyword": "",
        "expanded_keywords": "",
        "review_note": "",
    }


def _find_category(sentence: str) -> str:
    for category, terms in CATEGORY_INDICATORS.items():
        if _contains_any(sentence, terms):
            return category
    return "기타"


def _contains_any(sentence: str, terms: Iterable[str]) -> bool:
    return any(term in sentence for term in terms)


if __name__ == "__main__":
    candidates = build_claim_candidates(
        input_path=Path("data/Final_news.csv"),
        output_path=Path("data/claim_candidates_review.csv"),
        limit=100,
    )
    print(f"검토용 Claim 후보 {len(candidates)}건을 저장했습니다.")

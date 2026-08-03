# Claim 문장에서 규칙 기반 초기 KOSIS 검색 키워드를 추출합니다.
"""동의어 사전 없이 Claim 내부 표현으로 초기 검색 키워드를 추출한다."""

import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple, Union

try:
    from kiwipiepy import Kiwi
except ImportError:  # Kiwi는 선택 의존성이므로 정규식 fallback을 사용한다.
    Kiwi = None  # type: ignore[misc, assignment]


REQUIRED_COLUMNS = ("candidate_id", "original_claim")
ADDED_COLUMNS = (
    "code_primary_keyword",
    "code_expanded_keywords",
    "extraction_status",
    "extraction_note",
)

TIME_PATTERNS = (
    r"전년\s*동월",
    r"지난\s*달",
    r"지난달",
    r"지난\s*해",
    r"지난해",
    r"작년",
    r"올해",
    r"전월",
    r"전년",
    r"최근\s*\d+년",
    r"\d{4}\s*년",
    r"\d{1,2}\s*월",
    r"\d{1,2}\s*분기",
)
CHANGE_PATTERNS = (
    r"증가했다",
    r"감소했다",
    r"상승했다",
    r"하락했다",
    r"늘었다",
    r"줄었다",
    r"올랐다",
    r"내렸다",
    r"기록했다",
    r"집계됐다",
    r"나타났다",
    r"증가",
    r"감소",
    r"상승",
    r"하락",
    r"기록",
    r"집계",
)
NOISE_PATTERNS = (
    r"것으로\s*나타났다",
    r"것으로",
    r"관련",
    r"기준",
    r"결과",
    r"수치",
    r"수준",
    r"규모",
)
NUMERIC_WITH_UNIT_PATTERN = re.compile(
    r"\d[\d,.]*(?:\s*(?:만|억|조|천))?\s*(?:명|원|억원|조원|만원|건|대|%|퍼센트|배|천명|만명|포인트)?"
)
CONDITION_PATTERNS = (
    r"\d+\s*세\s*이상",
    r"(?:1[5-9]|[2-9]\d)\s*대",
    r"전국",
    r"국내",
    r"청년",
    r"여성",
    r"남성",
    r"비정규직",
    r"다문화",
    r"제조업",
    r"보건·사회복지업",
)
CONDITION_PREFIXES = ("전국", "국내", "청년", "여성", "남성", "비정규직", "다문화", "제조업", "보건·사회복지업")
INDICATOR_WORDS = (
    "취업자",
    "실업자",
    "근로자",
    "자영업자",
    "출생아",
    "혼인",
    "이혼",
    "수출",
    "수입",
    "임금",
    "부채",
    "생산",
    "판매",
    "인구",
)
COMPOUND_INDICATOR_PATTERN = re.compile(
    r"[가-힣A-Za-z]+(?:수출액|수입액|국내총생산|생산지수|판매액지수|소비자물가지수|생산자물가지수|혼인율|출산율|사망률|고용률|실업률|비율|비중|건수|금액|지수|률|율)"
)
SPACED_INDICATOR_PATTERN = re.compile(
    r"(?:[가-힣A-Za-z·]+\s+){0,2}[가-힣A-Za-z·]+\s+(?:건수|금액|지수|비중|비율|수|률|율|생산)(?:은|는|이|가|을|를|의|에|와|과|도)?"
)
WORD_INDICATOR_PATTERN = re.compile(
    r"(?:[가-힣A-Za-z·]+\s+){0,2}(?:취업자|실업자|근로자|자영업자|출생아|혼인|이혼|수출|수입|임금|부채|생산|판매|인구)"
)
REPORTING_CONTEXT_PATTERN = re.compile(
    r"(?:국가데이터처|통계청|기획재정부).*?에\s*따르면"
)


def clean_claim_text(claim: str) -> str:
    """숫자, 단위, 시점, 변화 표현 등 불필요한 요소를 정리한다."""

    if not isinstance(claim, str):
        return ""

    text = " ".join(claim.split())
    text = REPORTING_CONTEXT_PATTERN.sub(" ", text)
    protected_text, protected_conditions = _protect_conditions(text)
    for pattern in TIME_PATTERNS + CHANGE_PATTERNS + NOISE_PATTERNS:
        protected_text = re.sub(pattern, " ", protected_text)
    protected_text = NUMERIC_WITH_UNIT_PATTERN.sub(" ", protected_text)
    protected_text = re.sub(r"[()\[\]'‘’“”.,:;!?]", " ", protected_text)
    return _restore_conditions(" ".join(protected_text.split()), protected_conditions)


def extract_initial_keywords(claim: str) -> Dict[str, Union[str, List[str]]]:
    """Claim 안의 실제 지표 표현만으로 대표·확장 키워드를 만든다."""

    if not isinstance(claim, str) or not claim.strip():
        return {"primary_keyword": "", "expanded_keywords": []}

    normalized_claim = " ".join(claim.split())
    cleaned_claim = clean_claim_text(normalized_claim)
    candidates = extract_keyword_candidates(cleaned_claim)
    if not candidates:
        return {"primary_keyword": "", "expanded_keywords": []}

    primary = _choose_primary_keyword(candidates)
    expanded = _build_expanded_keywords(primary, candidates, normalized_claim)
    return {"primary_keyword": primary, "expanded_keywords": expanded}


def extract_keyword_candidates(cleaned_claim: str) -> List[str]:
    """정리된 Claim에서 통계 지표 형태의 명사·명사구 후보를 추출한다."""

    candidates: List[str] = []
    for pattern in (SPACED_INDICATOR_PATTERN, COMPOUND_INDICATOR_PATTERN, WORD_INDICATOR_PATTERN):
        candidates.extend(match.group(0).strip() for match in pattern.finditer(cleaned_claim))

    for word in INDICATOR_WORDS:
        if word in cleaned_claim:
            candidates.append(word)

    candidates.extend(_extract_kiwi_noun_candidates(cleaned_claim))
    return _deduplicate(_normalize_candidate(candidate) for candidate in candidates)


def extract_keywords_from_csv(
    input_path: Union[str, Path], json_output_path: Union[str, Path], csv_output_path: Union[str, Path]
) -> List[Dict[str, Union[str, List[str]]]]:
    """Claim 후보 CSV를 읽어 키워드 JSON과 비교 검토용 CSV를 만든다."""

    input_file = Path(input_path)
    json_file = Path(json_output_path)
    csv_file = Path(csv_output_path)
    rows, fieldnames = _read_claim_rows(input_file)
    results: List[Dict[str, Union[str, List[str]]]] = []
    output_rows: List[Dict[str, str]] = []

    for row in rows:
        result, status, note = _extract_row_keywords(row)
        results.append(result)
        output_row = dict(row)
        output_row.update(
            {
                "code_primary_keyword": str(result["primary_keyword"]),
                "code_expanded_keywords": "|".join(result["expanded_keywords"]),
                "extraction_status": status,
                "extraction_note": note,
            }
        )
        output_rows.append(output_row)

    json_file.parent.mkdir(parents=True, exist_ok=True)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with json_file.open("w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)
    with csv_file.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[*fieldnames, *ADDED_COLUMNS])
        writer.writeheader()
        writer.writerows(output_rows)
    return results


def _protect_conditions(text: str) -> Tuple[str, Dict[str, str]]:
    protected: Dict[str, str] = {}
    for index, pattern in enumerate(CONDITION_PATTERNS):
        placeholder = f"CONDITIONTOKEN{chr(ord('A') + index)}"
        match = re.search(pattern, text)
        if not match:
            continue
        protected[placeholder] = match.group(0)
        text = text[: match.start()] + placeholder + text[match.end() :]
    return text, protected


def _restore_conditions(text: str, protected: Dict[str, str]) -> str:
    for placeholder, condition in protected.items():
        text = text.replace(placeholder, condition)
    return " ".join(text.split())


def _normalize_candidate(candidate: str) -> str:
    candidate = " ".join(candidate.split())
    candidate = re.sub(r"(?:은|는|이|가|을|를|의|에|와|과|도)$", "", candidate)
    stop_prefixes = ("동향", "에", "따르면", "발표한", "결과", "일", "한해", "중")
    tokens = candidate.split()
    while tokens and tokens[0] in stop_prefixes:
        tokens.pop(0)
    candidate = " ".join(tokens)
    for prefix in CONDITION_PREFIXES:
        candidate = re.sub(rf"^{re.escape(prefix)}\s+", "", candidate)
    return candidate.strip()


def _choose_primary_keyword(candidates: Sequence[str]) -> str:
    return max(candidates, key=_candidate_score)


def _candidate_score(candidate: str) -> Tuple[int, int]:
    indicator_bonus = 0
    if re.search(r"(?:수|률|율|지수|금액|건수|비중|비율|생산)$", candidate):
        indicator_bonus = 100
    elif candidate in INDICATOR_WORDS:
        indicator_bonus = 60
    return indicator_bonus, len(candidate.replace(" ", ""))


def _build_expanded_keywords(primary: str, candidates: Sequence[str], original_claim: str) -> List[str]:
    expanded: List[str] = []
    shortened = re.sub(r"\s+(?:수|률|율|지수|금액|건수|비중|비율)$", "", primary)
    if shortened and shortened != primary and shortened in original_claim:
        expanded.append(shortened)

    for condition in CONDITION_PREFIXES:
        contextual = f"{condition} {primary}"
        if contextual in original_claim:
            expanded.append(contextual)

    for candidate in candidates:
        if candidate != primary and candidate in original_claim:
            expanded.append(candidate)
    return _deduplicate(expanded)[:3]


def _extract_kiwi_noun_candidates(text: str) -> List[str]:
    if Kiwi is None:
        return []
    try:
        tokens = Kiwi().tokenize(text)
    except Exception:
        return []
    return [token.form for token in tokens if token.tag.startswith("N") and token.form in INDICATOR_WORDS]


def _deduplicate(candidates: Iterable[str]) -> List[str]:
    unique: List[str] = []
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _read_claim_rows(input_path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with input_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            raise ValueError(f"필수 CSV 컬럼이 없습니다: {', '.join(missing)}")
        return list(reader), fieldnames


def _extract_row_keywords(row: Dict[str, str]) -> Tuple[Dict[str, Union[str, List[str]]], str, str]:
    try:
        keywords = extract_initial_keywords(row["original_claim"])
    except Exception as error:
        return (
            {"candidate_id": row["candidate_id"], "original_claim": row["original_claim"], "primary_keyword": "", "expanded_keywords": []},
            "error",
            str(error),
        )

    result = {"candidate_id": row["candidate_id"], "original_claim": row["original_claim"], **keywords}
    if not keywords["primary_keyword"]:
        return result, "empty", "통계 지표 후보를 찾지 못함"
    return result, "success", "규칙 기반 추출"

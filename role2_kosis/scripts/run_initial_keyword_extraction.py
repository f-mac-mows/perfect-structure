# Claim 후보 CSV에서 초기 KOSIS 검색 키워드 결과 파일을 생성합니다.
"""초기 키워드 추출 실험을 실행하는 스크립트다."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.initial_keyword_extractor import extract_keywords_from_csv


def main() -> None:
    """기본 데이터 경로로 JSON과 비교 검토용 CSV를 생성한다."""

    results = extract_keywords_from_csv(
        input_path=Path("data/claim_candidates_review.csv"),
        json_output_path=Path("data/initial_keyword_extraction.json"),
        csv_output_path=Path("data/claim_candidates_with_keywords.csv"),
    )
    print(f"초기 키워드 추출 결과 {len(results)}건을 저장했습니다.")


if __name__ == "__main__":
    main()

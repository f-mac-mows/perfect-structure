# KOSIS 검색 키워드 생성 Task

> 뉴스 Claim 문장을 KOSIS 통계표에서 잘 검색되는 키워드로 변환하는 Task입니다.

## 1. 이 Task가 하는 일

입력 Claim에서 **핵심 통계 지표**와 **조건 표현**을 분리해 대표 검색어와 확장 검색어를 만듭니다.

```text
Claim 문장
→ 숫자·시점·변화 표현 제거
→ 지표형 명사구 추출
→ 대표 키워드 선택
→ 조건을 포함한 확장 키워드 생성
```

예시:

```json
{
  "claim_id": "C001",
  "original_claim": "지난달 전국 취업자 수는 28만 명 증가했다."
}
```

```json
{
  "primary_keyword": "취업자 수",
  "expanded_keywords": ["취업자", "전국 취업자 수"]
}
```

## 2. 구현 방향

| 구분 | 방식 |
|---|---|
| 입력 형식 | `claim_id`, `original_claim`을 가진 고정 JSON 객체 검증 |
| 테스트 Claim 준비 | 뉴스 원문에서 수치 문장을 후보로 뽑고, 사람이 검토할 100건으로 정리 |
| 초기 키워드 추출 | Claim 안에 실제로 있는 표현만 규칙 기반으로 추출 |
| 제외 대상 | 숫자·단위·시점·증감 표현·보도 문맥 등 지표가 아닌 표현 |
| 보존 대상 | 전국·청년·비정규직·다문화 등 지표의 대상·범위를 설명하는 표현 |
| 외부 도구 | Kiwi가 설치되면 사용하고, 없으면 정규식 fallback 사용 |

현재 초기 추출 단계에서는 **동의어 사전, KOSIS API, 문장에 없는 지표명 추가를 사용하지 않습니다.**

## 3. 현재 구현 현황

| 단계 | 상태 | 핵심 파일 | 결과 |
|---|---|---|---|
| Claim 후보 추출 | ✅ 완료 | `scripts/build_claim_candidates.py` | 뉴스 2,690건에서 검토 후보 100건 생성 |
| Claim 입력 정규화 | ✅ 완료 | `src/input_normalizer.py` | 고정 JSON 입력 검증·공백 정리 |
| 초기 키워드 추출 | ✅ 완료 | `src/initial_keyword_extractor.py` | 대표·확장 키워드 초기 결과 생성 |
| 결과 파일 생성 | ✅ 완료 | `scripts/run_initial_keyword_extraction.py` | JSON·비교용 CSV 생성 |
| 사람 검토 | ⏳ 다음 작업 | Claim 후보 20~30건 | KOSIS 검색 가능 여부와 적절한 검색어 확인 |
| 최종 키워드 생성기 | 예정 | `src/keyword_generator.py` | 검토 정답 데이터를 바탕으로 구현 |

### 이번 단계에서 만든 결과

| 항목 | 수치 |
|---|---:|
| 원본 뉴스 | 2,690건 |
| 원시 Claim 후보 | 6,903건 |
| 중복 제거 후 후보 | 6,678건 |
| 최종 검토 Claim | 100건 |
| 전체 자동 테스트 | 47 passed, 1 skipped |

`47 passed`는 코드 규칙 테스트가 통과했다는 뜻이며, 100개 키워드의 품질을 보장하는 정확도 지표는 아닙니다.

## 4. 주요 파일

| 파일 | 역할 |
|---|---|
| `src/input_normalizer.py` | 고정 Claim JSON 입력 검증 |
| `src/initial_keyword_extractor.py` | 초기 규칙 기반 키워드 추출 로직 |
| `scripts/build_claim_candidates.py` | 뉴스 원문에서 Claim 후보 생성 |
| `scripts/run_initial_keyword_extraction.py` | 100건 Claim에 키워드 추출 실행 |
| `tests/test_initial_keyword_extractor.py` | 초기 키워드 추출 규칙 10개 검증 |
| `data/input_examples.json` | 비식별 Claim 입력 예시 |

## 5. 데이터 공유 안내

뉴스 원문과 뉴스에서 파생된 Claim·키워드 결과 파일은 저작권 이슈로 GitHub에 올리지 않습니다. `.gitignore`에 등록되어 있으며, 팀원은 코드와 테스트만으로 로직을 확인할 수 있습니다.

## 6. 다음 작업

검토용 Claim 100건 중 대표성이 있는 20~30건을 직접 확인합니다.

| 확인할 내용 | 기록할 값 |
|---|---|
| KOSIS 통계표 연결 가능 여부 | `human_review` — 사용·보류·제외 |
| 대표 검색어 | `primary_keyword` |
| 확장 검색어 | `expanded_keywords` |
| 판단 근거 | `review_note` |

검토 결과를 기준으로 초기 규칙의 문제를 보완하고, 이후 최종 키워드 생성기를 구현합니다.

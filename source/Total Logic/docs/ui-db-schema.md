# FactQ UI 필수 데이터 계약

프론트는 DB에 직접 접근하지 않고 API 응답을 받는다. 판정 결과의 기준은 `verdict_output_schema.json`이다.

## Article

| Field | Type | 필수 | UI 용도 |
|---|---|---|---|
| article_id | string | Y | 기사 연결 키 |
| url | string | Y | 중복 확인, 원문 링크 |
| title | string | Y | 제목 |
| publisher | string | Y | 언론사 |
| published_at | string \| null | N | 게시일 |
| content | string | Y | 원문 전체와 Claim 하이라이트 |
| status | `PENDING\|PROCESSING\|COMPLETED\|FAILED` | Y | 목록·결과 상태 |
| stage | string \| null | N | 메인 카드의 현재 처리 단계 |
| request_input | string \| null | N | 사용자가 입력한 URL 또는 제목 유지 |
| created_at | string | Y | 요청 저장일 |
| verified_at | string \| null | N | 완료일 |

## VerdictResult

공통 필수 필드:

| Field | Type | UI 용도 |
|---|---|---|
| claim_id | string | Claim·패널·탐색 연결 키 |
| claim | string | 원문에서 정확히 매칭할 Claim 문장 |
| verdict | string | 판정 원본값 |
| explanation | string | 판정 근거 |

`NOT_ELIGIBLE`, `ERROR`는 위 네 필드만 가진 MinimalResult다.

그 외 FullResult 추가 필드:

| Field | Type | UI 용도 |
|---|---|---|
| claimed_value | number \| null | 기사 주장값 |
| actual_value | number \| null | KOSIS 조회값 |
| hedge_type | string \| null | 수치 표현 방식 |
| mode | string | 판정 방식 |
| ai_used | boolean | AI 재해석 여부 |
| ai_note | string \| null | AI 재해석 설명 |
| evidence.table_org_id | string \| null | KOSIS 기관 코드 |
| evidence.table_tbl_id | string \| null | 통계표 ID |
| evidence.table_nm | string \| null | 통계표명 |
| evidence.retrieval_status | `RESOLVED\|NOT_FOUND\|UNRESOLVED` | 근거 조회 상태 |

현재 schema에 없는 기간, 지역, 단위, 항목명, 공식 URL은 프론트에서 생성하거나 추측하지 않는다.

## UI 판정 매핑

| Backend verdict | UI |
|---|---|
| VERIFIED | 일치 |
| MISMATCH | 불일치 |
| UNVERIFIED_* | 판단 불가 |
| NOT_ELIGIBLE | 검증 대상 아님 |
| ERROR | 검증 오류 |
| RAW_ONLY | 원자료 확인 |

## 관계

```text
Article 1 ── N VerdictResult
Article.content ↔ VerdictResult.claim
모든 UI 연결 키 = VerdictResult.claim_id
```

요약 개수는 VerdictResult 배열을 verdict별로 집계하며 DB에 중복 저장하지 않는다.

# run01_result.jsonl 신규 스키마 (1번 확정, 2026-08-19)

## 상태

**2026-08-19, 1번과 대화로 확정.** 아래 스키마로 claims.jsonl(run01_result.jsonl)을
주기로 합의됨 - 더 이상 "출력 예시"가 아니라 확정된 계약이다.

다만 **실제 데이터는 아직 안 왔다.** 이 문서 + 코드는 1번이 준 예시 1건을 근거로
작성한 것이고, 실제 run01_result.jsonl이 이 포맷으로 오면:
- 필드가 실제로 항상 채워지는지(특히 `approx`/`comparison_period`는 1번도 "확신이
  들지 않아 일단 추가했다"고 밝힘)
- `comparison_period`의 실제 표기 포맷(YYYY-MM 고정인지, "5년 전"처럼 상대 표현이
  섞여 오는지)
- `value_type`/`direction`이 애매한 문장(예: "동결됐다")에서 어떻게 채워지는지

를 반드시 재검증해야 한다(CLAUDE.md 실측 우선 원칙 - 스키마가 "확정"된 것과 그
스키마의 실제 값 분포/엣지케이스가 "실측"된 것은 다른 문제다). 아래 코드는 이
필드들이 **없어도(구 포맷)** 안전하게 기존 휴리스틱으로 폴백하도록 짰다 -
실제 데이터로 검증되기 전까지 새 필드는 "있으면 우선 사용, 없으면 기존 방식"
정도의 신뢰로만 다룬다.

## 스키마 전체

```jsonl
{
  "claim_id": "A82ae9f41-C002",
  "article_id": "A82ae9f41",
  "sent_id": "s005",
  "claim": "16일 통계청이 발표한 '2025년 6월 고용동향'에 따르면, 지난달 15세 이상 취업자는 2909만1000명으로 전년 동월 대비 18만3000명 늘었다.",
  "metric": "15세 이상 취업자",
  "metric_normalized": "15세 이상 취업자",
  "approx": "",
  "value": "18만3000",
  "value_num": 183000,
  "unit": "명",
  "value_type": "change_amount",
  "direction": "increase",
  "period": "2025-06",
  "comparison_basis": "YOY",
  "comparison_period": "2024-06",
  "kosis_eligible": true,
  "exclusion_code": ""
}
```

| 필드 | 값/타입 | 설명 |
|---|---|---|
| `sent_id` | `"s005"` | 기사 내 몇 번째 문장인지. |
| `approx` | `GTE`/`LTE`/`APPROX`/`""` | `GTE`=실제값≥value(하한), `LTE`=실제값≤value(상한), `APPROX`=±근사, `""`=정확값 주장. |
| `value_num` | `183000` | value("18만3000")를 이미 숫자로 정규화. |
| `value_type` | `level`/`change_rate`/`change_amount` | 단순값 / 증감률 / 증감량. |
| `direction` | `increase`/`decrease`/`""` | value_type이 rate/amount일 때 증가·감소 방향. |
| `comparison_basis` | `YOY`/`PREV_PERIOD`/`SPECIFIC` | 전년대비 / 바로 직전 주기 / 특정 날짜. |
| `comparison_period` | 날짜 문자열 | comparison_basis에 대응하는 실제 비교 시점. |
| `exclusion_code` | `FORECAST`/`PARTIAL_PERIOD`/`AMBIGUOUS_METRIC`/`""` | kosis_eligible=false인 이유. |

## 매핑: 새 필드 -> 기존 로직 대체

| 새 필드 | 대체 대상(파일:함수) | 비고 |
|---|---|---|
| `value_type`+`direction` | `local_db_agent._needs_rate_derivation`, `_claim_expresses_pairwise_change`, `_window_has_change_verb`, `_window_has_rate_comparison` | 오늘(8/18) raw_sentence 동사 위치로 "이 값이 파생 필요한지" 추론하던 로직 전부. `direction`은 이미 `adapter.parse_claim`이 읽어서 judgment.py `Claim.direction`으로 흘러가고 있음(코드 변경 불필요, 이미 호환). |
| `comparison_basis`+`comparison_period` | `local_db_agent._extract_explicit_reference_period`, `kls._yoy_reference_period` 호출부 | "5년 전에 비해"/"2020년 9월에 비해" 정규식 추출을 대체. `SPECIFIC`이면 `comparison_period`를 그대로 쓰고, `YOY`/`PREV_PERIOD`는 기존 `_yoy_reference_period`류 계산과 동일한 역할. |
| `value_num` | `adapter._parse_claimed_value` | 한글 축약 표기("10만2000") 파싱을 대체. 폴백은 유지(구 포맷 대비). |
| `sent_id` | `LocalDbAgent.process_claim_group_keywords`의 `by_raw_sentence`(claim 텍스트 완전일치로 형제 묶기) | sent_id가 있으면 그걸로 묶고, 없으면 기존 텍스트 완전일치로 폴백. |
| `approx` | `judgment.extract_hedge`(raw_sentence 정규식으로 hedge_type 추론) | `""`→exact, `APPROX`→approx, `GTE`→at_least, `LTE`→at_most. judgment.py의 `approach_below`(근접/육박)는 1번 스키마에 대응값이 없어 계속 규칙 기반 폴백으로 남음. **주의: judgment.py는 이 프로젝트가 "5번 역할"까지 겸하는 코드라 이 매핑은 실제로 건드릴지 별도 판단 필요(아래 참고).** |
| `exclusion_code` | 없음(신규) - `LocalDbAgent`의 `not_eligible` 결과 설명에 이유를 덧붙이는 용도로 활용 가능. | 지금은 "1번이 판단했다"고만 나옴 - exclusion_code를 메시지에 넣으면 왜 제외됐는지 투명해짐. |

## 적용하지 않기로 한 것 / 보류

- **`approx` -> judgment.extract_hedge 대체는 이번 라운드에서 보류.** judgment.py는
  이 프로젝트가 "5번(판정) 역할"까지 겸해서 만든 모듈이라, hedge 추론을 1번 필드로
  넘기는 건 "문장 해석은 5번이 한다"는 기존 역할 분리 원칙(adapter.py 주석,
  `parse_claim` 문서 참고)과 충돌 여지가 있다 - 1번 실제 출력에서 이 필드가
  얼마나 정확한지 확인 후 재논의.
- `value_type`이 `level`인데 unit이 "%"인 경우(예: "고용률 70.3%") - 기존
  `_needs_rate_derivation`이 이미 처리하던 "level인데 %가 그 항목 고유 단위인
  경우"와 동일 케이스. value_type="level"이면 애초에 derivation 트리거 자체를
  안 해야 하므로 오히려 기존 코드보다 더 명확해짐.

## 코드 배선 상태

- `local_db_agent.resolve_claim_evidence`: `claim`에 `value_type`/`comparison_basis`/
  `comparison_period`/`value_num`이 있으면 우선 사용, 없으면 기존 휴리스틱
  (`_needs_rate_derivation` 등)으로 폴백 - 2026-08-19 배선 완료.
- `LocalDbAgent.process_claim_group_keywords`: `sent_id`가 있으면 그걸로 형제
  그룹핑, 없으면 raw_sentence 완전일치로 폴백 - 2026-08-19 배선 완료.

## 검증 상태 (2026-08-19)

- `test_claims_schema_v2.py`(합성 데이터, 5개 테스트) 전체 PASS.
- 기존 회귀 테스트 4종(`test_local_db_agent_derivation.py`,
  `test_local_search_special_tables.py`, `test_warehouse_scope_policy.py`,
  `test_vdb_discovery.py`) 전체 PASS - 이번 배선이 기존 로직을 깨지 않음.
- 90개 claim 전체 재검증(`run04_local.py`, 아직 구 포맷 실데이터) 결과, 스키마
  배선 자체는 구 포맷 입력에 대해 verdict 0건 변경(안전) - 단, 같이 진행한
  "완전 corroboration" 임계값 완화(`matched_phrase_count == len(match_phrases)`
  -> `>= 2`, `_tokenize()`의 가운뎃점 복합어 변형 토큰 과생성 문제 우회)는
  4건 변경을 냈고 전부 개선 방향:
  - `A82ae9f41-C005`: `MISMATCH` -> `UNVERIFIED_DERIVED_NEEDED` (원자료 지수값과
    직접 비교하던 오탐 해소 - 실제로는 파생 필요한데 로컬 DB 미보유라 정직하게
    "파생 필요"로 분류됨)
  - `A93bfa851-C002/C006/C023`: `UNVERIFIED_UNRESOLVED` -> `UNVERIFIED_DERIVED_NEEDED`
    (항목/축 확정에 성공해서 더 정확한 상태로 분류됨)
  - 새로 생긴 MISMATCH나 VERIFIED 오분류 없음(회귀 없음).
- 실제 1번 데이터(신규 스키마 포맷)는 아직 미도착 - 위 검증은 전부 (a) 합성
  스키마 데이터, (b) 구 포맷 실데이터 기준. 실 데이터 도착 시 이 문서 상단
  "상태" 섹션의 재검증 항목(필드 항상 채워지는지, comparison_period 포맷,
  애매 문장에서의 value_type/direction)을 다시 확인해야 함.

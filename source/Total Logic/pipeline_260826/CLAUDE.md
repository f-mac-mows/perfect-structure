# 이 프로젝트에서 반드시 지킬 것

## 실측 우선 원칙 (2026-08-17, 사용자가 여러 번 반복해서 강조함 - 절대 어기지 말 것)

**어떤 DB 스키마/구조 설계도 사용자가 제공하는 실제 API 호출 결과(실측)를 받기
전까지는 만들지 않는다.** 샌드박스에서 테스트한 값, MCP 도구가 보여주는 요약/
번역된 필드명, 공개 R 클라이언트나 공식 문서에서 유추한 필드명 — 이런 것들을
근거로 "아마 이런 필드명일 것"이라고 어림짐작해서 스키마나 파싱 로직의 뼈대를
먼저 만들면 안 된다.

구체적으로:
- 새로운 API(예: KOSIS `statisticsList.do`/`get_statistics_list`)를 처음
  다룰 때, 그 응답의 정확한 필드명/구조는 사용자가 실제 API 키로 직접 호출해서
  알려줄 때까지 "미확정"으로 취급한다.
- 미검증 상태에서 코드를 먼저 짜야 한다면(예: 막힌 작업을 풀기 위해), 반드시
  "이건 아직 실측 전이라 추정" 이라고 코드 주석과 대화에서 명시하고, 실측이
  들어오면 그 자리를 실제 값으로 교체한다는 걸 분명히 한다.
- 이 프로젝트는 이미 이 원칙(Decision 003, "추측하지 않는다")을 검색/판정
  로직에 적용해왔다 - 이번 VDB 재구축(스키마 변경 포함)에도 예외 없이 똑같이
  적용한다. 사용자가 실제 KOSIS API를 호출해서 정확한 포맷을 알려주기 전까지
  스키마 변경 작업에 착수하지 않는다.

이 규칙을 어겨서 같은 지적을 반복하게 만든 적이 이 세션에서 여러 번 있었다 -
다시 반복하지 않는다.

## DB 파일에 직접 쓰기/삭제 금지 (2026-08-19, 사고 발생 후 사용자가 명시적으로 정함)

**`kosis_warehouse.db`(그리고 `-journal`/`-wal`/`-shm` 등 관련 파일)에 대해
쓰기/삭제가 필요한 명령은 절대 이 세션(샌드박스)에서 직접 실행하지 않는다.**
행 개수 확인처럼 읽기만 하려던 작업도 실수로 쓰기 커넥션(`kosis_warehouse.
get_connection()` 등)을 썼다가 disk I/O 에러로 stale journal을 남긴 사고가
있었다 - 사용자가 이후 "DB 관련 file I/O에서 remove 권한은 내가 막아놨다,
Claude 세션에서 DB 관련 권한이 막혀있다"고 확인해줬고, DB 작업은 항상
사용자가 로컬에서 직접 확인/실행하는 방식으로 가기로 함.

앞으로 지킬 것:
- DB 조회/적재/스키마 변경이 필요하면, 이 세션에서 직접 실행(bash로 sqlite3/
  python 커넥션 열기)하지 말고 **실행할 정확한 명령어(쉘 명령 또는 SQL)를
  사용자에게 제시**하고, 사용자가 로컬에서 실행한 결과를 받아서 이어간다.
- `local_db_agent.py`처럼 이미 `mode=ro`(읽기 전용) 연결만 쓰도록 설계된
  코드 경로를 통한 조회(예: `run04_local.py` 실행)는 계속 이 세션에서 직접
  해도 된다 - 문제가 됐던 건 그 관례를 벗어나 별도로 쓰기 가능 커넥션을
  직접 연 것이었다.
- DB 파일 자체(`.db`/`.db-journal`/`.db-wal`/`.db-shm`)를 대상으로 한 `rm`/
  `os.remove`/스키마 변경 SQL 등은 이 세션에서 시도조차 하지 않는다(설령
  권한 에러 없이 될 것 같아 보여도).

## 담당 범위(scope) 정정 - run02/run03(키워드 생성·KOSIS 검색)도 포함 (2026-08-21, 사용자가 명시적으로 정정함)

**"1번 파트(claim 추출) 소관이라 범위 밖"이라고 스스로 규정해온 경계가 이제
틀렸다.** 예전엔 이 프로젝트를 "run01(기사→claim 추출, 1번 파트) → run02(claim별
KOSIS 키워드 생성) → run03(그 키워드로 KOSIS 라이브 검색) → run04/검색(로컬 DB
매칭) → 판정" 순서로 보고, run02/run03을 run01과 같은 1번 파트 소관으로 취급해왔다.
**이제 run02(키워드 생성)와 run03(KOSIS 라이브 검색)도 사용자 담당으로 바뀌었다** -
run01(기사→claim 추출)만 여전히 1번 파트고, **키워드 생성부터 검색(로컬 DB
매칭)과 최종 판정까지 전체가 사용자 소관**이다.

구체적으로:
- 어떤 실패 원인이 run02(키워드 생성) 또는 run03(KOSIS 라이브 검색) 단계에서
  비롯됐다고 해서 "범위 밖"이라고 결론 내리고 진단을 멈추면 안 된다 - 그 단계도
  이제 사용자가 고쳐야 할 대상이다.
- 사용자가 파이프라인 앞단에 VDB(임베딩 기반 로컬 검색, `vdb_discovery.py`)를
  두려고 한 이유가 바로 이것과 직결된다 - run03가 의존하는 KOSIS 통합검색
  자체가 불안정하다는 게 이미 실측으로 확인돼 있는데(README 2.1: 연산자
  우선순위가 이상하게 동작, 공백 기준 분리가 따옴표 그룹핑보다 먼저 적용됨),
  이제 그 불안정성이 사용자 본인이 고쳐야 하는 문제가 됐다는 뜻이다. run02/run03의
  "라이브 검색에 문구가 그대로 걸리는가"라는 취약한 게이트를 로컬 임베딩 기반
  검색으로 대체/보강하는 방향이 자연스러운 우선순위가 된다.
- 실제로 이 정정 계기가 된 사례(2026-08-21): claim `A93bfa851-C018`("주류 및
  담배는 상승률이 5.0%에 그쳤지만 이 중 주류만 보면 13.1%였다")를 run03가
  라이브 검색 패러프레이즈 10개를 전부 시도했는데도 0건으로 실패시켰고(정답
  표 `DT_1J22001`이 실제로 로컬 DB엔 존재함에도), 그 결과 로컬 Stage 1이
  키워드 없이 raw_sentence 폴백으로 넘어가면서 별도 버그(숫자 토큰 "13"이
  무관한 표의 축 코드와 우연히 FTS로 걸려 채택되는 문제)까지 겹쳐 완전히
  엉뚱한 표("유가증권 순위별 거래")를 골랐다. 처음엔 이 사례의 run03쪽
  원인을 "1번 소관이라 범위 밖"이라고 잘못 적었다가 사용자가 정정함.

이 프로젝트 문서(README.md 등)에 남아있는 과거 서술 중 "run02/run03은 1번
파트"라는 취지의 문장은 이 시점 이후로는 낡은 서술로 취급한다 - 지우지는
않되(과거 결정 기록 보존 원칙), 새로 작업/진단할 때는 이 정정된 scope를
기준으로 삼는다.

## 작업 범위는 e2e 폴더로 제한 (2026-08-22, 사용자가 명시적으로 정함)

**이 프로젝트에서 파일을 만들거나 고치는 작업은 `/Users/mows/e2e/` 폴더 안에서만
한다.** 세션 압축(compaction) 시 문맥 손실이 커서 작업량 자체를 줄이기로 함 -
Obsidian vault(`/Users/mows/Obs/Research/...`)나 다른 폴더의 설계 노트/문서는
필요하면 참고(읽기)만 하고, 이 세션에서 직접 수정하지 않는다. 문서화가
필요하면 e2e 폴더 안의 `README.md`에 남긴다.

## 샌드박스에서 직접 실행 금지 - 명령어만 제시 (2026-08-22, 사용자가 명시적으로 정함)

**이 세션의 샌드박스는 네트워크가 막혀 있어 이 세션에서 직접 실행한 결과를
신뢰할 수 없다.** 코드 실행(테스트 스크립트, python 스니펫, DB 조회 등)이
필요하면 이 세션에서 bash로 직접 돌리지 말고, **정확한 실행 명령어(쉘 명령
또는 python 코드)를 사용자에게 제시**하고, 사용자가 로컬에서 실행한 결과를
받아서 이어간다.

이 규칙은 위 "DB 파일에 직접 쓰기/삭제 금지" 항목의 예외("`mode=ro`
읽기 전용 조회는 이 세션에서 직접 해도 된다")를 더 넓게 대체한다 - 이제는
읽기 전용 조회/단위 테스트 실행까지 포함해서 **이 세션에서 직접 실행하지
않는다.** DB 쓰기/삭제 자체가 막혀 있다는 사실은 여전히 유효하지만, 그와
별개로 실행 자체를 최소화하는 게 이번 규칙의 핵심이다.

## run01_result.jsonl 신규 스키마 계약 (2026-08-19, 1번과 확정 - 압축으로 한 번 유실됐던 내용, 원문 그대로 보존)

**이 섹션은 `claims_schema_1번_v2.md`의 내용을 그대로 복사한 것이다.** 세션
압축(compaction) 중 이 결정 자체가 문맥에서 유실돼 이미 완료된 배선을 다시
설계하려던 적이 있었다 - 다시 반복하지 않기 위해 원문을 CLAUDE.md에 그대로
박아둔다. 원본 파일(`claims_schema_1번_v2.md`)도 e2e 폴더에 계속 존재한다.

### 상태

**2026-08-19, 1번과 대화로 확정.** 아래 스키마로 claims.jsonl(run01_result.jsonl)을
주기로 합의됨 - 더 이상 "출력 예시"가 아니라 확정된 계약이다.

다만 **실제 데이터는 아직 안 왔다.** 이 문서 + 코드는 1번이 준 예시 1건을 근거로
작성한 것이고, 실제 run01_result.jsonl이 이 포맷으로 오면:
- 필드가 실제로 항상 채워지는지(특히 `approx`/`comparison_period`는 1번도 "확신이
  들지 않아 일단 추가했다"고 밝힘)
- `comparison_period`의 실제 표기 포맷(YYYY-MM 고정인지, "5년 전"처럼 상대 표현이
  섞여 오는지)
- `value_type`/`direction`이 애매한 문장(예: "동결됐다")에서 어떻게 채워지는지

를 반드시 재검증해야 한다(실측 우선 원칙 - 스키마가 "확정"된 것과 그
스키마의 실제 값 분포/엣지케이스가 "실측"된 것은 다른 문제다). 아래 코드는 이
필드들이 **없어도(구 포맷)** 안전하게 기존 휴리스틱으로 폴백하도록 짰다 -
실제 데이터로 검증되기 전까지 새 필드는 "있으면 우선 사용, 없으면 기존 방식"
정도의 신뢰로만 다룬다.

**2026-08-24 재확인**: 이 시점까지도 실제 신규 포맷 데이터는 미도착 -
`run01_result.jsonl`을 직접 덤프해보면 여전히 구 포맷(`claim_id`/`claim`/
`metric`/`metric_normalized`/`value`/`unit`/`period`/`kosis_eligible`만 있고
`comparison_basis`/`comparison_period`/`value_num`/`sent_id`/`exclusion_code`/
`approx`는 없음)이다. 즉 "5년 전 대비"류 reference-period 문제는 **이미 설계와
배선이 끝난 상태로 1번의 데이터 도착만 기다리는 중**이지, 우리 쪽에서 새로
(예: claim group 간 cross-reference 같은) 별도 메커니즘을 다시 설계할 필요가
없다 - 이 사실을 잊고 새 설계에 착수하려 한 적이 있었으므로 명시해둔다.

### 스키마 전체

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

### 매핑: 새 필드 -> 기존 로직 대체

| 새 필드 | 대체 대상(파일:함수) | 비고 |
|---|---|---|
| `value_type`+`direction` | `local_db_agent._needs_rate_derivation`, `_claim_expresses_pairwise_change`, `_window_has_change_verb`, `_window_has_rate_comparison` | 오늘(8/18) raw_sentence 동사 위치로 "이 값이 파생 필요한지" 추론하던 로직 전부. `direction`은 이미 `adapter.parse_claim`이 읽어서 judgment.py `Claim.direction`으로 흘러가고 있음(코드 변경 불필요, 이미 호환). |
| `comparison_basis`+`comparison_period` | `local_db_agent._extract_explicit_reference_period`, `kls._yoy_reference_period` 호출부 | "5년 전에 비해"/"2020년 9월에 비해" 정규식 추출을 대체. `SPECIFIC`이면 `comparison_period`를 그대로 쓰고, `YOY`/`PREV_PERIOD`는 기존 `_yoy_reference_period`류 계산과 동일한 역할. |
| `value_num` | `adapter._parse_claimed_value` | 한글 축약 표기("10만2000") 파싱을 대체. 폴백은 유지(구 포맷 대비). |
| `sent_id` | `LocalDbAgent.process_claim_group_keywords`의 `by_raw_sentence`(claim 텍스트 완전일치로 형제 묶기) | sent_id가 있으면 그걸로 묶고, 없으면 기존 텍스트 완전일치로 폴백. |
| `approx` | `judgment.extract_hedge`(raw_sentence 정규식으로 hedge_type 추론) | `""`→exact, `APPROX`→approx, `GTE`→at_least, `LTE`→at_most. judgment.py의 `approach_below`(근접/육박)는 1번 스키마에 대응값이 없어 계속 규칙 기반 폴백으로 남음. **주의: judgment.py는 이 프로젝트가 "5번 역할"까지 겸하는 코드라 이 매핑은 실제로 건드릴지 별도 판단 필요(아래 참고).** |
| `exclusion_code` | 없음(신규) - `LocalDbAgent`의 `not_eligible` 결과 설명에 이유를 덧붙이는 용도로 활용 가능. | 지금은 "1번이 판단했다"고만 나옴 - exclusion_code를 메시지에 넣으면 왜 제외됐는지 투명해짐. |

### 적용하지 않기로 한 것 / 보류

- **`approx` -> judgment.extract_hedge 대체는 이번 라운드에서 보류.** judgment.py는
  이 프로젝트가 "5번(판정) 역할"까지 겸해서 만든 모듈이라, hedge 추론을 1번 필드로
  넘기는 건 "문장 해석은 5번이 한다"는 기존 역할 분리 원칙(adapter.py 주석,
  `parse_claim` 문서 참고)과 충돌 여지가 있다 - 1번 실제 출력에서 이 필드가
  얼마나 정확한지 확인 후 재논의.
- `value_type`이 `level`인데 unit이 "%"인 경우(예: "고용률 70.3%") - 기존
  `_needs_rate_derivation`이 이미 처리하던 "level인데 %가 그 항목 고유 단위인
  경우"와 동일 케이스. value_type="level"이면 애초에 derivation 트리거 자체를
  안 해야 하므로 오히려 기존 코드보다 더 명확해짐.

### 코드 배선 상태

- `local_db_agent.resolve_claim_evidence`: `claim`에 `value_type`/`comparison_basis`/
  `comparison_period`/`value_num`이 있으면 우선 사용, 없으면 기존 휴리스틱
  (`_needs_rate_derivation` 등)으로 폴백 - 2026-08-19 배선 완료.
- `LocalDbAgent.process_claim_group_keywords`: `sent_id`가 있으면 그걸로 형제
  그룹핑, 없으면 raw_sentence 완전일치로 폴백 - 2026-08-19 배선 완료.

### 검증 상태 (2026-08-19)

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

## 이 문서에 대해

이건 코드베이스 전체 문서가 아니라, 반복 위반이 있었던 핵심 작업 규칙만 담은
최소 CLAUDE.md다. 프로젝트 배경/아키텍처/이력은 `README.md`,
`Research Overview.md`, `Research Overview 2.md`(Obsidian 볼트,
`가짜뉴스 팩트체크` 프로젝트 폴더)를 참고할 것.

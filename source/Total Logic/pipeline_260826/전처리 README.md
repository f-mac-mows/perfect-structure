# 1번 파트 인수인계 번들 — Claim 스키마 v0.5 (17필드)

2026-08-24 · 구성: **HCX-007(thinking low) · 문단 맥락 · 프롬프트 extract_v2.2 · p3_v1**
범위: **dev 18기사** (튜닝용 기사 — 아래 §3 참조)
형식: `claims_jsonl_출력_예시.md` 합의안 그대로 (필드 순서·빈값 표기 포함)

---

## 1. 공식 계약 — `claims.jsonl`

한 행 = 수치 주장 하나. **17필드가 항상 모두 존재**하며, 값이 없으면 **빈 문자열 `""`** 입니다
(`value_num`만 숫자 필드라 변환 불가 시 `null`).

```json
{
  "claim_id": "Ae21581c3-C002",
  "article_id": "Ae21581c3",
  "sent_id": "s002",
  "claim": "관세청에 따르면 지난 1~20일 수출액은 386억7200만달러로 전년 동기 대비 8.3% 증가했다.",
  "metric": "수출액",
  "metric_normalized": "수출액",
  "approx": "",
  "value": "386억7200만",
  "value_num": 38672000000,
  "unit": "달러",
  "value_type": "level",
  "direction": "",
  "period": "",
  "comparison_basis": "",
  "comparison_period": "",
  "kosis_eligible": false,
  "exclusion_code": "PARTIAL_PERIOD"
}
```

| 필드 | 타입 | 의미 |
|---|---|---|
| `claim_id` | string | `{article_id}-C{일련}` |
| `article_id` | string | 기사 조인 키 |
| `sent_id` | string | 기사 내 문장 번호(`s001`…) — `sentences.jsonl` 조인 키 |
| `claim` | string | **기사 원문 그대로**(verbatim) |
| `metric` | string | 지표명 — **기사 표현 그대로** |
| `metric_normalized` | string | 표준 지표명. 현재는 verbatim 복사(검증된 동의어만 승격하는 정책) |
| `approx` | string | `GTE`(실제 ≥ value) / `LTE`(실제 ≤ value) / `APPROX`(±근사) / `""`(정확값 주장) |
| `value` | string | **수치 표현부만, 기사 표기 그대로**(`"13만"`, `"1조2000억"`) |
| `value_num` | number \| null | value의 숫자 변환. **표기 그대로 해석**(`"27억6000"` → 2,700,006,000). 범위·분수 표기는 `null` |
| `unit` | string | 기사 표기 그대로. **`%`와 `%p`는 다른 단위 — 혼용 금지** |
| `value_type` | string | `level`(단순값) / `change_rate`(증감률) / `change_amount`(증감량) |
| `direction` | string | `increase` / `decrease` / `""` — 증감형에서만 채워짐 |
| `period` | string | **주장이 말하는 대상 시점.** `YYYY`·`YYYY-MM`·`YYYY-Qn`·`YYYY-Hn` + 월범위·연범위 |
| `comparison_basis` | string | `YOY`(전년 대비) / `PREV_PERIOD`(직전 주기) / `SPECIFIC`(특정 시점) / `""` |
| `comparison_period` | string | 비교 기준의 절대 시점(period와 같은 형식) |
| `kosis_eligible` | boolean | **검증 시도 가능 상태** |
| `exclusion_code` | string | eligible=false의 사유 — `PARTIAL_PERIOD` / `FORECAST` / `AMBIGUOUS_METRIC` |

### 쓸 때 주의할 것 세 가지

1. **`period`가 대상 시점이고, 비교 시점은 `comparison_period`입니다.**
   "전년 동월 대비 18만3000명 증가" → `period=2025-06` · `comparison_basis=YOY` · `comparison_period=2024-06`.
   `comparison_basis`로 YoY 계산과 직전 주기 계산을 분기하시면 됩니다.
2. **`approx`가 있으면 숫자 대조에 허용 오차를 두세요.**
   `GTE`는 실제값이 value 이상("8% 넘게"의 실제는 8.3%), `LTE`는 이하, `APPROX`는 ±근사입니다.
   무시하고 `value_num`만 정확 비교하면 참인 주장이 불일치로 판정됩니다.
3. **`kosis_eligible=false`는 "틀렸다"가 아니라 "검증을 시도할 수 없다"입니다.**
   최종 리포트의 판별 보류 사유로 `exclusion_code`를 쓰시면 됩니다.

### ⚠ 코드명 주의 — `AMBIGUOUS_METRIC`

합의안대로 **시점 미상**(또는 비교 수치인데 비교 시점 없음)에 `AMBIGUOUS_METRIC`을 씁니다.
다만 같은 이름이 `excluded.jsonl`에서는 **"지표 특정 불가"**라는 다른 뜻으로 쓰입니다(내부 규약 §5.3).
두 파일은 소비 지점이 달라 실무 충돌은 없지만, 헷갈리시면 알려주세요 — 이름은 바꿀 수 있습니다.

---

## 2. 부속 파일 (참조용 — 계약 아님)

| 파일 | 행 수 | 용도 |
|---|---|---|
| **`claims.jsonl`** | **360** | ★ 공식 계약. eligible=true **234건** |
| `articles_clean.jsonl` | 18 | 정제 기사 본문(article_id·title·posted_date·url·**publisher**·**paragraphs**) |
| `sentences.jsonl` | 412 | 문장 단위(sent_id·start/end 오프셋·문단 번호) — 화면 하이라이트 좌표 |
| `excluded.jsonl` | 109 | 제외 대장(비통계 수치 등) — 판단불가 물량 가늠용 |
| `claims_full.jsonl` | 360 | 내부 18필드(forecast·note 등 계약 밖 필드 포함) |
| `claims_trace.jsonl` | 360 | 계보 — 문자 오프셋·period 표면형과 해소 방법·감사 플래그 |
| `errors.jsonl` | 4 | 추출 실패 기록(전수 회계) |

`article_id`·`sent_id`가 계약 파일에 직접 실려 있어 부속 파일과 바로 조인됩니다.

---

## 3. 범위와 품질 (정직하게)

**이 번들은 dev 18기사분입니다** — 프롬프트 튜닝에 사용한 기사들이라, 아직 보지 않은 기사에서는
성적이 다소 낮을 수 있습니다. 전량(2,696기사) 실행은 프롬프트 동결 후 진행 예정입니다.

dev 18기사 기준 실측(정답 데이터 대조):

| 지표 | 값 |
|---|---|
| Claim 검출 F1 | 0.969 (정밀도 0.974 · 재현율 0.963) |
| 한 문장 다중 주장 완전 분리 | 0.912 |
| value / unit | 0.988 / 0.991 |
| value_type / direction | 0.967 / 0.980 |
| kosis_eligible | 0.845 |
| period | 0.729 |
| comparison_basis | 0.578 |
| metric | 0.506 |

**신뢰 수준별 안내**
- **높음** — `value`·`unit`·`value_num`·`direction`·`value_type`: 그대로 쓰셔도 됩니다.
- **중간** — `period`·`kosis_eligible`: 대상 시점 자체는 맞는 경우가 많으나 3~4건 중 1건꼴로 어긋납니다.
  조회 실패 시 `claim` 원문의 시점 표현을 재확인하는 폴백을 권합니다.
- **낮음** — `metric`: **골든과의 정확 일치를 목표로 튜닝하지 않았습니다.** 기사에 없는 단어를
  지어내지 않는 것(환각 차단)만 보장하며, 표기 정규화·동의어 확장은 검색단 소관이라는 팀 합의에
  따른 것입니다. `metric_normalized`도 현재는 verbatim 복사입니다.

---

## 4. 재현 정보

- 골든셋 `3d24ff49` · 프롬프트 `extract_v2.2` · 파이프라인 `p3_v1`
- 모델 `HCX-007`, thinking `low`(서버 기본), 맥락 모드 `paragraph`(같은 문단 형제 문장)
- 재생성: `venv\Scripts\python.exe -m src.p3_stage_b --dev-run --model HCX-007 --outdir data\handoff`
- 문의: 1번 파트 담당

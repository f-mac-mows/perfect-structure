# 1번 파트 통합 안내 (2026-08-26)

> 1번(뉴스 파싱·Claim 추출) 코드가 최신본으로 교체·통합됐습니다.
> **팀장 코드(adapter.py · judgment.py · local_db_agent.py · hcx_*.py · client.py ·
> 루트 config.py 등)는 한 줄도 수정하지 않았습니다.** 이 문서는 1번 쪽 변경만 다룹니다.

---

## 1. 무엇이 바뀌었나

| 항목 | 이전 (8/14 사본) | 현재 |
|---|---|---|
| 코드 배치 | flat 파일 일부만(불완전 — p0~p3 모듈 부재로 run01.py 실행 불가) | **`src/` 서브패키지**에 1번 모듈 전체 |
| 산출 계약 | 8필드 구 포맷 | **17필드 v0.5** — `claims_schema_1번_v2.md` 합의안 그대로 (빈 값 `""`) |
| Stage B 구성 | HCX-005 | **HCX-007 · thinking low · 문단 맥락 · extract_v2.2** (동결 — 새 블라인드 25기사 F1 0.817) |
| 비통계 필터 | 없음 | **제도 기준값 룰**(세율·요율·한도 → 제외) 기본 켜짐 · 오검출의 92%는 `kosis_eligible=false`로 표시됨 |
| 서비스 경로 | 없음 | **`run01_url.py`** — URL 하나 → 크롤 → Claim → 검색·판정 → 게시글 JSON |

- 루트의 `config.py`(팀장 확장본)·`llm_meter.py`는 그대로 있습니다. 1번 코드는 이제
  `src/config.py`·`src/llm_meter.py`를 쓰므로 이름 충돌이 없습니다.
  (루트 `llm_meter.py`는 이제 어디서도 참조되지 않는 구본입니다.)
- 교체 전 파일은 `_backup_1번_통합전/`에 보존.

## 2. 설치·환경

```
# 의존성: 1번 코드는 표준 라이브러리만 사용 (xlsx 입력 시에만 openpyxl)
pip install -r requirements.txt

# 비밀키: 프로젝트 루트의 .env (팀장 config.py와 src/config.py가 같은 파일을 읽음)
#   NCP_CLOVASTUDIO_API_KEY=...    ← Claim 추출(HCX)
#   KOSIS_API_KEY=...              ← 검색·판정(run04)
```

산출물은 전부 이 폴더 기준 상대 경로에 생깁니다 — `data/`(산출물)·`cache/`(LLM
record-replay 캐시 — 재실행 시 성공분 재과금 0).

## 3. 실행

### 배치(파일 입력) — 기존과 동일한 CLI

```
python run01.py --input D:/part1/articles.xlsx --outdir data/run     # 전 구간
python run01.py --input crawled.json --outdir data/one               # 크롤 파일 1건
python run01.py --outdir data/run --from p3                          # 중단 재개(캐시 재생)
```

끝나면 `claims.jsonl`(17필드)이 `run01_result.jsonl`로 루트에 복사됩니다 —
`run04_local.py`가 인자 없이 바로 받는 기존 계약 그대로.

> ⚠ **구 크롤 파일(개행 없는 본문)을 입력하면 정확도가 떨어집니다.** Claim 추출이
> **문단 맥락**을 쓰는데 구 크롤은 문단이 소실돼 있어, 나열형 기사에서 옆 문장
> 수치가 잘못 귀속되고 역검증 폐기(오류율 급증 → 서킷브레이커)로 이어집니다
> (실측: 같은 기사가 구 크롤 오류 21% ↔ **새 크롤 0건**). 배치도 새 크롤 산출
> (`paragraphs` 보유 jsonl)을 입력으로 쓰거나, URL 경로(`run01_url.py`)를 쓰세요.

### 단일 URL(서비스 경로) — 신규

```
python run01_url.py https://www.chosun.com/...        # 끝까지 (판정 포함)
python run01_url.py <URL> --no-verdict                # Claim 추출까지만
```

흐름: 크롤(`src/p_crawl.py` — 조선일보 Fusion·구형·ndsoft·범용 폴백) → 정제·문장화
(메모리) → Claim 추출 → `run04_local.py` subprocess 호출(검색·판정, **팀장 코드
무수정** — 구성 변경 시 자동 반영) → 게시글 JSON.

`kosis_warehouse.db`가 루트에 없으면 판정만 건너뛰고
(`verdict_status: "skipped_no_db"`) 나머지는 전부 저장됩니다 — db가 오면 같은
명령을 다시 돌리면 됩니다(LLM은 캐시 재생이라 재과금 0).

백엔드 API에서는 CLI 대신 함수를 그대로 부르면 됩니다:

```python
from run01_url import analyze_url
post = analyze_url("https://...", verdict=True)   # 반환 = 게시글 dict
```

## 4. 게시글 JSON (프론트 계약)

`data/posts/{article_id}.json` — 기사 화면 하나를 그리는 데 필요한 전부:

```jsonc
{
  "article":   { "article_id", "title", "posted_date", "url", "publisher",
                 "text", "paragraphs": ["문단1", ...] },        // 화면에 보이는 본문
  "sentences": [ { "sent_id", "text", "start", "end",           // 본문 전체 기준 오프셋
                   "para", "para_start", "para_end" } ],        // 문단 안 좌표(하이라이트용)
  "claims":    [ { ...17필드(claims_schema_1번_v2.md)... ,
                   "verdict": { /* run04 결과 통째 — STRICT/TOLERANCE/RAW_ONLY 3-mode
                                  (verdict_output_schema.json), 판정 전이면 null */ } } ],
  "excluded":  [ { "sent_id", "sentence", "exclusion_code", "note" } ],  // 왜 검증 안 했나
  "summary":   { "n_claims", "n_eligible", "verdict_status", "verdict_counts", ... },
  "versions":  { "crawl", "clean", "split", "pipeline", "generated_at" }
}
```

- **하이라이트**: claim의 `sent_id` → sentences에서 오프셋을 찾아 본문에 칠한다.
  색은 verdict(예: 일치=green / 불일치=red / 판단불가=yellow)로.
- **목록 화면**: `data/posts/index.json` — 게시글 요약 배열(최신순, 같은 URL 재분석 시 갱신).

## 5. 신뢰 수준 (블라인드 실측 기준)

- `value`·`unit`·`value_num`: 0.99~1.00 — 그대로 사용.
- `period`·`kosis_eligible`·`value_type`·`direction`: 0.81~0.95.
- `comparison_basis`: 빈값이 많음(틀린 값을 주진 않음) — 빈값이면 claim 원문의
  "전년 동월 대비" 등을 직접 읽는 폴백 권장 (adapter.py의 기존 휴리스틱이 이미 그 역할).
- `metric`: 골든 일치를 목표로 하지 않음(팀 합의 — 검색단 키워드 확장이 흡수).
  기사에 없는 단어를 지어내지 않는 것만 보장.
- 오검출(비통계 승격)은 대부분 `kosis_eligible=false`로 함께 표시됨 — run04가
  NOT_ELIGIBLE로 자동 처리하므로 별도 필터 불필요.

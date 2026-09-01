# FactQ 프론트 데이터 계약 변경 로그

## 2026-08-25 — 실제 verdict JSONL 기반 PostgreSQL 스키마 정합화

- `mock_db/schema.postgresql.sql`의 판정값을 백엔드 출력과 동일한 `VERIFIED`, `MISMATCH`, `UNVERIFIED_*`, `RAW_ONLY`, `NOT_ELIGIBLE`, `ERROR`로 변경했다.
- Task1 `claim_id` 형식(`A82ae9f41-C010`)을 보존하도록 UUID 대신 `VARCHAR(100)`을 사용한다.
- `claimed_value`, `actual_value`, `hedge_type`, `mode`, `ai_used`, `ai_note`, `evidence`를 FullResult 구조에 맞췄다.
- `NOT_ELIGIBLE`, `ERROR`는 네 공통 필드만 존재하는 MinimalResult 제약을 적용했다.
- UI의 일치/불일치/판단불가 그룹과 기사별 count는 VIEW에서 계산하며 중복 저장하지 않는다.
- 이 스키마는 서비스 결과 저장용이며 `kosis_warehouse.db`의 검색 웨어하우스를 변경하거나 대체하지 않는다.

## 2026-08-24 — 실제 verdict 출력 기준으로 전환

- Source of truth를 `static/data/verdict_output_schema.json`으로 변경했다.
- Mock 결과는 `static/data/verdict_output_examples.jsonl`만 사용한다.
- 숫자 문장 정규식 기반 Claim 생성과 임의 verdict·수치·KOSIS 근거 생성을 삭제했다.
- 원본 verdict를 `rawVerdict`로 보존하고 UI 표시용 `verdictGroup`, `verdictLabel`만 adapter에서 생성한다.
- `NOT_ELIGIBLE`, `ERROR`, `RAW_ONLY`를 판단 불가와 분리했다.
- MinimalResult는 존재하지 않는 value/evidence 필드를 읽지 않는다.
- Claim 및 DOM 연결은 `claim_id`를 사용한다.
- 하이라이트는 backend offset → 정확한 claim 문자열 → 공백 정규화 문자열 순서로만 찾는다.
- 매칭 실패 시 다른 문장을 선택하지 않고 개발 경고만 표시한다.
- schema에 없는 기간, 지역, 단위, 항목명은 생성하지 않는다.
- 기사 요약 수치는 원본 verdict 배열에서 계산한다.

현재 계약의 짧은 명세는 `docs/ui-db-schema.md`를 참고한다.

## 2026-08-25 — 메인 검증 상태 표시

- 메인 상태는 Article `status`로만 결정한다.
- 처리 단계는 backend가 제공하는 `stage`가 있을 때만 단계별로 표시한다.
- 세부 stage가 없으면 임의 단계를 만들지 않고 요청 접수와 처리 중 상태만 표시한다.
- `request_input`으로 사용자가 입력한 URL 또는 제목을 처리 중에도 유지한다.

## 2026-08-25 — Backend JSONL → Frontend Mock DB 매핑

- `scripts/build_frontend_mock_db.py`가 `run01_result.jsonl`, `articles_clean.jsonl`, `verdict_output_examples.jsonl`을 읽어 `static/data/frontend_mock_db.json`을 생성한다.
- Article과 Claim은 `article_id` 및 `claim_id`의 정확한 일치만으로 연결하며 제목·본문 유사도 기반 연결은 하지 않는다.
- `claim_id`의 마지막 `-C숫자` suffix에서 추출한 Article ID와 Task1 `article_id`가 같은지 검사한다.
- 백 원본 판정은 `raw_verdict`, UI 판정은 `ui_verdict`, 사용자 문구는 `verdict_label`로 분리한다.
- 하이라이트는 verdict 결과의 `claim`이 기사 본문에 정확히 존재할 때만 offset을 저장한다.
- 변환 결과에는 중복 ID, ID 충돌, 미연결 Article/Claim, 하이라이트 불일치 진단값을 함께 저장한다.
- 브라우저 UI는 생성된 Mock DB만 읽으며 임의 Claim·판정·KOSIS 값을 생성하지 않는다.

## 2026-08-25 — pipeline_handoff FastAPI 연결

- handoff의 `adapter.py`, `judgment.py`, `local_db_agent.py`, `run04_local.py`를 런타임 정본으로 동기화했다.
- FastAPI wrapper는 `LocalDbAgent`와 `run_search_and_judge(..., all_modes=True)`만 호출하고 검색·판정 로직을 재구현하지 않는다.
- FullResult는 `strict`, `tolerance`, `raw_only` 원본을 모두 보존하며 현재 UI는 tolerance 결과를 표시한다.
- `NOT_ELIGIBLE`, `ERROR` MinimalResult는 기존 flat 구조를 유지한다.
- 기본 프론트 데이터 소스를 `/api`로 전환했으며, Mock 모드는 `FACTQ_API_BASE`를 빈 값으로 명시한 경우에만 사용한다.
- seed ingest는 HTTP 요청에서 실행하지 않는다. `kosis_warehouse.db`와 `.env`는 운영자가 별도로 연결해야 한다.
- 현재 1차 연동은 DB가 없어도 백이 생성한 `verdict_output_examples.jsonl` 결과를 `claim_id`로 조회해 반환한다. DB가 있을 때만 기존 LocalDbAgent 실시간 파이프라인을 사용한다.

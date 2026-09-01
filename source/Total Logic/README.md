# FactQ

KOSIS 기반 뉴스 기사 통계 검수 UI와 FastAPI 연결 예제입니다.

## 실행

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. API 명세는 `/docs`에서 확인할 수 있습니다.

현재 데이터 흐름은 `Frontend → static/js/api.js → articles_clean.jsonl + verdict_output_examples.jsonl + localStorage`입니다. Claim과 판정은 실제 verdict 예제에 존재하는 값만 사용합니다. 실제 FastAPI가 준비되면 UI 코드는 유지하고 adapter 입력만 API 응답으로 교체합니다.

실제 API 주소를 `FACTQ_API_BASE`로 설정하면 JSONL Mock을 건너뛰고 FastAPI를 사용합니다.

```bash
FACTQ_API_BASE="http://127.0.0.1:8001/api" python3 -m uvicorn app:app --reload --port 8000
```

같은 origin에서 `/api`로 제공할 경우 `FACTQ_API_BASE="/api"`를 사용합니다. 별도 origin이면 FastAPI CORS 설정이 필요합니다.

프론트와 백엔드 사이의 스키마 변경 이력은 `docs/schema-changelog.md`에서 관리합니다.
현재 프론트가 요구하는 최소 데이터 계약은 `docs/ui-db-schema.md`입니다.

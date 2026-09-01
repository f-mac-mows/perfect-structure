"""파이프라인 단계 간 산출물 파일 이름 — 프로젝트 루트에 그대로 둔다.

**2026-08-14 결정**: 중간 산출물을 별도 `data/` 폴더로 옮기지 않고 루트에 모은다
(디스크 I/O가 공짜라서가 아니라, 지금은 e2e 연결을 위한 실험 단계이고 스키마
정리·보존 정책은 나중에 따로 다룰 문제라는 판단). 이 파일은 그 결정을 코드
전체가 한 곳만 보고 따르게 하는 용도다.

**2026-08-24 변경 - PIPELINE02/03 제거(담당 범위 정정 반영)**: 애초 이 파일은
"1번(claim 추출) → 2번(키워드 생성) → 3번(KOSIS 라이브 검색) → 4번(검색+판정)"
네 명이 각자 스크립트를 짜서 파일로 인수인계하는 걸 가정하고 만들어졌다.
그런데 2026-08-21에 담당 범위가 "run02/03도 우리 소관"으로 정정됐고, 실제로
Stage 1 키워드/표 선택은 이제 별도 파일 인수인계 없이 `local_db_agent.py`
안에서 그때그때 계산한다(stage1_keywords="llm_table_select"/
"metric_normalized") - `run02_result.json`은 실제 코드에서 참조하는 곳이
이미 없었고(grep으로 확인, PIPELINE02는 죽은 상수), `run03_result.json`도
`run04_local.py`가 llm_table_select로 승격되면서 더 이상 프로덕션에 필요
없어졌다(README "마흔여섯 번째" 참고) - 남은 참조는 `run04_local_stage1_ab.py`/
`run04_local_llm_table_select_batch.py`(레거시 run03 방식과의 A/B 비교
실험용, 이미 목적 달성해 프로덕션 판단엔 안 쓰임) 둘뿐이라 그 두 파일은
리터럴 문자열로 직접 바꿨다. 이제 이 파일이 표현하는 건 "1번(외부 프로젝트,
run01.py) → 우리(local_db_agent.py 이하 전부)"라는 경계 하나뿐이다.

각 스크립트(adapter.py 등)는 이 상수를 **자신의 CLI 기본값**으로만 쓴다 —
실행할 때 --input/--output 등으로 언제든 덮어쓸 수 있고, 이 파일이 강제하는
건 아니다. run01.py는 자체적으로 훨씬 정교한 산출물 구조(--outdir 아래
articles.jsonl → ... → claims.jsonl, 재개·예산 상한 포함)를 갖고 있어 이 파일이
건드리지 않는다 — 대신 run01.py가 끝나면 최종 claims.jsonl을 PIPELINE01 이름으로
루트에 복사해 다음 단계가 그 이름 하나만 보면 되게 한다.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

PIPELINE01 = "run01_result.jsonl"   # 1번(run01.py) claims.jsonl → 이 이름으로 인수인계
PIPELINE04 = "run04_result.json"    # adapter.py(검색+판정) 출력

PIPELINE01_PATH = PROJECT_ROOT / PIPELINE01
PIPELINE04_PATH = PROJECT_ROOT / PIPELINE04

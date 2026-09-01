"""[2026-08-15 신규] KOSIS 원본 데이터를 로컬 SQLite에 정형화해서 적재하는
ingestion 파이프라인 - "라이브 검색+해석"에서 "사전 적재+로컬 쿼리"로 아키텍처를
옮기자는 논의(README.md 9.3)의 첫 구현.

## [2026-08-16 중요 - 이 계층의 역할 재정의]

이 파일(kosis_warehouse.py)은 KOSIS 원본 API 응답을 flat한 테이블 구조로
"미러링"만 한다 - 사용자가 원래 제안한 그대로("KOSIS에 있는 데이터 자체를
정형화 데이터로 flat하게 저장"), 어떤 항목이 "등락률이냐 지수냐" 같은 해석,
어떤 표가 "국제기구 표냐 아니냐" 같은 판단은 이 계층에 넣지 않는다 - 그건
전부 검색 시점의 판단이고, 검색 로직이 바뀌면 같이 바뀌어야 하는 성격이라
kosis_local_search.py(검색 엔진) 쪽에만 둔다.

[반성/기록] 처음엔 dimensions에 measure_type(지수/등락률/구성비 분류),
tables_registry에 is_international(국제기구 여부 boolean)을 직접 저장했었다.
"검색 엔진 필터 설계 어떻게 할까"라는 질문에 답하다가, 그 답(내가 만든
분류 규칙)을 검색 엔진이 아니라 적재 스키마 자체에 넣어버린 것 - 사용자가
지적한 대로, 이건 "KOSIS 원본을 flat하게 미러링"이 아니라 내 개인적 판단을
원본 데이터인 것처럼 섞어 넣은 것이었다. VW_CD/STAT_NM/CD_NM(unit_hint)
같은 원본 필드 자체는 그대로 저장하고, 그걸 "국제기구다/등락률이다"로
해석하는 규칙은 여기서 완전히 뺐다(kosis_local_search.py로 이동).

## 스키마 - fact + dimension 스타 스키마

KOSIS는 표마다 축(objL1~objL8)이 뭘 의미하는지 제각각이라(한 표의 축1은 "국가",
다른 표의 축1은 "채무내역별") 그 의미 자체를 fact 테이블에 매번 반복해서 넣으면
안 되고, 별도 dimension 테이블로 분리해야 한다:

- `tables_registry`: 표 자체의 메타(제목/조사명/원본 VW_CD/수록기간) - 전부
  KOSIS가 직접 준 원본 필드 그대로.
- `dimensions`: getMeta(type=ITM) 응답을 그대로 정형화 - 이 표의 각 축(axis_
  position)이 뭘 의미하는지(axis_label)와, 그 축/항목의 각 코드가 뭘 뜻하는지
  (code -> name, 계층은 parent_code)
- `facts`: getList 응답을 그대로 정형화 - (표, 항목, 시점, 축1~8 코드) 조합마다
  값 하나. 축 코드만 저장하고 이름(C1_NM 등)/영문명은 저장하지 않는다 - 그건
  dimensions 테이블을 조인해서 구하면 되므로 중복 저장할 필요가 없다(사용자
  지적: "쓸모없는 C~_ENG 이런 부분은 다 날리고 필요한 것만 적재").

## 적재 방법 - API 2번, 규칙 기반(LLM 없음)

한 표를 적재하는 데 필요한 API 호출은 정확히 2종류뿐이다:
1. get_itm_meta_list(getMeta type=ITM) - 이 표의 구조(축이 몇 개고 각 축/항목에
   어떤 코드값이 있는지) - 표 구조는 거의 안 바뀌므로 한 번 적재하면 오래 쓴다.
2. get_period_meta(getMeta type=PRD) + fetch_actual_statistics_bounded_retry
   (getList) - 이 표가 실제로 지원하는 주기(연/분기/월)별로 전체 수록기간의
   실제 값. 값은 KOSIS가 새 데이터를 낼 때마다 바뀌므로 주기적으로 재적재해야
   한다(증분 갱신 전략은 아직 미구현 - 지금은 전체 재적재만 지원).

두 응답 모두 이미 client.py가 파싱해서 돌려주는 걸 그대로 받아 필요한 필드만
골라 테이블에 넣는 게 전부라, LLM 판단이 전혀 필요 없는 순수 ETL이다.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Task2.KosisChatAgent")

_SCHEMA_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tables_registry (
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    tbl_nm TEXT,
    stat_id TEXT,
    stat_nm TEXT,
    vw_cd TEXT,                  -- 원본 그대로: 국제기구 여부 판별은 검색 엔진이
                                  -- (kosis_local_search.is_international_survey)
                                  -- vw_cd/stat_nm을 보고 그때그때 계산한다.
    full_path_id TEXT,           -- search_metadata()의 목록경로ID(KOSIS 분류트리
                                  -- breadcrumb, 예: "P2 > P2_6") - 검색 시 주제
                                  -- 카테고리로 1차 필터링하는 데 쓴다. 사람이 읽는
                                  -- 라벨이 아니라 KOSIS 내부 코드 경로라 그대로는
                                  -- 안 읽히지만, 같은 상위 노드를 공유하는 표는
                                  -- 같은 주제라는 사실 자체는 그대로 써먹을 수 있다.
    topic_root TEXT,              -- full_path_id의 최상위 노드만 잘라낸 것(인덱싱용)
    strt_prd_de TEXT,
    end_prd_de TEXT,
    ingested_at TEXT,
    PRIMARY KEY (org_id, tbl_id)
);

CREATE TABLE IF NOT EXISTS dimensions (
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    obj_id TEXT NOT NULL,        -- 'ITEM' 또는 축 코드('A'/'B'/...)
    axis_position INTEGER,       -- ITEM=0, 그 외 OBJ_ID_SN
    axis_label TEXT,             -- OBJ_NM(축 의미) - ITEM 행은 '항목'
    code TEXT NOT NULL,          -- ITM_ID(이 행 자체의 코드)
    name TEXT,                   -- ITM_NM(사람이 읽는 이름)
    parent_code TEXT,            -- UP_ITM_ID(계층 구조, 없으면 NULL)
    unit_hint TEXT,              -- CD_NM(단위 힌트, 있는 표만) - 원본 그대로:
                                  -- "이 항목이 지수냐 등락률이냐" 해석은 검색
                                  -- 엔진이(kosis_local_search._infer_measure_type)
                                  -- name/unit_hint를 보고 그때그때 계산한다.
    PRIMARY KEY (org_id, tbl_id, obj_id, code)
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- surrogate key: 13컬럼 복합키로는
                                             -- SQLite rowid alias가 안 붙어 조회가
                                             -- 느리고, 나중에 "이 claim이 어느
                                             -- fact row에서 나왔는지" 감사 추적을
                                             -- 걸 때도 단일 id가 필요해진다.
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    itm_id TEXT NOT NULL,
    prd_de TEXT NOT NULL,
    prd_se TEXT NOT NULL,
    c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, c5 TEXT, c6 TEXT, c7 TEXT, c8 TEXT,
    value REAL,
    unit TEXT
);

-- [2026-08-17 신규 - "역대 최고/최저" claim 대응, 사용자 제안] years_back으로
-- facts에서 과거 원자료를 잘라내는 대신, 그 계열(표+항목+축+주기)의 "전체
-- 기간" 최댓값/최솟값과 그 시점만 요약해서 남긴다. facts처럼 시점마다 한
-- 행이 아니라 계열마다 한 행이라 용량이 훨씬 작다(예: 시점 799개짜리
-- 계열도 records엔 딱 1행). ingest_table이 이 값을 계산할 때는 반드시
-- years_back으로 자르기 *전*의 전체 조회 결과를 써야 한다(자세한 이유는
-- ingest_records docstring) - "최근 N년 중 최댓값"이 아니라 "전체 역사
-- 중 최댓값"이어야 "역대" claim에 대한 답이 되므로.
CREATE TABLE IF NOT EXISTS records (
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    itm_id TEXT NOT NULL,
    prd_se TEXT NOT NULL,
    c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, c5 TEXT, c6 TEXT, c7 TEXT, c8 TEXT,
    max_value REAL,
    max_prd_de TEXT,              -- 최댓값이 처음 나타난 시점(동점이면 가장 이른 시점)
    min_value REAL,
    min_prd_de TEXT,
    coverage_strt_prd_de TEXT,    -- 이 최댓값/최솟값을 계산할 때 실제로 훑은 범위
    coverage_end_prd_de TEXT,     -- (감사 추적용 - "역대"라고 주장해도 KOSIS 자체
                                   -- 수록기간을 벗어난 과거는 원천적으로 모른다는
                                   -- 걸 투명하게 남긴다)
    computed_at TEXT
);

-- [2026-08-17 신규 - VDB discovery 원재료] tables_registry는 "깊게 적재된"
-- (getMeta+getList까지 끝난) 표만 담는다 - 이 테이블은 그 반대로, 제목만
-- 아는 "얕고 넓은" 카탈로그다. crawl_catalog()가 get_statistics_list()를
-- parentListId로 재귀적으로 드릴다운하며 리프(실제 표) 노드를 전부 모아
-- 채운다. 목적은 검색이 아니라 임베딩 원재료 수집 - "KOSIS 전체에 어떤
-- 표들이 있는지" 제목만이라도 다 모아두면, 뉴스 claim을 이 제목들과
-- 의미적으로(임베딩 유사도로) 비교해서 키워드가 문자 그대로 안 겹쳐도
-- 후보 표를 찾을 수 있다(Research Overview 2 "VDB 두 가지 역할" 중
-- Stage 1 - 배추/외식업 사례처럼 문자 일치만으로는 못 찾거나 잘못 찾는
-- 경우 보완). ORG_ID+TBL_ID가 이미 tables_registry에 있어도 상관없이
-- 별도로 들고 있는다 - 이 테이블은 "표 후보 발견"용, tables_registry는
-- "확정된 표의 실제 데이터"용으로 역할이 다르기 때문이다.
-- [2026-08-17 실측 확정] 컬럼 구성은 get_statistics_list() 리프 노드의
-- 실제 필드(TBL_ID/TBL_NM/ORG_ID/STAT_ID/REC_TBL_SE/SEND_DE/VW_CD, 사용자가
-- 실제 API로 직접 호출해 statistics_list_probe.json으로 확인) 그대로
-- 미러링한다 - 다른 스키마들과 같은 원칙(해석 컬럼 없이 원본 필드만).
--
-- [2026-08-17 sqlite-vec 통합 위해 surrogate id 추가] org_id+tbl_id 복합
-- 자연키 대신 정수 id를 진짜 PRIMARY KEY로 둔다 - facts 테이블과 똑같은
-- 이유(id INTEGER PRIMARY KEY AUTOINCREMENT 주석 참고): 복합키로는 벡터를
-- 담을 catalog_vec(vec0 virtual table, rowid만 받음)과 조인할 안정적인
-- 정수 key가 없다. org_id/tbl_id는 여전히 UNIQUE로 걸어서 INSERT OR
-- REPLACE 중복 방지 의미는 그대로 유지한다.
CREATE TABLE IF NOT EXISTS catalog_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- catalog_vec.rowid와 1:1 대응(아래 참고)
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    tbl_nm TEXT,
    stat_id TEXT,
    rec_tbl_se TEXT,          -- REC_TBL_SE 원본 그대로(추천표 여부 Y/N) -
                               -- "이게 추천표라 우선순위를 높게 준다" 같은
                               -- 해석은 여기서 안 하고 검색 엔진 쪽에서 필요시 계산
    send_de TEXT,              -- SEND_DE 원본 그대로(최종갱신일) - 신선도 판단용
    vw_cd TEXT,
    parent_list_id TEXT,     -- 크롤 당시 이 표가 걸려 있던 상위 분류 노드
                              -- (감사 추적/주제 필터링용, full_path_id처럼
                              -- 전체 breadcrumb은 아니고 바로 위 1단계만)
    crawled_at TEXT
);

-- [2026-08-18 신규 - 적재 범위 정책 3번 레버: narrow/wide 이원화 + 기간
-- 커버리지 추적] 사용자와 논의한 결론: "완전 적재 vs 표 1개만 적재"는
-- 잘못된 이분법이고, 실측(kosis_warehouse.db 실제 facts 분포 - 상위 5개
-- wide 표가 전체 저장량의 96.4%)으로 확인된 진짜 경계는 표의 "폭"(축×항목
-- 조합 수)이다. narrow한 표는 지금처럼 itmId=all로 배치 완전 적재해도
-- 비용이 미미하고, wide한 표(예: DT_404Y016 928차원)만 getMeta는 그대로
-- 미리 받아두되 getList(실값)는 claim이 실제로 필요로 하는 (항목,축,기간)
-- 조합만 그때그때 objl_fixed로 정밀하게 온디맨드 조회한다.
--
-- 이 테이블은 그 "어디까지 이미 조회해서 facts에 있는지"를 기록한다 -
-- narrow 표는 배치 적재 시 itm_id='all'/axis_key='all'로 통째 커버리지
-- 한 줄만 남기고(전체가 이미 있다는 뜻), wide 표는 처음엔 커버리지가
-- 하나도 없다가 claim이 들어올 때마다 그 claim이 실제로 조회한 좁은
-- (itm_id, axis_key, 기간) 조합만큼만 한 줄씩 쌓인다. 이렇게 하면
-- "narrow=배치, wide=온디맨드" 정책과 "기간 커버리지 밖이면 그 구간만
-- 백필"(지난 논의) 두 요구사항을 별도 컬럼/플래그 없이 이 테이블 하나로
-- 통일해서 표현할 수 있다.
CREATE TABLE IF NOT EXISTS fact_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    tbl_id TEXT NOT NULL,
    prd_se TEXT NOT NULL,
    itm_id TEXT NOT NULL,     -- 'all'이면 이 표의 전체 항목이 커버됨(narrow
                               -- 배치 적재 시 기록). 특정 항목 코드면 그
                               -- 항목만 커버됨(wide 표 온디맨드 조회 시 기록).
    axis_key TEXT NOT NULL,   -- objl_fixed를 정규화한 서명 문자열(_normalize_
                               -- axis_key 참고, 예: "1=A0201|2=all"). 'all'이면
                               -- 축 제한 없이 전체가 커버됨.
    strt_prd_de TEXT NOT NULL,
    end_prd_de TEXT NOT NULL,
    fetched_at TEXT
);
"""

# 인덱스/FTS는 새 컬럼(topic_root 등)을 참조하므로, 반드시
# _MIGRATIONS(ALTER TABLE ADD COLUMN)가 끝난 뒤에 실행해야 한다 - 구버전
# DB에 새로 연결할 때 이 순서를 지키지 않으면 "no such column" 에러가 난다
# (실측: 마이그레이션 테스트로 처음 발견).
#
# [2026-08-16 실측 발견] uq_facts_natural_key가 c1~c8을 그냥 컬럼으로
# 걸어놓으면, 축 없는 컬럼이 NULL로 저장된 행과 ''(빈 문자열)로 저장된
# 행을 SQL이 서로 다른 값으로 취급해서(NULL != NULL) 중복을 못 잡는다 -
# 실제 kosis_warehouse.db에서 이 때문에 84,320개 중복 행(전체의 3.7%)이
# 발견됐다(같은 값이 NULL 버전/'' 버전으로 둘 다 남아있었음 - ingest_facts가
# NULL 대신 ''로 저장하도록 고쳐지기 *전*에 한 번, 고쳐진 *뒤*에 다시
# 적재되면서 생긴 것으로 보인다). 이 인덱스 자체를 COALESCE 표현식
# 기반으로 만들면, ingest_facts가 실수로 다시 NULL을 넣어도(다른 코드
# 경로가 생기더라도) 구조적으로 막힌다 - "쓰는 쪽에서 늘 ''로 맞춰야
# 한다"는 규칙에 기대는 대신 인덱스 자체가 보장하게 하는 게 훨씬 안전
# 하다(이번에 실제로 규칙을 지키는 데 실패한 사례가 나왔으므로).
_SCHEMA_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_dimensions_lookup
    ON dimensions (org_id, tbl_id, obj_id);
CREATE INDEX IF NOT EXISTS idx_facts_lookup
    ON facts (org_id, tbl_id, itm_id, prd_se);
CREATE INDEX IF NOT EXISTS idx_tables_registry_filter
    ON tables_registry (topic_root);

-- 항목/축 이름으로 표를 찾는 키워드 검색용 FTS5 인덱스 - 지금은 이 매칭을
-- 전부 LLM에 맡기고 있는데, 그 전에 이걸로 후보를 몇 개로 좁혀두면 LLM
-- 호출 자체가 줄어든다. content 테이블 없이 독립 테이블로 두고
-- ingest_dimensions가 직접 채운다(트리거로 자동 동기화하지 않는 이유:
-- INSERT OR REPLACE라 매번 delete+insert가 더 명확하고 디버깅하기 쉽다).
CREATE VIRTUAL TABLE IF NOT EXISTS dimensions_fts USING fts5(
    name,
    org_id UNINDEXED,
    tbl_id UNINDEXED,
    obj_id UNINDEXED,
    code UNINDEXED
);

-- records는 신규 테이블이라(facts처럼 구버전 NULL/'' 혼재 이력이 없음)
-- facts의 uq_facts_natural_key처럼 별도 마이그레이션/DROP-재생성 없이
-- 처음부터 COALESCE 기반으로 바로 만든다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_records_natural_key ON records
    (org_id, tbl_id, itm_id, prd_se,
     COALESCE(c1,''), COALESCE(c2,''), COALESCE(c3,''), COALESCE(c4,''),
     COALESCE(c5,''), COALESCE(c6,''), COALESCE(c7,''), COALESCE(c8,''));

-- catalog_titles도 records와 같은 이유로 처음부터 이렇게 만든다(신규
-- 테이블, 구버전 이력 없음) - org_id/tbl_id 자연키는 UNIQUE로 중복만
-- 막고(ingest_catalog_titles의 INSERT OR REPLACE가 이 인덱스로 동작한다),
-- 진짜 PRIMARY KEY는 위에서 추가한 surrogate id(catalog_vec과의 조인용).
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_titles_natural_key
    ON catalog_titles (org_id, tbl_id);

CREATE INDEX IF NOT EXISTS idx_fact_coverage_lookup
    ON fact_coverage (org_id, tbl_id, prd_se, itm_id, axis_key);

-- [2026-08-18 신규 - 값 기반 검색(VDB 진입점 ②) 실측 준비] search_by_value가
-- "이 시점(prd_de)에 이 값과 가까운 행을 표 전체에서 찾아라"를 실행할 때
-- 쓴다. idx_facts_lookup은 org_id/tbl_id/itm_id/prd_se로 시작해서 이
-- 질의(표를 모르는 채로 prd_de+value만 아는 상태)엔 안 맞는다 - 실측:
-- 인덱스 없이 prd_de만으로 필터링해도 418만 행 스캔에 3.4초가 걸려서
-- claim마다 부르기엔 너무 느렸다(이 인덱스 추가 후 재측정 필요).
CREATE INDEX IF NOT EXISTS idx_facts_value_search
    ON facts (prd_de, value);
"""

# [2026-08-16 마이그레이션] 이미 구버전 스키마로 만들어진 kosis_warehouse.db가
# 있을 수 있다(CREATE TABLE IF NOT EXISTS는 기존 테이블에 새 컬럼을 안 붙여준다).
# ALTER TABLE ADD COLUMN을 시도하고, 이미 있으면(중복 컬럼 에러) 조용히 넘어간다.
_MIGRATIONS = [
    "ALTER TABLE tables_registry ADD COLUMN full_path_id TEXT",
    "ALTER TABLE tables_registry ADD COLUMN topic_root TEXT",
]

_FACTS_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX uq_facts_natural_key ON facts "
    "(org_id, tbl_id, itm_id, prd_de, prd_se, "
    "COALESCE(c1,''), COALESCE(c2,''), COALESCE(c3,''), COALESCE(c4,''), "
    "COALESCE(c5,''), COALESCE(c6,''), COALESCE(c7,''), COALESCE(c8,''))"
)


def _ensure_facts_unique_index(conn: sqlite3.Connection) -> None:
    """[2026-08-16 신규] facts.uq_facts_natural_key를 COALESCE 표현식 기반
    정의로 맞춘다(위 _SCHEMA_INDEXES_SQL 옆 주석 참고 - NULL/'' 중복 재발
    방지). "CREATE ... IF NOT EXISTS"는 이름만 보고 기존 정의는 비교하지
    않으므로, 구버전(컬럼 그대로) 인덱스가 이미 있는 DB에 새로 연결해도
    조용히 그대로 남는다 - 그래서 sqlite_master에서 실제 정의를 직접
    비교해 다를 때만 DROP 후 다시 만든다(2백만 행 넘는 테이블에서 매
    연결마다 인덱스를 재생성하면 느리므로, 정의가 같으면 바로 반환).

    [중요] 이미 중복 행이 남아있는 DB에서는 이 UNIQUE 인덱스 생성 자체가
    "UNIQUE constraint failed"로 실패한다(중복을 먼저 지워야 함 - 이
    함수가 아니라 dedupe_facts.py가 하는 일). 그 경우 예외를 그대로
    올리지 않고 경고만 남기고 기존(약한) 인덱스 상태로 계속 동작한다 -
    이 자동 업그레이드 로직 때문에 기존 스크립트가 갑자기 크래시하면
    안 되기 때문이다. dedupe_facts.py를 한 번 실행해서 중복을 지우고
    나면, 그 다음 연결부터 자동으로 업그레이드된다."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_facts_natural_key'"
    ).fetchone()
    current_sql = (row[0] or "").strip() if row else None
    desired_sql = _FACTS_UNIQUE_INDEX_SQL.replace(
        "CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS", 1
    )
    if current_sql == _FACTS_UNIQUE_INDEX_SQL:
        return
    try:
        conn.execute("DROP INDEX IF EXISTS uq_facts_natural_key")
        conn.execute(desired_sql)
        conn.commit()
        logger.info("  └─ [facts UNIQUE 인덱스 강화] COALESCE 표현식 기반으로 재생성됨(중복 재발 방지)")
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        # [2026-08-16 실측 버그 수정] UNIQUE 위반은 sqlite3.OperationalError가
        # 아니라 sqlite3.IntegrityError로 올라온다 - 처음엔 OperationalError만
        # 잡아서 실제 실행 시(중복이 남아있는 real DB) 이 except에 안 걸리고
        # 그대로 크래시했다(dedupe_facts.py 첫 실행에서 실측 확인).
        if "unique" in str(e).lower():
            logger.warning(
                "  └─ [facts UNIQUE 인덱스 강화 보류] 아직 중복 행이 남아있어 강화된 인덱스를"
                " 만들 수 없습니다 - dedupe_facts.py를 먼저 실행하세요."
            )
        else:
            raise


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_TABLES_SQL)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
    conn.executescript(_SCHEMA_INDEXES_SQL)
    _ensure_facts_unique_index(conn)
    conn.commit()
    return conn


def _split_meta_rows(raw_list: List[Dict[str, Any]]):
    item_rows = [r for r in raw_list if r.get("OBJ_ID") == "ITEM"]
    category_rows = [r for r in raw_list if r.get("OBJ_ID") != "ITEM"]
    return item_rows, category_rows


def _sync_dimensions_fts(conn: sqlite3.Connection, org_id: str, tbl_id: str, rows: List[tuple]) -> None:
    """dimensions_fts를 dimensions와 같은 내용으로 맞춘다 - 트리거가 아니라
    ingest_dimensions가 매번 delete+insert로 직접 동기화한다(INSERT OR
    REPLACE 의미론을 트리거로 흉내내는 것보다 명확하고 디버깅하기 쉽다)."""
    conn.execute("DELETE FROM dimensions_fts WHERE org_id=? AND tbl_id=?", (org_id, tbl_id))
    if rows:
        conn.executemany(
            "INSERT INTO dimensions_fts (name, org_id, tbl_id, obj_id, code) VALUES (?,?,?,?,?)",
            [(r[6], r[0], r[1], r[2], r[5]) for r in rows],
        )


def ingest_dimensions(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, raw_meta_rows: List[Dict[str, Any]]
) -> int:
    """getMeta(type=ITM) 응답(항목 행 + 축 행이 섞여서 옴)을 dimensions
    테이블 행으로 그대로 정형화한다 - ENG 필드는 저장하지 않는다. 해석/
    분류(지수냐 등락률이냐 등)는 여기서 하지 않는다 - name/unit_hint
    원본 값만 저장하고, 그 해석은 검색 엔진(kosis_local_search.py)이
    검색 시점에 한다."""
    rows = []
    for r in raw_meta_rows:
        obj_id = r.get("OBJ_ID") or ""
        code = r.get("ITM_ID") or r.get("itmId")
        if not code:
            continue
        if obj_id == "ITEM":
            axis_position = 0
            axis_label = "항목"
        else:
            try:
                axis_position = int(str(r.get("OBJ_ID_SN")).strip())
            except (TypeError, ValueError):
                axis_position = None
            axis_label = r.get("OBJ_NM") or obj_id
        name = r.get("ITM_NM") or r.get("itmNm") or ""
        unit_hint = r.get("CD_NM") or r.get("CD_ENG_NM")
        rows.append((
            org_id, tbl_id, obj_id, axis_position, axis_label,
            code, name, r.get("UP_ITM_ID"), unit_hint,
        ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO dimensions "
            "(org_id, tbl_id, obj_id, axis_position, axis_label, code, name, parent_code, unit_hint) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        _sync_dimensions_fts(conn, org_id, tbl_id, rows)
    return len(rows)


def _parse_fact_rows(
    org_id: str, tbl_id: str, raw_data_rows: List[Dict[str, Any]]
) -> List[tuple]:
    """fetch_actual_statistics_bounded_retry(getList) 응답을 (org_id, tbl_id,
    itm_id, prd_de, prd_se, c1..c8, value, unit) 튜플 목록으로 파싱만 한다
    (DB에 쓰지는 않음) - ingest_facts(원자료 저장)와 ingest_records(전체
    기간 최댓값/최솟값 요약, 2026-08-17 신규)가 이 파싱 로직을 공유한다.
    각 행은 {"raw_dict": {원본 KOSIS 행}, ...} 형태로 오므로 raw_dict에서
    필요한 필드만 뽑는다 - C{n}_NM/C{n}_OBJ_NM 등 이름/의미 필드는
    dimensions 테이블에 이미 있으므로 코드(C{n})만 저장한다."""
    rows = []
    for r in raw_data_rows:
        raw = r.get("raw_dict") if isinstance(r, dict) and isinstance(r.get("raw_dict"), dict) else r
        itm_id = raw.get("ITM_ID")
        prd_de = raw.get("PRD_DE")
        prd_se = raw.get("PRD_SE")
        if not itm_id or not prd_de or not prd_se:
            continue
        value = raw.get("DT")
        try:
            value = float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        c_codes = [raw.get(f"C{i}") or "" for i in range(1, 9)]
        rows.append((
            org_id, tbl_id, itm_id, prd_de, prd_se,
            *c_codes,
            value, raw.get("UNIT_NM"),
        ))
    return rows


def ingest_facts(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, raw_data_rows: List[Dict[str, Any]]
) -> int:
    """_parse_fact_rows로 파싱한 행을 facts 테이블에 그대로 저장한다.

    [2026-08-16 실측 버그 발견] SQL의 UNIQUE 제약(PK든 별도 인덱스든)은
    NULL을 항상 서로 다른 값으로 취급한다(NULL != NULL) - 축이 없는 표
    (c1~c8이 전부 NULL)는 재적재해도 자연키가 "겹치지 않는다"고 판정되어
    INSERT OR REPLACE가 매번 새 행을 쌓기만 했다(테스트로 실측: 같은 행을
    두 번 넣으니 2건이 됨). 이건 이번 v2 스키마(surrogate id) 때문에 생긴
    문제가 아니라, v1의 복합 PRIMARY KEY 시절부터 이미 있던 잠재 버그다
    (v1도 같은 NULL 의미론을 따른다) - 축 없는 표를 다시 적재해본 적이
    없어서 지금까지 드러나지 않았을 뿐이다. NULL 대신 빈 문자열('')로
    저장해서 우회한다(c1~c8은 이미 TEXT 컬럼이라 자연스럽다)."""
    rows = _parse_fact_rows(org_id, tbl_id, raw_data_rows)
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO facts "
            "(org_id, tbl_id, itm_id, prd_de, prd_se, c1,c2,c3,c4,c5,c6,c7,c8, value, unit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def _filter_rows_after(raw_data_rows: List[Dict[str, Any]], floor_prd_de: str) -> List[Dict[str, Any]]:
    """years_back으로 이미 전체 기간을 당겨온 raw_data_rows 중 floor_prd_de
    이상인 것만 남긴다(API 재호출 없이 파이썬에서 필터링) - facts에는
    윈도우 안쪽만 저장하고 싶을 때 쓴다. prd_de는 같은 prd_se 안에서는
    자릿수가 고정된 순수 숫자 문자열이라(_normalize_kosis_period_bound)
    문자열 비교가 곧 시간 순서 비교와 같다."""
    filtered = []
    for r in raw_data_rows:
        raw = r.get("raw_dict") if isinstance(r, dict) and isinstance(r.get("raw_dict"), dict) else r
        prd_de = raw.get("PRD_DE")
        if prd_de is not None and str(prd_de) >= floor_prd_de:
            filtered.append(r)
    return filtered


def ingest_records(
    conn: sqlite3.Connection, org_id: str, tbl_id: str, raw_data_rows: List[Dict[str, Any]]
) -> int:
    """[2026-08-17 신규 - 사용자 제안, "역대 최고/최저" claim 대응] 계열
    (org_id, tbl_id, itm_id, prd_se, c1~c8)마다 전체 기간의 최댓값/최솟값과
    그 시점만 뽑아 records 테이블에 저장한다. facts처럼 시점마다 한 행이
    아니라 계열마다 한 행이라 훨씬 작다.

    [중요] 반드시 years_back으로 자르기 *전*의 raw_data_rows를 넘겨야
    한다 - 이미 잘린 데이터로 계산하면 "최근 N년 중 최댓값"이 되어버려
    "역대"(전체 역사) claim에 대한 답이 아니게 된다. ingest_table이 이
    함수를 호출할 때 항상 전체 조회 결과를 먼저 넘기고, facts 저장용
    필터링(_filter_rows_after)은 그 다음에 별도로 한다.

    [비용 - 반드시 알아야 할 트레이드오프] 이 함수를 쓰려면(즉 compute_
    records=True, 기본값) ingest_table이 매번 표의 전체 기간을 KOSIS에서
    실제로 조회해야 한다 - years_back을 줘도 "적게 조회하고 적게 저장"이
    아니라 "똑같이 다 조회하고 적게 저장"이 된다. years_back의 API 호출
    절감 효과는 없어지고 저장 용량 절감 효과만 남는다(1번 레버의 원래
    목적 중 절반). API 호출 자체를 줄이고 싶고 "역대" claim 지원이
    필요 없는 표라면 ingest_table(..., compute_records=False)로 이
    비용을 피할 수 있다(그 표는 records가 안 생기고 원래처럼 windowed
    구간만 조회한다).

    동점 처리: 여러 시점이 정확히 같은 최댓값/최솟값이면 시간순으로 가장
    이른 시점을 남긴다(추측 없이 결정론적으로 하나를 고르기 위한 임의
    규칙 - "최초 기록"이라는 의미로 읽으면 자연스럽다).
    """
    rows = _parse_fact_rows(org_id, tbl_id, raw_data_rows)
    groups: Dict[tuple, List[tuple]] = {}
    for row in rows:
        itm_id, prd_de, prd_se = row[2], row[3], row[4]
        c_codes = row[5:13]
        value = row[13]
        if value is None:
            continue
        key = (itm_id, prd_se, *c_codes)
        groups.setdefault(key, []).append((prd_de, value))

    record_rows = []
    computed_at = datetime.now(timezone.utc).isoformat()
    for key, points in groups.items():
        itm_id, prd_se, *c_codes = key
        points.sort(key=lambda p: p[0])  # 동점일 때 "가장 이른 시점"을 결정론적으로 고르기 위함
        max_prd_de, max_value = max(points, key=lambda p: p[1])
        min_prd_de, min_value = min(points, key=lambda p: p[1])
        coverage_strt = points[0][0]
        coverage_end = points[-1][0]
        record_rows.append((
            org_id, tbl_id, itm_id, prd_se, *c_codes,
            max_value, max_prd_de, min_value, min_prd_de,
            coverage_strt, coverage_end, computed_at,
        ))
    if record_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO records "
            "(org_id, tbl_id, itm_id, prd_se, c1,c2,c3,c4,c5,c6,c7,c8, "
            " max_value, max_prd_de, min_value, min_prd_de, "
            " coverage_strt_prd_de, coverage_end_prd_de, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            record_rows,
        )
    return len(record_rows)


def get_record(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    prd_se: str,
    axis_codes: Optional[Dict[int, str]] = None,
) -> Optional[Dict[str, Any]]:
    """records 테이블에서 이 (표, 항목, 축, 주기) 조합의 전체 기간 최댓값/
    최솟값을 순수 조회한다. "이 claim의 값이 정말 역대 최고/최저인가"
    판정(허용오차 비교, "역대" 패턴 감지 등)은 여기서 하지 않는다 - 그건
    검색/판정 엔진(kosis_local_search.py/judgment.py)의 몫이다."""
    where = ["org_id=?", "tbl_id=?", "itm_id=?", "prd_se=?"]
    params: List[Any] = [org_id, tbl_id, itm_id, prd_se]
    for axis, code in (axis_codes or {}).items():
        where.append(f"c{axis}=?")
        params.append(code)
    row = conn.execute(
        f"SELECT max_value, max_prd_de, min_value, min_prd_de, "
        f"coverage_strt_prd_de, coverage_end_prd_de FROM records WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if not row:
        return None
    return {
        "max_value": row[0], "max_prd_de": row[1],
        "min_value": row[2], "min_prd_de": row[3],
        "coverage_strt_prd_de": row[4], "coverage_end_prd_de": row[5],
    }


def _is_leaf_catalog_node(node: Dict[str, Any]) -> bool:
    """get_statistics_list() 응답 원소 하나가 리프(실제 표)인지 판단한다.

    [2026-08-17 실측 확정] 사용자가 `probe_statistics_list.py`로 실제 API
    키로 직접 호출해 확인함(`statistics_list_probe.json`) - 리프 노드는
    TBL_ID/TBL_NM/ORG_ID/STAT_ID/REC_TBL_SE/SEND_DE/VW_NM/VW_CD(8개) 필드를
    갖고 LIST_ID가 전혀 없다. 카테고리(중간) 노드는 LIST_ID/LIST_NM/VW_NM/
    VW_CD(4개) 필드만 갖고 TBL_ID가 전혀 없다 - 두 노드 종류가 완전히
    배타적이라 TBL_ID 존재 여부만으로 안전하게 구분된다(추정이 아니라
    실측으로 확인된 필드명이라, 예전에 방어적으로 넣었던 소문자/한글
    라벨 폴백은 제거했다)."""
    return "TBL_ID" in node


def crawl_catalog(
    kosis_client: Any,
    vw_cd: str = "MT_ZTITLE",
    parent_list_id: Optional[str] = None,
    _depth: int = 0,
    _max_depth: int = 8,
) -> List[Dict[str, Any]]:
    """[VDB discovery 원재료 수집] get_statistics_list()를 parentListId로
    재귀적으로 드릴다운하며 리프(실제 표) 노드를 전부 모은다 - 카테고리
    노드는 더 깊이 내려가고, 리프 노드는 결과 목록에 담는다.

    search_metadata(searchNm 키워드 검색)와 달리 검색어가 필요 없다 -
    "KOSIS 전체에 어떤 표가 있는지" 제목만이라도 넓게 모으는 게 목적
    이므로, 이 함수는 claim이나 키워드를 전혀 모른 채로 호출된다(보통
    한 번 크게 돌려서 catalog_titles를 채워두고, 그 뒤로는 이 함수를
    다시 안 부른다 - 주기적 재크롤은 별도 스케줄링 문제).

    _max_depth는 무한 재귀 방지용 안전장치 - 정상적인 KOSIS 분류
    트리는 이보다 훨씬 얕다(실측: 대분류 -> 물가(P2) -> 소비자물가조사
    (P2_6) -> 리프 표, 총 3단계). 순환 참조나 예상 밖의 깊은 트리를
    만나도 무한 루프에 빠지지 않게 막는다.

    [2026-08-17 실측 완료] `get_statistics_list` 자체(HTTP 호출+파싱)와
    이 함수의 분기 로직(리프 vs 카테고리 판단, 재귀 드릴다운) 둘 다 실제
    데이터로 확인됐다 - 사용자가 `probe_statistics_list.py`를 실제 API
    키로 3번 호출(parentListId 없음/P2/P2_6)해서 raw 응답을 받았고
    (`statistics_list_probe.json`), 그 필드명 그대로 아래 파싱 로직과
    `_is_leaf_catalog_node`를 확정했다.
    """
    if _depth > _max_depth:
        logger.warning(
            f"[카탈로그 크롤 안전장치] parent_list_id={parent_list_id} - "
            f"최대 깊이({_max_depth}) 도달, 중단"
        )
        return []
    nodes = kosis_client.get_statistics_list(vw_cd=vw_cd, parent_list_id=parent_list_id)
    leaves: List[Dict[str, Any]] = []
    for node in nodes:
        if _is_leaf_catalog_node(node):
            leaves.append({
                "org_id": node.get("ORG_ID"),
                "tbl_id": node.get("TBL_ID"),
                "tbl_nm": node.get("TBL_NM"),
                "stat_id": node.get("STAT_ID"),
                "rec_tbl_se": node.get("REC_TBL_SE"),
                "send_de": node.get("SEND_DE"),
                "vw_cd": vw_cd,
                "parent_list_id": parent_list_id,
            })
        else:
            list_id = node.get("LIST_ID")
            if list_id:
                leaves.extend(
                    crawl_catalog(kosis_client, vw_cd, list_id, _depth + 1, _max_depth)
                )
            else:
                logger.warning(
                    f"[카탈로그 크롤] 리프도 카테고리도 아닌 노드 발견(필드 미상): {node}"
                )
    return leaves


def ingest_catalog_titles(conn: sqlite3.Connection, entries: List[Dict[str, Any]]) -> int:
    """crawl_catalog() 결과를 catalog_titles에 적재한다 - 깊은 적재
    (ingest_table, getMeta+getList)가 아니라 "제목만" 얕게 담는다.
    org_id/tbl_id가 없는 항목(파싱 실패한 노드)은 조용히 건너뛴다.

    [2026-08-17 실측 버그 수정] 처음엔 INSERT OR REPLACE를 썼는데, 직접
    테스트해보니 같은 (org_id, tbl_id)를 재적재하면 surrogate id가
    바뀌었다(1 -> 2) - INSERT OR REPLACE는 PRIMARY KEY가 아닌 UNIQUE
    제약(org_id/tbl_id)에 충돌해도 내부적으로 DELETE+INSERT라 매번 새
    id를 발급한다. sqlite-vec 붙이기 전이라 지금까지는 문제가 없었지만,
    catalog_vec이 이 id를 rowid로 그대로 쓰므로 재크롤할 때마다 id가
    바뀌면 기존 벡터가 전부 고아가 된다 - ON CONFLICT ... DO UPDATE로
    바꿔서 기존 행을 제자리에서 갱신(id 보존)하도록 고쳤다.

    [2026-08-17 실측 버그 수정 - 2차] catalog_vec(sqlite-vec)과 같은
    커넥션에 catalog_titles를 같이 넣으려면 이 함수도 apsw 커넥션을 받을
    수 있어야 하는데, apsw.Connection에는 commit()이 없다(기본
    autocommit - vdb_discovery.py의 _compat_commit과 같은 이유,
    사용자의 실제 probe_sqlite_vec.py 실행에서 AttributeError로 확인됨).
    이 파일의 다른 ingest_* 함수들(ingest_facts 등)은 항상 표준 sqlite3/
    pysqlite3 커넥션(kosis_warehouse.get_connection())으로만 쓰이므로 안
    건드리고, catalog_vec과 커넥션을 공유할 가능성이 있는 이 함수만
    방어적으로 고친다."""
    rows = [
        (
            e.get("org_id"), e.get("tbl_id"), e.get("tbl_nm"), e.get("stat_id"),
            e.get("rec_tbl_se"), e.get("send_de"),
            e.get("vw_cd"), e.get("parent_list_id"),
            datetime.now(timezone.utc).isoformat(),
        )
        for e in entries if e.get("org_id") and e.get("tbl_id")
    ]
    if rows:
        conn.executemany(
            "INSERT INTO catalog_titles "
            "(org_id, tbl_id, tbl_nm, stat_id, rec_tbl_se, send_de, vw_cd, parent_list_id, crawled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(org_id, tbl_id) DO UPDATE SET "
            "tbl_nm=excluded.tbl_nm, stat_id=excluded.stat_id, "
            "rec_tbl_se=excluded.rec_tbl_se, send_de=excluded.send_de, "
            "vw_cd=excluded.vw_cd, parent_list_id=excluded.parent_list_id, "
            "crawled_at=excluded.crawled_at",
            rows,
        )
        if hasattr(conn, "commit"):
            conn.commit()
    return len(rows)


def _topic_root(full_path_id: Optional[str]) -> Optional[str]:
    """full_path_id("P2 > P2_6" 또는 "R_SUB_OTITLE > ...")의 최상위 노드만
    잘라낸다 - 사람이 읽는 라벨은 아니지만, 같은 최상위 노드를 공유하는
    표는 같은 주제(물가/고용/재정/국제기구 등)라는 사실은 그대로 필터에
    쓸 수 있다."""
    if not full_path_id:
        return None
    return full_path_id.split(">")[0].strip() or None


def register_table(
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    tbl_nm: Optional[str] = None,
    stat_id: Optional[str] = None,
    stat_nm: Optional[str] = None,
    vw_cd: Optional[str] = None,
    full_path_id: Optional[str] = None,
    strt_prd_de: Optional[str] = None,
    end_prd_de: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tables_registry "
        "(org_id, tbl_id, tbl_nm, stat_id, stat_nm, vw_cd, "
        " full_path_id, topic_root, strt_prd_de, end_prd_de, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            org_id, tbl_id, tbl_nm, stat_id, stat_nm, vw_cd,
            full_path_id, _topic_root(full_path_id),
            strt_prd_de, end_prd_de, datetime.now(timezone.utc).isoformat(),
        ),
    )


def _normalize_kosis_period_bound(raw: Optional[str], prd_se: str) -> Optional[str]:
    """[2026-08-16 실측 발견] get_period_meta()(getMeta type=PRD)가 돌려주는
    STRT_PRD_DE/END_PRD_DE는 getList가 요구하는 순수 숫자 코드가 아니라
    사람이 읽기 좋은 표시 포맷으로 온다 - seed_ingest.py 첫 실행 로그에서
    실측:
      - 연(Y): "1966" - 이미 4자리 순수 숫자라 우연히 문제없음
      - 분기(Q): "1960 1/4" - 공백 + "분자/4" 표기. 숫자만 남기는 naive
        strip을 쓰면 분모 "4"까지 붙어 "196014"(6자리, 틀림)가 된다
      - 월(M): "1960.01" - 마침표 포함. naive strip으로는 우연히
        "196001"(6자리, 맞음)이 되지만 그대로 넘기면 마침표 때문에 KOSIS가
        거부한다

    반면 getList가 실제로 받는 PRD_DE 코드는 구분자 없는 순수 숫자이고
    길이로 주기를 구분한다(연=4자리/분기=5자리/월=6자리 -
    new_kosis_agent.py._period_to_prd_se와 동일한 규칙). 이 정규화 없이
    raw 값을 그대로 fetch에 넘기면 KOSIS 에러코드 21("수록 시점... 숫자만
    사용해야 합니다")이 나고, err21은 fetch_actual_statistics_bounded_retry가
    "차원 불일치"로 오인해서 objL을 늘려가며 최대 8번 재시도하다가 결국
    빈 리스트로 끝난다(실측: DT_2IFS002 분기/월이 이 경로로 값 0건).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if prd_se == "Q":
        m = re.search(r"(\d{4})\D+(\d)\s*/\s*4", s)
        if m:
            return f"{m.group(1)}{m.group(2)}"
        digits = re.sub(r"[^0-9]", "", s)
        if len(digits) == 5:
            return digits
        logger.warning(f"  └─ [분기 수록시점 정규화 실패] 원본='{s}' - 그대로 사용(에러 가능성 있음)")
        return s
    if prd_se == "M":
        digits = re.sub(r"[^0-9]", "", s)
        if len(digits) == 6:
            return digits
        logger.warning(f"  └─ [월 수록시점 정규화 실패] 원본='{s}' - 그대로 사용(에러 가능성 있음)")
        return s
    if prd_se == "Y":
        digits = re.sub(r"[^0-9]", "", s)
        if len(digits) == 4:
            return digits
        logger.warning(f"  └─ [연 수록시점 정규화 실패] 원본='{s}' - 그대로 사용(에러 가능성 있음)")
        return s
    # F(다년)/IR(부정기) 등 형식이 문서화돼 있지 않은 주기 - 숫자만 남겨서
    # 시도하되, 실패해도 원본을 보존한다(추측으로 데이터를 왜곡하지 않는다).
    digits = re.sub(r"[^0-9]", "", s)
    return digits or s


_PRD_SE_LABEL_TO_CODE = {
    "년": "Y", "연": "Y", "연간": "Y", "Y": "Y",
    "분기": "Q", "Q": "Q",
    "월": "M", "M": "M",
    "반기": "H", "H": "H",
    "다년": "F", "F": "F",
    "부정기": "IR", "IR": "IR",
    "격년": "F",  # 2년마다 - 아래 "F" 설명 참고
}

# [2026-08-17 실측 발견 - 독서 실태 조사(DT_113_STBL_1024687)] "2년"처럼
# 숫자+"년"으로 된 라벨(3년/5년 등도 같은 패턴일 수 있음 - N년마다 하는
# 조사)이 위 딕셔너리에 없으면 원본 문자열("2년")을 그대로 prdSe 파라미터로
# 넘겨서 KOSIS가 이 값 자체를 인식 못 해 **조회 대상 연도 전부**가
# 에러코드 30("데이터가 존재하지 않습니다")으로 실패했다(실측: 2011~2025
# 15개 연도 전부 실패, 값 0건 적재).
#
# [2026-08-17 1차 수정이 틀렸음 - 재수정] 처음엔 "getList의 prdSe는
# Y/Q/M만 받는다"고 잘못 판단해서 Y로 매핑했는데, 그렇게 고쳐도 여전히
# 15개 연도 전부 에러30이 났다(사용자가 직접 재실행해서 실측 확인). KOSIS
# MCP 도구(kosis_get_data)의 공식 파라미터 설명을 다시 확인하니 "F=다년
# (2~10년)"이라는, Y/Q/M/H/IR과 별도인 전용 코드가 있었다 - "다년": "F"
# 매핑이 이미 위 딕셔너리에 있었는데 정작 숫자+"년" 패턴(정규식 폴백)에는
# 안 쓰고 있었다. 실제로 prdSe=F로 라이브 조회해서 진짜 데이터(2021/2023/
# 2025년, 2년 간격)가 나오는 것까지 확인 후 F로 재수정.
_PRD_SE_NUMBERED_YEAR_RE = re.compile(r"^\d+년$")


def _canonical_prd_se(raw_prd_se: Optional[str]) -> str:
    """[2026-08-16 실측 발견 - seed_ingest.py 2차 실행] get_period_meta()
    (getMeta type=PRD)의 PRD_SE 필드는 client.py 자체 docstring이 이미
    경고했던 대로("필드명이 공식 문서에 명확히 없어 방어적으로 파싱한다")
    실제로는 Y/Q/M 코드가 아니라 한글 라벨("년"/"분기"/"월")이었다 -
    사용자가 로컬에서 raw 응답을 직접 찍어 확인:
      [{"PRD_SE": "월", "STRT_PRD_DE": "1965.01", ...},
       {"PRD_SE": "분기", "STRT_PRD_DE": "1965 1/4", ...},
       {"PRD_SE": "년", "STRT_PRD_DE": "1965", ...}]
    코드 필드 자체가 원본 응답에 없다(KOSIS MCP 도구가 보여주는 "prdSe":
    "M" 같은 코드는 그 MCP 서버가 자체적으로 매핑해서 붙인 것이지 raw
    필드가 아니다). getList(fetch_actual_statistics_bounded_retry)의
    prdSe 파라미터는 Y/Q/M/H/F/IR 코드만 받으므로(F=다년(2~10년) -
    2026-08-17 KOSIS MCP 공식 파라미터 설명으로 재확인, 처음엔 Y/Q/M만
    받는 줄 알았던 게 틀렸었다 - 아래 _PRD_SE_NUMBERED_YEAR_RE 주석 참고),
    한글 라벨을 그대로 넘기면 KOSIS가 그 주기 자체를 인식 못 해 에러코드
    30("데이터가 존재하지 않습니다")을 낸다 - 수록시점 포맷 정규화
    (_normalize_kosis_period_bound)만으로는 못 잡았던, 더 근본적인
    원인이었다(8/10표가 이걸로 값 0건이었음).
    """
    if not raw_prd_se:
        return "Y"
    cleaned = str(raw_prd_se).strip()
    mapped = _PRD_SE_LABEL_TO_CODE.get(cleaned)
    if mapped:
        return mapped
    if _PRD_SE_NUMBERED_YEAR_RE.match(cleaned):
        return "F"
    logger.warning(f"  └─ [주기 코드 매핑 실패] 알 수 없는 PRD_SE='{raw_prd_se}' - 원본 그대로 사용(에러 가능성 있음)")
    return str(raw_prd_se)


def _period_to_ordinal(period: Optional[str], prd_se: str) -> Optional[int]:
    """정규화된 순수 숫자 PRD_DE 코드를 정수 순서값으로 변환한다 - 기간을
    반으로 쪼개는 이분 탐색(_fetch_with_chunking)에서 "중간 지점"을 계산하는
    데 쓴다. Y는 그 자체가 순서값, Q/M은 연도*주기수+오프셋으로 변환해야
    "1999년 4분기"보다 "2000년 1분기"가 1 큰 값이 되는 게 보장된다(단순
    문자열 비교/뺄셈으로는 이게 안 맞는다)."""
    if not period:
        return None
    s = str(period)
    try:
        if prd_se == "Y":
            return int(s)
        if prd_se == "Q":
            return int(s[:4]) * 4 + int(s[4:5])
        if prd_se == "M":
            return int(s[:4]) * 12 + int(s[4:6])
        return int(re.sub(r"[^0-9]", "", s) or 0)
    except (ValueError, IndexError):
        return None


def _ordinal_to_period(ordinal: int, prd_se: str) -> str:
    if prd_se == "Y":
        return str(ordinal)
    if prd_se == "Q":
        year, q0 = divmod(ordinal - 1, 4)
        return f"{year}{q0 + 1}"
    if prd_se == "M":
        year, m0 = divmod(ordinal - 1, 12)
        return f"{year}{m0 + 1:02d}"
    return str(ordinal)


# [2026-08-17 신규 - 적재 범위 정책 1번 레버] Research Overview 2("데이터
# 웨어하우스 전환") 문서에서 실측 확정한 것: kosis_warehouse.db 표 크기를
# 지배하는 건 축 세분화가 아니라 "수십 년치 시계열을 통째로 담는 것"이었다
# (예: DT_404Y016이 1965~2026년/799개 시점을 전부 적재해서 표 하나가
# 558,256행). 뉴스 claim은 최근 시점 값이나 기준연도(예: 2020=100) 비교를
# 주로 다루므로, 최근 N년만 적재해도 정확도 손실 없이 크기를 크게 줄일 수
# 있다는 판단 - 다만 이건 "판단"이지 아직 실측 검증은 안 됐다(Future Work).
_PERIODS_PER_YEAR = {"Q": 4, "M": 12, "H": 2}

# [2026-08-17 - "역대 최고/최저" claim 대응] 연간(annual) 데이터는
# years_back과 무관하게 절대 자르지 않는다. 이유 두 가지:
# (1) 용량 - 실측(kosis_warehouse.db 전체)해보면 같은 표의 연간 데이터는
#     월간 데이터의 10분의 1 이하 크기다(예: DT_2IFS002 - 월간 389,022행
#     vs 연간 33,499행). 표 크기를 지배하는 건 애초에 M/Q였지 연간이
#     아니었다 - 연간을 안 잘라도 용량 절감 효과는 거의 그대로 남는다.
# (2) "역대" claim - README.md 3장 H번("역대 최고/최저") 논의에서 이미
#     "확인하려면 과거 전체 기록과 비교해야 하는데 지금 모듈은 1~2개
#     시점만 본다"는 한계가 진단됐고, judgment.py는 이런 claim을 아예
#     UNVERIFIED_RECORD_CLAIM으로 declining하고 있다(_RECORD_CLAIM_RE).
#     근데 웨어하우스 구조에서는 "이 항목의 전체 기간 중 최댓값"이
#     `SELECT MAX(value) ... WHERE itm_id=? AND c1=? ...`로 단순 SQL이라
#     라이브 API로 매 과거 시점을 훑어야 했던 예전 구조보다 오히려 훨씬
#     쉽게 풀린다 - 단, 그 항목의 전체 역사가 실제로 DB에 있어야만
#     성립한다. years_back으로 연간 데이터까지 잘라버리면 이 가능성
#     자체가 원천적으로 막힌다. "역대" claim을 나중에 실제로 지원하기로
#     하든(declining을 풀든) 계속 declining하든, 연간 데이터를 지금
#     미리 잘라둘 이유가 없다(용량 이득도 거의 없으므로) - 그래서 잘라야
#     할 이유가 없는 쪽(보존)을 기본값으로 한다.
#
# [주의 - 남는 한계] 이건 "연간 단위로 집계된 기록" claim만 구제한다.
# "역대 최고 폭염일수"처럼 월/일 단위 기록을 주장하는 claim은 그 표의
# M/Q 데이터가 여전히 잘리므로 이 예외로 못 구한다 - 그 claim까지
# 지원하려면 "역대" 패턴을 claim에서 감지해서 해당 표만 그 축/항목에
# 한해 years_back=None으로 강제하는 별도 로직이 필요하다(아직 미구현).
#
# [주의 - 코드 불일치 발견] `_period_to_ordinal`/`_ordinal_to_period`
# 등 이 파일 다른 곳은 연간 주기 코드를 전부 "Y"로 가정하는데, 실제
# kosis_warehouse.db의 facts.prd_se를 조회해보면 "Y"는 단 하나도 없고
# 전부 "A"다(getList 응답 자체의 원본 PRD_SE 필드 - _canonical_prd_se가
# *요청* 파라미터용으로 매핑하는 "Y"와는 다른, *응답*에 실제로 찍혀오는
# 코드). 지금까지는 _period_to_ordinal의 마지막 폴백(순수 숫자 파싱)이
# 연간 문자열("2020")엔 우연히 맞아떨어져서 문제가 드러나지 않았지만,
# 정확한 코드는 아니다 - 전체 정리는 범위가 커서 별도 작업으로 남겨두고
# (Future Work), 여기서는 실제 코드("A")와 문서상 가정("Y") 둘 다
# 안전하게 처리한다.
_ANNUAL_PRD_SE_CODES = {"Y", "A"}


def _clip_period_window(
    strt_norm: Optional[str], end_norm: Optional[str], prd_se: str, years_back: Optional[int],
) -> Optional[str]:
    """정규화된 시작 시점(strt_norm)을 "끝 시점(end_norm)에서 최근
    years_back년까지"로 당겨온다(끝 시점은 절대 안 건드린다 - 최신 데이터가
    잘리면 안 되므로). 원래 시작이 이미 그 윈도우보다 최근이면(표 자체가
    짧으면) 그대로 둔다 - 이 함수는 절대 기간을 "늘리지" 않는다.

    연간 데이터(_ANNUAL_PRD_SE_CODES)는 항상 그대로 보존한다(위 주석
    참고 - 용량도 작고 "역대" claim 대응을 위해 일부러 안 자름).
    F(다년)/IR(부정기) 등 "1년에 몇 개 시점"이 명확하지 않은 주기도
    _PERIODS_PER_YEAR에 없으므로 클리핑하지 않고 원본 그대로 반환한다 -
    추측으로 자르는 것보다 안전한 쪽(원본 보존)을 택한다(모듈 전체의
    "추측하지 않는다" 원칙과 일관).
    """
    if years_back is None or not strt_norm or not end_norm:
        return strt_norm
    if prd_se in _ANNUAL_PRD_SE_CODES:
        return strt_norm
    per_year = _PERIODS_PER_YEAR.get(prd_se)
    if not per_year:
        return strt_norm
    strt_ord = _period_to_ordinal(strt_norm, prd_se)
    end_ord = _period_to_ordinal(end_norm, prd_se)
    if strt_ord is None or end_ord is None:
        return strt_norm
    floor_ord = end_ord - years_back * per_year
    if floor_ord <= strt_ord:
        return strt_norm  # 표가 이미 윈도우보다 짧다 - 자를 게 없음
    return _ordinal_to_period(floor_ord, prd_se)


# [2026-08-18 신규 - 적재 범위 정책 3번 레버] narrow/wide 경계값을 새로
# 추측하지 않고, 이미 실측으로 확인된 KOSIS 서버 자체의 제약을 그대로
# 재사용한다: err31 메시지 원문이 "40,000 셀을 초과한 결과값은 요청하실
# 수 없습니다"라고 명시한다(_fetch_with_chunking 참고, 이 프로젝트가 이미
# 여러 표에서 실제로 이 에러를 만나 청킹 로직까지 만든 값). 이 값보다
# 넓은 표는 한 번의 요청으로도 못 받아 어차피 청킹이 필요하고, 실제
# kosis_warehouse.db 실측(2026-08-18)에서도 청킹이 필요했던 표들이
# 그대로 전체 저장량의 96.4%(상위 5개/12개)를 차지했다 - "청킹이 필요할
# 만큼 넓다"와 "배치 완전 적재 비용이 크다"가 실측으로 같은 표 집합을
# 가리켰으므로, 별도 임계값을 새로 정하지 않고 이 서버 제약값을 그대로
# narrow/wide 분류 경계로 쓴다.
_WIDE_TABLE_CELL_THRESHOLD = 40000

# [2026-08-18 수정 - 사용자 지적] 처음 버전은 wide 표를 만나면 getList를
# 통째로 건너뛰고 온디맨드에만 의존하게 만들었는데, 이건 틀렸다 - getMeta
# (분류 코드 목록)만으로는 그 표의 실제 값이 어떤 모양으로 나오는지(단위/
# 결측 패턴/실제로 존재하는 축 조합이 무엇인지 - 위 axis_product가 상한선
# 추정일 뿐 실제 셀 수가 아닌 이유와 같음) 전혀 알 수 없다. 그래서 wide
# 표도 반드시 최소 한 구간은 실제 getList로 통째 적재해서 실제 값 형태를
# 확보해야 한다. 대신 "narrow/wide로 아예 다르게 취급"하는 이분법을
# 버리고, "모든 표를 기간 단위로 쪼개서 적재하고, 없는 기간은 온디맨드로
# 채운다"로 통일한다 - wide 표는 배치 시점에 years_back이 안 주어져도
# (None) 이 기본값만큼은 강제로 캡을 씌워 "적어도 최근 구간은" 실제
# getList로 채우고, 그보다 오래된 구간은 fetch_scoped_slice가 claim이
# 실제로 필요로 할 때 채운다. 값 자체는 임의로 고른 정책값(추측 아님,
# 순수 엔지니어링 결정) - narrow 표는 이 캡의 영향을 안 받는다(narrow는
# 원래도 전체 적재해도 비용이 미미했으므로).
_WIDE_TABLE_DEFAULT_YEARS_BACK = 5


def _estimate_table_cell_count(
    raw_meta: List[Dict[str, Any]], period_count: Optional[int]
) -> int:
    """getMeta(type=ITM) 응답(raw_meta - item_rows+category_rows 원본)과
    기간 수(period_count)만으로, 실제 getList 호출 없이 "이 표를 itmId=all
    +objL=all로 한 번에 당기면 셀이 몇 개나 나올까"를 상한선으로 추정한다.

    상한선(추정치가 실제보다 크거나 같음)인 이유: 항목 수 × (각 분류축의
    코드 수를 전부 곱한 값) × 기간 수로 계산하는데, 이건 모든 축 조합이
    실제로 다 존재한다고 가정한 최댓값이다(표가 성긴 경우 실제 셀 수는
    이보다 적을 수 있음). "넓다"고 잘못 판단해 온디맨드로 돌리는 쪽은
    안전하고(비용만 조금 손해), "좁다"고 잘못 판단해 배치 완전 적재를
    시도하는 쪽은 err31로 드러나므로, 상한선 추정이 이 용도에 맞다
    (추측 대신 항상 안전한 쪽으로 판단한다는 모듈 전체 원칙과 일관).

    period_count가 None이면(주기 정보를 못 구한 경우) 기간은 1로 취급해서
    축×항목만으로 추정한다 - 기간을 모른다고 무조건 narrow로 취급하면
    실제로 넓은 표를 놓칠 수 있으므로, 아는 것만으로 최대한 보수적으로
    판단한다."""
    item_rows, category_rows = _split_meta_rows(raw_meta)
    item_count = len(item_rows) or 1

    codes_per_axis: Dict[str, set] = {}
    for r in category_rows:
        obj_id = r.get("OBJ_ID")
        code = r.get("ITM_ID") or r.get("itmId")
        if obj_id is None or code is None:
            continue
        codes_per_axis.setdefault(obj_id, set()).add(code)

    axis_product = 1
    for codes in codes_per_axis.values():
        axis_product *= max(len(codes), 1)

    return item_count * axis_product * (period_count or 1)


def classify_table_width(
    raw_meta: List[Dict[str, Any]], strt_norm: Optional[str], end_norm: Optional[str], prd_se: str,
) -> Dict[str, Any]:
    """표 하나(특정 prd_se 기준)가 narrow인지 wide인지 판정한다. ingest_table의
    prd_se 루프 안에서 매 주기마다 호출한다 - 같은 표라도 연간은 narrow,
    월간은 wide일 수 있다(예: 항목이 적어도 월간은 시점 수가 12배).

    반환: {"width": "narrow"|"wide", "estimated_cells": int, "threshold": int}
    - narrow: 지금처럼 itmId=all 배치 완전 적재(compute_records 포함)
    - wide: getMeta(이미 별도로 적재됨)만 확정하고 getList는 온디맨드로 미룸
      (fetch_scoped_slice + fact_coverage가 담당, Task #52)."""
    period_count = None
    if strt_norm and end_norm:
        strt_ord = _period_to_ordinal(strt_norm, prd_se)
        end_ord = _period_to_ordinal(end_norm, prd_se)
        if strt_ord is not None and end_ord is not None:
            period_count = end_ord - strt_ord + 1

    estimated = _estimate_table_cell_count(raw_meta, period_count)
    width = "wide" if estimated > _WIDE_TABLE_CELL_THRESHOLD else "narrow"
    return {"width": width, "estimated_cells": estimated, "threshold": _WIDE_TABLE_CELL_THRESHOLD}


def _normalize_axis_key(objl_fixed: Optional[Dict[int, str]]) -> str:
    """objl_fixed({축번호: 코드})를 fact_coverage.axis_key에 저장할 정규화된
    문자열로 만든다 - 축 번호 오름차순으로 정렬해 "1=A0201|2=B01"처럼 만들면
    같은 조합이 항상 같은 문자열이 되어(딕셔너리 순서 문제 없이) 커버리지
    조회 시 정확히 일치 비교를 할 수 있다. 축 제한이 전혀 없으면(narrow
    배치 적재처럼 전체를 다 받은 경우) 'all'을 쓴다."""
    if not objl_fixed:
        return "all"
    return "|".join(f"{axis}={code}" for axis, code in sorted(objl_fixed.items()))


def record_coverage(
    conn: sqlite3.Connection,
    org_id: str, tbl_id: str, prd_se: str, itm_id: str, axis_key: str,
    strt_prd_de: str, end_prd_de: str,
) -> None:
    """이 (표, 주기, 항목, 축조합)이 [strt_prd_de, end_prd_de] 구간만큼
    facts에 실제로 적재됐다는 사실을 fact_coverage에 한 줄 남긴다. 같은
    (org_id, tbl_id, prd_se, itm_id, axis_key)로 여러 번 기록될 수 있다
    (매번 병합하지 않고 이력을 그대로 쌓는다 - find_coverage_gap이 여러
    줄 중 하나라도 필요한 구간을 덮으면 충분하다고 판단하므로, 병합
    로직 없이도 정확하게 동작한다. 병합/정리는 필요해지면 별도 유지보수
    작업으로 남겨둔다)."""
    conn.execute(
        "INSERT INTO fact_coverage "
        "(org_id, tbl_id, prd_se, itm_id, axis_key, strt_prd_de, end_prd_de, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (org_id, tbl_id, prd_se, itm_id, axis_key, strt_prd_de, end_prd_de, _now_iso()),
    )


def is_period_covered(
    conn: sqlite3.Connection,
    org_id: str, tbl_id: str, prd_se: str, itm_id: str, axis_key: str,
    needed_strt: str, needed_end: str,
) -> bool:
    """claim이 필요로 하는 [needed_strt, needed_end] 구간이 이미
    fact_coverage에 기록된 범위 안에 완전히 들어있는지 확인한다.

    itm_id/axis_key는 정확히 일치하는 커버리지 행뿐 아니라, narrow 배치
    적재가 남긴 itm_id='all'/axis_key='all' 행(그 표/주기 전체가 이미
    커버됨)도 함께 인정한다 - OR 조건으로 둘 다 조회.

    [알려진 단순화] 부분적으로 겹치는 여러 커버리지 행을 이어붙여
    "구간 전체가 조각조각 커버됨"을 판단하지는 않는다 - 단일 행이 통째로
    덮는 경우만 covered로 본다. 실제로 겹치지만 분절된 커버리지가 쌓이는
    경우(예: 2020~2022 한 번, 2023~2024 한 번 따로 온디맨드로 채워진 뒤
    2020~2024 claim이 들어오는 경우)는 아직 커버된 것으로 인식 못 하고
    다시 조회한다 - 안전한 방향(중복 조회는 낭비지만 데이터 누락은 아님)
    이라 일단 이렇게 두고, 실제로 이 케이스가 자주 나오면 그때 구간
    병합 로직을 추가한다(추측으로 미리 만들지 않음)."""
    row = conn.execute(
        "SELECT 1 FROM fact_coverage "
        "WHERE org_id=? AND tbl_id=? AND prd_se=? "
        "AND (itm_id=? OR itm_id='all') AND (axis_key=? OR axis_key='all') "
        "AND strt_prd_de<=? AND end_prd_de>=? LIMIT 1",
        (org_id, tbl_id, prd_se, itm_id, axis_key, needed_strt, needed_end),
    )
    return row.fetchone() is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_max_working_span(
    kosis_client: Any, org_id: str, tbl_id: str, anchor_ord: int, upper_bound_span: int,
    prd_se: str, itm_id: str,
    objl_fixed: Optional[Dict[int, str]] = None, current_dim: int = 0,
) -> tuple:
    """anchor_ord에서 시작하는 요청이 성공하는 "최대 폭"을 이분탐색으로
    한 번만 찾는다 - 호출부는 이미 upper_bound_span 전체가 실패했다는
    걸 알고 있는 상태에서 부른다. 반환값은 (성공한 최대 폭 또는 None,
    그 폭으로 실제 받은 데이터). 폭 0(단일 시점)조차 실패하면 (None, []).

    objl_fixed/current_dim: [2026-08-18 신규] wide 표 온디맨드 정밀 조회
    (fetch_scoped_slice)가 넘긴다 - 기본값(None/0)이면 예전처럼 모든 축을
    "all"로 받고 차원 수를 0부터 스스로 재발견한다(narrow 배치 적재 경로는
    이 인자를 안 넘기므로 기존 동작 그대로 안 바뀜)."""
    lo, hi = 0, upper_bound_span - 1
    best_span, best_raw = None, []
    while lo <= hi:
        mid = (lo + hi) // 2
        probe_end = _ordinal_to_period(anchor_ord + mid, prd_se)
        anchor = _ordinal_to_period(anchor_ord, prd_se)
        raw = kosis_client.fetch_actual_statistics_bounded_retry(
            org_id, tbl_id, anchor, probe_end, itm_id=itm_id, prd_se=prd_se,
            objl_fixed=objl_fixed, current_dim=current_dim,
        )
        if raw:
            best_span, best_raw = mid, raw
            lo = mid + 1
        else:
            hi = mid - 1
    return best_span, best_raw


def _fetch_with_chunking(
    kosis_client: Any,
    org_id: str,
    tbl_id: str,
    strt: str,
    end: str,
    prd_se: str,
    itm_id: str = "all",
    _guard: int = 0,
    objl_fixed: Optional[Dict[int, str]] = None,
    current_dim: int = 0,
) -> List[Dict[str, Any]]:
    """[2026-08-16 신규, 사용자 제안으로 최적화] KOSIS 에러코드 31("40,000
    셀을 초과한 결과값은 요청하실 수 없습니다")을 우회한다 - itmId=all +
    전체 수록기간을 한 번에 요청하면, 항목/축이 많은 표(예: DT_404Y016,
    928 dims)는 결과 셀 수가 4만을 넘어 통째로 실패한다.

    [최초 구현 - 순수 이분 재시도] 실패하면 그냥 반으로 쪼개 양쪽을
    각각 재귀 호출했더니, seed_ingest.py 실측(10개 표)에서 API 호출이
    1,022회, ~10분이 걸렸다 - 로그를 보면 형제 가지들이 "같은 상한(성공
    하는 최대 폭)"을 서로 독립적으로 몇 번이고 다시 찾고 있었다(예:
    DT_404Y016의 월간 조회 하나에서만 이분 실패 로그가 30줄 넘게 반복).

    [이 버전 - 한 번만 탐색 + 재사용] 실패를 처음 만나면 시작점을
    기준으로 "성공하는 최대 폭"을 이분탐색으로 **한 번만** 찾고
    (_probe_max_working_span), 그 폭을 그대로 전체 나머지 구간에
    적용해서 순차적으로 직접 파티셔닝한다 - 매 조각마다 처음부터 다시
    탐색하지 않는다. 데이터 밀도가 구간마다 달라(예: 옛날 자료는 항목이
    적어 더 넓게 가져와도 되고, 최근 자료는 항목이 많아 안 될 수 있음)
    특정 조각만 유독 실패하면, 그 조각에 한해서만 재귀적으로 다시
    처음부터 시도한다(전체를 다시 탐색하지 않고 그 조각만).

    _guard: 재귀 안전장치(무한 루프 방지용 상한, 정상 흐름에서는 도달할
    일이 거의 없다 - 매 재귀 호출마다 다루는 구간이 항상 더 좁아지므로
    자연스럽게 종료되지만, 예상 못 한 데이터 이상으로 폭이 안 줄어드는
    경우를 대비한 방어선이다).

    objl_fixed/current_dim: [2026-08-18 신규] wide 표 온디맨드 정밀 조회
    (fetch_scoped_slice)가 넘긴다 - 기본값(None/0)이면 예전처럼 모든 축을
    "all"로 받고 차원 수를 0부터 스스로 재발견한다(narrow 배치 적재 경로는
    이 인자를 안 넘기므로 기존 동작 그대로 안 바뀜). objl_fixed를 넘기면
    그만큼 응답 셀 수 자체가 줄어들어(_estimate_table_cell_count가 추정한
    것과 같은 이유) 애초에 이 함수의 청킹 분기(err31)를 안 탈 가능성이
    높다 - 그래도 혹시 남는 축이 여전히 넓어 err31이 나면 기존 이분탐색
    로직이 그대로 커버한다.
    """
    if _guard > 60:
        logger.warning(f"  └─ [청크 조회 안전장치 발동] {strt}~{end} - 재귀 상한 도달, 포기")
        return []

    raw = kosis_client.fetch_actual_statistics_bounded_retry(
        org_id, tbl_id, strt, end, itm_id=itm_id, prd_se=prd_se,
        objl_fixed=objl_fixed, current_dim=current_dim,
    )
    if raw:
        return raw

    strt_ord = _period_to_ordinal(strt, prd_se)
    end_ord = _period_to_ordinal(end, prd_se)
    if strt_ord is None or end_ord is None or end_ord < strt_ord:
        return []
    total_span = end_ord - strt_ord
    if total_span < 1:
        return []  # 폭 0(단일 시점)인데도 실패 - 이 시점엔 정말 데이터가 없다.

    safe_span, first_raw = _probe_max_working_span(
        kosis_client, org_id, tbl_id, strt_ord, total_span, prd_se, itm_id,
        objl_fixed=objl_fixed, current_dim=current_dim,
    )
    if safe_span is None:
        # 이 anchor 시점 자체가 실패(폭 0도 안 됨) - 한 시점 건너뛰고
        # 나머지 구간에서 처음부터 다시 시도한다.
        next_ord = strt_ord + 1
        if next_ord > end_ord:
            return []
        logger.info(f"  └─ [빈 시점 건너뜀] {strt} - 데이터 없음, 다음 시점부터 재시도")
        return _fetch_with_chunking(
            kosis_client, org_id, tbl_id, _ordinal_to_period(next_ord, prd_se), end, prd_se, itm_id, _guard + 1,
            objl_fixed=objl_fixed, current_dim=current_dim,
        )

    logger.info(
        f"  └─ [청크 폭 확정] {strt}~{end} - 폭 {safe_span}로 나눠서 순차 조회"
        f" (원래 폭 {total_span})"
    )
    results = list(first_raw)
    cursor_ord = strt_ord + safe_span + 1
    while cursor_ord <= end_ord:
        chunk_end_ord = min(cursor_ord + safe_span, end_ord)
        chunk_strt = _ordinal_to_period(cursor_ord, prd_se)
        chunk_end = _ordinal_to_period(chunk_end_ord, prd_se)
        chunk_raw = kosis_client.fetch_actual_statistics_bounded_retry(
            org_id, tbl_id, chunk_strt, chunk_end, itm_id=itm_id, prd_se=prd_se,
            objl_fixed=objl_fixed, current_dim=current_dim,
        )
        if chunk_raw:
            results.extend(chunk_raw)
        else:
            # 이 조각만 유독 실패 - 전체를 다시 탐색하지 않고 이 조각만
            # 재귀적으로(처음부터) 다시 쪼갠다.
            results.extend(_fetch_with_chunking(
                kosis_client, org_id, tbl_id, chunk_strt, chunk_end, prd_se, itm_id, _guard + 1,
                objl_fixed=objl_fixed, current_dim=current_dim,
            ))
        cursor_ord = chunk_end_ord + 1
    return results


def is_table_ingested(conn: sqlite3.Connection, org_id: str, tbl_id: str) -> bool:
    """tables_registry에 이미 이 표가 적재돼 있는지 확인한다 - ingest_table이
    이걸 기준으로 이미 있는 표는 API 호출 자체를 건너뛴다."""
    row = conn.execute(
        "SELECT 1 FROM tables_registry WHERE org_id=? AND tbl_id=? LIMIT 1", (org_id, tbl_id)
    ).fetchone()
    return row is not None


def fetch_scoped_slice(
    kosis_client: Any,
    conn: sqlite3.Connection,
    org_id: str, tbl_id: str, prd_se: str, itm_id: str,
    needed_strt: str, needed_end: str,
    objl_fixed: Optional[Dict[int, str]] = None,
    compute_records: bool = True,
) -> Dict[str, Any]:
    """[2026-08-18 신규 - 적재 범위 정책 3번 레버] wide 표 전용 온디맨드
    정밀 조회. narrow 표라면 이미 배치로 다 들어있어(fact_coverage의
    itm_id='all'/axis_key='all' 블랭킷 행) is_period_covered가 바로
    True를 반환하므로 이 함수를 쓸 필요 자체가 없다 - wide 표에서 claim이
    실제로 필요로 하는 (항목, 축조합, 기간)이 아직 facts에 없을 때만 부른다.

    itm_id는 검색 엔진이 이미 확정한 구체적 항목 코드를 넘겨야 한다
    ('all'을 넘기면 wide 표에서 그대로 셀 수 폭발 - narrow/wide 분기의
    의미가 없어짐, kosis_fetch.py의 obj_axis/obj_code 해석 결과를 그대로
    재사용하면 됨). objl_fixed도 마찬가지로 이미 해석된 {축번호: 코드}만
    넘긴다.

    current_dim은 이미 적재된 dimensions에서 직접 구한다(getMeta는 wide
    표도 항상 미리 적재되어 있으므로 - ingest_table 참고) - kosis_fetch.py의
    라이브 경로처럼 err20/21로 차원 수를 처음부터 재발견할 필요가 없다
    (불필요한 API 호출 절감).

    compute_records: [2026-08-18 신규 - 사용자 지적] "역대 최고/최저" claim은
    기사에 연도가 적혀 있어도 그 연도 자체가 맞다는 보장이 없어서(사용자:
    "믿지를 못 하겠어") 단순 연도 검색이 아니라 실제 KOSIS 데이터로 최고/
    최저를 대조해야 한다 - wide 표라고 이 대응 자체를 포기하면 안 된다.

    다만 ingest_table의 배치 적재 시점처럼 "표 전체 항목"의 역대 기록을
    한꺼번에 계산하지는 않는다(그건 다시 wide 표를 통째로 훑는 것과 같은
    비용 - narrow/wide를 나눈 이유 자체를 무너뜨림). 대신 이 함수는 이미
    claim이 실제로 필요로 해서 (항목, 축조합)으로 좁혀진 상태다 - 좁혀진
    스코프 자체는 narrow 표만큼 저렴하므로(wide 표가 비쌌던 이유는 "모든
    항목/축을 한꺼번에"였지 "긴 기간"이 아니었다는 게 이미 실측으로 확인된
    사실), True(기본값)면 needed_strt~needed_end 대신 이 (항목,축)의 전체
    수록기간을 한 번에 당겨와 facts+records를 같이 채운다 - 이후 같은
    (항목,축)에 대한 다른 기간 요청도 캐시로 바로 응답되고, "역대" claim도
    바로 답할 수 있다.

    [중요한 안전장치] itm_id='all'이면(구체적 항목으로 안 좁혀진 요청 -
    보통 narrow 표의 기간 윈도우 밖 백필에서만 나옴) 이 전체 이력 확장을
    절대 하지 않는다 - 좁혀지지 않은 'all' 요청에 전체 이력 확장을 적용하면
    wide 표에서 정확히 우리가 피하려던 "모든 항목×전체 기간" 비용이
    그대로 재현된다. 이 경우 compute_records를 True로 줘도 무시하고 원래
    needed_strt~needed_end만 조회한다(records 없음)."""
    axis_key = _normalize_axis_key(objl_fixed)
    if is_period_covered(conn, org_id, tbl_id, prd_se, itm_id, axis_key, needed_strt, needed_end):
        return {"source": "cache", "fact_rows": 0, "record_rows": 0}

    row = conn.execute(
        "SELECT MAX(axis_position) FROM dimensions WHERE org_id=? AND tbl_id=? AND obj_id != 'ITEM'",
        (org_id, tbl_id),
    ).fetchone()
    current_dim = row[0] or 0
    if objl_fixed:
        current_dim = max(current_dim, max(objl_fixed.keys()))

    scoped = itm_id != "all"  # 위 안전장치 - 'all'이면 전체 이력 확장 금지
    fetch_strt, fetch_end = needed_strt, needed_end
    will_compute_records = compute_records and scoped
    if will_compute_records:
        period_meta = kosis_client.get_period_meta(org_id, tbl_id)
        matching = [p for p in period_meta if _canonical_prd_se(p.get("PRD_SE")) == prd_se]
        full_strt = _normalize_kosis_period_bound(matching[0].get("STRT_PRD_DE"), prd_se) if matching else None
        full_end = _normalize_kosis_period_bound(matching[0].get("END_PRD_DE"), prd_se) if matching else None
        if full_strt and full_end:
            fetch_strt, fetch_end = full_strt, full_end
        else:
            will_compute_records = False  # 수록기간을 못 구하면 records 계산 포기(추측 안 함)

    logger.info(
        f"[wide 표 온디맨드 조회] {org_id}_{tbl_id} itm={itm_id} axis={axis_key}"
        f" 기간={fetch_strt}~{fetch_end}" + (" (역대 최고/최저 계산 포함 - 전체 이력 확장)" if will_compute_records else "")
    )
    raw_data = _fetch_with_chunking(
        kosis_client, org_id, tbl_id, fetch_strt, fetch_end, prd_se,
        itm_id=itm_id, objl_fixed=objl_fixed, current_dim=current_dim,
    )
    n = ingest_facts(conn, org_id, tbl_id, raw_data or [])
    rec_n = ingest_records(conn, org_id, tbl_id, raw_data or []) if will_compute_records else 0
    record_coverage(conn, org_id, tbl_id, prd_se, itm_id, axis_key, fetch_strt, fetch_end)
    conn.commit()
    logger.info(f"  └─ [온디맨드 조회 완료] {n}건 적재" + (f", 역대 최고/최저 {rec_n}개 계열" if will_compute_records else ""))
    return {"source": "live_fetch", "fact_rows": n, "record_rows": rec_n}


def ingest_table(
    kosis_client: Any,
    conn: sqlite3.Connection,
    org_id: str,
    tbl_id: str,
    search_cand: Optional[Dict[str, Any]] = None,
    force: bool = False,
    years_back: Optional[int] = None,
    compute_records: bool = True,
    respect_wide_policy: bool = True,
) -> Dict[str, Any]:
    """표 하나를 getMeta+getList 두 API 호출로 전부 정형 DB에 적재한다.

    respect_wide_policy: [2026-08-18 신규 - 적재 범위 정책 3번 레버,
    사용자와 합의한 방향, 이후 2차례 수정 거침] 기본 True - getMeta는
    넓든 좁든 항상 먼저 적재한다(구조 파악/검색에 필요). 표 폭(추정 셀
    수, classify_table_width)이 임계값을 넘는 "wide" 표는 getList(실값)를
    facts에는 최근 구간만 저장한다(years_back 미지정이면 기본
    _WIDE_TABLE_DEFAULT_YEARS_BACK년으로 강제 캡) - 더 오래된 구간은
    claim이 실제로 필요로 할 때 fetch_scoped_slice가 objl_fixed로 정밀
    온디맨드 조회해서 채운다(fact_coverage로 이미 채운 부분을 추적).
    narrow 표는 지금처럼 배치로 (years_back 지정 시 그 윈도우만, 아니면
    전체) 적재한다.

    **records(역대 최고/최저)는 narrow 표만 배치 시점에 전체 기간
    (strt_norm~end_norm)을 조회해서 계산한다.** wide 표는 배치 시점엔
    records를 아예 안 만든다 - [2026-08-18 실측으로 확정, 최종 결론]
    한때 wide 표도 배치 시점에 전체 이력을 훑도록 했었는데("역대" claim이
    캡 구간 밖일 수 있다는 지적은 맞으므로), 실제로 돌려보니 wide 표+
    긴 기간+성긴 과거 데이터(조사 시작 전 구간)가 겹치면 이분탐색이 "이
    시점엔 정말 데이터가 없다"를 확인하는 데만 시점마다 API 호출을 8번
    가까이 써야 하는 재앙적 비용이 실측됐다(예전 세션의 "1,022 API 호출,
    10분" 문제 재현). 그래서 "역대 claim에 진짜 전체 이력이 필요하다"는
    요구는 유지하되, 그 이력을 얻는 경로를 `fetch_scoped_slice`(claim이
    실제로 필요로 하는 항목 하나만 좁혀서 전체 이력을 훑음 - narrow만큼
    저렴)로 완전히 옮겼다 - "적재 안 된 데이터를 찾는" 바로 그 로직에
    편승하는 것. 배치 시점 "캡 구간 기준 공짜 records"(중간에 한때 있었음)
    도 이 온디맨드 경로 하나로 통합하며 제거했다 - 별도 부분 기록을 두는
    것보다 단일 경로가 더 단순하고, 캡 구간 기준 값은 "역대"의 답이 될
    수 없어(정확히 사용자가 처음 지적한 문제) 애초에 가치가 낮았다.

    facts에 실제로 저장되는 범위는 fact_coverage에 itm_id='all'/
    axis_key='all' 행으로 정직하게 남는다(narrow는 보통 전체 구간, wide는
    캡 구간) - "이 구간 이미 있냐" 조회가 narrow/wide 표에서도 똑같은
    fact_coverage 경로로 일관되게 동작한다. False로 주면 이 판정 자체를
    끄고 예전처럼 모든 표를 무조건 배치 완전 적재한다(facts 저장 범위도
    years_back 제한 없이 전체가 됨).

    search_cand: search_metadata()가 돌려준 원본 후보 dict(있으면 STAT_ID/
    STAT_NM/VW_CD/수록기간을 여기서 얻는다 - get_itm_meta_list/get_period_meta
    자체에는 이 필드들이 없다). 표 ID를 이미 알고 있어서 검색을 안 거치고
    바로 적재하는 경우엔 None으로 둬도 되지만, 그러면 STAT_NM/VW_CD가
    비어서(원본 필드 자체가 없음) 검색 엔진이 국제기구 여부를 판별할
    재료가 없어지니 가능하면 넘겨주는 게 좋다.

    force: [2026-08-16 신규] 기본은 False - tables_registry에 이미 이
    (org_id, tbl_id)가 등록돼 있으면 API 호출 없이 곧바로 건너뛴다.
    "직접 DB에 적재한다"는 아키텍처를 택한 이상, 매번 라이브로 다시
    검색+해석하던 예전 방식과 달리 한 번 적재한 표를 다시 적재할 이유가
    없다(사용자 지적) - 값 자체를 새로고침하고 싶을 때만 force=True로
    명시적으로 재적재한다. 증분 갱신(예: 최신 수록기간만 다시 받기)은
    아직 미구현이라, force=True는 항상 전체 재적재다.

    years_back: [2026-08-17 신규 - Research Overview 2 적재 범위 정책
    1번 레버] None(기본)이면 예전 그대로 표가 지원하는 전체 수록기간을
    다 당겨온다. 정수를 주면 각 주기(Y/Q/M/H)마다 "가장 최근 시점에서
    최근 N년"으로만 시작 시점을 당겨서 그만큼만 적재한다(끝 시점=최신
    데이터는 항상 그대로 유지 - 절대 안 잘림). F(다년)/IR(부정기)처럼
    1년당 시점 수가 불명확한 주기는 클리핑하지 않는다(_clip_period_window
    참고). tables_registry.strt_prd_de/end_prd_de는 그대로 KOSIS가
    광고하는 "원본 전체 수록기간"을 미러링하고(원본 필드 자체는 안 건드림
    - 파일 상단 원칙), 실제로 facts에 적재된 범위는 이보다 좁을 수 있다는
    뜻 - 이 함수의 로그(ingested_periods)로만 실제 적재 범위를 확인할 수
    있다.

    compute_records: [2026-08-17 신규 - "역대 최고/최저" claim 대응, 사용자
    제안] 기본 True - years_back으로 facts에서 잘려나가는 과거 데이터라도,
    그 계열의 전체 기간 최댓값/최솟값(및 시점)만은 records 테이블에 남긴다
    (ingest_records 참고). 이러려면 이 함수가 매번 전체 기간을 KOSIS에서
    실제로 조회해야 한다 - years_back을 줘도 API 호출량은 안 줄고 저장
    용량만 준다는 뜻(ingest_records docstring의 비용 설명 참고). API 호출
    자체를 줄이고 싶고 그 표에 "역대" claim 지원이 필요 없다면 False로
    끄면 원래(windowed 구간만 조회)대로 동작한다.
    """
    if not force and is_table_ingested(conn, org_id, tbl_id):
        logger.info(f"[DB 적재 건너뜀 - 이미 있음] {org_id}_{tbl_id} (force=True로 강제 재적재 가능)")
        return {"success": True, "skipped": True, "org_id": org_id, "tbl_id": tbl_id}

    logger.info(f"[DB 적재 시작] {org_id}_{tbl_id}" + (f" (최근 {years_back}년만)" if years_back else ""))
    raw_meta = kosis_client.get_itm_meta_list(org_id, tbl_id)
    if not raw_meta:
        logger.warning(f"  └─ [DB 적재 실패] {org_id}_{tbl_id}: 메타 조회 실패/빈 응답")
        return {"success": False, "message": "메타 조회 실패", "org_id": org_id, "tbl_id": tbl_id}
    dim_count = ingest_dimensions(conn, org_id, tbl_id, raw_meta)

    period_meta = kosis_client.get_period_meta(org_id, tbl_id)
    prd_se_raw_list = sorted({p.get("PRD_SE") for p in period_meta if p.get("PRD_SE")}) or [None]

    total_facts = 0
    total_records = 0
    ingested_periods = []
    for prd_se_raw in prd_se_raw_list:
        prd_se = _canonical_prd_se(prd_se_raw)
        if prd_se_raw and prd_se != prd_se_raw:
            logger.info(f"  └─ [주기 코드 변환] '{prd_se_raw}' -> '{prd_se}'")
        matching = [p for p in period_meta if p.get("PRD_SE") == prd_se_raw]
        strt = matching[0].get("STRT_PRD_DE") if matching else None
        end = matching[0].get("END_PRD_DE") if matching else None
        if not strt or not end:
            logger.warning(f"  └─ [{prd_se} 건너뜀] 수록기간 정보 없음")
            continue
        strt_norm = _normalize_kosis_period_bound(strt, prd_se)
        end_norm = _normalize_kosis_period_bound(end, prd_se)
        if (strt_norm, end_norm) != (strt, end):
            logger.info(f"  └─ [{prd_se} 수록시점 정규화] '{strt}~{end}' -> '{strt_norm}~{end_norm}'")

        is_wide = False
        effective_years_back = years_back
        if respect_wide_policy:
            width_info = classify_table_width(raw_meta, strt_norm, end_norm, prd_se)
            is_wide = width_info["width"] == "wide"
            if is_wide and years_back is None:
                effective_years_back = _WIDE_TABLE_DEFAULT_YEARS_BACK
                logger.info(
                    f"  └─ [{prd_se} wide 표] 추정 셀 수 {width_info['estimated_cells']:,}"
                    f" > 임계값 {width_info['threshold']:,} - years_back 미지정이라도"
                    f" 기본 {_WIDE_TABLE_DEFAULT_YEARS_BACK}년으로 강제 제한(전체 이력을"
                    f" 배치로 다 당기지 않되, 최근 구간은 반드시 실제 getList로 확보 -"
                    f" getMeta만으로는 실제 값 형태/존재 여부를 알 수 없으므로 표를"
                    f" 통째로 건너뛰지 않는다). 더 오래된 구간은 fetch_scoped_slice가"
                    f" claim이 실제로 필요로 할 때 온디맨드로 채운다."
                )

        clipped_strt = _clip_period_window(strt_norm, end_norm, prd_se, effective_years_back)

        if compute_records and not is_wide:
            # [2026-08-18 - 5차 수정, 실측으로 확정] narrow 표는 배치
            # 시점에 전체 기간(strt_norm~end_norm)을 훑어 records를 계산
            # 한다 - 실측(seed_ingest_cpi_breakdown.py 실행)으로 이게
            # narrow 표에서는 실제로 저렴하다는 게 확인됐다(DT_1J22001의
            # Y 주기가 wide 판정이었는데도 청크 폭 2로 77초 만에 완료).
            #
            # wide 표는 이 분기를 안 탄다 - 아래 else 참고. [경위] 한때
            # wide도 여기서 전체 이력을 훑도록 바꿨었는데(사용자 지적:
            # "역대" claim은 캡 구간 안에 있다는 보장이 없다 - 맞는
            # 지적), 실제로 돌려보니 wide 표의 Q(분기) 주기에서 재앙적
            # 비용이 실측됐다 - 넓은 축 + 긴 기간 + 성긴 과거 데이터
            # (조사 시작 전 구간)가 겹치면, 이분탐색이 "이 시점엔 정말
            # 데이터가 없다"를 확인하는 데만 시점마다 API 호출을 8번
            # 가까이 써야 했고, 3분 넘게 1970년대도 못 벗어났다(예전
            # 세션의 "1,022 API 호출, 10분" 문제 그대로 재현). 그래서
            # "역대 claim에 진짜 전체 이력이 필요하다"는 지적은 유지하되,
            # 그 이력을 얻는 방법을 "표 전체를 itm=all로 훑기"(비쌈)가
            # 아니라 "claim이 실제로 필요로 하는 항목 하나만 좁혀서 전체
            # 이력을 훑기"(fetch_scoped_slice, narrow만큼 저렴)로
            # 옮겼다 - 아래 else의 주석 참고.
            raw_data_full = _fetch_with_chunking(
                kosis_client, org_id, tbl_id, strt_norm, end_norm, prd_se, itm_id="all",
            )
            rec_n = ingest_records(conn, org_id, tbl_id, raw_data_full or [])
            total_records += rec_n
            if clipped_strt != strt_norm:
                logger.info(
                    f"  └─ [{prd_se} 기간 윈도우 제한] facts 저장은 '{clipped_strt}~{end_norm}'만"
                    f" (전체 {strt_norm}~{end_norm}는 이미 조회했으므로 재호출 없이 필터링,"
                    f" records는 전체 기간 기준 {rec_n}개 계열 갱신)"
                )
                raw_data = _filter_rows_after(raw_data_full, clipped_strt)
            else:
                raw_data = raw_data_full
        else:
            # wide 표(compute_records 여부 무관) 또는 compute_records=False
            # - 클리핑된 구간만 조회하고, records는 여기서 계산하지 않는다.
            #
            # [2026-08-18 - 사용자 확정] wide 표의 records는 배치 시점엔
            # 아예 안 만든다(캡 구간 기준 "공짜 records"도 포함 - 한때
            # 있었지만 온디맨드 경로 하나로 통합하기로 하면서 제거) - 대신
            # claim이 실제로 특정 항목/축을 필요로 해서 fetch_scoped_slice가
            # 온디맨드로 그 표를 건드릴 때(compute_records=True가 기본값),
            # "이미 적재 안 된 데이터를 찾는" 바로 그 로직에 편승해서
            # 그 좁은 스코프의 전체 이력을 함께 가져와 records를 계산한다
            # - 좁혀진 스코프라 narrow 표만큼 저렴하고, 배치 적재
            # 파이프라인에 별도 분기/비용을 안 만든다.
            if compute_records and is_wide:
                logger.info(
                    f"  └─ [{prd_se} wide 표] records는 배치 시점엔 계산 안 함 - claim이"
                    f" 항목을 특정하면 fetch_scoped_slice가 그때 전체 이력으로 채움"
                )
            if clipped_strt != strt_norm:
                logger.info(
                    f"  └─ [{prd_se} 기간 윈도우 제한] '{strt_norm}~{end_norm}' -> "
                    f"'{clipped_strt}~{end_norm}' (최근 {effective_years_back}년만 배치 조회)"
                )
            raw_data = _fetch_with_chunking(
                kosis_client, org_id, tbl_id, clipped_strt, end_norm, prd_se, itm_id="all",
            )

        n = ingest_facts(conn, org_id, tbl_id, raw_data or [])
        total_facts += n
        ingested_periods.append(f"{prd_se}:{clipped_strt}~{end_norm}({n}건)")
        logger.info(f"  └─ [{prd_se} 적재] {n}건 (기간 {clipped_strt}~{end_norm})")
        # [2026-08-18 신규] narrow 배치 적재가 실제로 끝난 구간을
        # fact_coverage에 블랭킷 행(itm_id='all'/axis_key='all')으로
        # 남긴다 - is_period_covered/fetch_scoped_slice가 narrow/wide
        # 표를 구분 없이 같은 경로로 "이미 있냐"를 물어볼 수 있게 하기
        # 위함(narrow 표는 언제나 바로 True가 나옴).
        record_coverage(conn, org_id, tbl_id, prd_se, "all", "all", clipped_strt, end_norm)

    item_rows, _ = _split_meta_rows(raw_meta)
    tbl_nm = (
        (search_cand or {}).get("TBL_NM")
        or (item_rows[0].get("TBL_NM") if item_rows else None)
    )
    register_table(
        conn, org_id, tbl_id,
        tbl_nm=tbl_nm,
        stat_id=(search_cand or {}).get("STAT_ID"),
        stat_nm=(search_cand or {}).get("STAT_NM"),
        vw_cd=(search_cand or {}).get("VW_CD"),
        full_path_id=(search_cand or {}).get("FULL_PATH_ID"),
        strt_prd_de=(search_cand or {}).get("STRT_PRD_DE"),
        end_prd_de=(search_cand or {}).get("END_PRD_DE"),
    )
    conn.commit()
    logger.info(
        f"  └─ [DB 적재 완료] {org_id}_{tbl_id}: 차원 {dim_count}건,"
        f" 값 {total_facts}건, 역대 최고/최저 요약 {total_records}개 계열"
        f" ({', '.join(ingested_periods) or '없음'})"
    )
    return {
        "success": True,
        "org_id": org_id,
        "tbl_id": tbl_id,
        "dimension_rows": dim_count,
        "fact_rows": total_facts,
        "record_rows": total_records,
    }


def ingest_tables(
    kosis_client: Any,
    db_path: str,
    org_tbl_pairs: List[Any],
    force: bool = False,
    years_back: Optional[int] = None,
    compute_records: bool = True,
) -> List[Dict[str, Any]]:
    """(org_id, tbl_id) 튜플 목록(또는 search_metadata 후보 dict 목록)을
    받아 순서대로 전부 적재한다 - 배치 적재 진입점. force=True를 넘기면
    이미 적재된 표도 전부 강제 재적재한다(기본은 이미 있는 표는 건너뜀).
    years_back/compute_records은 ingest_table에 그대로 전달한다(적재 범위
    정책 1번 레버 - years_back=None이면 예전처럼 전체 기간, compute_records=
    True(기본)면 "역대" claim 대응용 요약도 함께 계산)."""
    conn = get_connection(db_path)
    results = []
    try:
        for item in org_tbl_pairs:
            if isinstance(item, dict):
                org_id, tbl_id, cand = item.get("ORG_ID"), item.get("TBL_ID"), item
            else:
                org_id, tbl_id, cand = item[0], item[1], None
            if not org_id or not tbl_id:
                continue
            results.append(
                ingest_table(
                    kosis_client, conn, org_id, tbl_id, cand,
                    force=force, years_back=years_back, compute_records=compute_records,
                )
            )
    finally:
        conn.close()
    return results


def ensure_tables_for_claim(
    kosis_client: Any,
    conn: sqlite3.Connection,
    raw_sentence: str,
    keywords: Optional[List[str]] = None,
    result_count: int = 20,
    years_back: Optional[int] = None,
    compute_records: bool = True,
) -> Dict[str, Any]:
    """[2026-08-17 신규 - 적재 범위 정책 2번 레버: 수요 기반 증분 확장]
    Research Overview 2에서 확정한 cache-miss trigger. 사용자가 지정한
    순서 그대로: ① 내부 DB를 먼저 검색 → ② 없으면 그때만 라이브로 KOSIS에
    검색 → ③ 새로 나온 표 중 아직 안 적재된 것만 적재.

    ① 내부 DB 검색은 kosis_local_search.search_local을 그대로 재사용한다
    (라이브 호출 없이 dimensions_fts/tables_registry만 훑는 이미 있는
    로직 - 여기서 다시 구현하지 않는다). 후보가 하나라도 나오면 라이브
    호출 자체를 안 한다 - "이미 적재된 표로 충분히 답이 되는 claim"에
    불필요한 API 비용을 안 쓰기 위해서다.

    ② 내부 DB에 아무것도 없을 때만 client.search_metadata(searchNm=keyword)
    로 라이브 검색한다. keywords가 있으면 공백으로 합쳐서 검색어로 쓰고,
    없으면 raw_sentence를 그대로 쓴다.

    ③ 라이브 검색 결과 중 tables_registry에 아직 없는 (org_id, tbl_id)만
    ingest_table로 적재한다(years_back 그대로 전달 - 1번 레버와 일관되게
    적용).

    [중요 - 이 함수가 못 푸는 것] search_metadata는 KOSIS 자체 검색엔진을
    쓰는데, 이건 연산자 기반 정확 문자열 매칭이지 의미 검색이 아니다
    (Research Overview 1 Decision 004). 그러니까 이 함수는 "찾을 수
    있는데 아직 안 당겨온 표"는 채워주지만, "제목 검색으로 애초에 못
    찾는 표"(예: "음식 서비스 물가" claim이 "지출목적별 소비자물가지수"
    표를 못 찾는 경우 - README.md 9.3)는 여전히 못 채운다. 그 문제는
    Research Overview 2에서 설계한 VDB discovery의 몫으로 남겨둔다
    (아직 미구현).

    반환: {"source": "internal_db"|"live_search", "candidates": [...],
    "newly_ingested": [ingest_table 결과 dict, ...], "live_search_skipped": bool}
    """
    import kosis_local_search as kls

    local_candidates = kls.search_local(conn, raw_sentence, keywords=keywords, top_n=5)
    if local_candidates:
        return {
            "source": "internal_db",
            "candidates": local_candidates,
            "newly_ingested": [],
            "live_search_skipped": True,
        }

    keyword = " ".join(keywords) if keywords else raw_sentence
    logger.info(f"[cache-miss] 내부 DB에 후보 없음 - 라이브 검색: '{keyword}'")
    live_results = kosis_client.search_metadata(keyword, result_count=result_count) or []

    newly_ingested = []
    for cand in live_results:
        org_id, tbl_id = cand.get("ORG_ID"), cand.get("TBL_ID")
        if not org_id or not tbl_id or is_table_ingested(conn, org_id, tbl_id):
            continue
        result = ingest_table(
            kosis_client, conn, org_id, tbl_id, cand,
            years_back=years_back, compute_records=compute_records,
        )
        if result.get("success"):
            newly_ingested.append(result)

    refreshed_candidates = (
        kls.search_local(conn, raw_sentence, keywords=keywords, top_n=5) if newly_ingested else local_candidates
    )
    return {
        "source": "live_search",
        "candidates": refreshed_candidates,
        "newly_ingested": newly_ingested,
        "live_search_skipped": False,
        "live_search_raw_count": len(live_results),
    }


# [2026-08-16 신규] 시딩 대상 표 목록 확정 - "이번 세션 90건 테스트에서 이미
# 마주친 표들부터 시작" 결정에 따라, adapter.py 실행 결과(run0N_result.json)에서
# "table+item+axis까지 전부 확정되고 실제 판정(VERIFIED/MISMATCH/...)에 쓰인"
# (evidence.retrieval_status == "RESOLVED") 표만 뽑아 시드로 쓴다.
#
# 왜 이 소스인가: kosis_factcheck.log의 실제 API 호출 기록(orgId+tblId)은
# 이번 세션에서만 distinct 1,875개, freq>=5도 1,487개로 - axis 확장 재시도
# (dimension 0~8 반복) 때문에 "많이 조회됨"이 "맞는 표"의 신호가 아니었다.
# 반면 RESOLVED 필터는 이미 LLM+client-side fallback을 거쳐 실제로 값까지
# 뽑아낸(맞았든 틀렸든) 표만 남기므로 훨씬 깨끗하다. run04_result.json(90건,
# 가장 최근 전체 파이프라인 실행) 기준 10개.
_KNOWN_STAT_NM_OVERRIDES = {
    # 이번 세션에 실측 확인(2026-08-16, kosis_search MCP 도구로 직접 조회).
    # run 결과 JSON의 evidence 딕셔너리에는 STAT_NM/VW_CD가 없어서
    # (table_org_id/tbl_id/table_nm만 있음) 알려진 오탐 2건만 수동으로
    # 채워 넣는다 - 이렇게 해야 STAT_NM이라는 원본 필드 자체가 비어있지
    # 않게 적재되고, 검색 엔진(kosis_local_search.is_international_survey)이
    # 그 필드를 보고 "국내 CPI인 줄 알고 잘못 골랐던 국제기구 표"를 검색
    # 시점에 정확히 걸러낼 수 있다(README.md 9.3의 "표 이름만으로는
    # 구분 불가" 문제의 실제 해법).
    ("101", "DT_2IFS002"): "IMF",       # 소비자물가지수 - 국내 CPI 오탐 (A93bfa851-C022/024/027/029)
    ("101", "DT_2OEEO029"): "OECD",     # GDP 대비 일반정부 총금융부채 비율 - 기초재정수지 오탐 (A272c31f6-C013)
}


def extract_seed_candidates_from_run_result(path: str) -> List[Dict[str, Any]]:
    """adapter.py 실행 결과 JSON(claim별 verdict/evidence 리스트, 또는
    {"claims": [...]}로 감싼 형태 둘 다 지원)에서 시딩할 (org_id, tbl_id)
    후보 목록을 뽑는다.

    기준: evidence.retrieval_status == "RESOLVED"인 것만 - 표 이름만 후보로
    떠 있었지만 항목/축을 못 채운 UNVERIFIED_UNRESOLVED는 제외한다(그런 건
    애초에 org_id/tbl_id가 null인 경우가 대부분이기도 하고, 확정되지 않은
    표를 시드로 넣으면 "일단 다 넣고 보자"가 되어 버려 이번 세션에서
    확인한 문제(로그의 1,875개 노이즈)를 DB에서 그대로 반복하게 된다).

    반환값은 ingest_tables()에 바로 넘길 수 있는 search_cand 형태의 dict
    목록(ORG_ID/TBL_ID/TBL_NM/STAT_NM 키) - STAT_NM은 _KNOWN_STAT_NM_OVERRIDES에
    있는 표만 채워지고, 나머지는 None이라 STAT_NM 원본 필드 자체가 비어
    적재된다(= 검색 엔진이 나중에 국제기구 여부를 판별할 재료가 없다). 그
    표들은 실제 적재 전에 search_metadata로 다시 조회해서 STAT_NM/VW_CD를
    채우는 걸 권장한다(seed_ingest.py에서 이 보강을 수행한다).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else (data or {}).get("claims", [])

    seen: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        ev = (row or {}).get("evidence") or {}
        if ev.get("retrieval_status") != "RESOLVED":
            continue
        org_id = ev.get("table_org_id")
        tbl_id = ev.get("table_tbl_id")
        if not org_id or not tbl_id:
            continue
        key = (str(org_id), str(tbl_id))
        if key not in seen:
            seen[key] = {
                "ORG_ID": key[0],
                "TBL_ID": key[1],
                "TBL_NM": ev.get("table_nm"),
                "STAT_NM": _KNOWN_STAT_NM_OVERRIDES.get(key),
                "sample_claim_ids": [],
                "verdicts_seen": [],
            }
        seen[key]["sample_claim_ids"].append(row.get("claim_id"))
        verdict = row.get("verdict")
        if verdict and verdict not in seen[key]["verdicts_seen"]:
            seen[key]["verdicts_seen"].append(verdict)
    return list(seen.values())

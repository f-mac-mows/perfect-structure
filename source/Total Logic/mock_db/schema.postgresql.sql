-- FactQ service/result schema (PostgreSQL 15+)
-- Source of truth: verdict_output_schema.json / verdict_output_examples.jsonl
-- This stores frontend service data and the backend verdict output unchanged.
-- It is NOT the schema for the backend search warehouse (kosis_warehouse.db).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE article_input_type AS ENUM ('URL', 'TITLE', 'STORED_ARTICLE');
CREATE TYPE article_status AS ENUM ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED');
CREATE TYPE verification_stage AS ENUM (
  'REQUESTED', 'FETCHING_ARTICLE', 'EXTRACTING_CLAIMS', 'SEARCHING_KOSIS',
  'VERIFYING', 'COMPLETED', 'FAILED'
);
CREATE TYPE backend_verdict AS ENUM (
  'VERIFIED', 'MISMATCH', 'UNVERIFIED_NOT_FOUND', 'UNVERIFIED_UNRESOLVED',
  'UNVERIFIED_DERIVED_NEEDED', 'UNVERIFIED_RECORD_CLAIM', 'RAW_ONLY',
  'NOT_ELIGIBLE', 'ERROR'
);
CREATE TYPE hedge_type AS ENUM ('exact', 'approx', 'approx_range', 'at_least', 'at_most');
CREATE TYPE judgment_mode AS ENUM ('strict', 'tolerance', 'raw_only');
CREATE TYPE retrieval_status AS ENUM ('RESOLVED', 'NOT_FOUND', 'UNRESOLVED');

CREATE TABLE articles (
  article_id VARCHAR(64) PRIMARY KEY,
  input_type article_input_type NOT NULL,
  request_input TEXT NOT NULL,
  url TEXT,
  canonical_url TEXT,
  title VARCHAR(500) NOT NULL,
  publisher VARCHAR(200),
  author VARCHAR(200),
  published_at TIMESTAMPTZ,
  content TEXT NOT NULL,
  category VARCHAR(100),
  status article_status NOT NULL DEFAULT 'PENDING',
  current_stage verification_stage NOT NULL DEFAULT 'REQUESTED',
  failure_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  verified_at TIMESTAMPTZ,
  CONSTRAINT article_input_value CHECK (
    (input_type = 'URL' AND url IS NOT NULL) OR input_type IN ('TITLE', 'STORED_ARTICLE')
  )
);

CREATE UNIQUE INDEX uq_articles_canonical_url
  ON articles (canonical_url) WHERE canonical_url IS NOT NULL;
CREATE INDEX ix_articles_recent_requests ON articles (created_at DESC);
CREATE INDEX ix_articles_title_search ON articles (lower(title));

CREATE TABLE claims (
  -- Task1 IDs are strings such as A82ae9f41-C010, not UUIDs.
  claim_id VARCHAR(100) PRIMARY KEY,
  article_id VARCHAR(64) NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  claim TEXT NOT NULL,
  start_offset INTEGER,
  end_offset INTEGER,
  metric VARCHAR(300),
  metric_normalized VARCHAR(300),
  value_raw VARCHAR(200),
  value_num NUMERIC(40,10),
  unit VARCHAR(100),
  period_text VARCHAR(200),
  region VARCHAR(200),
  kosis_eligible BOOLEAN,
  exclusion_code VARCHAR(100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_claim_sequence UNIQUE (article_id, sequence_no),
  CONSTRAINT valid_claim_offsets CHECK (
    (start_offset IS NULL AND end_offset IS NULL) OR
    (start_offset >= 0 AND end_offset > start_offset)
  )
);

CREATE INDEX ix_claims_article_sequence ON claims (article_id, sequence_no);

CREATE TABLE verification_results (
  -- Exactly one backend result per Claim.
  claim_id VARCHAR(100) PRIMARY KEY REFERENCES claims(claim_id) ON DELETE CASCADE,
  verdict backend_verdict NOT NULL,
  explanation TEXT NOT NULL,
  claimed_value NUMERIC(40,10),
  actual_value NUMERIC(40,10),
  hedge_type hedge_type,
  mode judgment_mode,
  ai_used BOOLEAN,
  ai_note TEXT,
  -- result.evidence is flattened without changing field meaning.
  table_org_id VARCHAR(100),
  table_tbl_id VARCHAR(150),
  table_nm VARCHAR(500),
  retrieval_status retrieval_status,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT verdict_result_shape CHECK (
    (
      verdict IN ('NOT_ELIGIBLE', 'ERROR')
      AND claimed_value IS NULL AND actual_value IS NULL AND hedge_type IS NULL
      AND mode IS NULL AND ai_used IS NULL AND ai_note IS NULL
      AND table_org_id IS NULL AND table_tbl_id IS NULL AND table_nm IS NULL
      AND retrieval_status IS NULL
    )
    OR
    (
      verdict NOT IN ('NOT_ELIGIBLE', 'ERROR')
      AND mode IS NOT NULL AND ai_used IS NOT NULL AND retrieval_status IS NOT NULL
    )
  )
);

CREATE INDEX ix_verification_results_verdict ON verification_results (verdict);
CREATE INDEX ix_verification_results_table ON verification_results (table_org_id, table_tbl_id);

-- UI labels are derived instead of storing a second, inconsistent verdict.
CREATE VIEW ui_verification_results AS
SELECT
  vr.*,
  CASE
    WHEN vr.verdict = 'VERIFIED' THEN 'MATCH'
    WHEN vr.verdict = 'MISMATCH' THEN 'MISMATCH'
    WHEN vr.verdict::text LIKE 'UNVERIFIED_%' THEN 'UNVERIFIED'
    WHEN vr.verdict = 'NOT_ELIGIBLE' THEN 'NOT_ELIGIBLE'
    WHEN vr.verdict = 'ERROR' THEN 'ERROR'
    WHEN vr.verdict = 'RAW_ONLY' THEN 'RAW_ONLY'
  END AS verdict_group
FROM verification_results vr;

-- Board counts are derived and never duplicated in articles.
CREATE VIEW article_verification_summary AS
SELECT
  a.article_id,
  COUNT(c.claim_id) AS total_claims,
  COUNT(*) FILTER (WHERE vr.verdict = 'VERIFIED') AS matched,
  COUNT(*) FILTER (WHERE vr.verdict = 'MISMATCH') AS mismatched,
  COUNT(*) FILTER (WHERE vr.verdict::text LIKE 'UNVERIFIED_%') AS unverified,
  COUNT(*) FILTER (WHERE vr.verdict = 'NOT_ELIGIBLE') AS not_eligible,
  COUNT(*) FILTER (WHERE vr.verdict = 'ERROR') AS errors
FROM articles a
LEFT JOIN claims c ON c.article_id = a.article_id
LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
GROUP BY a.article_id;

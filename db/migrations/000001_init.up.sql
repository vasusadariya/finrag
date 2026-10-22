CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE companies (
    id      serial PRIMARY KEY,
    cik     text NOT NULL UNIQUE,
    ticker  text NOT NULL UNIQUE,
    name    text NOT NULL
);

CREATE TABLE filings (
    id            serial PRIMARY KEY,
    company_id    integer NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    accession     text NOT NULL UNIQUE,
    form          text NOT NULL,
    fiscal_year   integer NOT NULL,
    period_end    date NOT NULL,
    filed_at      date,
    url           text
);
CREATE INDEX filings_company_fy_idx ON filings (company_id, fiscal_year);

-- Embedding dimension is 768 (Gemini output dimensionality); revisit in step 1.8 if local bge (384) is chosen.
CREATE TABLE chunks (
    id            bigserial PRIMARY KEY,
    filing_id     integer NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    company_id    integer NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    fiscal_year   integer NOT NULL,
    section       text NOT NULL,
    page          integer,
    chunk_index   integer NOT NULL,
    content       text NOT NULL,
    content_hash  text NOT NULL,
    token_count   integer NOT NULL,
    embedding     vector(768),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (filing_id, chunk_index)
);
CREATE INDEX chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_filter_idx ON chunks (company_id, fiscal_year, section);
CREATE INDEX chunks_content_hash_idx ON chunks (content_hash);

CREATE TABLE xbrl_facts (
    id            bigserial PRIMARY KEY,
    company_id    integer NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filing_id     integer REFERENCES filings(id) ON DELETE SET NULL,
    concept       text NOT NULL,
    fiscal_year   integer NOT NULL,
    fiscal_period text NOT NULL,
    period_start  date,
    period_end    date NOT NULL,
    unit          text NOT NULL,
    value         numeric NOT NULL,
    accession     text NOT NULL,
    UNIQUE (company_id, concept, period_start, period_end, unit, accession)
);
CREATE INDEX xbrl_facts_lookup_idx ON xbrl_facts (company_id, concept, fiscal_year);

CREATE TABLE query_traces (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question         text NOT NULL,
    answer           text,
    refused          boolean NOT NULL DEFAULT false,
    steps            jsonb NOT NULL DEFAULT '[]',
    tokens_in        integer NOT NULL DEFAULT 0,
    tokens_out       integer NOT NULL DEFAULT 0,
    latency_ms       integer,
    shadow_cost_usd  numeric(12, 6) NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Read-only role used by the text-to-SQL executor. Only the allow-listed tables are readable.
-- The dev password is overridden in deployment (ALTER ROLE finrag_ro PASSWORD ...).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finrag_ro') THEN
        CREATE ROLE finrag_ro LOGIN PASSWORD 'finrag_ro';
    END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO finrag_ro;
GRANT SELECT ON companies, filings, xbrl_facts TO finrag_ro;

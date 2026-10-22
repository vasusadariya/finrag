REVOKE ALL ON companies, filings, xbrl_facts FROM finrag_ro;
REVOKE USAGE ON SCHEMA public FROM finrag_ro;
DROP TABLE IF EXISTS query_traces, xbrl_facts, chunks, filings, companies;
-- The finrag_ro role is cluster-wide and is intentionally left in place.

# FinRAG

Agentic RAG analyst over SEC 10-K filings: hybrid-search retrieval on filing text plus
validated read-only text-to-SQL on XBRL facts, with cited answers and refusal on weak
evidence. Measured against a vector-only baseline.

> Not investment advice.

Status: Phase 1 (data foundation) complete (tag v0.1); Phase 2 (retrieval) in progress. Build plan: `docs/build-plan.md`; design decisions: `docs/decisions.md`.

Companies: AAPL, MSFT, GOOGL, AMZN, NVDA, META, JPM, XOM; the last three 10-Ks of each, plus XBRL company facts.

## Layout
`ingest-go/` ingestion CLI (EDGAR client, parser, chunker, XBRL, loader), `sqlguard-go/` SQL validator service,
`backend/` FastAPI + agent + embeddings, `db/migrations/` schema, `eval/` datasets and reports,
`frontend/` Next.js UI, `infra/terraform/`.

## Quick start (data pipeline)
```bash
cp .env.example .env                  # set EDGAR_USER_AGENT (your name + email) and, later, GEMINI_API_KEY
make up                               # Postgres 16 + pgvector in Docker   (or: make db-local, no Docker)
(cd backend && uv sync --group embed) # local bge-base embedding model (CPU torch)
make ingest                           # download, parse, chunk, embed, load all 8 companies (resumable, ~40 min cold)
make ingest-report                    # row counts per company
```
`make ingest TICKERS=AAPL,JPM` limits the run. Re-running skips filings that are already up to date; an
interrupted run resumes where it stopped (embeddings are cached in `.cache/embeddings.sqlite`).

## Development
`make lint`, `make test`. Tests that need real filings: `scripts/fetch-sample-filings.sh` (pinned sample 10-Ks and
companyfacts under `ingest-go/testdata/`, git-ignored). Database tests use a throwaway database on the local Postgres and
are skipped without one; set `FINRAG_REQUIRE_DB=1` to make that a failure.

Inspect the parser: `cd ingest-go && go run ./cmd/finrag-ingest parse testdata/filings/AAPL.htm`
(see `docs/parsing-report.md` for all 8).

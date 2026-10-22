# FinRAG

Agentic RAG analyst over SEC 10-K filings: hybrid-search retrieval on filing text plus
validated read-only text-to-SQL on XBRL facts, with cited answers and refusal on weak
evidence. Measured against a vector-only baseline.

> Not investment advice.

Status: under construction (Phase 1, foundation). See the build plan in `docs/`.

## Layout
`ingest-go/` ingestion CLI, `sqlguard-go/` SQL validator service, `backend/` FastAPI + agent,
`db/migrations/` schema, `eval/` datasets and reports, `frontend/` Next.js UI, `infra/terraform/`.

## Dev
`make lint`, `make test`. Database: `make up` (Docker) or `make db-local` (no Docker; needs apt `postgresql-16` + `postgresql-16-pgvector`).

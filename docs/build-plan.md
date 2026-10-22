# FinRAG build plan

Four phases, one commit per step: implement, test, commit, push, next.

- Phase 1 Foundation & Data: 1.1 scaffold, 1.2 Docker+Postgres, 1.3 config/logging, 1.4 schema+migrations,
  1.5 EDGAR client (Go), 1.6 filing parser (Go), 1.7 chunker (Go), 1.8 embeddings, 1.9 chunk loader,
  1.10 XBRL facts, 1.11 ingest CLI. Gate: tag v0.1.
- Phase 2 Retrieval & Baseline (A vector, B hybrid RRF, C rerank): 2.1-2.9. Gate: tag v0.2.
- Phase 3 Agent, Tools & Safety (D): SQL guard (Go, pg_query_go), executor, text-to-SQL, calculator,
  filing search, agent loop, citations/refusal, tracing, FastAPI, eval. Gate: tag v0.3.
- Phase 4 UI, Deploy, Deliverables: Next.js, Docker, CI, Terraform, deploy, hardening, docs, demo. Tag v1.0.

Stack: Python 3.11 + FastAPI, Go (ingestion, SQL guard), Postgres 16 + pgvector + tsvector,
Gemini free tier (Groq/OpenRouter fallback), local bge reranker, Next.js 14.

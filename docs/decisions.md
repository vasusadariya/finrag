# Decisions

## Embeddings: local `bge-base-en-v1.5` (step 1.8)

The plan allowed Gemini `gemini-embedding-001` (free tier) or a local bge model, to be chosen after a rate-limit test.

**Gemini test (batchEmbedContents, 768-d, ~3000-char chunks):** 4 batches of 10 succeeded in ~0.6s each, then HTTP 429:
`embed_content_free_tier_requests, limit: 100` per minute, retry after ~50s. The quota counts every text in a batch, so
free-tier throughput is about 100 chunks/min, and the documented 30k tokens/min limit allows only ~40 chunks of this size
per minute. The corpus (8 companies x 3 filings, ~7k chunks) would take well over an hour per full pass, with a daily
cap on top that we could not verify without exhausting it. Re-embedding after any chunker change would be slow and flaky.

**Local bge benchmark (4 CPU cores, 512-token inputs):** bge-small 8.9 chunks/s (384-d), bge-base 3.3 chunks/s (768-d).
A full corpus pass is ~35 min with bge-base, once; afterwards the SQLite cache makes re-runs instant (156 AAPL chunks:
45s cold, 0.19s warm).

**Decision:** local `BAAI/bge-base-en-v1.5`, 768-d (matches `vector(768)` in the schema, no migration needed).
Gemini stays the LLM for the agent; the `EmbeddingProvider` interface lets a Gemini embedder be added later.

**Consequence:** bge has a 512-token window, so chunks are smaller than the plan's 500-800 tokens. `chunk.MaxChars` is
1500 chars (overlap 250); measured with the bge tokenizer on AAPL/MSFT/JPM/XOM the median chunk is ~270-310 tokens and
1-4% of chunks (mostly numeric tables) slightly exceed 512 tokens and lose their tail, which the overlap covers.
Query embeddings use the bge query instruction prefix (`QUERY_INSTRUCTION`); documents do not.

## Fiscal year

Taken from the filing's own `dei:DocumentFiscalYearFocus` tag (MSFT FY ending June 2026 -> 2026, NVDA FY ending
Jan 2026 -> 2026), not derived from the period-end date.

## Page numbers

Chunks carry the physical page index (CSS page-break count), not the printed page number.

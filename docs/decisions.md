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

## LLM models (step 3.4 prerequisite)

The plan named `gemini-2.5-flash` / `gemini-2.5-flash-lite`. In Oct 2026 both return 404 "no longer available to
new users", so the plan's assumption was stale. Probed with a tool-calling request on the free tier:

| model | result |
|---|---|
| gemini-3.5-flash | works, tool call, ~100 thinking tokens |
| gemini-3.5-flash-lite | works, tool call |
| gemini-3.1-flash-lite | works, tool call |
| gemini-3.7-flash | 503 "high demand" |
| gemini-3.8-flash, gemini-flash-latest | timed out after 60 s |
| gemini-2.5-flash, -flash-lite | 404 closed to new users |

**Decision:** main = `gemini-3.5-flash`, cheap/judge = `gemini-3.5-flash-lite`, both falling back to
`gemini-3.1-flash-lite`. Versions are pinned (not `*-latest`, which moves and timed out). Chains are settings
(`LLM_MAIN_MODELS`, `LLM_CHEAP_MODELS`). The client retries 429/5xx with exponential backoff, then falls through
the chain. Free-tier availability is visibly flaky (503s, 60 s hangs), so latency targets must be reported
honestly. Gemini 3 function calling requires the model's raw parts ("thought signatures") to be replayed on the next
turn; `Message.raw` carries them and a live test verifies the round trip.

## Text-to-SQL accuracy (step 3.4)

Live test (`backend/tests/test_text_to_sql_live.py`): 15 questions, each with an independent reference SQL run
directly on the database; the model's guard-validated SQL result must contain the same numbers/names.
`gemini-3.5-flash`, temperature 0, schema + glossary + 3 worked examples in the prompt.

- First run, original checker: **13/15**. Every case succeeded on the first attempt (no retries needed).
- The two failures ("list the tickers", "which fiscal years do we have for NVIDIA") were correct answers that
  added descriptive columns; the checker compared the whole cell set and penalised the extras. The checker was
  then changed (post hoc, after seeing the result) to require the expected names plus one row per expected name.
  Re-scored on the same cached answers: **15/15**.
- The pass threshold in the test is 12/15. Treat the honest figure as "13/15 as first scored, 15/15 after a
  fairer checker" on 15 easy-to-medium single-fact and ratio questions. Harder questions (multi-company
  comparisons over several concepts, fiscal-year edge cases such as MSFT/NVDA) are covered by the full eval in 3.13.

# Phase 2 report: retrieval ablation (A / B / C)

Data: 24 filings (8 companies x last three 10-Ks), 8,420 chunks, local `bge-base-en-v1.5` embeddings.
Eval: `eval/dataset/questions.jsonl` (72 questions, Claude-drafted and machine-verified against the database,
**not yet human-reviewed**); 30 of them have text evidence and are used here (20 factual, 6 multi-hop,
4 cross-year). The rest are numeric-only (answered via SQL in Phase 3) or unanswerable. Raw numbers:
`retrieval.md`, `retrieval_details.json`, `diagnostics.md`. Reproduce: `make eval-retrieval`.

## Result

hit@5 = a gold passage is among the top 5 results. With the company as a filter (as an agent would set it):

| variant | hit@1 | hit@5 | hit@10 | MRR@10 | p50 latency |
|---|---|---|---|---|---|
| A vector | 0.30 | 0.47 | 0.63 | 0.39 | 0.06 s |
| B hybrid (vector + keyword, RRF) | 0.20 | 0.47 | 0.53 | 0.30 | 0.08 s |
| C hybrid + cross-encoder rerank | 0.33 | 0.53 | 0.57 | 0.40 | 8.4 s |

Without the company filter: A 0.47, B 0.33, C 0.50 (hit@5).

**Plain reading:** none of the variants is good enough on its own. About half of the questions are answered in the
top 5. On 30 questions the differences between A, B and C at hit@5 (0.47 / 0.47 / 0.53, i.e. one or two
questions) are within noise; we cannot claim that hybrid or reranking helps from this data. Hybrid is, if anything,
worse than vector alone at hit@1 and hit@10. Reranking adds ~8 s per query on CPU for a gain we cannot
distinguish from chance. By type (variant C): factual 0.75, multi-hop 0.17, cross-year 0.00.

## Why it misses

Of the 16 misses for vector (with company filter): 10 retrieve the **wrong section** (e.g. a question about
employee headcount returns Item 7/8 financial tables, because the date and number-heavy wording resembles them),
4 hit the right section but the wrong chunk, and only 2 are the right passage in the wrong fiscal year.
The exact-year requirement in the metric is therefore not the main cause.

Diagnostic (gold section/year given to the retriever as filters; an **upper bound**, not an achievable result):

| filters | variant | hit@1 | hit@5 |
|---|---|---|---|
| company only | A / B | 0.30 / 0.20 | 0.47 / 0.47 |
| + correct section | A / B | 0.47 / 0.63 | 0.80 / 0.93 |
| + correct section and year | A / B | 0.77 / 0.87 | 1.00 / 1.00 |

Once the search space is narrowed to the right section, hybrid does beat vector (0.63 vs 0.47 at hit@1) and
nearly everything is found. The retrieval components work; what is missing is **routing**: choosing the section
and fiscal year from the question.

## Confidence score

Top reranker logit as a signal that the question is answerable: AUC 0.59 (with filter) / 0.56 (no filter), barely
better than chance. At threshold 0.5 it answers 93% of answerable questions but refuses only 33% of unanswerable
ones. In its current form it cannot gate refusals. A smoke test also showed the reranker preferring a
"Rest of Asia Pacific" sales table over the headquarters sentence for "Where is Apple's corporate headquarters
located?" (scores +2.0 vs -1.5, same with batch size 1, so not a batching artifact).

## Implications for Phase 3

1. `filing_search` must expose `ticker`, `fiscal_year` and `section` arguments, and the agent prompt needs a
   section guide (headcount -> Item 1, risks -> 1A, revenue drivers -> 7, statements/notes -> 8). That is where
   most of the measured headroom is.
2. Re-evaluate A/B/C *with agent-chosen filters*; that, not this table, is the number to report for the system.
3. Refusal cannot rely on reranker logits alone. Candidates to test on this eval set: LLM-judged sufficiency of the
   retrieved passages, vector similarity, SQL-empty checks. Keep the "refuse when unsure" behaviour conservative.
4. Try a larger reranker or none at all; with only 30 questions the current evidence does not justify 8 s/query.

## Limits of this evaluation

- n = 30 text questions; one question is 3.3 points. No confidence intervals, no held-out split.
- Questions were drafted by the same assistant that built the system; gold passages are verified to exist but
  question phrasing may favour or hurt particular retrievers. A human-reviewed set is needed before quoting numbers
  externally.
- Fitted confidence weights in `retrieval.md` are in-sample on 7+30 examples and must not be used as is.

# Retrieval ablation

30 questions with text evidence. hit@k = a gold passage is in the top k; coverage = share of a question's gold passages found in the top 10.

## With company filter

| variant | hit@1 | hit@5 | hit@10 | MRR@10 | coverage | p50 s | p95 s |
|---|---|---|---|---|---|---|---|
| A vector | 0.30 | 0.47 | 0.63 | 0.39 | 0.60 | 0.06 | 0.17 |
| B hybrid (RRF) | 0.20 | 0.47 | 0.53 | 0.30 | 0.50 | 0.08 | 0.16 |
| C hybrid + rerank | 0.33 | 0.53 | 0.57 | 0.40 | 0.57 | 8.35 | 11.22 |

hit@5 by question type, last variant:

| type | n | hit@5 |
|---|---|---|
| cross_year | 4 | 0.00 |
| factual | 20 | 0.75 |
| multi_hop | 6 | 0.17 |

## No filter

| variant | hit@1 | hit@5 | hit@10 | MRR@10 | coverage | p50 s | p95 s |
|---|---|---|---|---|---|---|---|
| A vector | 0.30 | 0.47 | 0.63 | 0.39 | 0.60 | 0.07 | 0.09 |
| B hybrid (RRF) | 0.20 | 0.33 | 0.43 | 0.27 | 0.40 | 0.18 | 0.32 |
| C hybrid + rerank | 0.33 | 0.50 | 0.63 | 0.41 | 0.63 | 7.85 | 10.61 |

hit@5 by question type, last variant:

| type | n | hit@5 |
|---|---|---|
| cross_year | 4 | 0.00 |
| factual | 20 | 0.65 |
| multi_hop | 6 | 0.33 |

## Confidence (With company filter)

AUC of the top reranker logit, answerable vs unanswerable: **0.592**. At threshold 0.5: answered 93% of answerable questions, refused 33% of unanswerable ones. Fitted weights (in-sample, small n): w_top=0.09, w_gap=0.08, bias=0.54.

## Confidence (No filter)

AUC of the top reranker logit, answerable vs unanswerable: **0.564**. At threshold 0.5: answered 80% of answerable questions, refused 33% of unanswerable ones. Fitted weights (in-sample, small n): w_top=0.02, w_gap=0.21, bias=0.56.


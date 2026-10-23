# Retrieval diagnostics

30 questions with text evidence. ORACLE rows give the retriever the gold section/year as filters: an upper bound for a routing step, not an achievable result.

| filters | variant | hit@1 | hit@5 | miss: wrong section | miss: right section, wrong chunk | miss: wrong year only |
|---|---|---|---|---|---|---|
| tickers only (what the eval uses) | A vector | 0.30 | 0.47 | 10 | 4 | 2 |
| tickers only (what the eval uses) | B hybrid | 0.20 | 0.47 | 12 | 2 | 2 |
| ORACLE sections | A vector | 0.47 | 0.80 | 0 | 5 | 1 |
| ORACLE sections | B hybrid | 0.63 | 0.93 | 0 | 1 | 1 |
| ORACLE sections + years | A vector | 0.77 | 1.00 | 0 | 0 | 0 |
| ORACLE sections + years | B hybrid | 0.87 | 1.00 | 0 | 0 | 0 |

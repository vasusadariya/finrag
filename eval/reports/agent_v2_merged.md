# Agent evaluation - final (lite models, prompt v2 on trend/cross_year)

72 questions (0 infrastructure errors).

| metric | value |
|---|---|
| answer correct (of answered answerable) | 98% |
| answer correct (of all answerable; a refusal counts as wrong) | 97% |
| numeric questions: all gold numbers incl. derived % (strict) | 97% |
| numeric questions: gold base values only (percentages ignored) | 97% |
| faithful to cited evidence (LLM judge) | 92% |
| cited passage matches a gold source | 62% |
| refused when unanswerable | 83% |
| refused when answerable (false refusal) | 2% |
| answers with a number no tool supports | 16% |
| SQL attempts / blocked by guard / DB errors | 62 / 0 / 0 |
| latency p50 / p95 (s) | 34.4 / 109.8 |
| shadow cost per query (USD, assumed list prices) | 0.0017 |
| tokens per query | 9896 |

## By question type

| type | n | correct | refused |
|---|---|---|---|
| cross_year | 10 | 90% | 0% |
| factual | 24 | 96% | 4% |
| multi_hop | 16 | 100% | 0% |
| trend | 10 | 100% | 0% |
| unanswerable | 12 | n/a | 83% |

## Wrong answers (1)

- q040 (cross_year): 'Yes, JPMorgan Chase spent more on share repurchases in 2025 than in 2024. The company spent $18.83 billion on share repurchases in 2024 and $31.59 billion in 20' 

## Answerable questions that were refused (1)

- q003 (factual): "The 10-K filings retrieved do not contain the specific text stating Alphabet's headquarters address." model_declined

## Unanswerable questions that were answered (2)

- q065 (unanswerable): 'Apple does not report unit sales numbers (the number of individual units sold) for iPhones in its annual 10-K filings. \n\nHowever, Apple reported iPhone net sale' 
- q068 (unanswerable): 'ExxonMobil does not disclose a specific price prediction or price forecast for crude oil in 2030 in its 10-K filings [c1]. Its disclosures discuss demand and su' 

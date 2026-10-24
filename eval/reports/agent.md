# Agent evaluation - final (lite models, prompt v2)

72 questions (0 infrastructure errors).

| metric | value |
|---|---|
| answer correct (of answered answerable) | 95% |
| answer correct (of all answerable; a refusal counts as wrong) | 93% |
| numeric questions: all gold numbers incl. derived % (strict) | 92% |
| numeric questions: gold base values only (percentages ignored) | 89% |
| faithful to cited evidence (LLM judge) | 98% |
| cited passage matches a gold source | 66% |
| refused when unanswerable | 92% |
| refused when answerable (false refusal) | 2% |
| answers with a number no tool supports | 2% |
| SQL attempts / blocked by guard / DB errors | 44 / 2 / 0 |
| latency p50 / p95 (s) | 13.7 / 42.6 |
| shadow cost per query (USD, assumed list prices) | 0.0013 |
| tokens per query | 8440 |

## By question type

| type | n | correct | refused |
|---|---|---|---|
| cross_year | 10 | 90% | 0% |
| factual | 24 | 96% | 4% |
| multi_hop | 16 | 94% | 0% |
| trend | 10 | 90% | 0% |
| unanswerable | 12 | n/a | 92% |

## Wrong answers (3)

- q028 (trend): "Amazon's operating income experienced a strong upward trend from 2023 to 2025, increasing by **117.0%** overall from $36.85 billion in 2023 to $79.98 billion in" 
- q039 (cross_year): "ExxonMobil's total revenues decreased from $349.59 billion in 2024 to $332.24 billion in 2025, representing a decline of 5.0% [sql1]." 
- q048 (multi_hop): "Amazon's operating margin in 2025 was **11.2%** (based on operating income of $79.98 billion and total revenue of $716.92 billion) [sql1]." 

## Answerable questions that were refused (1)

- q003 (factual): 'The provided 10-K filings for Alphabet Inc. do not explicitly state the street address or city of its headquarters in the searched sections.' model_declined

## Unanswerable questions that were answered (1)

- q065 (unanswerable): 'Apple does not disclose the unit sales numbers for iPhones in its annual Form 10-K filings. However, Apple reported iPhone net sales of $209,586 million for fis' 

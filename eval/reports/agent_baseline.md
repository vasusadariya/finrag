# Agent evaluation - baseline (lite models, prompt v1)

72 questions (0 infrastructure errors).

| metric | value |
|---|---|
| answer correct (of answered answerable) | 86% |
| answer correct (of all answerable; a refusal counts as wrong) | 85% |
| numeric questions: all gold numbers incl. derived % (strict) | 78% |
| numeric questions: gold base values only (percentages ignored) | 97% |
| faithful to cited evidence (LLM judge) | 98% |
| cited passage matches a gold source | 62% |
| refused when unanswerable | 83% |
| refused when answerable (false refusal) | 2% |
| answers with a number no tool supports | 5% |
| SQL attempts / blocked by guard / DB errors | 72 / 0 / 0 |
| latency p50 / p95 (s) | 34.2 / 102.2 |
| shadow cost per query (USD, assumed list prices) | 0.0018 |
| tokens per query | 10415 |

## By question type

| type | n | correct | refused |
|---|---|---|---|
| cross_year | 10 | 90% | 0% |
| factual | 24 | 96% | 4% |
| multi_hop | 16 | 100% | 0% |
| trend | 10 | 30% | 0% |
| unanswerable | 12 | n/a | 83% |

## Wrong answers (8)

- q026 (trend): "NVIDIA's revenue trend for fiscal years 2024 through 2026 is as follows:\n\n* **Fiscal Year 2024:** $60,922,000,000 [sql2]\n* **Fiscal Year 2025:** $130,497,000,00" 
- q028 (trend): "Amazon's operating income increased each year from 2023 to 2025:\n\n* **2023:** $36,852,000,000 [sql3]\n* **2024:** $68,593,000,000 [sql3]\n* **2025:** $79,975,000," 
- q029 (trend): "Alphabet's research and development (R&D) expense evolved as follows:\n* **2023:** $45,427,000,000 [sql2]\n* **2024:** $49,326,000,000 [sql2]\n* **2025:** $61,087," 
- q030 (trend): 'Meta’s cash flow from operating activities showed a strong upward trend from 2023 to 2025, increasing each year [sql2]:\n- **2023:** $71,113,000,000 [sql2]\n- **2' 
- q031 (trend): "ExxonMobil's net income decreased from $36,010,000,000 in 2023 to $33,680,000,000 in 2024, and further decreased to $28,844,000,000 in 2025 [sql2]." 
- q032 (trend): "JPMorgan Chase's net income developed as follows:\n- **2023:** $49,552,000,000 [sql2]\n- **2024:** $58,471,000,000 [sql2] (an increase of 18.0% from 2023)\n- **202" 
- q034 (trend): "Microsoft's research and development (R&D) expenses for fiscal years 2024 to 2026 are as follows:\n\n* **FY 2024:** $29,510,000,000 [sql2]\n* **FY 2025:** $32,488," 
- q040 (cross_year): "Yes, JPMorgan Chase spent more on share repurchases in 2025 than in 2024. \n\nIn 2025, JPMorgan Chase's payments for repurchase of common stock were $31,591,000,0" 

## Answerable questions that were refused (1)

- q003 (factual): "The 10-K filings retrieved do not contain the specific text stating Alphabet's headquarters address." model_declined

## Unanswerable questions that were answered (2)

- q065 (unanswerable): 'Apple does not report unit sales numbers (the number of individual units sold) for iPhones in its annual 10-K filings. \n\nHowever, Apple reported iPhone net sale' 
- q068 (unanswerable): 'ExxonMobil does not disclose a specific price prediction or price forecast for crude oil in 2030 in its 10-K filings [c1]. Its disclosures discuss demand and su' 

# Phase 1 data summary

Ingested by `make ingest` (8 companies x last 3 10-Ks + XBRL company facts), embedded with local `bge-base-en-v1.5`.

| Ticker | Filings | Chunks | XBRL facts |
|---|---|---|---|
| AAPL | 3 | 463 | 188 |
| AMZN | 3 | 664 | 141 |
| GOOGL | 3 | 780 | 167 |
| JPM | 3 | 2,818 | 136 |
| META | 3 | 1,142 | 172 |
| MSFT | 3 | 757 | 182 |
| NVDA | 3 | 789 | 181 |
| XOM | 3 | 1,007 | 140 |
| **Total** | **24** | **8,420** | **1,307** |

Fiscal years follow each company's own `dei:DocumentFiscalYearFocus` (MSFT FY2024-2026 = years ending June, NVDA
FY2024-2026 = years ending January, others FY2023-2025). Every filing has content in Items 1, 1A, 7 and 8; the
pipeline refuses to load a filing whose key sections are implausibly small.

## Problems found and fixed while building this (for the record)

- EDGAR's submissions `recent` list only covers ~1000 filings: META and JPM (heavy filers) lost their older 10-Ks until
  older pages were followed.
- A running-header cleanup deleted a heading that doubles as a page header, so two JPM filings lost ~98% of their
  content; fixed, regression-tested, and a plausibility guard now fails the load instead of loading a thin parse.
- `CrossEncoder.predict` applies a sigmoid by default; the reranker wrapper now returns true logits.

## Known limitations

- Pages are physical page indexes, not printed page numbers.
- 1-4% of chunks (mostly numeric tables) exceed the embedder's 512-token window and lose their tail.
- XBRL facts are limited to ~45 core concepts, annual values only.

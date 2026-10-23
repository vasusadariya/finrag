"""The filing_search tool: retrieval over 10-K text, returning passages with citation objects."""

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.retrieval.types import SECTIONS, Filters, Hit, Retriever

MAX_RESULTS = 8
DEFAULT_RESULTS = 5
MAX_PASSAGE_CHARS = 1500


@dataclass(frozen=True)
class Citation:
    """Where a passage came from; ``id`` is what the answer cites, e.g. ``[c3]``."""

    id: str
    chunk_id: int
    ticker: str
    company: str
    fiscal_year: int
    section: str
    page: int | None
    accession: str

    @property
    def label(self) -> str:
        page = f", p.{self.page}" if self.page is not None else ""
        return f"{self.ticker} FY{self.fiscal_year} 10-K, {self.section}{page}"


def citation_for(hit: Hit, n: int) -> Citation:
    return Citation(
        id=f"c{n}",
        chunk_id=hit.chunk_id,
        ticker=hit.ticker,
        company=hit.company,
        fiscal_year=hit.fiscal_year,
        section=hit.section,
        page=hit.page,
        accession=hit.accession,
    )


class FilingSearch:
    """Wraps a retriever. Citation ids are stable within one instance (one agent run)."""

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever
        self.citations: dict[str, Citation] = {}
        self._by_chunk: dict[int, str] = {}
        self.last_hits: list[Hit] = []

    def _cite(self, hit: Hit) -> Citation:
        cid = self._by_chunk.get(hit.chunk_id)
        if cid is None:
            cid = f"c{len(self.citations) + 1}"
            self._by_chunk[hit.chunk_id] = cid
            self.citations[cid] = citation_for(hit, len(self.citations) + 1)
        return self.citations[cid]

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        """Tool entry point: always returns a dict, never raises."""
        try:
            query, filters, k = parse_args(args)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        try:
            hits = self._retriever.search(query, filters, k)
        except Exception as e:  # noqa: BLE001 - tool errors go back to the model, not up the stack
            return {"ok": False, "error": f"search failed: {type(e).__name__}"}
        self.last_hits = hits
        results = []
        for hit in hits:
            c = self._cite(hit)
            results.append(
                {
                    "citation_id": c.id,
                    "source": c.label,
                    "text": hit.content[:MAX_PASSAGE_CHARS],
                    "relevance": round(hit.score, 4),
                }
            )
        return {"ok": True, "count": len(results), "results": results}

    def citation_dicts(self, ids: list[str]) -> list[dict[str, Any]]:
        return [asdict(self.citations[i]) | {"label": self.citations[i].label} for i in ids]


def normalize_section(raw: str) -> str:
    """'7a', 'item 7A' and 'Item 7A' all become 'Item 7A'."""
    m = re.fullmatch(r"\s*(?:item\s*)?(\w+)\s*", raw, flags=re.IGNORECASE)
    return f"Item {m.group(1).upper()}" if m else raw.strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def parse_args(args: dict[str, Any]) -> tuple[str, Filters, int]:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("'query' must be a non-empty string")
    tickers = _as_list(args.get("tickers", args.get("ticker")))
    if not all(isinstance(t, str) and t.strip() for t in tickers):
        raise ValueError("'ticker' must be a ticker symbol string")
    years: list[int] = []
    for y in _as_list(args.get("fiscal_years", args.get("fiscal_year"))):
        if isinstance(y, bool) or not isinstance(y, int | float) or int(y) != y:
            raise ValueError("'fiscal_year' must be an integer such as 2024")
        years.append(int(y))
    sections = [
        normalize_section(str(s)) for s in _as_list(args.get("sections", args.get("section")))
    ]
    bad = [s for s in sections if s not in SECTIONS]
    if bad:
        raise ValueError(f"unknown section(s) {bad}; valid: {sorted(SECTIONS)}")
    k = args.get("k", DEFAULT_RESULTS)
    if isinstance(k, bool) or not isinstance(k, int | float) or int(k) != k or k < 1:
        raise ValueError("'k' must be a positive integer")
    filters = Filters(tuple(t.strip() for t in tickers), tuple(years), tuple(sections))
    return query.strip(), filters, min(int(k), MAX_RESULTS)

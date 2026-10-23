"""Shared types for retrieval: filters, hits and the Retriever protocol."""

from dataclasses import dataclass, field
from typing import Protocol

# Valid section labels as stored in chunks.section.
SECTIONS = frozenset(
    {f"Item {i}" for i in ("1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9")}
    | {"Item 9A", "Item 9B", "Item 9C", "Item 10", "Item 11", "Item 12", "Item 13", "Item 14"}
)


@dataclass(frozen=True)
class Filters:
    """Restrict a search. Empty collections mean 'no restriction'."""

    tickers: tuple[str, ...] = ()
    fiscal_years: tuple[int, ...] = ()
    sections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        bad = [s for s in self.sections if s not in SECTIONS]
        if bad:
            raise ValueError(f"unknown section(s): {bad}")
        object.__setattr__(self, "tickers", tuple(t.upper() for t in self.tickers))

    def to_sql(self, alias: str = "c") -> tuple[str, list[object]]:
        """Return (" AND ..." fragment, params) using %s placeholders; values are never inlined."""
        clauses: list[str] = []
        params: list[object] = []
        if self.tickers:
            clauses.append("co.ticker = ANY(%s)")
            params.append(list(self.tickers))
        if self.fiscal_years:
            clauses.append(f"{alias}.fiscal_year = ANY(%s)")
            params.append(list(self.fiscal_years))
        if self.sections:
            clauses.append(f"{alias}.section = ANY(%s)")
            params.append(list(self.sections))
        return "".join(f" AND {c}" for c in clauses), params


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    score: float
    content: str
    ticker: str
    company: str
    fiscal_year: int
    section: str
    page: int | None
    accession: str
    # per-retriever diagnostics (e.g. {"vector_rank": 3, "keyword_rank": 1}), filled by fusion
    debug: dict[str, float] = field(default_factory=dict, compare=False)


class Retriever(Protocol):
    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]: ...

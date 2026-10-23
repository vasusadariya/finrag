"""Keyword retriever: Postgres full-text search (tsvector + ts_rank_cd) with ticker boosting."""

import re

import psycopg

from app.retrieval.types import Filters, Hit
from app.retrieval.vector import row_to_hit

TICKER_BOOST = 1.5

# Placeholders, in order: boosted tickers, boost factor, tsquery.
SCORED_SELECT = """
SELECT ts_rank_cd(c.tsv, q.query, 1) * CASE WHEN co.ticker = ANY(%s) THEN %s ELSE 1 END AS score,
       c.id AS chunk_id, c.content, co.ticker, co.name AS company, c.fiscal_year, c.section, c.page,
       f.accession
FROM chunks c
JOIN filings f ON f.id = c.filing_id
JOIN companies co ON co.id = c.company_id
CROSS JOIN (SELECT to_tsquery('english', %s) AS query) q
"""

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# numbers keep thousands separators / decimals ("416,161", "3.5"); the rest is alphanumeric runs
_WORD = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|[A-Za-z0-9]+")


def _raw_words(query: str) -> list[str]:
    return _WORD.findall(query)


def tokenize(query: str) -> list[str]:
    """Words of a query, with camelCase / PascalCase identifiers (XBRL concept names) split apart.

    Only [A-Za-z0-9,.] survive, and tsquery terms are single-quoted, so the result is safe to embed.
    """
    words: list[str] = []
    for raw in _raw_words(query):
        words.extend(p for p in _CAMEL.split(raw) if p)
    return words


def build_tsquery(query: str, ignore: frozenset[str] = frozenset()) -> str | None:
    """OR of the distinct lower-cased words, plus a phrase for each multi-part identifier.

    ``ignore`` holds lower-case words that must not become search terms (company names / tickers,
    which only boost). If that would leave nothing, they are used after all. The 'english' config
    drops stop words later; inside a phrase they become distance gaps ('research' <2> 'develop').
    """
    terms: dict[str, None] = {}
    phrases: dict[str, None] = {}
    for raw in _raw_words(query):
        parts = [p.lower() for p in _CAMEL.split(raw) if p]
        if len(parts) > 1:
            phrases["(" + " <-> ".join(f"'{p}'" for p in parts) + ")"] = None
        for p in parts:
            terms[f"'{p}'"] = None
    kept = {t: None for t in terms if t.strip("'") not in ignore}
    if kept:
        terms = kept
    alts = [*terms, *phrases]
    return " | ".join(alts) if alts else None


class KeywordRetriever:
    def __init__(self, conn: psycopg.Connection[dict[str, object]]) -> None:
        self._conn = conn
        self._aliases: dict[str, str] | None = None

    def _company_aliases(self) -> dict[str, str]:
        """Lower-case ticker and first word of the company name -> ticker (e.g. 'apple' -> AAPL)."""
        if self._aliases is None:
            aliases: dict[str, str] = {}
            for r in self._conn.execute("SELECT ticker, name FROM companies").fetchall():
                ticker, name = str(r["ticker"]), str(r["name"])
                aliases[ticker.lower()] = ticker
                first = _WORD.findall(name)
                if first:
                    aliases[first[0].lower()] = ticker
            self._aliases = aliases
        return self._aliases

    def mentioned_tickers(self, query: str) -> list[str]:
        aliases = self._company_aliases()
        found = {aliases[w.lower()] for w in tokenize(query) if w.lower() in aliases}
        return sorted(found)

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be >= 1")
        boost = self.mentioned_tickers(query)
        tsq = build_tsquery(query, frozenset(self._company_aliases()) if boost else frozenset())
        if tsq is None:
            return []
        where, params = (filters or Filters()).to_sql()
        sql = (
            SCORED_SELECT + "WHERE c.tsv @@ q.query" + where + " ORDER BY score DESC, c.id LIMIT %s"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, [boost, TICKER_BOOST, tsq, *params, k])
            return [row_to_hit(r, float(r["score"])) for r in cur.fetchall()]  # type: ignore[arg-type]

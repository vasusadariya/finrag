"""Baseline A: dense vector search over chunk embeddings (pgvector, cosine distance)."""

from typing import Protocol

import psycopg

from app.retrieval.types import Filters, Hit

BASE_SELECT = """
SELECT c.id AS chunk_id, c.content, co.ticker, co.name AS company, c.fiscal_year, c.section, c.page,
       f.accession
FROM chunks c
JOIN filings f ON f.id = c.filing_id
JOIN companies co ON co.id = c.company_id
"""


class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def row_to_hit(row: dict[str, object], score: float) -> Hit:
    return Hit(
        chunk_id=int(row["chunk_id"]),  # type: ignore[call-overload]
        score=score,
        content=str(row["content"]),
        ticker=str(row["ticker"]),
        company=str(row["company"]),
        fiscal_year=int(row["fiscal_year"]),  # type: ignore[call-overload]
        section=str(row["section"]),
        page=None if row["page"] is None else int(row["page"]),  # type: ignore[call-overload]
        accession=str(row["accession"]),
    )


class VectorRetriever:
    """Top-k chunks by cosine similarity (score = 1 - cosine distance, higher is better)."""

    def __init__(
        self, conn: psycopg.Connection[dict[str, object]], embedder: QueryEmbedder
    ) -> None:
        self._conn = conn
        self._embedder = embedder

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be >= 1")
        where, params = (filters or Filters()).to_sql()
        vec = vector_literal(self._embedder.embed_query(query))
        sql = (
            BASE_SELECT
            + "WHERE c.embedding IS NOT NULL"
            + where
            + " ORDER BY c.embedding <=> %s::vector LIMIT %s"
        )
        select_score = sql.replace(
            "SELECT c.id AS chunk_id,",
            "SELECT 1 - (c.embedding <=> %s::vector) AS score, c.id AS chunk_id,",
            1,
        )
        with self._conn.transaction(), self._conn.cursor() as cur:
            # approximate HNSW + filters can under-fill; widen the candidate list
            cur.execute("SET LOCAL hnsw.ef_search = 200")
            cur.execute(select_score, [vec, *params, vec, k])
            return [row_to_hit(r, float(r["score"])) for r in cur.fetchall()]  # type: ignore[arg-type]

"""Sanity queries against the real ingested database with the real bge model.

Skipped unless the embed dependency group is installed and DATABASE_URL has ingested data.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest

pytest.importorskip("sentence_transformers")

from app.db.conn import connect  # noqa: E402
from app.embed.local_bge import LocalBgeProvider  # noqa: E402
from app.retrieval.types import Filters  # noqa: E402
from app.retrieval.vector import VectorRetriever  # noqa: E402


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")


@pytest.fixture(scope="module")
def retriever() -> Iterator[VectorRetriever]:
    try:
        conn = connect(_dsn())
    except psycopg.OperationalError as e:
        pytest.skip(f"database unavailable: {e}")
    with conn:
        n = conn.execute("SELECT count(*) AS n FROM chunks WHERE embedding IS NOT NULL").fetchone()
        have = {
            r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM companies").fetchall()
        }
        if not n or int(n["n"]) < 100 or not {"AAPL", "MSFT"} <= have:  # type: ignore[call-overload]
            pytest.skip("database not populated (run make ingest)")
        yield VectorRetriever(conn, LocalBgeProvider())


def test_headquarters_question_finds_properties_section(retriever: VectorRetriever) -> None:
    hits = retriever.search(
        "Where is Apple's headquarters located?", Filters(tickers=("AAPL",)), k=5
    )
    assert any("Cupertino" in h.content for h in hits)
    assert any(h.section == "Item 2" for h in hits)


def test_company_is_inferred_without_filter(retriever: VectorRetriever) -> None:
    hits = retriever.search("Our corporate headquarters are located in Redmond, Washington", k=3)
    assert hits[0].ticker == "MSFT"


def test_risk_question_lands_in_risk_factors(retriever: VectorRetriever) -> None:
    hits = retriever.search(
        "What risks does Apple face from reliance on single-source suppliers?",
        Filters(tickers=("AAPL",)),
        k=5,
    )
    assert sum(h.section == "Item 1A" for h in hits) >= 3


def test_section_and_year_filters_hold(retriever: VectorRetriever) -> None:
    f = Filters(tickers=("MSFT",), fiscal_years=(2025,), sections=("Item 7",))
    hits = retriever.search("cloud revenue growth", f, k=5)
    assert len(hits) == 5
    assert {(h.ticker, h.fiscal_year, h.section) for h in hits} == {("MSFT", 2025, "Item 7")}

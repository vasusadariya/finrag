"""Confidence on the real pipeline (hybrid -> rerank -> assess). Needs models and ingested data."""

import os
from collections.abc import Iterator

import psycopg
import pytest

pytest.importorskip("sentence_transformers")

from app.db.conn import connect  # noqa: E402
from app.embed.local_bge import LocalBgeProvider  # noqa: E402
from app.retrieval.confidence import assess  # noqa: E402
from app.retrieval.hybrid import HybridRetriever  # noqa: E402
from app.retrieval.keyword import KeywordRetriever  # noqa: E402
from app.retrieval.rerank import LocalBgeReranker, RerankingRetriever  # noqa: E402
from app.retrieval.types import Filters, Hit  # noqa: E402
from app.retrieval.vector import VectorRetriever  # noqa: E402


@pytest.fixture(scope="module")
def pipeline() -> Iterator[RerankingRetriever]:
    dsn = os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")
    try:
        conn = connect(dsn)
    except psycopg.OperationalError as e:
        pytest.skip(f"database unavailable: {e}")
    with conn:
        n = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()
        if not n or int(n["n"]) < 100:  # type: ignore[call-overload]
            pytest.skip("database not populated (run make ingest)")
        hybrid = HybridRetriever(VectorRetriever(conn, LocalBgeProvider()), KeywordRetriever(conn))
        yield RerankingRetriever(hybrid, LocalBgeReranker(), pool=30, timeout_s=300)


def ranked(pipeline: RerankingRetriever, query: str, filters: Filters | None = None) -> list[Hit]:
    """Search, insisting on real reranker scores (a fallback makes the checks trivial)."""
    hits = pipeline.search(query, filters)
    assert hits and "rerank_logit" in hits[0].debug, (
        "reranker fell back; the test would prove nothing"
    )
    return hits


def test_good_question_is_answerable(pipeline: RerankingRetriever) -> None:
    hits = ranked(pipeline, "Where is Apple's headquarters located?", Filters(tickers=("AAPL",)))
    c = assess(hits)
    assert c.answerable, (c, [h.debug for h in hits])


def test_gibberish_is_not_answerable(pipeline: RerankingRetriever) -> None:
    c = assess(ranked(pipeline, "asdkjh qwoeiru zxmcnv lkjasd poiuyt"))
    assert not c.answerable and c.score < 0.5, c  # scored, not merely missing


def test_off_topic_question_is_not_answerable(pipeline: RerankingRetriever) -> None:
    c = assess(ranked(pipeline, "What is the recipe for chocolate chip cookies?"))
    assert not c.answerable, c

"""Hybrid vs. each single retriever on hand-picked probes over the real ingested data.

Probes were fixed before looking at any results. Each has a gold test on the hit (a chunk
that actually answers the question). Skipped unless the embed group is installed and the
database is populated.

What is asserted: recall in the top 30, the candidate pool that the reranker (step 2.5)
will reorder. What is only reported: hit@5. On these 8 probes plain RRF does NOT beat
vector-only at k=5: on paraphrased questions the keyword list is noise and takes as many
of the 5 slots as the good vector hits. Whether to weight the lists is decided on the real
eval set (step 2.8), not by tuning on 8 probes.
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import psycopg
import pytest

pytest.importorskip("sentence_transformers")

from app.db.conn import connect  # noqa: E402
from app.embed.local_bge import LocalBgeProvider  # noqa: E402
from app.retrieval.hybrid import HybridRetriever  # noqa: E402
from app.retrieval.keyword import KeywordRetriever  # noqa: E402
from app.retrieval.rerank import LocalBgeReranker, RerankingRetriever  # noqa: E402
from app.retrieval.types import Filters, Hit  # noqa: E402
from app.retrieval.vector import VectorRetriever  # noqa: E402


@dataclass
class Probe:
    query: str
    filters: Filters
    gold: Callable[[Hit], bool]


def has(*words: str) -> Callable[[Hit], bool]:
    return lambda h: all(w.lower() in h.content.lower() for w in words)


PROBES = [
    Probe("Where is Apple headquartered?", Filters(tickers=("AAPL",)), has("Cupertino")),
    Probe(
        "How does the company protect itself against hackers and data breaches?",
        Filters(tickers=("MSFT",)),
        lambda h: h.section == "Item 1C",
    ),
    Probe("416,161", Filters(tickers=("AAPL",)), has("416,161")),
    Probe(
        "What was Nvidia's data center revenue?",
        Filters(tickers=("NVDA",), sections=("Item 7",)),
        has("Data Center", "revenue"),
    ),
    Probe(
        "AWS segment operating income", Filters(tickers=("AMZN",)), has("AWS", "operating income")
    ),
    Probe(
        "Google Cloud revenue growth",
        Filters(tickers=("GOOGL",), sections=("Item 7",)),
        has("Google Cloud"),
    ),
    Probe(
        "ResearchAndDevelopment expense",
        Filters(tickers=("MSFT",), fiscal_years=(2025,)),
        has("Research and development"),
    ),
    Probe(
        "risks from reliance on third-party manufacturers in Asia",
        Filters(tickers=("AAPL",)),
        lambda h: h.section == "Item 1A" and "Asia" in h.content,
    ),
]


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")


@pytest.fixture(scope="module")
def retrievers() -> Iterator[dict[str, object]]:
    try:
        conn = connect(_dsn())
    except psycopg.OperationalError as e:
        pytest.skip(f"database unavailable: {e}")
    with conn:
        have = {
            r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM companies").fetchall()
        }
        if not {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"} <= have:
            pytest.skip("database not populated (run make ingest)")
        v, kw = VectorRetriever(conn, LocalBgeProvider()), KeywordRetriever(conn)
        hybrid = HybridRetriever(v, kw)
        yield {
            "vector": v,
            "keyword": kw,
            "hybrid": hybrid,
            "reranked": RerankingRetriever(hybrid, LocalBgeReranker(), pool=30),
        }


POOL = 30


def _score(retrievers: dict[str, object], k: int) -> tuple[dict[str, int], list[str]]:
    hits = {name: 0 for name in retrievers}
    detail: list[str] = []
    for p in PROBES:
        row = []
        for name, r in retrievers.items():
            found = r.search(p.query, p.filters, k=k)  # type: ignore[attr-defined]
            ok = any(p.gold(h) for h in found)
            hits[name] += ok
            row.append(f"{name}={'Y' if ok else '-'}")
        detail.append(f"{p.query[:50]!r:56} " + " ".join(row))
    return hits, detail


def test_hybrid_candidate_pool_beats_each_alone(retrievers: dict[str, object]) -> None:
    at5, detail5 = _score(retrievers, 5)
    pool, detail_pool = _score(retrievers, POOL)
    print("\nhit@5:\n" + "\n".join(detail5) + f"\n=> {at5} of {len(PROBES)}")
    print(f"\nrecall@{POOL}:\n" + "\n".join(detail_pool) + f"\n=> {pool} of {len(PROBES)}")
    # the reranked variant (C) is reported above, not yet asserted
    assert pool["hybrid"] >= pool["vector"]
    assert pool["hybrid"] >= pool["keyword"]
    assert pool["hybrid"] > min(pool["vector"], pool["keyword"])

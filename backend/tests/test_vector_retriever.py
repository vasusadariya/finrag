import psycopg

from app.retrieval.types import Filters
from app.retrieval.vector import VectorRetriever, vector_literal

DIM = 768


def hot(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


class FakeEmbedder:
    """Maps a query string to a pre-chosen vector."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    def embed_query(self, text: str) -> list[float]:
        return self.table[text]


def seed(conn: psycopg.Connection[dict[str, object]]) -> None:
    """Two companies, two years; chunk i has unit vector e_i."""
    conn.execute(
        "INSERT INTO companies (id, cik, ticker, name)"
        " VALUES (1,'1','AAA','Alpha'), (2,'2','BBB','Beta')"
    )
    conn.execute(
        "INSERT INTO filings (id, company_id, accession, form, fiscal_year, period_end) VALUES "
        "(1,1,'acc-a-25','10-K',2025,'2025-12-31'), (2,1,'acc-a-24','10-K',2024,'2024-12-31'),"
        "(3,2,'acc-b-25','10-K',2025,'2025-12-31')"
    )
    rows = [
        (1, 1, 2025, "Item 1A", 3, "alpha risk text"),
        (1, 1, 2025, "Item 7", 9, "alpha mdna text"),
        (2, 1, 2024, "Item 1A", 4, "alpha old risk"),
        (3, 2, 2025, "Item 1A", 5, "beta risk text"),
        (3, 2, 2025, "Item 2", None, "beta properties"),
    ]
    for i, (fid, cid, fy, sec, page, text) in enumerate(rows):
        conn.execute(
            "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
            " content, content_hash, token_count, embedding)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,10,%s::vector)",
            (fid, cid, fy, sec, page, i, text, f"h{i}", vector_literal(hot(i))),
        )
    conn.commit()


def test_nearest_first_with_scores_and_metadata(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    seed(conn)
    # query closest to chunk 3, then chunk 0
    q = [0.0] * DIM
    q[3], q[0] = 1.0, 0.5
    r = VectorRetriever(conn, FakeEmbedder({"q": q}))
    hits = r.search("q", k=3)
    assert [h.content for h in hits[:2]] == ["beta risk text", "alpha risk text"]
    assert hits[0].score > hits[1].score > hits[2].score
    top = hits[0]
    assert (top.ticker, top.company, top.fiscal_year, top.section, top.page, top.accession) == (
        "BBB", "Beta", 2025, "Item 1A", 5, "acc-b-25",
    )  # fmt: skip
    assert 0.0 < top.score <= 1.0


def test_k_limits_results(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    r = VectorRetriever(conn, FakeEmbedder({"q": hot(0)}))
    assert len(r.search("q", k=1)) == 1
    assert len(r.search("q", k=50)) == 5


def test_filters_restrict_results(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    r = VectorRetriever(conn, FakeEmbedder({"q": hot(3)}))  # chunk 3 is BBB Item 1A
    assert {h.ticker for h in r.search("q", Filters(tickers=("AAA",)), k=10)} == {"AAA"}
    assert {h.fiscal_year for h in r.search("q", Filters(fiscal_years=(2024,)), k=10)} == {2024}
    assert {h.section for h in r.search("q", Filters(sections=("Item 7",)), k=10)} == {"Item 7"}
    combo = r.search(
        "q", Filters(tickers=("aaa",), fiscal_years=(2025,), sections=("Item 1A",)), k=10
    )
    assert [h.content for h in combo] == ["alpha risk text"]
    assert r.search("q", Filters(tickers=("ZZZ",)), k=10) == []


def test_chunks_without_embedding_are_ignored(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    conn.execute("UPDATE chunks SET embedding = NULL WHERE chunk_index = 0")
    conn.commit()
    hits = VectorRetriever(conn, FakeEmbedder({"q": hot(0)})).search("q", k=10)
    assert len(hits) == 4 and all(h.content != "alpha risk text" for h in hits)


def test_invalid_k(conn: psycopg.Connection[dict[str, object]]) -> None:
    import pytest

    with pytest.raises(ValueError):
        VectorRetriever(conn, FakeEmbedder({})).search("q", k=0)

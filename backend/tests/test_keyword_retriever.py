import psycopg
import pytest

from app.retrieval.keyword import KeywordRetriever, build_tsquery, tokenize
from app.retrieval.types import Filters


def test_tokenize_splits_identifiers_and_strips_punctuation() -> None:
    assert tokenize("ResearchAndDevelopmentExpense") == [
        "Research",
        "And",
        "Development",
        "Expense",
    ]
    assert tokenize("R&D: net-income! (AAPL's)") == ["R", "D", "net", "income", "AAPL", "s"]
    assert tokenize("sales of $416,161 or 3.5%") == ["sales", "of", "416,161", "or", "3.5"]
    assert tokenize("HTTPServer2xx") == ["HTTP", "Server2xx"]
    assert tokenize("'; DROP TABLE x;--") == ["DROP", "TABLE", "x"]


def test_build_tsquery_dedupes_and_ors() -> None:
    assert build_tsquery("Risk risk RISK factors") == "'risk' | 'factors'"
    assert build_tsquery("   !!! ") is None


def test_build_tsquery_adds_phrase_for_identifiers_and_keeps_numbers() -> None:
    q = build_tsquery("ResearchAndDevelopment")
    assert q == "'research' | 'and' | 'development' | ('research' <-> 'and' <-> 'development')"
    assert (
        build_tsquery("revenue of 416,161 and 3.5")
        == "'revenue' | 'of' | '416,161' | 'and' | '3.5'"
    )


def test_build_tsquery_ignores_company_words_unless_nothing_else() -> None:
    assert build_tsquery("apple headquarters", frozenset({"apple"})) == "'headquarters'"
    assert build_tsquery("apple", frozenset({"apple"})) == "'apple'"


def seed(
    conn: psycopg.Connection[dict[str, object]], extra: list[tuple[int, str]] | None = None
) -> None:
    conn.execute(
        "INSERT INTO companies (id, cik, ticker, name)"
        " VALUES (1,'1','AAA','Alpha Corp'), (2,'2','BBB','Beta Inc.')"
    )
    conn.execute(
        "INSERT INTO filings (id, company_id, accession, form, fiscal_year, period_end) VALUES "
        "(1,1,'a25','10-K',2025,'2025-12-31'), (2,2,'b25','10-K',2025,'2025-12-31'), "
        "(3,1,'a24','10-K',2024,'2024-12-31')"
    )
    rows = [
        (1, 1, 2025, "Item 7", "Research and development expense increased due to headcount."),
        (2, 2, 2025, "Item 7", "Research and development expense decreased slightly."),
        (1, 1, 2025, "Item 1A", "Supply chain disruption could harm our manufacturing."),
        (2, 2, 2025, "Item 1A", "Supply chain disruption could harm our manufacturing."),
        (3, 1, 2024, "Item 7", "Revenue grew in cloud services; research spending was flat."),
    ]
    for i, (fid, cid, fy, sec, text) in enumerate(rows):
        conn.execute(
            "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
            " content, content_hash, token_count) VALUES (%s,%s,%s,%s,1,%s,%s,%s,10)",
            (fid, cid, fy, sec, i, text, f"h{i}"),
        )
    conn.commit()


def test_exact_identifier_hits(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    hits = KeywordRetriever(conn).search("ResearchAndDevelopmentExpense", k=5)
    assert {h.content[:8] for h in hits[:2]} == {"Research"}
    assert all("esearch" in h.content for h in hits)


def test_or_semantics_for_natural_questions(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    # 'zebra' appears nowhere; an AND query would return nothing
    hits = KeywordRetriever(conn).search("zebra supply chain disruption manufacturing", k=5)
    assert len(hits) == 2 and all("Supply chain" in h.content for h in hits)


def test_ticker_or_company_name_boosts_that_company(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    seed(conn)
    r = KeywordRetriever(conn)
    assert r.mentioned_tickers("what did BBB say, and alpha?") == ["AAA", "BBB"]
    for mention, expected in [
        ("BBB supply chain", "BBB"),
        ("beta supply chain", "BBB"),
        ("alpha supply chain", "AAA"),
    ]:
        hits = r.search(mention, k=2)
        assert hits[0].ticker == expected, mention
        assert hits[0].score > hits[1].score  # identical text; only the boost separates them


def test_filters_apply(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    r = KeywordRetriever(conn)
    assert {h.ticker for h in r.search("research", Filters(tickers=("AAA",)), k=10)} == {"AAA"}
    assert {h.fiscal_year for h in r.search("research", Filters(fiscal_years=(2024,)), k=10)} == {
        2024
    }
    assert r.search("research", Filters(sections=("Item 1A",)), k=10) == []


def test_stopword_only_and_garbage_queries_are_safe(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    seed(conn)
    r = KeywordRetriever(conn)
    assert r.search("the of and", k=5) == []
    assert r.search("", k=5) == []
    for nasty in [
        "a & b | !c",
        "'; DROP TABLE chunks; --",
        "(((",
        "research:*",
        "\\ \x00".replace("\x00", ""),
    ]:
        r.search(nasty, k=5)  # must not raise
    assert r.search("research", k=5)  # connection still healthy, table still there


def test_k_validation_and_limit(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    r = KeywordRetriever(conn)
    assert len(r.search("research", k=1)) == 1
    with pytest.raises(ValueError):
        r.search("research", k=0)


def test_company_name_only_boosts_it_is_not_a_required_term(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    seed(conn)
    conn.execute(
        "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
        " content, content_hash, token_count)"
        " VALUES (1,1,2025,'Item 2',1,99,'Our headquarters is in Springfield.','hx',10)"
    )
    conn.commit()
    # 'alpha' is absent from the properties text, yet the chunk must be found (and boosted)
    hits = KeywordRetriever(conn).search("alpha headquarters Springfield", k=3)
    assert hits and "Springfield" in hits[0].content and hits[0].ticker == "AAA"


def test_phrase_ranks_above_scattered_words(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    conn.execute(
        "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
        " content,"
        " content_hash, token_count) VALUES (1,1,2025,'Item 7',1,98,"
        "'Research was funded. Separately, development of new offices continued.','hy',10)"
    )
    conn.commit()
    hits = KeywordRetriever(conn).search("ResearchAndDevelopment", k=10)
    contents = [h.content for h in hits]
    scattered = next(i for i, c in enumerate(contents) if c.startswith("Research was funded"))
    phrase = [i for i, c in enumerate(contents) if "Research and development" in c]
    assert len(phrase) == 2 and max(phrase) < scattered


def test_numbers_match_as_a_unit(conn: psycopg.Connection[dict[str, object]]) -> None:
    seed(conn)
    conn.execute(
        "INSERT INTO chunks (filing_id, company_id, fiscal_year, section, page, chunk_index,"
        " content,"
        " content_hash, token_count)"
        " VALUES (1,1,2025,'Item 8',1,97,'Net sales were 416,161 million.','hz',10),"
        "(1,1,2025,'Item 8',1,96,'We sold 416 units and 161 widgets.','hw',10)"
    )
    conn.commit()
    hits = KeywordRetriever(conn).search("416,161", k=5)
    assert "416,161" in hits[0].content

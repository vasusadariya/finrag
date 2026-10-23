from typing import Any

import psycopg
import pytest

from app.retrieval.types import Filters, Hit
from app.tools.filing_search import FilingSearch, normalize_section, parse_args


def hit(i: int, **kw: Any) -> Hit:
    base: dict[str, Any] = dict(
        chunk_id=i, score=0.9, content=f"passage {i}", ticker="AAPL", company="Apple Inc.",
        fiscal_year=2024, section="Item 1A", page=12, accession="0000320193-24-000123",
    )  # fmt: skip
    return Hit(**{**base, **kw})


class FakeRetriever:
    def __init__(self, hits: list[Hit] | Exception) -> None:
        self.hits, self.calls = hits, []  # type: ignore[var-annotated]

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        self.calls.append((query, filters, k))
        if isinstance(self.hits, Exception):
            raise self.hits
        return self.hits[:k]


@pytest.mark.parametrize(
    "raw, want",
    [
        ("7a", "Item 7A"),
        ("Item 7A", "Item 7A"),
        ("item1a", "Item 1A"),
        (" 8 ", "Item 8"),
        ("???", "???"),
    ],
)
def test_normalize_section(raw: str, want: str) -> None:
    assert normalize_section(raw) == want


def test_args_parsed_into_filters() -> None:
    q, f, k = parse_args(
        {"query": " risks ", "ticker": "aapl", "fiscal_year": 2024, "section": "1a", "k": 3}
    )
    assert (q, k) == ("risks", 3)
    assert f == Filters(("AAPL",), (2024,), ("Item 1A",))
    _, f2, _ = parse_args(
        {"query": "x", "tickers": ["aapl", "msft"], "fiscal_years": [2023, 2024.0]}
    )
    assert f2.tickers == ("AAPL", "MSFT") and f2.fiscal_years == (2023, 2024)


@pytest.mark.parametrize(
    "args",
    [
        {}, {"query": ""}, {"query": 5}, {"query": "x", "ticker": 5}, {"query": "x", "ticker": " "},
        {"query": "x", "fiscal_year": "2024"}, {"query": "x", "fiscal_year": 2024.5},
        {"query": "x", "fiscal_year": True}, {"query": "x", "section": "Item 99"},
        {"query": "x", "k": 0}, {"query": "x", "k": "3"}, {"query": "x", "k": True},
    ],
)  # fmt: skip
def test_bad_args_become_errors_not_exceptions(args: dict[str, Any]) -> None:
    out = FilingSearch(FakeRetriever([]))(args)
    assert out["ok"] is False and out["error"]


def test_results_carry_stable_citations() -> None:
    fs = FilingSearch(FakeRetriever([hit(1), hit(2, ticker="MSFT", page=None)]))
    out = fs({"query": "q"})
    assert out["ok"] and out["count"] == 2
    assert [r["citation_id"] for r in out["results"]] == ["c1", "c2"]
    assert out["results"][0]["source"] == "AAPL FY2024 10-K, Item 1A, p.12"
    assert out["results"][1]["source"] == "MSFT FY2024 10-K, Item 1A"
    again = fs({"query": "q2"})  # same chunks -> same ids
    assert [r["citation_id"] for r in again["results"]] == ["c1", "c2"]
    assert fs.citation_dicts(["c1"])[0]["accession"] == "0000320193-24-000123"
    assert fs.citation_dicts(["c1"])[0]["label"].startswith("AAPL FY2024")


def test_k_capped_and_text_truncated() -> None:
    r = FakeRetriever([hit(i, content="x" * 5000) for i in range(20)])
    out = FilingSearch(r)({"query": "q", "k": 50})
    assert r.calls[0][2] == 8 and out["count"] == 8
    assert len(out["results"][0]["text"]) == 1500


def test_retriever_failure_is_reported_not_raised() -> None:
    out = FilingSearch(FakeRetriever(RuntimeError("db down")))({"query": "q"})
    assert out == {"ok": False, "error": "search failed: RuntimeError"}


def test_against_real_database(conn: psycopg.Connection[dict[str, object]]) -> None:
    from app.retrieval.keyword import KeywordRetriever

    out = FilingSearch(KeywordRetriever(conn))({"query": "risk factors", "ticker": "AAPL"})
    assert out["ok"] is True  # empty scratch DB is fine; the contract is the shape
    assert isinstance(out["results"], list)

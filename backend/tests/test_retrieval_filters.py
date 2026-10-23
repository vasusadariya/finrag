import pytest

from app.retrieval.types import Filters


def test_empty_filters_add_nothing() -> None:
    sql, params = Filters().to_sql()
    assert sql == "" and params == []


def test_all_filters_combine_with_and() -> None:
    f = Filters(tickers=("aapl", "MSFT"), fiscal_years=(2025,), sections=("Item 1A", "Item 7"))
    sql, params = f.to_sql()
    assert sql.count(" AND ") == 3
    assert (
        "co.ticker = ANY(%s)" in sql
        and "c.fiscal_year = ANY(%s)" in sql
        and "c.section = ANY(%s)" in sql
    )
    assert params == [["AAPL", "MSFT"], [2025], ["Item 1A", "Item 7"]]


def test_values_never_interpolated() -> None:
    sql, _ = Filters(tickers=("A'; DROP TABLE chunks;--",)).to_sql()
    assert "DROP" not in sql


def test_alias_is_used() -> None:
    sql, _ = Filters(fiscal_years=(2024,)).to_sql(alias="ch")
    assert "ch.fiscal_year" in sql


def test_unknown_section_rejected() -> None:
    with pytest.raises(ValueError, match="unknown section"):
        Filters(sections=("Item 99",))


def test_filters_are_hashable_and_immutable() -> None:
    f = Filters(tickers=("AAPL",))
    assert hash(f) == hash(Filters(tickers=("aapl",)))
    with pytest.raises(AttributeError):
        f.tickers = ()  # type: ignore[misc]

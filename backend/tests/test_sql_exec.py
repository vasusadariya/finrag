import time
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.tools.sql_exec import SqlExecutionError, SqlExecutor, SqlTimeoutError, to_json_value


def ro_dsn(db_url: str) -> str:
    parts = urlsplit(db_url)
    return urlunsplit(parts._replace(netloc=f"finrag_ro:finrag_ro@{parts.hostname}:{parts.port}"))


@pytest.fixture
def seeded(conn: psycopg.Connection[dict[str, object]], db_url: str) -> str:
    conn.execute(
        "INSERT INTO companies (cik, ticker, name)"
        " SELECT g, 'T' || g, 'Co ' || g FROM generate_series(1, 300) g",
    )
    conn.execute("INSERT INTO companies (cik, ticker, name) VALUES ('0', 'AAPL', 'Apple Inc.')")
    conn.commit()
    return ro_dsn(db_url)


def test_returns_columns_rows_and_the_executed_query(seeded: str) -> None:
    sql = "SELECT ticker, name FROM companies WHERE ticker = 'AAPL'"
    r = SqlExecutor(seeded).execute(sql)
    assert r.columns == ["ticker", "name"]
    assert r.rows == [["AAPL", "Apple Inc."]]
    assert (r.row_count, r.truncated, r.executed_sql) == (1, False, sql)
    assert r.elapsed_ms >= 0


def test_row_cap_truncates_and_says_so(seeded: str) -> None:
    r = SqlExecutor(seeded, max_rows=50).execute("SELECT * FROM companies")
    assert r.row_count == 50 and len(r.rows) == 50 and r.truncated
    exact = SqlExecutor(seeded, max_rows=301).execute("SELECT * FROM companies")
    assert exact.row_count == 301 and not exact.truncated


def test_statement_timeout_cancels_slow_queries(seeded: str) -> None:
    start = time.perf_counter()
    with pytest.raises(SqlTimeoutError, match="time limit"):
        SqlExecutor(seeded, timeout_ms=300).execute("SELECT pg_sleep(10)")
    assert time.perf_counter() - start < 3


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO companies (cik, ticker, name) VALUES ('9', 'X', 'X')",
        "UPDATE companies SET name = 'x'",
        "DELETE FROM companies",
        "TRUNCATE companies",
        "DROP TABLE companies",
        "CREATE TABLE evil (id int)",
        "SET statement_timeout = 0",
        "SET ROLE postgres",
        "COPY companies TO '/tmp/x'",
        "",
        "   ",
    ],
)
def test_non_select_never_reaches_the_database(seeded: str, sql: str) -> None:
    with pytest.raises(SqlExecutionError, match="only SELECT"):
        SqlExecutor(seeded).execute(sql)


@pytest.mark.parametrize(
    "sql, why",
    [
        ("SELECT * FROM chunks", "permission denied"),
        ("SELECT * FROM query_traces", "permission denied"),
        ("SELECT * FROM pg_shadow", "permission denied"),
        ("SELECT * INTO newt FROM companies", "permission denied|read-only"),
        (
            "WITH d AS (DELETE FROM companies RETURNING *) SELECT * FROM d",
            "permission denied|read-only",
        ),
        (
            "WITH i AS (INSERT INTO companies (cik,ticker,name) VALUES ('1','x','y')"
            " RETURNING *) SELECT * FROM i",
            "permission denied|read-only",
        ),
        ("SELECT * FROM companies; DROP TABLE companies", "multiple commands"),
        ("SELECT 1; SELECT 2", "multiple commands"),
        ("SELECT * FROM companies FOR UPDATE", "permission denied|read-only"),
    ],
)
def test_database_enforces_read_only_even_if_the_validator_is_bypassed(
    seeded: str, sql: str, why: str
) -> None:
    import re

    with pytest.raises(SqlExecutionError) as e:
        SqlExecutor(seeded).execute(sql)
    assert re.search(why, str(e.value)), str(e.value)


def test_data_is_untouched_after_all_those_attempts(
    seeded: str, conn: psycopg.Connection[dict[str, object]]
) -> None:
    for sql in ["DELETE FROM companies", "WITH d AS (DELETE FROM companies RETURNING *) SELECT 1"]:
        with pytest.raises(SqlExecutionError):
            SqlExecutor(seeded).execute(sql)
    row = conn.execute("SELECT count(*) AS n FROM companies").fetchone()
    assert row is not None and row["n"] == 301


def test_sql_errors_are_reported_without_internals(seeded: str) -> None:
    with pytest.raises(SqlExecutionError) as e:
        SqlExecutor(seeded).execute("SELECT nope FROM companies")
    assert (
        "nope" in str(e.value)
        and "LINE 1" not in str(e.value)
        and "SELECT nope" not in str(e.value)
    )


def test_unreachable_database(db_url: str) -> None:
    bad = ro_dsn(db_url).replace(str(urlsplit(db_url).port), "1")
    with pytest.raises(SqlExecutionError, match="unavailable"):
        SqlExecutor(bad).execute("SELECT 1")


def test_select_start_detection_allows_cte_and_parentheses(seeded: str) -> None:
    ex = SqlExecutor(seeded)
    assert ex.execute("WITH t AS (SELECT 1 AS a) SELECT a FROM t").rows == [[1]]
    assert ex.execute("  \n(SELECT 2 AS b)").rows == [[2]]


def test_json_values() -> None:
    assert to_json_value(Decimal("416161000000")) == 416161000000
    assert isinstance(to_json_value(Decimal("416161000000")), int)
    assert to_json_value(Decimal("7.46")) == 7.46
    assert to_json_value(None) is None


def test_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        SqlExecutor("x", timeout_ms=0)
    with pytest.raises(ValueError):
        SqlExecutor("x", max_rows=0)

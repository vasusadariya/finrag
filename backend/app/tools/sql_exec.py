"""Executes validated SQL as the read-only role, with hard limits.

This is defence in depth *behind* the SQL guard. Even if a query got past the validator it
still runs as `finrag_ro` (SELECT on three tables only), in a read-only transaction, through
the extended protocol (so stacked statements are refused), with a statement timeout and a row
cap, and only if it starts with SELECT/WITH (so the session cannot be reconfigured with SET).
"""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg

_SELECT_START = re.compile(r"^\s*\(*\s*(select|with)\b", re.IGNORECASE)


class SqlExecutionError(RuntimeError):
    """The database refused or failed the query. The message is safe to show to the model."""


class SqlTimeoutError(SqlExecutionError):
    """The query ran longer than the statement timeout and was cancelled."""


@dataclass(frozen=True)
class ExecResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool  # more rows existed than max_rows
    executed_sql: str
    elapsed_ms: int


def to_json_value(v: Any) -> Any:
    """JSON-friendly value. Integral decimals stay exact ints; others become floats."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


class SqlExecutor:
    def __init__(self, dsn_ro: str, timeout_ms: int = 5000, max_rows: int = 200) -> None:
        if timeout_ms <= 0 or max_rows <= 0:
            raise ValueError("timeout_ms and max_rows must be positive")
        self._dsn, self._timeout_ms, self._max_rows = dsn_ro, timeout_ms, max_rows

    def execute(self, sql: str) -> ExecResult:
        if not _SELECT_START.match(sql):
            raise SqlExecutionError("only SELECT queries can be executed")
        start = time.perf_counter()
        try:
            with psycopg.connect(
                self._dsn,
                options=f"-c statement_timeout={self._timeout_ms} -c lock_timeout=1000",
                connect_timeout=5,
            ) as conn:
                conn.read_only = True
                # prepare=True forces the extended protocol, which rejects multiple statements
                cur = conn.execute(sql, prepare=True)
                columns = [d.name for d in (cur.description or [])]
                fetched = cur.fetchmany(self._max_rows + 1)
        except psycopg.errors.QueryCanceled as e:
            raise SqlTimeoutError(f"query exceeded the {self._timeout_ms} ms time limit") from e
        except psycopg.OperationalError as e:
            raise SqlExecutionError("database unavailable") from e
        except psycopg.Error as e:
            # primary message only: no query text, hints or server internals
            raise SqlExecutionError(str(e.diag.message_primary or "query failed")) from e
        truncated = len(fetched) > self._max_rows
        rows = [[to_json_value(v) for v in row] for row in fetched[: self._max_rows]]
        return ExecResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            executed_sql=sql,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )

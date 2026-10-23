"""Persist every agent run to ``query_traces`` and read it back."""

import json
import logging
import uuid
from dataclasses import asdict
from typing import Any

import psycopg

from app.agent.cost import run_cost
from app.agent.types import AgentResult
from app.db.conn import connect

log = logging.getLogger(__name__)


def trace_payload(result: AgentResult) -> dict[str, Any]:
    """JSON for the ``steps`` column and ``meta``; tool payloads are summarised, never stored."""
    steps = [
        {
            "index": s.index,
            "model": s.model,
            "text": s.text[:2000],
            "latency_s": round(s.latency_s, 3),
            "cached": s.cached,
            "tokens": asdict(s.usage),
            "tools": [asdict(t) | {"latency_s": round(t.latency_s, 3)} for t in s.tools],
        }
        for s in result.steps
    ]
    meta = {
        "flags": result.flags,
        "models": result.models,
        "citations": result.citations,
        "ungrounded": result.ungrounded,
        "sql_tokens": asdict(result.sql_usage),
        "sql_verdicts": result.sql_verdicts,
    }
    return {"steps": steps, "meta": meta}


class TraceStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def save(self, result: AgentResult) -> str | None:
        """Insert one trace; returns its id, or None if the database is unavailable.

        Tracing must never fail a user's request, so errors are logged and swallowed.
        """
        payload = trace_payload(result)
        total_in = result.usage.prompt_tokens + result.sql_usage.prompt_tokens
        total_out = (
            result.usage.output_tokens
            + result.usage.thinking_tokens
            + result.sql_usage.output_tokens
            + result.sql_usage.thinking_tokens
        )
        try:
            with connect(self._dsn) as conn:
                row = conn.execute(
                    "INSERT INTO query_traces (question, answer, refused, steps, tokens_in,"
                    " tokens_out, latency_ms, shadow_cost_usd, status, reason, meta)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        result.question,
                        result.answer,
                        result.status == "refused",
                        json.dumps(payload["steps"]),
                        total_in,
                        total_out,
                        int(result.latency_s * 1000),
                        round(run_cost(result), 6),
                        result.status,
                        result.reason,
                        json.dumps(payload["meta"]),
                    ),
                ).fetchone()
        except psycopg.Error as e:
            log.error("could not save trace", extra={"error": type(e).__name__})
            return None
        return str(row["id"]) if row else None

    def get(self, trace_id: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(trace_id)
        except ValueError:
            return None
        with connect(self._dsn) as conn:
            row = conn.execute("SELECT * FROM query_traces WHERE id = %s", (trace_id,)).fetchone()
        if row is None:
            return None
        return {k: (str(v) if k in {"id", "created_at"} else _plain(v)) for k, v in row.items()}


def _plain(v: Any) -> Any:
    return float(v) if v.__class__.__name__ == "Decimal" else v

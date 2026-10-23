import psycopg
import pytest

from app.agent.cost import FALLBACK, PRICES_PER_M, run_cost, usage_cost
from app.agent.loop import Agent
from app.agent.traces import TraceStore, trace_payload
from app.agent.types import AgentResult
from app.llm.types import Usage
from tests.fake_llm import ScriptedLLM, call, say
from tests.test_agent_loop import make


def test_usage_cost_arithmetic() -> None:
    u = Usage(prompt_tokens=1_000_000, output_tokens=500_000, thinking_tokens=500_000)
    inp, out = PRICES_PER_M["gemini-3.5-flash"]
    assert usage_cost(u, "gemini-3.5-flash") == pytest.approx(inp + out)
    assert usage_cost(u, "some-new-model") == pytest.approx(FALLBACK[0] + FALLBACK[1])
    assert usage_cost(Usage(), "gemini-3.5-flash") == 0


def run_one() -> AgentResult:
    llm = ScriptedLLM([call("financial_sql", question="q"), say("Revenue $391.0 billion [sql1].")])
    return Agent(llm, make, "cov").run("Apple revenue?")


def test_run_cost_includes_sql_subcalls() -> None:
    r = run_one()
    r.sql_usage = Usage(1_000_000, 0, 0)
    base = sum(usage_cost(s.usage, s.model) for s in r.steps)
    assert run_cost(r) == pytest.approx(base + PRICES_PER_M["gemini-3.5-flash"][0])


def test_trace_payload_summarises_not_dumps() -> None:
    r = run_one()
    p = trace_payload(r)
    assert p["steps"][0]["tools"][0]["name"] == "financial_sql"
    assert "391035000000" not in str(p["steps"])  # tool output is summarised, not stored
    assert p["meta"]["citations"][0]["id"] == "sql1"


def test_save_and_load_roundtrip(db_url: str) -> None:
    store = TraceStore(db_url)
    r = run_one()
    tid = store.save(r)
    assert tid
    got = store.get(tid)
    assert got is not None
    assert got["question"] == "Apple revenue?" and got["status"] == "answered"
    assert got["refused"] is False and got["tokens_in"] == 200
    assert got["steps"][0]["tools"][0]["name"] == "financial_sql"
    assert got["meta"]["citations"][0]["id"] == "sql1"
    assert got["shadow_cost_usd"] > 0


def test_refusal_is_recorded(db_url: str) -> None:
    r = Agent(ScriptedLLM([say("INSUFFICIENT_EVIDENCE: nothing.")]), make, "cov").run("q")
    got = TraceStore(db_url).get(TraceStore(db_url).save(r) or "")
    assert got is not None and got["refused"] is True and got["reason"] == "model_declined"


def test_get_rejects_bad_ids_and_save_survives_db_failure(db_url: str) -> None:
    store = TraceStore(db_url)
    assert store.get("not-a-uuid") is None
    assert store.get("00000000-0000-0000-0000-000000000000") is None
    broken = TraceStore("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    assert broken.save(run_one()) is None  # logged, not raised


def test_migration_adds_columns(conn: psycopg.Connection[dict[str, object]]) -> None:
    cols = {
        str(r["column_name"])
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='query_traces'"
        ).fetchall()
    }
    assert {"status", "reason", "meta"} <= cols

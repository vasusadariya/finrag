from typing import Any

import pytest

from app.llm.gemini import to_gemini_schema
from app.llm.types import ToolCall, ToolSpec
from app.retrieval.types import Filters, Hit
from app.tools.builder import CALCULATOR, FILING_SEARCH, FINANCIAL_SQL, build_registry
from app.tools.filing_search import FilingSearch
from app.tools.financial_sql import FinancialSql
from app.tools.registry import MAX_RESULT_CHARS, ToolRegistry, validate_args
from app.tools.sql_exec import ExecResult
from app.tools.sql_guard import SqlGuardUnavailableError
from app.tools.text_to_sql import SqlAnswer

SPEC = ToolSpec(
    "t",
    "d",
    {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
            "n": {"type": "integer"},
            "x": {"type": "number"},
            "e": {"type": "string", "enum": ["a", "b"]},
            "l": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["s"],
    },
)


@pytest.mark.parametrize(
    "args, bad",
    [
        ({"s": "ok"}, 0),
        ({"s": "ok", "n": 1, "x": 2.5, "e": "a", "l": ["p"]}, 0),
        ({}, 1),
        ({"s": 1}, 1),
        ({"s": "a", "n": True}, 1),
        ({"s": "a", "n": 1.5}, 1),
        ({"s": "a", "e": "c"}, 1),
        ({"s": "a", "l": [1]}, 1),
        ({"s": "a", "zzz": 1}, 1),
        ("nope", 1),
    ],
)
def test_validate_args(args: Any, bad: int) -> None:
    assert len(validate_args(SPEC.parameters, args)) == bad


def test_dispatch_and_error_paths() -> None:
    reg = ToolRegistry()
    reg.register(SPEC, lambda a: {"ok": True, "echo": a["s"]})
    assert reg.call(ToolCall("1", "t", {"s": "hi"})).content == {"ok": True, "echo": "hi"}
    unknown = reg.call(ToolCall("2", "nope", {}))
    assert unknown.call_id == "2" and "unknown tool" in unknown.content["error"]
    bad = reg.call(ToolCall("3", "t", {}))
    assert bad.content["ok"] is False and "missing required" in bad.content["error"]
    with pytest.raises(ValueError):
        reg.register(SPEC, lambda a: {})


def test_crashing_and_oversized_tools_are_contained() -> None:
    def boom(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("secret internals")

    reg = ToolRegistry()
    reg.register(ToolSpec("boom", "d", {"type": "object", "properties": {}}), boom)
    reg.register(
        ToolSpec("big", "d", {"type": "object", "properties": {}}),
        lambda a: {"ok": True, "blob": "x" * (MAX_RESULT_CHARS + 1)},
    )
    reg.register(
        ToolSpec("odd", "d", {"type": "object", "properties": {}}), lambda a: {"v": {1, 2}}
    )
    crashed = reg.call(ToolCall("1", "boom", {})).content
    assert crashed == {"ok": False, "error": "tool failed: RuntimeError"}  # no message leaked
    assert "too large" in reg.call(ToolCall("2", "big", {})).content["error"]
    assert "v" in reg.call(ToolCall("3", "odd", {})).content  # default=str handles sets


class FakeRetriever:
    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        return [Hit(1, 0.9, "text", "AAPL", "Apple", 2024, "Item 7", 3, "acc")]


class FakeT2S:
    def __init__(self, ans: SqlAnswer | Exception) -> None:
        self.ans = ans

    def run(self, question: str) -> SqlAnswer:
        if isinstance(self.ans, Exception):
            raise self.ans
        return self.ans


def sql_tool(ans: SqlAnswer | Exception) -> FinancialSql:
    return FinancialSql(FakeT2S(ans))  # type: ignore[arg-type]


def test_real_tool_set_end_to_end() -> None:
    ok = SqlAnswer(
        True, "SELECT 1 LIMIT 50", ExecResult(["v"], [[5]], 1, False, "SELECT 1 LIMIT 50", 3)
    )
    fsql = sql_tool(ok)
    reg = build_registry(FilingSearch(FakeRetriever()), fsql)
    assert reg.names == ["filing_search", "financial_sql", "calculator"]
    s = reg.call(
        ToolCall("a", "filing_search", {"query": "q", "ticker": "AAPL", "section": "Item 7"})
    )
    assert s.content["ok"] and s.content["results"][0]["citation_id"] == "c1"
    q = reg.call(ToolCall("b", "financial_sql", {"question": "revenue?"})).content
    assert q["ok"] and q["citation_id"] == "sql1" and q["rows"] == [[5]]
    assert fsql.queries["sql1"]["sql"] == "SELECT 1 LIMIT 50"
    c = reg.call(ToolCall("c", "calculator", {"expression": "pct_change(100, 110)"})).content
    assert c["result"] == pytest.approx(10)


def test_financial_sql_failures_become_errors() -> None:
    refused = SqlAnswer(False, None, None, error="CANNOT_ANSWER: no such data")
    assert "CANNOT_ANSWER" in sql_tool(refused)({"question": "q"})["error"]
    down = sql_tool(SqlGuardUnavailableError("down"))({"question": "q"})
    assert down["ok"] is False and "unavailable" in down["error"]
    assert sql_tool(refused)({"question": "  "})["ok"] is False


def test_schemas_are_accepted_by_the_gemini_converter() -> None:
    for spec in (FILING_SEARCH, FINANCIAL_SQL, CALCULATOR):
        assert to_gemini_schema(spec.parameters)["type"].upper() == "OBJECT"
        assert spec.description and "required" in spec.parameters

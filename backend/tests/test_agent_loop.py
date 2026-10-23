from typing import Any

from app.agent.loop import Agent, AgentConfig
from app.llm.types import LLMError
from app.retrieval.types import Filters, Hit
from app.tools.builder import ToolBundle, new_bundle
from app.tools.sql_exec import ExecResult
from app.tools.text_to_sql import SqlAnswer
from tests.fake_llm import ScriptedLLM, call, say


class FakeRetriever:
    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        return [
            Hit(7, 0.9, "Apple faces supply risks.", "AAPL", "Apple", 2024, "Item 1A", 9, "acc")
        ]


class FakeT2S:
    def run(self, question: str) -> SqlAnswer:
        res = ExecResult(["value"], [[391035000000]], 1, False, "SELECT 1 LIMIT 50", 2)
        return SqlAnswer(True, "SELECT 1 LIMIT 50", res)


def make() -> ToolBundle:
    return new_bundle(FakeRetriever(), FakeT2S())  # type: ignore[arg-type]


def agent(script: list[Any], **cfg: Any) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(script)
    return Agent(llm, make, "AAPL FY2022-2024", AgentConfig(**cfg)), llm


def test_direct_answer_without_tools() -> None:
    a, llm = agent([say("Hello [c1]")])
    r = a.run("hi")
    assert r.status == "refused" and r.reason == "no_evidence"  # an answer needs tool evidence
    assert len(r.steps) == 1
    assert llm.requests[0]["tools"] and "AAPL FY2022-2024" in str(llm.requests[0]["system"])


def test_tool_round_trip_is_fed_back_and_traced() -> None:
    a, llm = agent(
        [
            call("financial_sql", question="Apple FY2024 revenue"),
            call("calculator", expression="pct_change(100, 110)"),
            say("Revenue was $391.0 billion [sql1]."),
        ]
    )
    r = a.run("Apple revenue?")
    assert r.status == "answered" and len(r.steps) == 3
    assert [t.name for s in r.steps for t in s.tools] == ["financial_sql", "calculator"]
    assert r.steps[0].tools[0].summary == "1 rows" and r.steps[1].tools[0].summary == "= 10.0"
    third = llm.requests[2]["messages"]
    assert [m.role for m in third] == ["user", "model", "tool", "model", "tool"]  # type: ignore[attr-defined]
    assert third[2].tool_results[0].content["rows"] == [[391035000000]]  # type: ignore[index]
    assert r.usage.prompt_tokens == 300 and r.models == ["fake"]


def test_bad_tool_calls_are_reported_to_the_model_not_raised() -> None:
    a, llm = agent([call("nope", x=1), call("calculator", expression=5), say("Cannot.")])
    r = a.run("q")
    assert len(r.steps) == 3
    assert [t.ok for s in r.steps for t in s.tools] == [False, False]


def test_step_cap_forces_a_final_answer_without_tools() -> None:
    script = [call("filing_search", query=f"q{i}") for i in range(3)] + [say("Done [c1]")]
    a, llm = agent(script, max_steps=3)
    r = a.run("q")
    assert "step_limit_reached" in r.flags and r.answer == "Done [c1]"
    assert len(r.steps) == 4 and llm.requests[-1]["tools"] is None
    assert "limit on tool use" in llm.requests[-1]["messages"][-1].text  # type: ignore[index]


def test_token_budget_stops_tool_use() -> None:
    a, llm = agent([call("calculator", tokens=500, expression="1+1"), say("ok")], token_budget=400)
    r = a.run("q")
    assert "token_budget_exceeded" in r.flags and len(r.steps) == 2


def test_extra_parallel_calls_still_get_a_response_each() -> None:
    from app.llm.types import LLMResponse, Message, ToolCall, Usage

    calls = [ToolCall(f"i{i}", "calculator", {"expression": "1+1"}) for i in range(6)]
    first = LLMResponse("", calls, Usage(1, 1), "fake", 0.0, message=Message("model", "", calls))
    a, llm = agent([first, say("fine")], max_calls_per_step=4)
    r = a.run("q")
    tool_msg = llm.requests[1]["messages"][2]  # type: ignore[index]
    assert len(tool_msg.tool_results) == 6
    assert [t.ok for t in r.steps[0].tools] == [True] * 4 + [False] * 2


def test_declined_and_empty_answers() -> None:
    r = agent([say("INSUFFICIENT_EVIDENCE: Tesla is not covered.")])[0].run("Tesla revenue?")
    assert r.status == "refused" and r.answer == "Tesla is not covered."
    assert r.reason == "model_declined"
    e = agent([say("   ")])[0].run("q")
    assert e.status == "error" and e.reason == "empty_answer"


def test_llm_failure_returns_error_result() -> None:
    r = agent([LLMError("all models failed")])[0].run("q")
    assert r.status == "error" and r.reason == "llm_unavailable"
    assert "unavailable" in r.answer


def test_ungrounded_number_triggers_one_repair_that_can_use_tools() -> None:
    a, llm = agent(
        [
            call("financial_sql", question="Apple FY2024 revenue"),
            say("Revenue was $400 billion [sql1]."),  # invented
            call("calculator", expression="391035000000 / 1000000000"),
            say("Revenue was $391.0 billion [sql1]."),
        ]
    )
    r = a.run("Apple revenue?")
    assert r.status == "answered" and r.answer == "Revenue was $391.0 billion [sql1]."
    assert "number_repair_attempted" in r.flags and "ungrounded_numbers" not in r.flags
    assert "$400 billion" in llm.requests[2]["messages"][-1].text  # type: ignore[index]


def test_persistent_ungrounded_number_is_flagged_not_hidden() -> None:
    a, _ = agent(
        [
            call("financial_sql", question="q"),
            say("Revenue was $400 billion [sql1]."),
            say("Revenue was $400 billion [sql1]."),
        ]
    )
    r = a.run("q")
    assert r.status == "answered" and r.ungrounded == ["$400 billion"]
    assert "ungrounded_numbers" in r.flags


def test_invented_calculator_input_is_caught() -> None:
    a, _ = agent(
        [
            call("financial_sql", question="q"),
            call("calculator", expression="pct_change(391035000000, 450000000000)"),
            say("Up 15.1% [sql1]."),
            say("Up 15.1% [sql1]."),
        ]
    )
    r = a.run("q")
    assert r.ungrounded == ["450000000000"]


def test_refusals_skip_the_number_check() -> None:
    r = agent([say("INSUFFICIENT_EVIDENCE: no 2019 data, only 2022-2024.")])[0].run("2019?")
    assert r.status == "refused" and not r.flags


def test_citations_are_resolved_to_objects() -> None:
    a, _ = agent(
        [
            call("filing_search", query="risks", ticker="AAPL"),
            call("financial_sql", question="revenue"),
            say("Supply risks exist [c1]. Revenue was $391.0 billion [sql1, c1]."),
        ]
    )
    r = a.run("q")
    assert r.status == "answered" and [c["id"] for c in r.citations] == ["c1", "sql1"]
    assert r.citations[0]["type"] == "filing" and r.citations[0]["label"].startswith("AAPL FY2024")
    assert r.citations[1]["type"] == "sql" and r.citations[1]["rows"] == [[391035000000]]


def test_invented_citation_ids_are_removed_and_flagged() -> None:
    a, _ = agent([call("filing_search", query="r"), say("Risk exists [c1] and more [c9].")])
    r = a.run("q")
    assert r.answer == "Risk exists [c1] and more." and "invalid_citation_removed" in r.flags
    assert [c["id"] for c in r.citations] == ["c1"]


def test_uncited_answer_gets_one_repair_then_refusal() -> None:
    a, llm = agent([call("filing_search", query="r"), say("Risk exists."), say("Risk exists.")])
    r = a.run("q")
    assert r.status == "refused" and r.reason == "uncited"
    assert "citation_repair_attempted" in r.flags and len(llm.script) == 0
    fixed, _ = agent([call("filing_search", query="r"), say("Risk exists."), say("Risk [c1].")])
    assert fixed.run("q").status == "answered"


def test_answer_without_any_tool_evidence_is_refused() -> None:
    r = agent([say("Apple revenue is huge [c1].")])[0].run("q")
    assert r.status == "refused" and r.reason == "no_evidence" and r.citations == []
    only_calc = agent([call("calculator", expression="1+1"), say("It is 2 [c1].")])[0].run("q")
    assert only_calc.status == "refused" and only_calc.reason == "no_evidence"


def test_empty_search_results_are_not_evidence() -> None:
    class Empty:
        def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
            return []

    llm = ScriptedLLM([call("filing_search", query="r"), say("Something [c1].")])
    ag = Agent(llm, lambda: new_bundle(Empty(), FakeT2S()), "cov")  # type: ignore[arg-type]
    assert ag.run("q").reason == "no_evidence"

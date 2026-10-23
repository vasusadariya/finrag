import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.agent.loop import Agent
from app.agent.types import AgentResult
from app.evalkit.agent_eval import (
    citation_hit,
    judge,
    numbers_match,
    render_report,
    run_questions,
    score,
    summarize,
)
from app.evalkit.dataset import GoldSource, Question
from app.llm.types import LLMError
from tests.fake_llm import ScriptedLLM, call, say
from tests.test_agent_loop import make


@pytest.mark.parametrize(
    "gold, answer, ok",
    [
        ("R&D: $31.37 billion in FY2024 vs $34.55 billion in FY2025 (+10.1%).",
         "R&D rose from $31.4 billion to $34.6 billion, up 10.1%.", True),
        ("R&D: $31.37 billion in FY2024 vs $34.55 billion (+10.1%).",
         "R&D rose from $31.37 billion to $34.55 billion, a 9.8% rise.", False),
        ("Net income $88.14 billion.", "Net income was $88.1 billion.", True),
        ("Net income $88.14 billion.", "Net income was $98.1 billion.", False),
        ("Cupertino, California.", "Cupertino.", False),  # no numbers in gold -> not numeric
        ("Margin was 46.8%.", "The operating margin was 46.8 percent.", True),
    ],
)  # fmt: skip
def test_numbers_match(gold: str, answer: str, ok: bool) -> None:
    assert numbers_match(gold, answer) is ok


GOLD = dict(ticker="AAPL", fiscal_year=2024, section="Item 2", contains="Cupertino")


def question(**kw: Any) -> Question:
    base: dict[str, Any] = dict(
        id="q001", question="Where is Apple headquartered?", type="factual", tickers=["AAPL"],
        gold_sources=[GOLD],
        gold_answer="Cupertino, California.", tools=["filing_search"],
    )  # fmt: skip
    return Question(**{**base, **kw})


def test_citation_hit_requires_same_company_year_section_and_phrase() -> None:
    q = question()
    rec = {"citations": [dict(type="filing", chunk_id=1, ticker="AAPL", fiscal_year=2024,
                              section="Item 2")]}  # fmt: skip
    assert citation_hit(q, rec, {1: "HQ is in Cupertino, California"}) is True
    assert citation_hit(q, rec, {1: "HQ is somewhere else"}) is False
    rec["citations"][0]["fiscal_year"] = 2023
    assert citation_hit(q, rec, {1: "Cupertino"}) is False
    refs = [dict(ticker="AAPL", concept="x", fiscal_year=y, value=1.0) for y in (2023, 2024)]
    numeric = question(gold_sources=[], xbrl_refs=refs, type="trend", tools=["financial_sql"])
    assert citation_hit(numeric, rec, {}) is None


def test_judge_parses_json_and_survives_garbage() -> None:
    assert judge(ScriptedLLM([say('ok {"correct": true, "reason": "same"} done')]), "p") == {
        "correct": True, "reason": "same",
    }  # fmt: skip
    assert judge(ScriptedLLM([say("no json here")]), "p") is None
    assert judge(ScriptedLLM([say("{broken")]), "p") is None
    assert judge(ScriptedLLM([LLMError("down")]), "p") is None


class FakeService:
    def __init__(self, scripts: dict[str, list[Any]]) -> None:
        self.scripts, self.calls = scripts, []  # type: ignore[var-annotated]

    def run(self, question: str, on_event: Any = None) -> AgentResult:
        self.calls.append(question)
        return Agent(ScriptedLLM(list(self.scripts[question])), make, "cov").run(question)

    def health(self) -> dict[str, bool]:
        return {}


def test_run_questions_resumes_and_survives_failures(tmp_path: Path) -> None:
    q1, q2 = question(), question(id="q002", question="Where is Microsoft headquartered?")
    svc = FakeService({q1.question: [call("filing_search", query="hq"), say("Cupertino [c1].")]})
    out = tmp_path / "runs.jsonl"
    recs = run_questions(svc, [q1, q2], out)  # q2 has no script -> KeyError -> error record
    assert [r["status"] for r in recs] == ["answered", "error"]
    assert recs[0]["tools"] == ["filing_search"] and recs[0]["citations"][0]["chunk_id"] == 7
    again = run_questions(svc, [q1, q2], out)  # resume: q1 is kept, the errored q2 is retried
    assert svc.calls.count(q1.question) == 1 and svc.calls.count(q2.question) == 2
    assert [r["status"] for r in again] == ["answered", "error"]


def test_score_and_summary(conn: psycopg.Connection[dict[str, object]]) -> None:
    conn.execute(
        "INSERT INTO companies (cik, ticker, name) VALUES ('0000320193', 'AAPL', 'Apple Inc.')"
    )
    qa = question()
    qn = question(
        id="q002", question="How did Apple net sales change?", type="trend",
        gold_sources=[], gold_answer="Net sales $391.04 billion.", tools=["financial_sql"],
        xbrl_refs=[dict(ticker="AAPL", concept="Revenues", fiscal_year=2024, value=391.04e9),
                   dict(ticker="AAPL", concept="Revenues", fiscal_year=2023, value=383.29e9)],
    )  # fmt: skip
    qu = question(id="q003", question="What will Apple earn in 2030?", type="unanswerable",
                  gold_sources=[], gold_answer=None, unanswerable_reason="future")  # fmt: skip
    base = dict(flags=[], ungrounded=[], tools=[], tool_ok=[], n_steps=1, latency_s=2.0, tokens=100,
                cost_usd=0.01, models=["m"], sql_verdicts=[], reason=None)  # fmt: skip
    recs = [
        {**base, "id": "q001", "type": "factual", "status": "answered", "answer": "Cupertino.",
         "citations": []},
        {**base, "id": "q002", "type": "trend", "status": "answered",
         "answer": "Net sales were $391.0 billion.", "citations": [],
         "sql_verdicts": ["rejected", "ok"]},
        {**base, "id": "q003", "type": "unanswerable", "status": "refused", "answer": "no",
         "citations": []},
    ]  # fmt: skip
    llm = ScriptedLLM([
        say('{"correct": true, "reason": "same"}'), say('{"faithful": true, "unsupported": []}'),
        say('{"faithful": false, "unsupported": ["x"]}'),
    ])  # fmt: skip
    scored = score([qa, qn, qu], recs, conn, llm)
    assert scored[0]["correct"] is True and scored[0]["correct_by"] == "judge"
    assert scored[1]["correct"] is True and scored[1]["correct_by"] == "numbers"
    assert scored[2]["refused"] is True and "faithful" not in scored[2]
    s = summarize(scored)
    assert s["refusal_on_unanswerable"] == 1.0 and s["false_refusal_on_answerable"] == 0.0
    assert s["sql_attempts"] == 2 and s["sql_blocked_by_guard"] == 1
    assert s["faithful"] == 0.5 and s["answer_correct"] == 1.0
    assert s["latency_p50"] == 2.0 and s["cost_per_query_usd"] == pytest.approx(0.01)
    report = render_report(s, scored, "T")
    assert "| refused when unanswerable | 100% |" in report and "## By question type" in report
    json.dumps(scored, default=str)


def test_gold_source_model_is_unchanged() -> None:
    assert GoldSource(ticker="AAPL", fiscal_year=2024, section="Item 2", contains="abcd")

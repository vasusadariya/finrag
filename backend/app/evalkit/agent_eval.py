"""End-to-end agent evaluation (variant D): run questions, score answers, write a report.

Running and scoring are separate steps so a flaky API run can be resumed and the scorer can be
re-run (and its judge prompts changed) without re-asking the agent. Records are JSONL, one
question per line.

Correctness: questions with XBRL reference numbers are scored by number matching against the gold
answer (no LLM involved); text questions by an LLM judge comparing with the gold answer.
Faithfulness (judge): every claim in the answer is supported by the passages / SQL rows it cites.
Citation correctness: a cited passage matches a gold source (same company, year, section and
phrase). Refusal: unanswerable questions should be refused, answerable ones should not.
"""

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg

from app.agent.cost import run_cost
from app.agent.grounding import Num, extract_numbers
from app.agent.types import AgentResult
from app.api.service import AnswerService
from app.evalkit.dataset import Question
from app.llm.types import LLMClient, LLMError, Message


def record_for(q: Question, r: AgentResult) -> dict[str, Any]:
    return {
        "id": q.id,
        "type": q.type,
        "status": r.status,
        "reason": r.reason,
        "answer": r.answer,
        "flags": r.flags,
        "ungrounded": r.ungrounded,
        "citations": r.citations,
        "tools": [t.name for s in r.steps for t in s.tools],
        "tool_ok": [t.ok for s in r.steps for t in s.tools],
        "n_steps": len(r.steps),
        "latency_s": round(r.latency_s, 2),
        "tokens": r.usage.total + r.sql_usage.total,
        "cost_usd": round(run_cost(r), 6),
        "models": r.models,
        "sql_verdicts": r.sql_verdicts,
    }


def error_record(q: Question, e: Exception) -> dict[str, Any]:
    return {
        "id": q.id, "type": q.type, "status": "error", "reason": type(e).__name__, "answer": "",
        "flags": [], "citations": [], "tools": [], "latency_s": 0.0, "tokens": 0, "cost_usd": 0.0,
        "sql_verdicts": [], "ungrounded": [],
    }  # fmt: skip


def run_questions(
    service: AnswerService, questions: list[Question], out: Path, *, resume: bool = True
) -> list[dict[str, Any]]:
    """Run each question once, appending records to ``out``; already-done ids are skipped."""
    done: dict[str, dict[str, Any]] = {}
    if resume and out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec["status"] != "error":  # infrastructure failures are retried on resume
                    done[rec["id"]] = rec
                else:
                    done.pop(rec["id"], None)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        for q in questions:
            if q.id in done:
                continue
            try:
                rec = record_for(q, service.run(q.question))
            except Exception as e:  # noqa: BLE001 - one failure must not stop the whole run
                rec = error_record(q, e)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            done[q.id] = rec
    return [done[q.id] for q in questions if q.id in done]


# ---- scoring --------------------------------------------------------------------------------


def numbers_match(gold: str, answer: str, *, ignore_percent: bool = False) -> bool:
    """Every non-trivial number in the gold answer appears in the answer (within rounding).

    ``ignore_percent`` drops percentages from the gold: trend/cross-year golds append a derived
    "overall change" the question did not necessarily ask for.
    """
    gold_nums = [
        n for n in extract_numbers(gold) if not _trivial(n) and not (ignore_percent and n.percent)
    ]
    ans_nums = extract_numbers(answer)
    if not gold_nums:
        return False

    def close(g: Num, a: Num) -> bool:
        return abs(abs(g.value) - abs(a.value)) <= max(g.half, a.half) * (1 + 1e-9)

    return all(any(close(g, a) for a in ans_nums) for g in gold_nums)


def _trivial(n: Num) -> bool:
    v = abs(n.value)
    return v == int(v) and (v <= 10 or 1900 <= v <= 2100) and not n.percent


def _chunk_texts(conn: psycopg.Connection[dict[str, object]], ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = conn.execute("SELECT id, content FROM chunks WHERE id = ANY(%s)", (ids,)).fetchall()
    return {int(r["id"]): str(r["content"]) for r in rows}  # type: ignore[call-overload]


def citation_hit(q: Question, rec: dict[str, Any], texts: dict[int, str]) -> bool | None:
    """Did a cited passage match a gold source? None when the question has no text gold."""
    if not q.gold_sources:
        return None
    for c in rec["citations"]:
        if c.get("type") != "filing":
            continue
        text = texts.get(c["chunk_id"], "")
        for g in q.gold_sources:
            if (
                c["ticker"] == g.ticker
                and c["fiscal_year"] == g.fiscal_year
                and c["section"] == g.section
                and g.contains.lower() in text.lower()
            ):
                return True
    return False


CORRECT_PROMPT = """You grade an answer against a reference answer for a question about SEC filings.
Question: {question}
Reference answer: {gold}
Candidate answer: {answer}

The candidate is correct if it conveys the same facts as the reference (extra correct detail and
different wording are fine; wrong or missing key facts are not). Reply with JSON only:
{{"correct": true or false, "reason": "<one short sentence>"}}"""

FAITHFUL_PROMPT = """You check whether an answer is supported by the evidence it cites.
Question: {question}
Answer: {answer}

Evidence:
{evidence}

The answer is faithful if every factual claim in it is supported by the evidence above. Reply with
JSON only: {{"faithful": true or false, "unsupported": ["<claim not supported>", ...]}}"""


def judge(llm: LLMClient, prompt: str) -> dict[str, Any] | None:
    try:
        resp = llm.generate([Message("user", prompt)], tier="cheap", temperature=0.0,
                            max_output_tokens=400)  # fmt: skip
    except LLMError:
        return None
    m = re.search(r"\{.*\}", resp.text, re.DOTALL)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def evidence_text(rec: dict[str, Any], texts: dict[int, str]) -> str:
    parts = []
    for c in rec["citations"]:
        if c.get("type") == "filing":
            parts.append(f"[{c['id']}] {c['label']}: {texts.get(c['chunk_id'], '')[:1500]}")
        else:
            parts.append(f"[{c['id']}] SQL {c['sql']} -> columns {c['columns']} rows {c['rows']}")
    return "\n".join(parts) or "(no evidence)"


def score(
    questions: list[Question],
    records: list[dict[str, Any]],
    conn: psycopg.Connection[dict[str, object]],
    llm: LLMClient | None,
) -> list[dict[str, Any]]:
    by_id = {q.id: q for q in questions}
    all_ids = [c["chunk_id"] for r in records for c in r["citations"] if c.get("type") == "filing"]
    texts = _chunk_texts(conn, list(set(all_ids)))
    scored = []
    for rec in records:
        q = by_id[rec["id"]]
        s: dict[str, Any] = {**rec, "answerable": q.answerable}
        answered = rec["status"] == "answered"
        s["refused"] = rec["status"] == "refused"
        if q.answerable and answered and q.gold_answer:
            if q.xbrl_refs:
                s["correct"] = numbers_match(q.gold_answer, rec["answer"])
                s["correct_by"] = "numbers"
                s["correct_values"] = numbers_match(
                    q.gold_answer, rec["answer"], ignore_percent=True
                )
            elif llm is not None:
                j = judge(llm, CORRECT_PROMPT.format(question=q.question, gold=q.gold_answer,
                                                     answer=rec["answer"]))  # fmt: skip
                s["correct"] = None if j is None else bool(j.get("correct"))
                s["correct_by"] = "judge"
                s["judge_reason"] = None if j is None else j.get("reason")
            s["citation_hit"] = citation_hit(q, rec, texts)
        if answered and llm is not None:
            j = judge(llm, FAITHFUL_PROMPT.format(question=q.question, answer=rec["answer"],
                                                  evidence=evidence_text(rec, texts)))  # fmt: skip
            s["faithful"] = None if j is None else bool(j.get("faithful"))
            s["unsupported"] = [] if j is None else j.get("unsupported", [])
        scored.append(s)
    return scored


# ---- aggregation / report -------------------------------------------------------------------


def _rate(xs: list[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _pctl(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))] if s else 0.0


def summarize(scored: list[dict[str, Any]]) -> dict[str, Any]:
    ans = [s for s in scored if s["answerable"]]
    unans = [s for s in scored if not s["answerable"]]
    correct = [s["correct"] for s in ans if s.get("correct") is not None]
    attempts = [v for s in scored for v in s["sql_verdicts"]]
    lat = [s["latency_s"] for s in scored if s["status"] != "error"]
    by_type: dict[str, dict[str, float | int | None]] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in scored:
        groups[s["type"]].append(s)
    for t, g in groups.items():
        if t == "unanswerable":
            by_type[t] = {"n": len(g), "refused": _rate([x["refused"] for x in g])}
        else:
            by_type[t] = {
                "n": len(g),
                "correct": _rate([bool(x.get("correct")) for x in g]),
                "refused": _rate([x["refused"] for x in g]),
            }
    return {
        "n": len(scored),
        "errors": sum(1 for s in scored if s["status"] == "error"),
        "answer_correct": _rate([bool(c) for c in correct]),
        "answer_correct_of_all_answerable": _rate([bool(s.get("correct")) for s in ans]),
        "numeric_values_only": _rate(
            [bool(s["correct_values"]) for s in ans if "correct_values" in s]
        ),
        "numeric_strict": _rate(
            [bool(s["correct"]) for s in ans if s.get("correct_by") == "numbers"]
        ),
        "faithful": _rate([bool(s["faithful"]) for s in scored if s.get("faithful") is not None]),
        "citation_hit": _rate(
            [bool(s["citation_hit"]) for s in ans if s.get("citation_hit") is not None]
        ),  # fmt: skip
        "refusal_on_unanswerable": _rate([s["refused"] for s in unans]),
        "false_refusal_on_answerable": _rate([s["refused"] for s in ans]),
        "ungrounded_numbers": _rate(
            [bool(s["ungrounded"]) for s in scored if s["status"] == "answered"]
        ),
        "sql_attempts": len(attempts),
        "sql_blocked_by_guard": sum(1 for v in attempts if v == "rejected"),
        "sql_db_errors": sum(1 for v in attempts if v in {"error", "timeout"}),
        "latency_p50": statistics.median(lat) if lat else 0.0,
        "latency_p95": _pctl(lat, 0.95),
        "cost_per_query_usd": statistics.mean([s["cost_usd"] for s in scored]) if scored else 0.0,
        "tokens_per_query": statistics.mean([s["tokens"] for s in scored]) if scored else 0.0,
        "by_type": by_type,
    }


def render_report(summary: dict[str, Any], scored: list[dict[str, Any]], title: str) -> str:
    s = summary
    rows = [
        ("answer correct (of answered answerable)", _pct(s["answer_correct"])),
        ("answer correct (of all answerable; a refusal counts as wrong)",
         _pct(s["answer_correct_of_all_answerable"])),
        ("numeric questions: all gold numbers incl. derived % (strict)", _pct(s["numeric_strict"])),
        ("numeric questions: gold base values only (percentages ignored)",
         _pct(s["numeric_values_only"])),
        ("faithful to cited evidence (LLM judge)", _pct(s["faithful"])),
        ("cited passage matches a gold source", _pct(s["citation_hit"])),
        ("refused when unanswerable", _pct(s["refusal_on_unanswerable"])),
        ("refused when answerable (false refusal)", _pct(s["false_refusal_on_answerable"])),
        ("answers with a number no tool supports", _pct(s["ungrounded_numbers"])),
        ("SQL attempts / blocked by guard / DB errors",
         f"{s['sql_attempts']} / {s['sql_blocked_by_guard']} / {s['sql_db_errors']}"),
        ("latency p50 / p95 (s)", f"{s['latency_p50']:.1f} / {s['latency_p95']:.1f}"),
        ("shadow cost per query (USD, assumed list prices)", f"{s['cost_per_query_usd']:.4f}"),
        ("tokens per query", f"{s['tokens_per_query']:.0f}"),
    ]  # fmt: skip
    lines = [
        f"# {title}",
        "",
        f"{s['n']} questions ({s['errors']} infrastructure errors).",
        "",
        "| metric | value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in rows],
        "",
        "## By question type",
        "",
        "| type | n | correct | refused |",
        "|---|---|---|---|",
    ]
    for t, d in sorted(s["by_type"].items()):
        lines.append(f"| {t} | {d['n']} | {_pct(d.get('correct'))} | {_pct(d.get('refused'))} |")
    bad = [x for x in scored if x["answerable"] and x.get("correct") is False]
    wrong_refusals = [x for x in scored if x["answerable"] and x["refused"]]
    leaked = [x for x in scored if not x["answerable"] and x["status"] == "answered"]
    for heading, items in (
        ("Wrong answers", bad),
        ("Answerable questions that were refused", wrong_refusals),
        ("Unanswerable questions that were answered", leaked),
    ):
        lines += ["", f"## {heading} ({len(items)})", ""]
        for x in items:
            note = x.get("judge_reason") or x.get("reason") or ""
            lines.append(f"- {x['id']} ({x['type']}): {x['answer'][:160]!r} {note}")
    return "\n".join(lines) + "\n"


def dump_details(path: Path, scored: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps([asdict_safe(s) for s in scored], indent=1))


def asdict_safe(x: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(x, default=str))
    return out

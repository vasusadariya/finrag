"""Eval dataset schema and validation (structure, then resolution against the database)."""

import json
from collections import Counter
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel, Field, field_validator, model_validator

from app.retrieval.types import SECTIONS, Filters, Hit

QuestionType = Literal["factual", "trend", "cross_year", "multi_hop", "unanswerable"]
UnanswerableReason = Literal["out_of_scope", "future", "not_in_filings", "advice"]
Tool = Literal["filing_search", "financial_sql", "calculator"]

TICKERS = {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "XOM"}

# Minimum shape of a useful eval set (checked by validate_set).
MIN_QUESTIONS = 50
MIN_MULTI_HOP = 15
MIN_UNANSWERABLE = 10


class GoldSource(BaseModel):
    """A passage that answers (part of) the question: where it is and a phrase it must contain."""

    ticker: str
    fiscal_year: int
    section: str
    contains: str = Field(min_length=4)  # case-insensitive substring of the gold chunk's text
    page: int | None = None

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        if v not in TICKERS:
            raise ValueError(f"unknown ticker {v}")
        return v

    @field_validator("section")
    @classmethod
    def _section(cls, v: str) -> str:
        if v not in SECTIONS:
            raise ValueError(f"unknown section {v}")
        return v

    def matches(self, hit: Hit) -> bool:
        return (
            hit.ticker == self.ticker
            and hit.fiscal_year == self.fiscal_year
            and hit.section == self.section
            and self.contains.lower() in hit.content.lower()
        )


class XbrlRef(BaseModel):
    """A number that must equal the stored XBRL fact (verifies numeric gold answers)."""

    ticker: str
    concept: str
    fiscal_year: int
    value: float


class Question(BaseModel):
    id: str = Field(pattern=r"^q\d{3}$")
    question: str = Field(min_length=10)
    type: QuestionType
    tickers: list[str] = []  # companies the question is about (used as retrieval filters)
    gold_sources: list[GoldSource] = []
    gold_answer: str | None = None
    xbrl_refs: list[XbrlRef] = []
    tools: list[Tool] = []  # tools a good agent would use
    unanswerable_reason: UnanswerableReason | None = None
    notes: str = ""

    @property
    def answerable(self) -> bool:
        return self.type != "unanswerable"

    def filters(self) -> Filters:
        return Filters(tickers=tuple(self.tickers))

    @model_validator(mode="after")
    def _consistent(self) -> "Question":
        unknown = [t for t in self.tickers if t not in TICKERS]
        if unknown:
            raise ValueError(f"unknown tickers {unknown}")
        if self.type == "unanswerable":
            if self.gold_sources or self.gold_answer or self.xbrl_refs:
                raise ValueError("unanswerable questions have no gold sources/answer/xbrl_refs")
            if not self.unanswerable_reason:
                raise ValueError("unanswerable questions need unanswerable_reason")
            return self
        if self.unanswerable_reason:
            raise ValueError("answerable question has unanswerable_reason")
        if not self.gold_answer:
            raise ValueError("answerable questions need gold_answer")
        if not self.gold_sources and not self.xbrl_refs:
            raise ValueError("answerable questions need gold_sources or xbrl_refs")
        if self.type == "multi_hop" and len(self.gold_sources) + len(self.xbrl_refs) < 2:
            raise ValueError("multi_hop needs at least two pieces of evidence")
        if self.type == "cross_year":
            years = {s.fiscal_year for s in self.gold_sources} | {
                r.fiscal_year for r in self.xbrl_refs
            }
            if len(years) < 2:
                raise ValueError("cross_year evidence must span at least two fiscal years")
        if self.type == "trend" and len(self.xbrl_refs) < 2 and len(self.gold_sources) < 2:
            raise ValueError("trend needs at least two data points")
        return self


def load_questions(path: str | Path) -> list[Question]:
    out: list[Question] = []
    for n, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(Question.model_validate(json.loads(line)))
        except Exception as e:
            raise ValueError(f"{path}:{n}: {e}") from e
    return out


def validate_set(questions: list[Question]) -> list[str]:
    """Whole-set checks that need no database. Returns a list of problems."""
    problems: list[str] = []
    ids = Counter(q.id for q in questions)
    problems += [f"duplicate id {i}" for i, c in ids.items() if c > 1]
    texts = Counter(q.question.strip().lower() for q in questions)
    problems += [f"duplicate question text: {t[:50]!r}" for t, c in texts.items() if c > 1]
    by_type = Counter(q.type for q in questions)
    if len(questions) < MIN_QUESTIONS:
        problems.append(f"only {len(questions)} questions, need >= {MIN_QUESTIONS}")
    if by_type["multi_hop"] < MIN_MULTI_HOP:
        problems.append(f"only {by_type['multi_hop']} multi_hop, need >= {MIN_MULTI_HOP}")
    if by_type["unanswerable"] < MIN_UNANSWERABLE:
        problems.append(f"only {by_type['unanswerable']} unanswerable, need >= {MIN_UNANSWERABLE}")
    for t in ("factual", "trend", "cross_year"):
        if by_type[t] == 0:
            problems.append(f"no {t} questions")
    return problems


def validate_against_db(
    conn: psycopg.Connection[dict[str, object]], qs: list[Question]
) -> list[str]:
    """Every gold source must resolve to a real chunk, every XBRL ref to a matching fact."""
    problems: list[str] = []
    for q in qs:
        for i, s in enumerate(q.gold_sources):
            row = conn.execute(
                "SELECT count(*) AS n FROM chunks c JOIN companies co ON co.id = c.company_id"
                " WHERE co.ticker = %s AND c.fiscal_year = %s AND c.section = %s"
                " AND c.content ILIKE %s",
                (s.ticker, s.fiscal_year, s.section, f"%{s.contains}%"),
            ).fetchone()
            if not row or int(row["n"]) == 0:  # type: ignore[call-overload]
                problems.append(
                    f"{q.id} source[{i}] matches no chunk: {s.ticker} FY{s.fiscal_year} "
                    f"{s.section} {s.contains!r}"
                )
        for i, r in enumerate(q.xbrl_refs):
            rows = conn.execute(
                "SELECT x.value::float8 AS v FROM xbrl_facts x"
                " JOIN companies co ON co.id = x.company_id"
                " WHERE co.ticker = %s AND x.concept = %s AND x.fiscal_year = %s",
                (r.ticker, r.concept, r.fiscal_year),
            ).fetchall()
            values = [float(x["v"]) for x in rows]  # type: ignore[arg-type]
            if not values:
                problems.append(
                    f"{q.id} xbrl_ref[{i}] has no fact: {r.ticker} {r.concept} FY{r.fiscal_year}"
                )
            elif not any(abs(v - r.value) <= max(1e-9, abs(r.value) * 1e-9) for v in values):
                problems.append(f"{q.id} xbrl_ref[{i}] value {r.value} != stored {values}")
    return problems

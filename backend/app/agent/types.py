"""Agent run records: per-step trace and the final result."""

from dataclasses import dataclass, field
from typing import Any, Literal

from app.llm.types import Usage

Status = Literal["answered", "refused", "error"]


@dataclass
class ToolTrace:
    id: str
    name: str
    args: dict[str, Any]
    ok: bool
    summary: str  # short, safe description of the result (never the full payload)
    latency_s: float


@dataclass
class Step:
    index: int
    model: str
    text: str
    tools: list[ToolTrace]
    usage: Usage
    latency_s: float
    cached: bool = False


@dataclass
class AgentResult:
    question: str
    status: Status
    answer: str  # final text shown to the user (for refusals: the refusal message)
    reason: str | None = None  # machine-readable refusal/error reason
    citations: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # quality warnings, e.g. ungrounded_numbers
    steps: list[Step] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    models: list[str] = field(default_factory=list)
    sql_usage: Usage = field(default_factory=Usage)  # tokens spent inside the financial_sql tool
    ungrounded: list[str] = field(default_factory=list)  # numbers no tool output supports
    evidence: list[dict[str, Any]] = field(default_factory=list, repr=False)  # raw tool outputs

"""The agent loop: plan -> call tools -> observe, with step cap, token budget and a full trace."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.agent.citations import (
    build_citations,
    cited_ids,
    has_evidence,
    known_ids,
    strip_unknown,
)
from app.agent.grounding import (
    find_ungrounded,
    find_ungrounded_inputs,
    source_numbers,
)
from app.agent.prompt import REFUSAL_PREFIX, system_prompt
from app.agent.types import AgentResult, Step, ToolTrace
from app.llm.types import LLMClient, LLMError, LLMResponse, Message, ToolCall, ToolResult, Usage
from app.tools.builder import ToolBundle

log = logging.getLogger(__name__)

FORCE_ANSWER = (
    "You have reached the limit on tool use. Do not call any more tools. Answer now using only "
    "the evidence gathered so far, citing it. If it is not enough, reply with "
    f"{REFUSAL_PREFIX} and what is missing."
)


REPAIR_NUMBERS = (
    "These numbers in your answer do not appear in any tool result: {numbers}. Every number must "
    "come from a tool. Fetch or calculate them with the tools, or remove them, then give the "
    "corrected final answer (keep the citations)."
)


REPAIR_CITATIONS = (
    "Your answer has no valid citation. Cite each claim with the citation ids returned by the "
    "tools, like [c1] or [sql1]. If you have no tool evidence for it, call a tool first, or reply "
    f"with {REFUSAL_PREFIX} and what is missing."
)
NO_EVIDENCE_MESSAGE = (
    "I could not find supporting evidence for this in the filings I have, so I won't guess."
)
UNCITED_MESSAGE = "I could not tie an answer to a source in the filings, so I won't state one."


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 6  # model turns that may call tools
    max_calls_per_step: int = 4
    token_budget: int = 60_000  # total (prompt + output + thinking) tokens across the run
    max_output_tokens: int = 2048
    repair_steps: int = 3  # tool steps allowed in the one correction round


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.prompt_tokens + b.prompt_tokens,
        a.output_tokens + b.output_tokens,
        a.thinking_tokens + b.thinking_tokens,
    )


def summarize(result: ToolResult) -> tuple[bool, str]:
    c = result.content
    if not c.get("ok", False):
        return False, str(c.get("error", "failed"))[:200]
    if "results" in c:
        return True, f"{c.get('count', len(c['results']))} passages"
    if "rows" in c:
        return True, f"{c.get('row_count', len(c['rows']))} rows"
    if "result" in c:
        return True, f"= {c['result']}"
    return True, "ok"


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        make_tools: Callable[[], ToolBundle],
        coverage: str,
        config: AgentConfig | None = None,
    ) -> None:
        self._llm, self._make_tools = llm, make_tools
        self._system = system_prompt(coverage)
        self._cfg = config or AgentConfig()

    def run(self, question: str) -> AgentResult:
        started = time.perf_counter()
        bundle = self._make_tools()
        result = AgentResult(question=question, status="error", answer="")
        messages = [Message("user", question)]
        final: str | None = None
        try:
            final = self._loop(messages, bundle, result)
        except LLMError as e:
            result.reason = "llm_unavailable"
            result.answer = "The language model is unavailable right now. Please try again."
            log.error("agent llm failure", extra={"error": str(e)})
        if final is not None:
            final = self._verify_citations(final, messages, bundle, result)
            final = self._verify_numbers(final, question, messages, bundle, result)
            self.finish(final, bundle, result)
        result.sql_usage = bundle.sql.usage
        result.latency_s = time.perf_counter() - started
        return result

    def _verify_citations(
        self, final: str, messages: list[Message], bundle: ToolBundle, result: AgentResult
    ) -> str:
        """Answers must cite sources the tools actually returned; one repair round, then refuse."""
        if final.strip().upper().startswith(REFUSAL_PREFIX) or not final.strip():
            return final
        known = known_ids(bundle)
        if not [i for i in cited_ids(final) if i in known] and has_evidence(result.evidence):
            messages.append(Message("user", REPAIR_CITATIONS))
            result.flags.append("citation_repair_attempted")
            try:
                final = self._loop(messages, bundle, result, self._cfg.repair_steps)
            except LLMError:
                pass
            known = known_ids(bundle)
        if final.strip().upper().startswith(REFUSAL_PREFIX):
            return final
        unknown = [i for i in cited_ids(final) if i not in known]
        if unknown:
            result.flags.append("invalid_citation_removed")
            final = strip_unknown(final, known)
        return final

    def _ungrounded(self, text: str, question: str, result: AgentResult) -> list[str]:
        if text.strip().upper().startswith(REFUSAL_PREFIX):
            return []
        sources = source_numbers([question, *result.evidence])
        bad = find_ungrounded(text, sources)
        for step in result.steps:  # calculator inputs must come from tools too
            for t in step.tools:
                if t.name == "calculator" and isinstance(t.args.get("expression"), str):
                    bad += find_ungrounded_inputs(t.args["expression"], sources)
        return list(dict.fromkeys(bad))

    def _verify_numbers(
        self,
        final: str,
        question: str,
        messages: list[Message],
        bundle: ToolBundle,
        result: AgentResult,
    ) -> str:
        """Check numbers against tool outputs; allow one correction round, then flag."""
        bad = self._ungrounded(final, question, result)
        if bad:
            messages.append(Message("user", REPAIR_NUMBERS.format(numbers=", ".join(bad))))
            result.flags.append("number_repair_attempted")
            try:
                final = self._loop(messages, bundle, result, self._cfg.repair_steps)
            except LLMError:
                pass  # keep the original answer; it gets flagged below
            bad = self._ungrounded(final, question, result)
        if bad:
            result.ungrounded = bad
            result.flags.append("ungrounded_numbers")
        return final

    def finish(self, final: str, bundle: ToolBundle, result: AgentResult) -> AgentResult:
        """Turn the model's final text into a result (hook for grounding/citation checks)."""
        text = final.strip()
        if not text:
            result.reason, result.answer = "empty_answer", "The model returned no answer."
            return result
        if text.upper().startswith(REFUSAL_PREFIX):
            result.status, result.reason = "refused", "model_declined"
            result.answer = text[len(REFUSAL_PREFIX) :].strip() or "Not enough evidence."
            return result
        if not has_evidence(result.evidence):
            result.status, result.reason = "refused", "no_evidence"
            result.answer = NO_EVIDENCE_MESSAGE
            return result
        ids = [i for i in cited_ids(text) if i in known_ids(bundle)]
        if not ids:
            result.status, result.reason = "refused", "uncited"
            result.answer = UNCITED_MESSAGE
            return result
        result.status, result.answer = "answered", text
        result.citations = build_citations(ids, bundle)
        return result

    def _generate(
        self, messages: list[Message], result: AgentResult, *, with_tools: bool, bundle: ToolBundle
    ) -> LLMResponse:
        t0 = time.perf_counter()
        resp = self._llm.generate(
            messages,
            system=self._system,
            tools=bundle.registry.specs() if with_tools else None,
            tier="main",
            temperature=0.0,
            max_output_tokens=self._cfg.max_output_tokens,
        )
        result.usage = _add(result.usage, resp.usage)
        if resp.model not in result.models:
            result.models.append(resp.model)
        step = Step(
            index=len(result.steps) + 1,
            model=resp.model,
            text=resp.text,
            tools=[],
            usage=resp.usage,
            latency_s=time.perf_counter() - t0,
            cached=resp.cached,
        )
        result.steps.append(step)
        messages.append(resp.message or Message("model", resp.text, tool_calls=resp.tool_calls))
        return resp

    def _run_tools(self, calls: list[ToolCall], bundle: ToolBundle, result: AgentResult) -> Message:
        step = result.steps[-1]
        results: list[ToolResult] = []
        for i, call in enumerate(calls):
            t0 = time.perf_counter()
            if i >= self._cfg.max_calls_per_step:
                res = ToolResult(call.id, call.name, {"ok": False, "error": "too many tool calls"})
            else:
                res = bundle.registry.call(call)
                result.evidence.append(res.content)
            ok, summary = summarize(res)
            step.tools.append(
                ToolTrace(call.id, call.name, call.args, ok, summary, time.perf_counter() - t0)
            )
            results.append(res)
        return Message("tool", tool_results=results)

    def _loop(
        self,
        messages: list[Message],
        bundle: ToolBundle,
        result: AgentResult,
        max_steps: int | None = None,
    ) -> str:
        for _ in range(max_steps or self._cfg.max_steps):
            resp = self._generate(messages, result, with_tools=True, bundle=bundle)
            if not resp.tool_calls:
                return resp.text
            messages.append(self._run_tools(resp.tool_calls, bundle, result))
            if result.usage.total > self._cfg.token_budget:
                result.flags.append("token_budget_exceeded")
                break
        else:
            result.flags.append("step_limit_reached")
        messages.append(Message("user", FORCE_ANSWER))
        return self._generate(messages, result, with_tools=False, bundle=bundle).text

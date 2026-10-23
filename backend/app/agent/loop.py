"""The agent loop: plan -> call tools -> observe, with step cap, token budget and a full trace."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 6  # model turns that may call tools
    max_calls_per_step: int = 4
    token_budget: int = 60_000  # total (prompt + output + thinking) tokens across the run
    max_output_tokens: int = 2048


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
        result.latency_s = time.perf_counter() - started
        if final is None:
            return result
        return self.finish(final, bundle, result)

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
        result.status, result.answer = "answered", text
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

    def _run_tools(self, calls: list[ToolCall], bundle: ToolBundle, step: Step) -> Message:
        results: list[ToolResult] = []
        for i, call in enumerate(calls):
            t0 = time.perf_counter()
            if i >= self._cfg.max_calls_per_step:
                res = ToolResult(call.id, call.name, {"ok": False, "error": "too many tool calls"})
            else:
                res = bundle.registry.call(call)
            ok, summary = summarize(res)
            step.tools.append(
                ToolTrace(call.id, call.name, call.args, ok, summary, time.perf_counter() - t0)
            )
            results.append(res)
        return Message("tool", tool_results=results)

    def _loop(self, messages: list[Message], bundle: ToolBundle, result: AgentResult) -> str:
        for _ in range(self._cfg.max_steps):
            resp = self._generate(messages, result, with_tools=True, bundle=bundle)
            if not resp.tool_calls:
                return resp.text
            messages.append(self._run_tools(resp.tool_calls, bundle, result.steps[-1]))
            if result.usage.total > self._cfg.token_budget:
                result.flags.append("token_budget_exceeded")
                break
        else:
            result.flags.append("step_limit_reached")
        messages.append(Message("user", FORCE_ANSWER))
        return self._generate(messages, result, with_tools=False, bundle=bundle).text

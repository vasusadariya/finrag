"""A scripted LLM for agent tests: replays canned responses and records what it was sent."""

from dataclasses import dataclass, field

from app.llm.types import LLMError, LLMResponse, Message, ToolCall, ToolSpec, Usage


def say(text: str, tokens: int = 100) -> LLMResponse:
    return LLMResponse(text, [], Usage(tokens, 10), "fake", 0.0, message=Message("model", text))


def call(name: str, tokens: int = 100, **args: object) -> LLMResponse:
    calls = [ToolCall(f"call{abs(hash((name, str(args)))) % 10000}", name, dict(args))]
    return LLMResponse(
        "", calls, Usage(tokens, 10), "fake", 0.0, message=Message("model", "", calls)
    )


@dataclass
class ScriptedLLM:
    script: list[LLMResponse | Exception]
    requests: list[dict[str, object]] = field(default_factory=list)

    def generate(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        tier: str = "main",
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        self.requests.append({"messages": list(messages), "tools": tools, "system": system})
        if not self.script:
            raise LLMError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

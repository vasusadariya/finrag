"""Provider-neutral LLM types: messages, tool specs/calls, responses and the client protocol."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Tier = Literal["main", "cheap"]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON-schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "model", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Provider-native content of a model turn, replayed verbatim on the next request. Gemini 3
    # attaches "thought signatures" to function calls and rejects follow-ups that drop them.
    raw: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.output_tokens + self.thinking_tokens


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str  # the model that actually answered (may be a fallback)
    latency_s: float
    cached: bool = False
    message: Message | None = None  # ready to append to the conversation history
    attempts: int = 1


class LLMError(RuntimeError):
    """The model could not produce an answer after retries and fallbacks."""


class LLMClient(Protocol):
    def generate(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        tier: Tier = "main",
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> LLMResponse: ...

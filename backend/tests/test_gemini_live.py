"""Real Gemini calls (free tier); skipped without GEMINI_API_KEY.

Verifies what mocks cannot: tool calling works, and a follow-up turn that replays the model's
raw parts (thought signatures) is accepted.
"""

import os

import pytest

from app.core.config import Settings
from app.llm.gemini import build_gemini
from app.llm.types import LLMError, Message, ToolResult, ToolSpec

KEY = os.environ.get("GEMINI_API_KEY")
if not KEY:
    # .env is not loaded by pytest; read it for local runs
    env = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env):
        for line in open(env):
            if line.startswith("GEMINI_API_KEY="):
                KEY = line.split("=", 1)[1].strip()

pytestmark = pytest.mark.skipif(not KEY, reason="GEMINI_API_KEY not set")

TOOL = ToolSpec(
    "financial_sql",
    "Look up a financial figure from the database.",
    {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
)


def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="x",
        database_url_ro="x",
        edgar_user_agent="x",
        gemini_api_key=KEY,
    )


def test_tool_call_then_final_answer() -> None:
    llm = build_gemini(settings())
    system = "Answer using the tool. Report the number you are given."
    history = [Message("user", "What was Apple's net income in fiscal 2025? Use the tool.")]
    try:
        first = llm.generate(history, system=system, tools=[TOOL])
    except LLMError as e:
        pytest.skip(f"free tier unavailable right now: {e}")
    assert first.tool_calls, f"expected a tool call, got text: {first.text!r}"
    assert first.usage.prompt_tokens > 0 and first.model

    history += [
        first.message,  # type: ignore[list-item]
        Message(
            "tool",
            tool_results=[
                ToolResult(
                    first.tool_calls[0].id, "financial_sql", {"net_income_usd": 112010000000}
                )
            ],
        ),
    ]
    second = llm.generate(history, system=system, tools=[TOOL])
    assert "112" in second.text, second.text

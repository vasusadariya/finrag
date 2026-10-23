import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.llm.gemini import GeminiClient, ResponseCache, to_gemini_schema
from app.llm.types import LLMError, Message, Tier, ToolCall, ToolResult, ToolSpec

MODELS: dict[Tier, list[str]] = {"main": ["big", "backup"], "cheap": ["small"]}
TOOL = ToolSpec(
    "financial_sql",
    "Run SQL",
    {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "q"}},
        "required": ["question"],
    },
)


def ok(parts: list[dict[str, Any]], **usage: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, **usage},
        },
    )


class Server:
    """Scripted responses, one per request; records requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses, self.requests = list(responses), []  # type: ignore[var-annotated]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.url.path, json.loads(request.content), dict(request.headers)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(server: Server, **kw: Any) -> tuple[GeminiClient, list[float]]:
    sleeps: list[float] = []
    c = GeminiClient(
        "key", MODELS, transport=httpx.MockTransport(server), sleep=sleeps.append, **kw
    )
    return c, sleeps


def test_text_answer_usage_and_request_shape() -> None:
    s = Server(ok([{"text": "Hello"}], thoughtsTokenCount=7))
    c, _ = client(s)
    r = c.generate([Message("user", "hi")], system="be brief", tools=[TOOL], max_output_tokens=99)
    assert r.text == "Hello" and r.tool_calls == [] and r.model == "big" and not r.cached
    assert (
        r.usage.prompt_tokens,
        r.usage.output_tokens,
        r.usage.thinking_tokens,
        r.usage.total,
    ) == (10, 5, 7, 22)
    path, body, headers = s.requests[0]
    assert path.endswith("/models/big:generateContent") and headers["x-goog-api-key"] == "key"
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["generationConfig"] == {"temperature": 0.0, "maxOutputTokens": 99}
    decl = body["tools"][0]["functionDeclarations"][0]
    assert (
        decl["parameters"]["type"] == "OBJECT"
        and decl["parameters"]["properties"]["question"]["type"] == "STRING"
    )


def test_tier_selects_model_chain() -> None:
    s = Server(ok([{"text": "x"}]))
    client(s)[0].generate([Message("user", "q")], tier="cheap")
    assert s.requests[0][0].endswith("/models/small:generateContent")


def test_function_call_roundtrip_replays_thought_signatures() -> None:
    call_parts = [
        {
            "functionCall": {"name": "financial_sql", "args": {"question": "AAPL NI"}},
            "thoughtSignature": "SIG==",
        }
    ]
    s = Server(ok(call_parts), ok([{"text": "Apple earned $112B."}]))
    c, _ = client(s)
    first = c.generate([Message("user", "NI?")], tools=[TOOL])
    assert len(first.tool_calls) == 1 and first.tool_calls[0].name == "financial_sql"
    assert first.tool_calls[0].args == {"question": "AAPL NI"} and first.message is not None

    history = [
        Message("user", "NI?"),
        first.message,
        Message(
            "tool",
            tool_results=[
                ToolResult(first.tool_calls[0].id, "financial_sql", {"rows": [[112010000000]]})
            ],
        ),
    ]
    c.generate(history, tools=[TOOL])
    sent = s.requests[1][1]["contents"]
    assert sent[1] == {"role": "model", "parts": call_parts}  # verbatim, signature included
    assert sent[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "financial_sql", "response": {"rows": [[112010000000]]}}}
        ],
    }


def test_model_turn_without_raw_parts_is_rebuilt() -> None:
    s = Server(ok([{"text": "x"}]))
    msg = Message(
        "model", "thinking", tool_calls=[ToolCall("1", "financial_sql", {"question": "q"})]
    )
    client(s)[0].generate([Message("user", "q"), msg])
    assert s.requests[0][1]["contents"][1]["parts"] == [
        {"text": "thinking"},
        {"functionCall": {"name": "financial_sql", "args": {"question": "q"}}},
    ]


def test_retries_transient_errors_with_exponential_backoff() -> None:
    s = Server(
        httpx.Response(503, json={"error": {"message": "busy"}}),
        httpx.Response(429, text="slow down"),
        ok([{"text": "fine"}]),
    )
    c, sleeps = client(s, base_delay_s=2.0)
    r = c.generate([Message("user", "q")])
    assert r.text == "fine" and r.model == "big" and r.attempts == 3
    assert sleeps == [2.0, 4.0]


def test_transport_errors_are_retried_too() -> None:
    s = Server(httpx.ConnectTimeout("t"), ok([{"text": "fine"}]))
    c, sleeps = client(s)
    assert c.generate([Message("user", "q")]).text == "fine" and len(sleeps) == 1


def test_falls_back_to_next_model_when_primary_stays_down() -> None:
    s = Server(*[httpx.Response(503, text="busy")] * 4, ok([{"text": "from backup"}]))
    c, _ = client(s, max_retries=3)
    r = c.generate([Message("user", "q")])
    assert r.text == "from backup" and r.model == "backup" and r.attempts == 5
    assert s.requests[-1][0].endswith("/models/backup:generateContent")


def test_non_retryable_error_skips_to_next_model_without_waiting() -> None:
    s = Server(
        httpx.Response(404, json={"error": {"message": "no longer available"}}), ok([{"text": "b"}])
    )
    c, sleeps = client(s)
    r = c.generate([Message("user", "q")])
    assert r.model == "backup" and sleeps == []


def test_raises_when_every_model_fails() -> None:
    s = Server(*[httpx.Response(503, text="busy")] * 8)
    c, _ = client(s, max_retries=1)
    with pytest.raises(LLMError, match="all models failed.*503"):
        c.generate([Message("user", "q")])


@pytest.mark.parametrize(
    "response, match",
    [
        (
            httpx.Response(
                200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
            ),
            "SAFETY",
        ),
        (
            httpx.Response(
                200, json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
            ),
            "MAX_TOKENS",
        ),
    ],
)
def test_unusable_answers_raise(response: httpx.Response, match: str) -> None:
    s = Server(response)
    with pytest.raises(LLMError, match=match):
        client(s)[0].generate([Message("user", "q")])


def test_thought_parts_are_not_part_of_the_answer_text() -> None:
    s = Server(ok([{"text": "secret reasoning", "thought": True}, {"text": "Answer."}]))
    assert client(s)[0].generate([Message("user", "q")]).text == "Answer."


def test_cache_serves_repeats_without_a_request(tmp_path: Path) -> None:
    s = Server(ok([{"text": "once"}]))
    c, _ = client(s, cache=ResponseCache(tmp_path / "c.sqlite"))
    a = c.generate([Message("user", "same")])
    b = c.generate([Message("user", "same")])
    assert (a.text, b.text, b.cached, len(s.requests)) == ("once", "once", True, 1)
    # a different prompt, tier or temperature must not hit the cache
    s.responses.append(ok([{"text": "two"}]))
    assert c.generate([Message("user", "different")]).text == "two"
    s.responses.append(ok([{"text": "warm"}]))
    assert c.generate([Message("user", "same")], temperature=0.7).text == "warm"


def test_schema_conversion_drops_unsupported_keywords() -> None:
    out = to_gemini_schema(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "n": {"type": "integer", "minimum": 1, "default": 3},
                "xs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["n"],
        }
    )
    assert out == {
        "type": "OBJECT",
        "properties": {
            "n": {"type": "INTEGER", "minimum": 1},
            "xs": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["n"],
    }


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        GeminiClient("", MODELS)
    with pytest.raises(ValueError):
        GeminiClient("k", {"main": ["a"], "cheap": []})

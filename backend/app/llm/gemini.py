"""Gemini client (Google AI Studio REST API) with retries, model fallback and an optional cache."""

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.llm.types import (
    LLMError,
    LLMResponse,
    Message,
    Tier,
    ToolCall,
    ToolSpec,
    Usage,
)

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Retried on the same model, then the next model in the chain is tried.
RETRYABLE = {429, 500, 502, 503, 504}


class ResponseCache:
    """SQLite cache of full responses keyed by a hash of the request (temperature-0 calls only)."""

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS llm (key TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT body FROM llm WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, key: str, body: dict[str, Any]) -> None:
        self._db.execute("INSERT OR REPLACE INTO llm VALUES (?, ?)", (key, json.dumps(body)))
        self._db.commit()


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON-schema -> Gemini's OpenAPI subset (upper-case type names, no unsupported keywords)."""
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif k == "properties":
            out[k] = {name: to_gemini_schema(p) for name, p in v.items()}
        elif k == "items":
            out[k] = to_gemini_schema(v)
        elif k in {"description", "enum", "required", "nullable", "format", "minimum", "maximum"}:
            out[k] = v
    return out


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        models: dict[Tier, list[str]],
        *,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        base_delay_s: float = 2.0,
        cache: ResponseCache | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        if not models.get("main") or not models.get("cheap"):
            raise ValueError("models needs a non-empty 'main' and 'cheap' chain")
        self._models, self._max_retries, self._base_delay = models, max_retries, base_delay_s
        self._cache, self._sleep = cache, sleep
        self._http = httpx.Client(
            base_url=BASE_URL, timeout=timeout_s, transport=transport,
            headers={"x-goog-api-key": api_key},
        )  # fmt: skip

    def close(self) -> None:
        self._http.close()

    # ---- request building -------------------------------------------------------------------
    @staticmethod
    def _contents(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "parts": [{"text": m.text}]})
            elif m.role == "model":
                parts = m.raw
                if parts is None:
                    parts = [{"text": m.text}] if m.text else []
                    parts += [
                        {"functionCall": {"name": c.name, "args": c.args}} for c in m.tool_calls
                    ]
                out.append({"role": "model", "parts": parts})
            else:
                parts = [
                    {"functionResponse": {"name": r.name, "response": r.content}}
                    for r in m.tool_results
                ]
                out.append({"role": "user", "parts": parts})
        return out

    def _body(
        self, messages: list[Message], system: str, tools: list[ToolSpec] | None,
        temperature: float, max_output_tokens: int,
    ) -> dict[str, Any]:  # fmt: skip
        body: dict[str, Any] = {
            "contents": self._contents(messages),
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_output_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": to_gemini_schema(t.parameters),
                        }
                        for t in tools
                    ]
                }
            ]
        return body

    # ---- calling ----------------------------------------------------------------------------
    def generate(
        self,
        messages: list[Message],
        *,
        system: str = "",
        tools: list[ToolSpec] | None = None,
        tier: Tier = "main",
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        body = self._body(messages, system, tools, temperature, max_output_tokens)
        chain = self._models[tier]
        key = hashlib.sha256(json.dumps([chain, body], sort_keys=True).encode()).hexdigest()
        if self._cache is not None and temperature == 0.0:
            hit = self._cache.get(key)
            if hit is not None:
                return self._parse(hit["data"], hit["model"], 0.0, cached=True, attempts=0)

        start = time.perf_counter()
        attempts = 0
        last = "no attempt made"
        for model in chain:
            for attempt in range(self._max_retries + 1):
                attempts += 1
                try:
                    resp = self._http.post(f"/models/{model}:generateContent", json=body)
                except httpx.HTTPError as e:
                    last = f"{model}: {type(e).__name__}"
                    log.warning("llm transport error", extra={"model": model, "error": last})
                else:
                    if resp.status_code == 200:
                        data = resp.json()
                        if self._cache is not None and temperature == 0.0:
                            self._cache.put(key, {"data": data, "model": model})
                        return self._parse(
                            data, model, time.perf_counter() - start, attempts=attempts
                        )
                    last = f"{model}: HTTP {resp.status_code} {self._error_text(resp)}"
                    if resp.status_code not in RETRYABLE:
                        # 400/403/404 will not get better by retrying this model; try the next one
                        log.warning("llm non-retryable", extra={"error": last})
                        break
                    log.warning("llm retryable error", extra={"error": last})
                if attempt < self._max_retries:
                    self._sleep(self._base_delay * (2**attempt))
        raise LLMError(f"all models failed after {attempts} attempts; last error: {last}")

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            return str(resp.json()["error"]["message"])[:160].replace("\n", " ")
        except (ValueError, KeyError, TypeError):
            return resp.text[:160].replace("\n", " ")

    @staticmethod
    def _parse(
        data: dict[str, Any], model: str, latency: float, *, cached: bool = False, attempts: int = 1
    ) -> LLMResponse:
        try:
            cand = data["candidates"][0]
        except (KeyError, IndexError) as e:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"model returned no answer ({reason})") from e
        parts: list[dict[str, Any]] = (cand.get("content") or {}).get("parts") or []
        text = "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))
        calls = [
            ToolCall(
                uuid.uuid4().hex[:8],
                p["functionCall"]["name"],
                dict(p["functionCall"].get("args") or {}),
            )
            for p in parts
            if "functionCall" in p
        ]
        u = data.get("usageMetadata") or {}
        usage = Usage(
            prompt_tokens=int(u.get("promptTokenCount") or 0),
            output_tokens=int(u.get("candidatesTokenCount") or 0),
            thinking_tokens=int(u.get("thoughtsTokenCount") or 0),
        )
        if not text and not calls:
            raise LLMError(
                f"model returned an empty answer (finishReason={cand.get('finishReason')})"
            )
        msg = Message(role="model", text=text, tool_calls=calls, raw=parts or None)
        return LLMResponse(text, calls, usage, model, latency, cached, msg, attempts)


def build_gemini(settings: Settings, cache_path: str | Path | None = None) -> GeminiClient:
    """GeminiClient configured from application settings."""
    chains = settings.model_chains()
    return GeminiClient(
        settings.require_llm_key(),
        {"main": chains["main"], "cheap": chains["cheap"]},
        cache=ResponseCache(cache_path) if cache_path else None,
    )

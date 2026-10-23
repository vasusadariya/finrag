"""HTTP API: POST /chat (SSE or JSON), GET /health, GET /trace/{id}."""

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.cost import run_cost
from app.agent.traces import TraceStore
from app.agent.types import AgentResult
from app.api.service import AnswerService

log = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
RUN_TIMEOUT_S = 180.0


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    stream: bool = True


def result_payload(result: AgentResult, trace_id: str | None) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "status": result.status,
        "answer": result.answer,
        "reason": result.reason,
        "citations": result.citations,
        "flags": result.flags,
        "ungrounded": result.ungrounded,
        "latency_s": round(result.latency_s, 2),
        "tokens": result.usage.total + result.sql_usage.total,
        "shadow_cost_usd": round(run_cost(result), 6),
    }


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(
    service: AnswerService,
    traces: TraceStore | None = None,
    *,
    max_concurrent: int = 2,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    slots = threading.BoundedSemaphore(max_concurrent)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="FinRAG", version="0.3", lifespan=lifespan)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )  # fmt: skip

    def run_and_trace(question: str, emit: Any) -> tuple[AgentResult, str | None]:
        result = service.run(question, emit)
        return result, traces.save(result) if traces else None

    @app.get("/health")
    def health() -> JSONResponse:
        checks = service.health()
        ok = all(checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", **checks}, 200 if ok else 503)

    @app.get("/trace/{trace_id}")
    def get_trace(trace_id: str) -> dict[str, Any]:
        if traces is None:
            raise HTTPException(404, "tracing is disabled")
        found = traces.get(trace_id)
        if found is None:
            raise HTTPException(404, "trace not found")
        return found

    @app.post("/chat", response_model=None)
    async def chat(req: ChatRequest) -> StreamingResponse | JSONResponse:
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "the analyst is busy; retry in a few seconds")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ("step", event))

        def work() -> None:
            try:
                result, trace_id = run_and_trace(req.question, emit)
                final = ("answer", result_payload(result, trace_id))
            except Exception as e:  # noqa: BLE001 - never leak internals to the client
                log.exception("chat failed")
                final = ("error", {"error": "internal error", "type": type(e).__name__})
            finally:
                slots.release()
            loop.call_soon_threadsafe(queue.put_nowait, final)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, daemon=True, name="chat-run").start()

        if not req.stream:
            payload: dict[str, Any] = {}
            while (item := await asyncio.wait_for(queue.get(), RUN_TIMEOUT_S)) is not None:
                if item[0] != "step":
                    payload = item[1] | {"event": item[0]}
            code = 500 if payload.get("event") == "error" else 200
            return JSONResponse(payload, code)

        async def stream() -> AsyncIterator[str]:
            yield sse("start", {"question": req.question})
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), RUN_TIMEOUT_S)
                except TimeoutError:
                    yield sse("error", {"error": "timed out"})
                    return
                if item is None:
                    return
                yield sse(*item)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    return app


def create_default_app() -> FastAPI:  # pragma: no cover - needs models, DB and keys
    """Entry point for uvicorn: ``uvicorn app.api.main:create_default_app --factory``."""
    from app.api.service import LiveService
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    return create_app(
        LiveService(settings),
        TraceStore(settings.database_url),
        max_concurrent=settings.max_concurrent_runs,
        cors_origins=origins,
    )

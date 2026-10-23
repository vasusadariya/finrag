import json
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.loop import Agent
from app.agent.traces import TraceStore
from app.agent.types import AgentResult
from app.api.main import create_app
from tests.fake_llm import ScriptedLLM, call, say
from tests.test_agent_loop import make


class FakeService:
    def __init__(self, script_factory: Any = None, healthy: bool = True) -> None:
        self.factory: Callable[[], list[Any]] = script_factory or (
            lambda: [call("financial_sql", question="q"), say("Revenue was $391.0 billion [sql1].")]
        )
        self.healthy = healthy
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def run(self, question: str, on_event: Any = None) -> AgentResult:
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        return Agent(ScriptedLLM(self.factory()), make, "cov").run(question, on_event)

    def health(self) -> dict[str, bool]:
        return {"db": self.healthy, "sql_guard": True}


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_streams_steps_then_answer() -> None:
    client = TestClient(create_app(FakeService()))
    r = client.post("/chat", json={"question": "Apple revenue?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["start", "step", "answer"]
    assert events[1][1] == {
        "type": "tool", "name": "financial_sql", "args": {"question": "q"}, "ok": True,
        "summary": "1 rows",
    }  # fmt: skip
    final = events[2][1]
    assert final["status"] == "answered" and final["citations"][0]["id"] == "sql1"
    assert final["trace_id"] is None and final["tokens"] > 0


def test_chat_json_mode_and_refusal() -> None:
    svc = FakeService(lambda: [say("INSUFFICIENT_EVIDENCE: Tesla is not covered.")])
    r = TestClient(create_app(svc)).post(
        "/chat", json={"question": "Tesla revenue?", "stream": False}
    )
    body = r.json()
    assert r.status_code == 200 and body["status"] == "refused"
    assert body["answer"] == "Tesla is not covered."


@pytest.mark.parametrize(
    "payload", [{}, {"question": ""}, {"question": "ab"}, {"question": "x" * 1001}, {"question": 5}]
)
def test_invalid_requests_are_422(payload: dict[str, Any]) -> None:
    assert TestClient(create_app(FakeService())).post("/chat", json=payload).status_code == 422


def test_internal_errors_do_not_leak() -> None:
    class Boom(FakeService):
        def run(self, question: str, on_event: Any = None) -> AgentResult:
            raise RuntimeError("password=hunter2")

    client = TestClient(create_app(Boom()))
    streamed = client.post("/chat", json={"question": "hello there"}).text
    assert "hunter2" not in streamed and parse_sse(streamed)[-1][0] == "error"
    r = client.post("/chat", json={"question": "hello there", "stream": False})
    assert r.status_code == 500 and "hunter2" not in r.text


def test_busy_server_returns_429_and_recovers() -> None:
    svc = FakeService()
    svc.gate = threading.Event()
    client = TestClient(create_app(svc, max_concurrent=1))
    first: dict[str, Any] = {}
    t = threading.Thread(
        target=lambda: first.update(
            r=client.post("/chat", json={"question": "first one", "stream": False})
        )
    )
    t.start()
    assert svc.entered.wait(5), "first request never started"
    assert client.post("/chat", json={"question": "second one"}).status_code == 429
    svc.gate.set()
    t.join(10)
    assert first["r"].status_code == 200
    assert client.post("/chat", json={"question": "third one", "stream": False}).status_code == 200


def test_health_reports_degraded() -> None:
    assert TestClient(create_app(FakeService())).get("/health").json()["status"] == "ok"
    r = TestClient(create_app(FakeService(healthy=False))).get("/health")
    assert r.status_code == 503 and r.json()["db"] is False


def test_trace_roundtrip_via_api(db_url: str) -> None:
    client = TestClient(create_app(FakeService(), TraceStore(db_url)))
    body = client.post("/chat", json={"question": "Apple revenue?", "stream": False}).json()
    assert body["trace_id"]
    t = client.get(f"/trace/{body['trace_id']}").json()
    assert t["question"] == "Apple revenue?" and t["status"] == "answered"
    assert client.get("/trace/not-a-uuid").status_code == 404
    assert client.get("/trace/00000000-0000-0000-0000-000000000000").status_code == 404
    assert TestClient(create_app(FakeService())).get("/trace/abc").status_code == 404


def test_cors_only_for_configured_origins() -> None:
    client = TestClient(create_app(FakeService(), cors_origins=["http://ui.test"]))
    ok = client.get("/health", headers={"Origin": "http://ui.test"})
    assert ok.headers["access-control-allow-origin"] == "http://ui.test"
    bad = client.get("/health", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in bad.headers

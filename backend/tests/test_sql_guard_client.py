"""Contract tests: the Python client vs the real Go guard binary and vs misbehaving fakes."""

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from app.tools.sql_guard import GuardResult, SqlGuardClient, SqlGuardUnavailableError
from app.tools.sql_policy import POLICY_PATH, load_policy

GUARD_DIR = Path(__file__).resolve().parents[2] / "sqlguard-go"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def guard_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if shutil.which("go") is None:
        pytest.skip("go toolchain not available")
    binary = tmp_path_factory.mktemp("guard") / "sqlguard"
    build = subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/sqlguard"],
        cwd=GUARD_DIR, capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert build.returncode == 0, build.stderr
    port = free_port()
    proc = subprocess.Popen(
        [str(binary), "-addr", f"127.0.0.1:{port}", "-policy", str(POLICY_PATH)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("guard did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def client(guard_url: str) -> Iterator[SqlGuardClient]:
    c = SqlGuardClient(guard_url)
    yield c
    c.close()


def test_accepts_valid_query_and_returns_rewritten_sql(client: SqlGuardClient) -> None:
    assert client.healthy()
    r = client.validate("SELECT ticker FROM companies WHERE ticker = 'AAPL'")
    assert r == GuardResult(True, "SELECT ticker FROM companies WHERE ticker = 'AAPL' LIMIT 50", ())


@pytest.mark.parametrize(
    "sql, code",
    [
        ("DROP TABLE companies", "not_select"),
        ("SELECT * FROM pg_shadow", "disallowed_table"),
        ("SELECT pg_sleep(5)", "disallowed_function"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT 1 -- x", "comment"),
    ],
)
def test_rejects_with_reasons(client: SqlGuardClient, sql: str, code: str) -> None:
    r = client.validate(sql)
    assert not r.ok and r.sql is None
    assert any(code in reason for reason in r.reasons), r.reasons


def test_limits_come_from_the_shared_policy(client: SqlGuardClient) -> None:
    policy = load_policy()
    default = client.validate("SELECT * FROM companies")
    clamped = client.validate("SELECT * FROM companies LIMIT 999999")
    assert default.sql is not None and default.sql.endswith(f"LIMIT {policy.default_limit}")
    assert clamped.sql is not None and clamped.sql.endswith(f"LIMIT {policy.max_limit}")


def test_python_and_go_agree_on_the_policy(client: SqlGuardClient) -> None:
    """Every table, column and function advertised to the LLM must pass the Go guard."""
    policy = load_policy()
    for table, cols in policy.tables.items():
        cols_sql = ", ".join(cols)
        assert client.validate(f"SELECT {cols_sql} FROM {table}").ok, table
    assert not client.validate("SELECT * FROM chunks").ok
    assert not client.validate("SELECT * FROM query_traces").ok
    for fn in policy.functions:
        if fn in {"lag", "lead", "row_number", "rank"}:
            continue  # window functions need OVER; covered by the Go corpus
        assert client.validate(f"SELECT {fn}(value) FROM xbrl_facts").ok, fn


def test_fail_closed_when_guard_is_down() -> None:
    c = SqlGuardClient(f"http://127.0.0.1:{free_port()}", timeout_s=0.5)
    assert not c.healthy()
    with pytest.raises(SqlGuardUnavailableError):
        c.validate("SELECT 1")


def fake(handler: httpx.MockTransport) -> SqlGuardClient:
    c = SqlGuardClient("http://guard.test")
    c._http = httpx.Client(base_url="http://guard.test", transport=handler)
    return c


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(400, json={"error": "bad"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"sql": "SELECT 1"}),  # no ok
        httpx.Response(200, json={"ok": "yes", "sql": "SELECT 1"}),  # ok not a bool
        httpx.Response(200, json={"ok": True}),  # approved but no SQL
        httpx.Response(200, json={"ok": True, "sql": "  "}),
    ],
)
def test_malformed_or_unexpected_answers_fail_closed(response: httpx.Response) -> None:
    c = fake(httpx.MockTransport(lambda request: response))
    with pytest.raises(SqlGuardUnavailableError):
        c.validate("SELECT 1")


def test_rejection_without_reasons_still_has_one() -> None:
    c = fake(httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": False})))
    r = c.validate("DROP TABLE x")
    assert not r.ok and r.reasons == ("rejected without a reason",)

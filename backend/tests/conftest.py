"""Shared fixtures: a throwaway, migrated Postgres database (skipped when none is reachable)."""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


def _admin_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")


@pytest.fixture
def db_url() -> Iterator[str]:
    admin_url = _admin_url()
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        if os.environ.get("FINRAG_REQUIRE_DB"):
            pytest.fail(f"postgres required but unavailable: {e}")
        pytest.skip(f"postgres unavailable (set FINRAG_REQUIRE_DB=1 to fail instead): {e}")
    name = f"finrag_pytest_{time.time_ns()}"
    admin.execute(f"CREATE DATABASE {name}")
    parts = urlsplit(admin_url)
    url = urlunsplit(parts._replace(path=f"/{name}"))
    with psycopg.connect(url, autocommit=True) as conn:
        for f in sorted(MIGRATIONS.glob("*.up.sql")):
            conn.execute(f.read_text())
    try:
        yield url
    finally:
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
        admin.close()


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection[dict[str, object]]]:
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        yield c


GUARD_DIR = Path(__file__).resolve().parents[2] / "sqlguard-go"
POLICY = Path(__file__).resolve().parents[2] / "db" / "sql_policy.json"


@pytest.fixture(scope="session")
def guard_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A running instance of the real Go SQL guard (built once per test session)."""
    if shutil.which("go") is None:
        pytest.skip("go toolchain not available")
    binary = tmp_path_factory.mktemp("guard") / "sqlguard"
    build = subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/sqlguard"],
        cwd=GUARD_DIR, capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert build.returncode == 0, build.stderr
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    proc = subprocess.Popen(
        [str(binary), "-addr", f"127.0.0.1:{port}", "-policy", str(POLICY)],
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

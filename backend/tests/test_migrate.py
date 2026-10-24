import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.db.migrate import available, main, migrate, set_ro_password
from tests.conftest import _admin_url


@pytest.fixture
def empty_db() -> str:  # type: ignore[misc]
    admin_url = _admin_url()
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"postgres unavailable: {e}")
    name = f"finrag_mig_{time.time_ns()}"
    admin.execute(f"CREATE DATABASE {name}")
    url = urlunsplit(urlsplit(admin_url)._replace(path=f"/{name}"))
    yield url
    admin.execute(f"DROP DATABASE {name} WITH (FORCE)")
    admin.close()


def test_available_lists_real_migrations_in_order() -> None:
    versions = [v for v, _ in available()]
    assert versions == sorted(versions) and versions[0] == 1 and len(versions) >= 2


def test_applies_all_then_is_idempotent(empty_db: str) -> None:
    first = migrate(empty_db)
    assert first == [v for v, _ in available()]
    assert migrate(empty_db) == []
    with psycopg.connect(empty_db) as c:
        tables = {
            r[0]
            for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
        }
        assert {"companies", "filings", "chunks", "xbrl_facts", "query_traces",
                "schema_migrations"} <= tables  # fmt: skip


def test_failed_migration_rolls_back_and_is_retried(empty_db: str, tmp_path: Path) -> None:
    (tmp_path / "000001_a.up.sql").write_text("CREATE TABLE a (id int);")
    (tmp_path / "000002_b.up.sql").write_text("CREATE TABLE b (id int); SELECT 1/0;")
    with pytest.raises(psycopg.Error):
        migrate(empty_db, tmp_path)
    with psycopg.connect(empty_db) as c:
        have = {
            r[0]
            for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ).fetchall()
        }
        assert "a" in have and "b" not in have  # 000002 left no trace
        assert [r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()] == [1]
    (tmp_path / "000002_b.up.sql").write_text("CREATE TABLE b (id int);")
    assert migrate(empty_db, tmp_path) == [2]


def test_cli_and_ro_password(empty_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RO_PW", "s3cret'quote")
    assert main(["--dsn", empty_db, "--ro-password-env", "RO_PW"]) == 0
    assert main(["--dsn", empty_db, "--ro-password-env", "MISSING_VAR"]) == 2
    assert main([]) == 2 if not os.environ.get("DATABASE_URL") else True
    set_ro_password(empty_db, "another")  # must not raise (quoting handled by psycopg)

"""Apply db/migrations/*.up.sql in order (container entrypoint: ``python -m app.db.migrate``).

The version table has the same shape as golang-migrate's (``schema_migrations(version, dirty)``),
so ``make migrate-up`` and this runner can be mixed on one database.
"""

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
_NAME = re.compile(r"^(\d+)_.+\.up\.sql$")
LOCK_ID = 727_346_001  # advisory lock so two containers cannot migrate at once


def available(directory: Path = MIGRATIONS) -> list[tuple[int, Path]]:
    found = []
    for f in sorted(directory.glob("*.up.sql")):
        m = _NAME.match(f.name)
        if m:
            found.append((int(m.group(1)), f))
    return found


def migrate(dsn: str, directory: Path = MIGRATIONS) -> list[int]:
    """Apply pending migrations; returns the versions applied. Each runs in its own transaction."""
    applied: list[int] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("SELECT pg_advisory_lock({})").format(LOCK_ID))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations"
                " (version bigint PRIMARY KEY, dirty boolean NOT NULL DEFAULT false)"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for version, path in available(directory):
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied.append(version)
        finally:
            conn.execute(sql.SQL("SELECT pg_advisory_unlock({})").format(LOCK_ID))
    return applied


def set_ro_password(dsn: str, password: str) -> None:
    """Replace the development password of the read-only role (used by the SQL executor)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE finrag_ro PASSWORD {}").format(sql.Literal(password)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument(
        "--ro-password-env", help="name of an env var holding the finrag_ro password to set"
    )
    args = ap.parse_args(argv)
    if not args.dsn:
        print("DATABASE_URL (or --dsn) is required", file=sys.stderr)
        return 2
    applied = migrate(args.dsn)
    print(f"applied migrations: {applied or 'none (up to date)'}")
    if args.ro_password_env:
        password = os.environ.get(args.ro_password_env)
        if not password:
            print(f"{args.ro_password_env} is not set", file=sys.stderr)
            return 2
        set_ro_password(args.dsn, password)
        print("finrag_ro password updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
import re
from pathlib import Path

import psycopg
import pytest

from app.tools.sql_policy import POLICY_PATH, SqlPolicy, load_policy

GO_XBRL = Path(__file__).resolve().parents[2] / "ingest-go" / "internal" / "xbrl" / "xbrl.go"


def ingested_concepts() -> set[str]:
    """The concepts the Go ingest stores, read straight from its source so the two cannot drift."""
    src = GO_XBRL.read_text()
    block = src[src.index("var CoreConcepts = []string{") :]
    block = block[: block.index("\n}\n")]
    found = set(re.findall(r'"([A-Za-z0-9]+)"', block))
    assert len(found) > 30, "failed to parse CoreConcepts from the Go source"
    return found


def test_loads_and_exposes_expected_tables() -> None:
    p = load_policy()
    assert set(p.tables) == {"companies", "filings", "xbrl_facts"}
    assert p.allowed("xbrl_facts", "value") and p.allowed("companies")
    assert not p.allowed("chunks") and not p.allowed("query_traces")
    assert not p.allowed("xbrl_facts", "password") and not p.allowed("pg_catalog")


def test_no_dangerous_functions_allowed() -> None:
    p = load_policy()
    banned = {"pg_sleep", "pg_read_file", "set_config", "current_setting", "version", "dblink",
              "lo_import", "pg_ls_dir", "generate_series", "pg_terminate_backend"}  # fmt: skip
    assert not (banned & p.functions)
    assert {"sum", "avg", "lag"} <= p.functions


def test_limits_are_sane() -> None:
    p = load_policy()
    assert 0 < p.default_limit <= p.max_limit <= 1000


def test_schema_description_mentions_everything_the_model_needs() -> None:
    text = load_policy().describe_schema()
    needles = [
        "xbrl_facts", "companies", "filings", "value", "fiscal_year", "full units", "unit = 'USD'",
        "NetIncomeLoss", "ResearchAndDevelopmentExpense", "Allowed functions", "capped at",
    ]  # fmt: skip
    for needle in needles:
        assert needle in text, needle
    assert "chunks" not in text and "query_traces" not in text


def test_every_glossary_concept_is_a_loaded_core_concept() -> None:
    """The glossary must only promise concepts the ingest actually stores."""
    stored = ingested_concepts()
    for term, tags in load_policy().concepts.items():
        missing = [t for t in tags if t not in stored]
        assert not missing, f"{term}: {missing} are not ingested"


def test_every_stored_concept_is_discoverable_in_glossary() -> None:
    covered = {t for tags in load_policy().concepts.values() for t in tags}
    assert ingested_concepts() - covered == set(), "ingested concepts missing from the glossary"


def test_invalid_policies_rejected(tmp_path: Path) -> None:
    raw = json.loads(POLICY_PATH.read_text())

    def write(**kw: object) -> Path:
        f = tmp_path / "p.json"
        f.write_text(json.dumps({**raw, **kw}))
        return f

    with pytest.raises(ValueError, match="version"):
        load_policy(write(version=2))
    with pytest.raises(ValueError, match="no tables"):
        load_policy(write(tables={}))
    with pytest.raises(ValueError, match="default_limit"):
        load_policy(write(default_limit=500, max_limit=10))


def test_policy_matches_the_real_schema_and_ro_grants(
    conn: psycopg.Connection[dict[str, object]],
) -> None:
    p: SqlPolicy = load_policy()
    for table, cols in p.tables.items():
        actual = {
            str(r["column_name"])
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name=%s",
                (table,),
            ).fetchall()
        }
        assert set(cols) == actual, f"{table}: policy {sorted(cols)} vs schema {sorted(actual)}"
    # the read-only role can read exactly the allow-listed tables and nothing else
    granted = {
        str(r["table_name"])
        for r in conn.execute(
            "SELECT table_name FROM information_schema.role_table_grants"
            " WHERE grantee='finrag_ro' AND privilege_type='SELECT' AND table_schema='public'"
        ).fetchall()
    }
    assert granted == set(p.tables)

"""The SQL policy shared with the Go guard: allowed tables, columns, functions, schema text."""

import json
from dataclasses import dataclass
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[3] / "db" / "sql_policy.json"


@dataclass(frozen=True)
class SqlPolicy:
    max_limit: int
    default_limit: int
    tables: dict[str, dict[str, str]]  # table -> column -> description
    table_descriptions: dict[str, str]
    functions: frozenset[str]
    notes: tuple[str, ...]
    concepts: dict[str, tuple[str, ...]]

    def allowed(self, table: str, column: str | None = None) -> bool:
        cols = self.tables.get(table)
        return cols is not None and (column is None or column in cols)

    def describe_schema(self) -> str:
        """Schema text for the text-to-SQL prompt: tables, columns, notes, concept glossary."""
        out = ["Tables you may query (read-only SELECT only):"]
        for table, cols in self.tables.items():
            out.append(f"\n{table}: {self.table_descriptions[table]}")
            out += [f"  - {name}: {desc}" for name, desc in cols.items()]
        out.append("\nNotes:")
        out += [f"  - {n}" for n in self.notes]
        out.append("\nConcept glossary (business term -> xbrl_facts.concept values):")
        out += [f"  - {term}: {', '.join(tags)}" for term, tags in self.concepts.items()]
        funcs = ", ".join(sorted(self.functions))
        out.append(f"\nAllowed functions: {funcs} (plus operators, CASE, CAST, COALESCE).")
        out.append(f"Results are capped at {self.max_limit} rows; add LIMIT yourself when you can.")
        return "\n".join(out)


def load_policy(path: Path = POLICY_PATH) -> SqlPolicy:
    raw = json.loads(path.read_text())
    if raw.get("version") != 1:
        raise ValueError(f"unsupported sql_policy version {raw.get('version')}")
    tables = {t: dict(spec["columns"]) for t, spec in raw["tables"].items()}
    if not tables:
        raise ValueError("sql_policy has no tables")
    if raw["default_limit"] > raw["max_limit"]:
        raise ValueError("default_limit exceeds max_limit")
    return SqlPolicy(
        max_limit=int(raw["max_limit"]),
        default_limit=int(raw["default_limit"]),
        tables=tables,
        table_descriptions={t: spec["description"] for t, spec in raw["tables"].items()},
        functions=frozenset(f.lower() for f in raw["functions"]),
        notes=tuple(raw["glossary"]["notes"]),
        concepts={k: tuple(v) for k, v in raw["glossary"]["concepts"].items()},
    )

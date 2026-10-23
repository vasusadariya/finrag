"""Citation handling: find [c1]/[sql1] markers, validate them against what the tools returned."""

import re
from typing import Any

from app.tools.builder import ToolBundle

_GROUP = re.compile(r"\[\s*((?:c|sql)\d+(?:\s*,\s*(?:c|sql)\d+)*)\s*\]", re.IGNORECASE)
_ID = re.compile(r"(?:c|sql)\d+", re.IGNORECASE)


def cited_ids(text: str) -> list[str]:
    """Citation ids in order of first appearance."""
    ids = [i.lower() for g in _GROUP.findall(text) for i in _ID.findall(g)]
    return list(dict.fromkeys(ids))


def known_ids(bundle: ToolBundle) -> set[str]:
    return set(bundle.search.citations) | set(bundle.sql.queries)


def strip_unknown(text: str, known: set[str]) -> str:
    """Remove markers that point at nothing the tools returned (keeps valid ids in a group)."""

    def fix(m: re.Match[str]) -> str:
        keep = [i for i in _ID.findall(m.group(1)) if i.lower() in known]
        return f"[{', '.join(keep)}]" if keep else ""

    return re.sub(r"[ \t]+([.,;:])", r"\1", _GROUP.sub(fix, text))


def build_citations(ids: list[str], bundle: ToolBundle) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in ids:
        if i in bundle.search.citations:
            out.append({"type": "filing", **bundle.search.citation_dicts([i])[0]})
        elif i in bundle.sql.queries:
            q = bundle.sql.queries[i]
            out.append(
                {
                    "type": "sql",
                    "id": i,
                    "label": "Reported XBRL financial data",
                    "sql": q["sql"],
                    "columns": q["columns"],
                    "rows": q["rows"][:20],
                    "truncated": q["truncated"] or len(q["rows"]) > 20,
                }
            )
    return out


def has_evidence(evidence: list[dict[str, Any]]) -> bool:
    """True if any retrieval or SQL tool returned something (a calculator alone is not evidence)."""
    for c in evidence:
        if c.get("ok") and (c.get("results") or c.get("rows")):
            return True
    return False

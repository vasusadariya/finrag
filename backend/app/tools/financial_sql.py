"""The financial_sql tool: a natural-language question about reported numbers -> guarded SQL."""

from typing import Any

from app.llm.types import Usage
from app.tools.sql_guard import SqlGuardUnavailableError
from app.tools.text_to_sql import TextToSql


class FinancialSql:
    """Wraps TextToSql. Each successful query gets a citation id ``sqlN`` for the answer."""

    def __init__(self, t2s: TextToSql) -> None:
        self._t2s = t2s
        self.queries: dict[str, dict[str, Any]] = {}
        self.usage = Usage()
        self.verdicts: list[str] = []  # per generated SQL attempt: ok | rejected | error | timeout

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        question = args["question"]
        if not question.strip():
            return {"ok": False, "error": "'question' must not be empty"}
        try:
            ans = self._t2s.run(question)
        except SqlGuardUnavailableError:
            return {"ok": False, "error": "the SQL safety service is unavailable; try again later"}
        self.verdicts += [a.verdict for a in ans.attempts]
        self.usage = Usage(
            self.usage.prompt_tokens + ans.usage.prompt_tokens,
            self.usage.output_tokens + ans.usage.output_tokens,
            self.usage.thinking_tokens + ans.usage.thinking_tokens,
        )
        if not ans.ok or ans.result is None:
            return {"ok": False, "error": ans.error or "could not produce a valid query"}
        cid = f"sql{len(self.queries) + 1}"
        payload = {
            "citation_id": cid,
            "sql": ans.sql,
            "columns": ans.result.columns,
            "rows": ans.result.rows,
            "row_count": len(ans.result.rows),
            "truncated": ans.result.truncated,
        }
        self.queries[cid] = payload
        return {"ok": True, **payload}

"""Natural language -> validated, executed SQL, with a feedback retry loop.

Flow per attempt: the LLM writes SQL, the Go guard validates (and rewrites) it, and the read-only
executor runs it. A guard rejection or a database error is shown to the model and it tries again,
up to ``max_retries`` extra attempts. Timeouts are not retried. If the guard service itself is down
the error propagates: nothing is ever executed without validation.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.llm.types import LLMClient, Message, Tier, Usage
from app.tools.sql_exec import ExecResult, SqlExecutionError, SqlTimeoutError
from app.tools.sql_guard import GuardResult
from app.tools.sql_policy import SqlPolicy

SYSTEM_TEMPLATE = """You write one PostgreSQL SELECT query that answers a question about SEC 10-K
financial data.

{schema}

Rules:
- Reply with ONLY the SQL, no explanation. A ```sql fenced block is fine.
- Exactly one SELECT statement (WITH ... SELECT is fine). No comments, no stacked statements.
- Use only the tables, columns and functions listed above. Select human-readable columns (ticker,
  fiscal_year, a clear alias for each computed value), not internal ids.
- Dollar amounts: filter unit = 'USD'; divide by 1e9 when the question asks for billions.
- Filter companies with companies.ticker. Filter fiscal_year when the question names a year.
- If the question cannot be answered from these tables (for example it asks for opinions, forecasts,
  other companies or text from the filings), reply exactly: CANNOT_ANSWER: <short reason>

Examples:
Q: What was Apple's net income in fiscal 2025?
SELECT c.ticker, x.fiscal_year, x.value AS net_income_usd
FROM xbrl_facts x JOIN companies c ON c.id = x.company_id
WHERE c.ticker = 'AAPL' AND x.concept = 'NetIncomeLoss' AND x.fiscal_year = 2025 AND x.unit = 'USD'

Q: Compare Alphabet's revenue in 2024 and 2025.
SELECT c.ticker, x.fiscal_year, max(x.value) AS revenue_usd
FROM xbrl_facts x JOIN companies c ON c.id = x.company_id
WHERE c.ticker = 'GOOGL'
  AND x.concept IN ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax')
  AND x.fiscal_year IN (2024, 2025) AND x.unit = 'USD'
GROUP BY c.ticker, x.fiscal_year ORDER BY x.fiscal_year

Q: What was Amazon's operating margin in 2025?
SELECT c.ticker, x.fiscal_year, oi.value / max(x.value) AS operating_margin
FROM xbrl_facts x
JOIN companies c ON c.id = x.company_id
JOIN xbrl_facts oi ON oi.company_id = x.company_id AND oi.fiscal_year = x.fiscal_year
  AND oi.concept = 'OperatingIncomeLoss' AND oi.unit = 'USD'
WHERE c.ticker = 'AMZN' AND x.fiscal_year = 2025 AND x.unit = 'USD'
  AND x.concept IN ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax')
GROUP BY c.ticker, x.fiscal_year, oi.value"""


class Guard(Protocol):
    def validate(self, sql: str) -> GuardResult: ...


class Executor(Protocol):
    def execute(self, sql: str) -> ExecResult: ...


@dataclass
class SqlAttempt:
    sql: str
    verdict: str  # "ok" | "rejected" | "error" | "timeout"
    detail: str = ""


@dataclass
class SqlAnswer:
    ok: bool
    sql: str | None  # the guard-validated SQL that was executed
    result: ExecResult | None
    attempts: list[SqlAttempt] = field(default_factory=list)
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_sql(text: str) -> str:
    """SQL from a model reply: the first fenced block if any, else all of it; no trailing ';'."""
    m = _FENCE.search(text)
    sql = (m.group(1) if m else text).strip()
    return sql.rstrip(";").strip()


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.prompt_tokens + b.prompt_tokens,
        a.output_tokens + b.output_tokens,
        a.thinking_tokens + b.thinking_tokens,
    )


class TextToSql:
    def __init__(
        self,
        llm: LLMClient,
        guard: Guard,
        executor: Executor,
        policy: SqlPolicy,
        *,
        max_retries: int = 2,
        tier: Tier = "main",
    ) -> None:
        self._llm, self._guard, self._executor = llm, guard, executor
        self._max_retries, self._tier = max_retries, tier
        self._system = SYSTEM_TEMPLATE.format(schema=policy.describe_schema())

    def run(self, question: str) -> SqlAnswer:
        history = [Message("user", question)]
        answer = SqlAnswer(ok=False, sql=None, result=None)
        for _ in range(self._max_retries + 1):
            resp = self._llm.generate(
                history,
                system=self._system,
                tier=self._tier,
                temperature=0.0,
                max_output_tokens=1500,
            )
            answer.usage = _add(answer.usage, resp.usage)
            if resp.message is not None:
                history.append(resp.message)
            else:
                history.append(Message("model", resp.text))
            if resp.text.strip().upper().startswith("CANNOT_ANSWER"):
                answer.error = resp.text.strip()
                return answer
            sql = extract_sql(resp.text)

            verdict = self._guard.validate(sql)  # SqlGuardUnavailableError propagates: fail closed
            if not verdict.ok:
                reasons = "; ".join(verdict.reasons)
                answer.attempts.append(SqlAttempt(sql, "rejected", reasons))
                answer.error = f"query rejected by the safety check: {reasons}"
                history.append(
                    Message(
                        "user",
                        f"That query was rejected: {reasons}. Fix it and reply with only the SQL.",
                    )
                )
                continue
            assert verdict.sql is not None
            try:
                result = self._executor.execute(verdict.sql)
            except SqlTimeoutError as e:
                answer.attempts.append(SqlAttempt(verdict.sql, "timeout", str(e)))
                answer.error = str(e)
                return answer
            except SqlExecutionError as e:
                answer.attempts.append(SqlAttempt(verdict.sql, "error", str(e)))
                answer.error = f"database error: {e}"
                history.append(
                    Message(
                        "user", f"That query failed with: {e}. Fix it and reply with only the SQL."
                    )
                )
                continue
            answer.attempts.append(SqlAttempt(verdict.sql, "ok"))
            answer.ok, answer.sql, answer.result, answer.error = True, verdict.sql, result, None
            return answer
        return answer

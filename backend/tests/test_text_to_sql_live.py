"""Text-to-SQL accuracy on the real stack: Gemini -> Go guard -> read-only executor -> data.

15 questions, each with an independent reference SQL run directly against the database. A case
passes when the model's result contains the reference numbers (allowing for units: dollars,
millions, billions, or a ratio reported as a percentage) or the reference names, with one row per
expected name. LLM answers are cached in .cache/ (temperature 0), so reruns are fast and stable.
Skipped without GEMINI_API_KEY or an ingested database.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from app.core.config import Settings
from app.db.conn import connect
from app.llm.gemini import build_gemini
from app.llm.types import LLMError
from app.tools.sql_exec import SqlExecutor
from app.tools.sql_guard import SqlGuardClient
from app.tools.sql_policy import load_policy
from app.tools.text_to_sql import SqlAnswer, TextToSql

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")
DSN_RO = os.environ.get("DATABASE_URL_RO", "postgresql://finrag_ro:finrag_ro@localhost:5432/finrag")
MIN_CORRECT = 12  # of 15; chosen after the first honest run, see docs/decisions.md


def _key() -> str | None:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


pytestmark = pytest.mark.skipif(not _key(), reason="GEMINI_API_KEY not set")

REV = "('Revenues','RevenueFromContractWithCustomerExcludingAssessedTax')"
JOIN = "FROM xbrl_facts x JOIN companies c ON c.id = x.company_id"


def fact(ticker: str, concept: str, fy: int) -> str:
    """Reference SQL for one stored value."""
    return (
        f"SELECT x.value {JOIN} WHERE c.ticker = '{ticker}' AND x.concept = '{concept}' "
        f"AND x.fiscal_year = {fy} AND x.unit = 'USD'"
    )


def scalar(ticker: str, concept: str, fy: int) -> str:
    return f"({fact(ticker, concept, fy)})"


def ratio(ticker: str, num: str, den: str, fy: int) -> str:
    return f"SELECT {scalar(ticker, num, fy)} / {scalar(ticker, den, fy)}"


def growth(ticker: str, concept: str, y0: int, y1: int) -> str:
    return f"SELECT {scalar(ticker, concept, y1)} / {scalar(ticker, concept, y0)} - 1"


def years(ticker: str, concept: str, ys: str) -> str:
    return (
        f"SELECT x.value {JOIN} WHERE c.ticker = '{ticker}' AND x.concept = '{concept}' "
        f"AND x.fiscal_year IN {ys} AND x.unit = 'USD'"
    )


def top_ticker(where: str) -> str:
    return (
        f"SELECT c.ticker {JOIN} WHERE x.fiscal_year = {where} AND x.unit = 'USD' "
        "ORDER BY x.value DESC LIMIT 1"
    )


@dataclass
class Case:
    question: str
    reference: str  # SQL run directly on the database; first column = expected values
    kind: str  # "numbers" | "names"
    pct: bool = False  # a ratio may be reported as a percentage


CASES = [
    Case(
        "What was Apple's net income in fiscal 2025?",
        fact("AAPL", "NetIncomeLoss", 2025),
        "numbers",
    ),
    Case(
        "What was NVIDIA's revenue in fiscal year 2026?", fact("NVDA", "Revenues", 2026), "numbers"
    ),
    Case(
        "What was Microsoft's operating income in fiscal 2026?",
        fact("MSFT", "OperatingIncomeLoss", 2026),
        "numbers",
    ),
    Case(
        "Which of Apple, Alphabet, Amazon and Meta had the highest net income in 2025?",
        top_ticker(
            "2025 AND x.concept = 'NetIncomeLoss' AND c.ticker IN ('AAPL','GOOGL','AMZN','META')"
        ),
        "names",
    ),
    Case(
        "What was Amazon's operating margin in 2025?",
        ratio(
            "AMZN",
            "OperatingIncomeLoss",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            2025,
        ),
        "numbers",
        pct=True,
    ),
    Case(
        "Show Alphabet's research and development expense for 2023, 2024 and 2025.",
        years("GOOGL", "ResearchAndDevelopmentExpense", "(2023, 2024, 2025)"),
        "numbers",
    ),
    Case(
        "How many companies are covered in the database?",
        "SELECT count(*) FROM companies",
        "numbers",
    ),
    Case("List the tickers of all covered companies.", "SELECT ticker FROM companies", "names"),
    Case(
        "By what percent did Meta's operating cash flow grow from 2024 to 2025?",
        growth("META", "NetCashProvidedByUsedInOperatingActivities", 2024, 2025),
        "numbers",
        pct=True,
    ),
    Case(
        "What was ExxonMobil's net income in each of 2023, 2024 and 2025?",
        years("XOM", "NetIncomeLoss", "(2023, 2024, 2025)"),
        "numbers",
    ),
    Case(
        "Which fiscal years of 10-K filings do we have for NVIDIA?",
        "SELECT f.fiscal_year::text FROM filings f JOIN companies c ON c.id = f.company_id "
        "WHERE c.ticker = 'NVDA'",
        "names",
    ),
    Case(
        "What were JPMorgan's total assets in 2025, in billions of dollars?",
        fact("JPM", "Assets", 2025),
        "numbers",
    ),
    Case(
        "How much did Apple spend on share repurchases in fiscal 2025?",
        fact("AAPL", "PaymentsForRepurchaseOfCommonStock", 2025),
        "numbers",
    ),
    Case(
        "Which company had the largest revenue in fiscal 2025?",
        top_ticker(f"2025 AND x.concept IN {REV}"),
        "names",
    ),
    Case(
        "By what percent did Microsoft's net income grow from fiscal 2025 to fiscal 2026?",
        growth("MSFT", "NetIncomeLoss", 2025, 2026),
        "numbers",
        pct=True,
    ),
]

SCALES = (1.0, 1e-3, 1e-6, 1e-9)


def close(a: float, b: float, rel: float = 2e-3) -> bool:
    return abs(a - b) <= rel * max(abs(b), 1e-12)


def numbers_match(expected: list[float], rows: list[list[object]], pct: bool) -> bool:
    cells = [
        float(v) for r in rows for v in r if isinstance(v, int | float) and not isinstance(v, bool)
    ]
    for e in expected:
        scales = [*SCALES, 100.0] if pct else SCALES
        if not any(close(c, e * s) for c in cells for s in scales):
            return False
    return True


def names_match(expected: list[str], rows: list[list[object]]) -> bool:
    """All expected names appear, and the answer has exactly one row per expected name.

    Extra descriptive columns (company name, dates) are fine; extra rows are not, because "which
    company" needs one answer, not a table to pick from. The first version of this check compared
    the whole cell set and wrongly failed answers that merely added columns (2 of the 15).
    """
    got = {str(v) for r in rows for v in r}
    return set(expected) <= got and len(rows) == len(expected)


@pytest.fixture(scope="module")
def stack(guard_url: str) -> Iterator[tuple[TextToSql, psycopg.Connection[dict[str, object]]]]:
    try:
        conn = connect(DSN)
    except psycopg.OperationalError as e:
        pytest.skip(f"database unavailable: {e}")
    row = conn.execute("SELECT count(*) AS n FROM xbrl_facts").fetchone()
    if not row or int(row["n"]) < 500:  # type: ignore[call-overload]
        pytest.skip("database not populated (run make ingest)")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=DSN,
        database_url_ro=DSN_RO,
        edgar_user_agent="x",
        gemini_api_key=_key(),
    )
    cache = ROOT / ".cache" / "llm_text_to_sql_test.sqlite"
    cache.parent.mkdir(exist_ok=True)
    policy = load_policy()
    gen = TextToSql(
        build_gemini(settings, cache),
        SqlGuardClient(guard_url),
        SqlExecutor(DSN_RO, max_rows=policy.max_limit),
        policy,
    )
    with conn:
        yield gen, conn


def test_fifteen_questions_match_reference_results(
    stack: tuple[TextToSql, psycopg.Connection[dict[str, object]]],
) -> None:
    gen, conn = stack
    outcomes: list[tuple[bool, str]] = []
    for case in CASES:
        fetched = conn.execute(case.reference).fetchall()
        ref_rows = [list(r.values()) for r in fetched]
        assert ref_rows, f"reference SQL returned nothing: {case.reference}"
        expected = [r[0] for r in ref_rows]
        try:
            ans: SqlAnswer = gen.run(case.question)
        except LLMError as e:
            pytest.skip(f"LLM unavailable right now: {e}")
        if not ans.ok or ans.result is None:
            ok, note = False, f"no result ({ans.error})"
        elif case.kind == "numbers":
            nums = [float(e) for e in expected]  # type: ignore[arg-type]
            ok = numbers_match(nums, ans.result.rows, case.pct)
            note = f"{len(ans.attempts)} attempt(s), sql: {ans.sql}"
        else:
            ok = names_match([str(e) for e in expected], ans.result.rows)
            note = f"{len(ans.attempts)} attempt(s), sql: {ans.sql}"
        outcomes.append((ok, f"{'PASS' if ok else 'FAIL'} {case.question} -> {note}"))
    print("\n" + "\n".join(o[1] for o in outcomes))
    correct = sum(ok for ok, _ in outcomes)
    print(f"\n{correct}/{len(CASES)} correct")
    assert correct >= MIN_CORRECT, f"only {correct}/{len(CASES)} correct"

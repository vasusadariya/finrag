"""Assembles the agent's tool set and its model-facing descriptions."""

from app.llm.types import ToolSpec
from app.retrieval.types import SECTIONS
from app.tools.calculator import calculator_tool
from app.tools.filing_search import FilingSearch
from app.tools.financial_sql import FinancialSql
from app.tools.registry import ToolRegistry

FILING_SEARCH = ToolSpec(
    name="filing_search",
    description=(
        "Search the text of 10-K filings (business description, risk factors, MD&A, notes, "
        "legal proceedings). Use for qualitative questions: strategy, risks, explanations, "
        "policies. Narrow with ticker, fiscal_year and section whenever you can: section "
        "choice matters a lot. Item 1 business, 1A risk factors, 3 legal proceedings, "
        "7 MD&A, 7A market risk, 8 financial statements and notes. Returns passages with "
        "citation ids to cite."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for, in natural language."},
            "ticker": {"type": "string", "description": "Company ticker, e.g. AAPL."},
            "fiscal_year": {"type": "integer", "description": "Fiscal year, e.g. 2024."},
            "section": {
                "type": "string",
                "enum": sorted(SECTIONS),
                "description": "10-K Item to restrict to.",
            },
            "k": {"type": "integer", "description": "Passages to return (default 5, max 8)."},
        },
        "required": ["query"],
    },
)
FINANCIAL_SQL = ToolSpec(
    name="financial_sql",
    description=(
        "Look up reported financial figures (revenue, net income, assets, R&D, cash flow, ...) "
        "for the covered companies and years. Describe what you need in plain English; a query "
        "is written, safety-checked and run for you. Returns exact values in full units (USD) "
        "plus the SQL used. Never do arithmetic yourself: pass the values to calculator."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "A self-contained question, naming company, metric and years.",
            }
        },
        "required": ["question"],
    },
)
CALCULATOR = ToolSpec(
    name="calculator",
    description=(
        "Exact arithmetic. Expression may use + - * / ** parentheses and the functions "
        "pct_change(old, new), cagr(start, end, years), pct_of(part, whole), ratio(a, b), "
        "round(x, n), abs, min, max, sqrt, log. Use plain numbers without commas. Results "
        "of pct_change, cagr and pct_of are percentages."
    ),
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "e.g. cagr(100, 150, 3)"}},
        "required": ["expression"],
    },
)


def build_registry(search: FilingSearch, sql: FinancialSql) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FILING_SEARCH, search)
    reg.register(FINANCIAL_SQL, sql)
    reg.register(CALCULATOR, calculator_tool)
    return reg

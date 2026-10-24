"""The production wiring: models, retrievers, guard and executor behind one ``run`` call."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from app.agent.loop import Agent
from app.agent.types import AgentResult
from app.core.config import Settings
from app.db.conn import connect
from app.embed.local_bge import LocalBgeProvider
from app.llm.gemini import build_gemini
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.keyword import KeywordRetriever
from app.retrieval.rerank import LocalBgeReranker, RerankingRetriever
from app.retrieval.types import Retriever
from app.retrieval.vector import VectorRetriever
from app.tools.builder import new_bundle
from app.tools.sql_exec import SqlExecutor
from app.tools.sql_guard import SqlGuardClient
from app.tools.sql_policy import load_policy
from app.tools.text_to_sql import TextToSql

log = logging.getLogger(__name__)
EventSink = Callable[[dict[str, Any]], None]


class AnswerService(Protocol):
    def run(self, question: str, on_event: EventSink | None = None) -> AgentResult: ...

    def health(self) -> dict[str, bool]: ...


def describe_coverage(dsn: str) -> str:
    """e.g. 'AAPL (Apple Inc.): FY2022-FY2024; MSFT ...' from what is actually loaded."""
    with connect(dsn) as conn:
        rows = conn.execute(
            "SELECT c.ticker, c.name, min(f.fiscal_year) AS lo, max(f.fiscal_year) AS hi"
            " FROM companies c JOIN filings f ON f.company_id = c.id"
            " GROUP BY c.ticker, c.name ORDER BY c.ticker"
        ).fetchall()
    parts = [f"{r['ticker']} ({r['name']}): FY{r['lo']}-FY{r['hi']}" for r in rows]
    return "10-K filings for " + "; ".join(parts) if parts else "no filings loaded"


class LiveService:
    """Heavy models are created once; connections and retrievers are created per request."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._llm = build_gemini(settings)
        self._embedder = LocalBgeProvider()
        self._reranker = LocalBgeReranker() if settings.rerank_enabled else None
        self._rerank_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rerank")
        self._guard = SqlGuardClient(settings.sqlguard_url)
        self._sql_exec = SqlExecutor(settings.database_url_ro)
        self._policy = load_policy()
        self._coverage = describe_coverage(settings.database_url)

    def run(self, question: str, on_event: EventSink | None = None) -> AgentResult:
        with connect(self._s.database_url) as conn:
            hybrid = HybridRetriever(VectorRetriever(conn, self._embedder), KeywordRetriever(conn))
            retriever: Retriever = hybrid
            if self._reranker is not None:
                retriever = RerankingRetriever(hybrid, self._reranker, executor=self._rerank_pool)
            t2s = TextToSql(self._llm, self._guard, self._sql_exec, self._policy)
            agent = Agent(self._llm, lambda: new_bundle(retriever, t2s), self._coverage)
            return agent.run(question, on_event)

    def health(self) -> dict[str, bool]:
        try:
            with connect(self._s.database_url) as conn:
                conn.execute("SELECT 1")
            db = True
        except Exception:  # noqa: BLE001 - health must report, not raise
            db = False
        return {"db": db, "sql_guard": self._guard.healthy()}

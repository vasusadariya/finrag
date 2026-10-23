"""Retrieval metrics over ranked hit lists, scored against gold sources."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.evalkit.dataset import GoldSource, Question
from app.retrieval.types import Hit

KS = (1, 5, 10)


@dataclass
class QueryResult:
    qid: str
    type: str
    first_rank: int | None  # 1-based rank of the first hit matching ANY gold source
    covered: int  # distinct gold sources matched somewhere in the returned hits
    n_gold: int
    seconds: float = 0.0
    top_logit: float | None = None  # best reranker logit, when the variant reranks
    extra: dict[str, float] = field(default_factory=dict)


def score_query(q: Question, hits: Sequence[Hit], seconds: float = 0.0) -> QueryResult:
    gold: list[GoldSource] = q.gold_sources
    first: int | None = None
    matched: set[int] = set()
    for rank, hit in enumerate(hits, start=1):
        for gi, g in enumerate(gold):
            if g.matches(hit):
                matched.add(gi)
                first = first or rank
    logit = hits[0].debug.get("rerank_logit") if hits else None
    return QueryResult(q.id, q.type, first, len(matched), len(gold), seconds, logit)


def summarize(results: Sequence[QueryResult]) -> dict[str, float]:
    """hit@k (any gold source in top k), MRR@10, and mean coverage of gold sources."""
    n = len(results)
    if n == 0:
        return {"n": 0}
    out: dict[str, float] = {"n": float(n)}
    for k in KS:
        out[f"hit@{k}"] = sum(1 for r in results if r.first_rank and r.first_rank <= k) / n
    out["mrr@10"] = (
        sum(1.0 / r.first_rank for r in results if r.first_rank and r.first_rank <= 10) / n
    )
    out["coverage"] = sum(r.covered / r.n_gold for r in results) / n
    times = sorted(r.seconds for r in results)
    out["p50_s"] = statistics.median(times)
    out["p95_s"] = times[min(n - 1, int(round(0.95 * (n - 1))))]
    return out


def by_type(results: Sequence[QueryResult]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[QueryResult]] = {}
    for r in results:
        groups.setdefault(r.type, []).append(r)
    return {t: summarize(rs) for t, rs in sorted(groups.items())}


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """P(random positive scores higher than random negative); ties count half."""
    if not positives or not negatives:
        raise ValueError("need both positive and negative scores")
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))

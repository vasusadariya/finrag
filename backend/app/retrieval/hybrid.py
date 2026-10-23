"""Variant B: hybrid search = vector + keyword lists fused with Reciprocal Rank Fusion."""

from dataclasses import replace

from app.retrieval.types import Filters, Hit, Retriever

RRF_K = 60  # the constant from the original RRF paper; damps the advantage of top ranks


def rrf(rankings: dict[str, list[Hit]], k: int = RRF_K) -> list[Hit]:
    """Fuse ranked lists: score(d) = sum over lists of 1 / (k + rank(d)), ranks starting at 1.

    Documents are identified by chunk_id. Ties break by best single rank, then chunk_id,
    so the order is deterministic. Each result's ``debug`` records its 1-based rank in
    every list that contained it.
    """

    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    ranks: dict[int, dict[str, float]] = {}
    first: dict[int, Hit] = {}
    for name, hits in rankings.items():
        for rank, hit in enumerate(hits, start=1):
            cid = hit.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            best_rank[cid] = min(best_rank.get(cid, rank), rank)
            ranks.setdefault(cid, {})[f"{name}_rank"] = float(rank)
            first.setdefault(cid, hit)
    order = sorted(scores, key=lambda c: (-scores[c], best_rank[c], c))
    return [replace(first[c], score=scores[c], debug=ranks[c]) for c in order]


class HybridRetriever:
    """Runs both retrievers for ``candidates`` results each, fuses them, returns the top k."""

    def __init__(
        self, vector: Retriever, keyword: Retriever, candidates: int = 50, rrf_k: int = RRF_K
    ) -> None:
        if candidates < 1:
            raise ValueError("candidates must be >= 1")
        self._vector, self._keyword = vector, keyword
        self._candidates, self._rrf_k = candidates, rrf_k

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be >= 1")
        n = max(self._candidates, k)
        lists = {
            "vector": self._vector.search(query, filters, n),
            "keyword": self._keyword.search(query, filters, n),
        }
        return rrf(lists, self._rrf_k)[:k]

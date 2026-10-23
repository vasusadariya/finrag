"""Variant C: rerank a candidate pool with a cross-encoder; fall back to input order on failure."""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import Any, Protocol

from app.retrieval.types import Filters, Hit, Retriever

log = logging.getLogger(__name__)


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance logits, higher = more relevant, one per passage."""
        ...


class LocalBgeReranker:
    """BAAI/bge-reranker-base cross-encoder on CPU (512-token window)."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", batch_size: int = 16) -> None:
        self._model_name, self._batch_size = model_name, batch_size
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, max_length=512, device="cpu")
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        from torch.nn import Identity

        # predict() applies a sigmoid by default; the Reranker protocol promises raw logits
        out = self._load().predict(
            [(query, p) for p in passages],
            batch_size=self._batch_size,
            show_progress_bar=False,
            activation_fn=Identity(),
        )
        return [float(x) for x in out]


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


class RerankingRetriever:
    """Fetch ``pool`` candidates from ``base``, rerank them, return the best ``k``.

    ``Hit.score`` becomes sigmoid(reranker logit) in [0, 1]; the logit and the input rank are
    kept in ``debug``. If the reranker raises or does not answer within ``timeout_s`` the base
    order is used (``debug['rerank_fallback'] = 1``), so a slow or broken model degrades
    quality, never availability.
    """

    def __init__(
        self, base: Retriever, reranker: Reranker, pool: int = 30, timeout_s: float = 20.0
    ) -> None:
        if pool < 1:
            raise ValueError("pool must be >= 1")
        self._base, self._reranker, self._pool, self._timeout = base, reranker, pool, timeout_s
        # one worker: a hung model call must not spawn unbounded threads;
        # later calls queue behind it and time out into the fallback
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rerank")

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        if k < 1:
            raise ValueError("k must be >= 1")
        candidates = self._base.search(query, filters, max(self._pool, k))
        if not candidates:
            return []
        try:
            future = self._executor.submit(
                self._reranker.score, query, [h.content for h in candidates]
            )
            logits = future.result(timeout=self._timeout)
            if len(logits) != len(candidates):
                raise ValueError(
                    f"reranker returned {len(logits)} scores for {len(candidates)} passages"
                )
        except (FutureTimeout, Exception) as e:  # noqa: BLE001 - any failure must fall back
            log.warning(
                "rerank failed, using base order: %s", type(e).__name__, extra={"error": str(e)}
            )
            return [replace(h, debug={**h.debug, "rerank_fallback": 1.0}) for h in candidates[:k]]
        order = sorted(range(len(candidates)), key=lambda i: (-logits[i], i))
        return [
            replace(
                candidates[i],
                score=sigmoid(logits[i]),
                debug={
                    **candidates[i].debug,
                    "rerank_logit": logits[i],
                    "input_rank": float(i + 1),
                },
            )
            for i in order[:k]
        ]

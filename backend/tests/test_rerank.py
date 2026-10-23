import math
import time

import pytest

from app.retrieval.rerank import RerankingRetriever, sigmoid
from app.retrieval.types import Filters, Hit


def hit(i: int, text: str | None = None) -> Hit:
    return Hit(i, 1.0 / i, text or f"passage {i}", "AAA", "Alpha", 2025, "Item 1", 1, "acc")


class Base:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, Filters | None, int]] = []

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        self.calls.append((query, filters, k))
        return self.hits[:k]


class ByLength:
    """Longer passage = more relevant."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(len(p)) for p in passages]


class Broken:
    def score(self, query: str, passages: list[str]) -> list[float]:
        raise RuntimeError("model crashed")


class Slow:
    def score(self, query: str, passages: list[str]) -> list[float]:
        time.sleep(1.0)
        return [0.0] * len(passages)


class WrongCount:
    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.0]


def test_reorders_by_reranker_and_truncates() -> None:
    base = Base([hit(1, "a"), hit(2, "bbbb"), hit(3, "cc"), hit(4, "dddddd")])
    out = RerankingRetriever(base, ByLength(), pool=4).search("q", k=2)
    assert [h.chunk_id for h in out] == [4, 2]
    assert out[0].debug["input_rank"] == 4.0 and out[0].debug["rerank_logit"] == 6.0
    assert out[0].score == pytest.approx(sigmoid(6.0)) and 0.0 < out[1].score < out[0].score <= 1.0
    assert "rerank_fallback" not in out[0].debug


def test_pool_and_filters_are_passed_to_base() -> None:
    base = Base([hit(i) for i in range(1, 50)])
    f = Filters(tickers=("AAA",))
    RerankingRetriever(base, ByLength(), pool=30).search("q", f, k=5)
    assert base.calls == [("q", f, 30)]
    RerankingRetriever(base, ByLength(), pool=3).search("q", f, k=7)
    assert base.calls[-1][2] == 7  # pool never smaller than k


def test_falls_back_to_base_order_when_reranker_raises() -> None:
    base = Base([hit(1), hit(2), hit(3)])
    out = RerankingRetriever(base, Broken(), pool=3).search("q", k=2)
    assert [h.chunk_id for h in out] == [1, 2]
    assert all(h.debug["rerank_fallback"] == 1.0 for h in out)
    assert out[0].score == pytest.approx(1.0)  # untouched base score


def test_falls_back_on_timeout_quickly() -> None:
    base = Base([hit(1), hit(2)])
    r = RerankingRetriever(base, Slow(), pool=2, timeout_s=0.1)
    start = time.monotonic()
    out = r.search("q", k=2)
    assert time.monotonic() - start < 0.6
    assert [h.chunk_id for h in out] == [1, 2] and out[0].debug["rerank_fallback"] == 1.0


def test_falls_back_on_wrong_score_count() -> None:
    out = RerankingRetriever(Base([hit(1), hit(2)]), WrongCount(), pool=2).search("q", k=2)
    assert out[0].debug["rerank_fallback"] == 1.0


def test_empty_candidates_and_validation() -> None:
    assert RerankingRetriever(Base([]), ByLength()).search("q") == []
    with pytest.raises(ValueError):
        RerankingRetriever(Base([]), ByLength(), pool=0)
    with pytest.raises(ValueError):
        RerankingRetriever(Base([hit(1)]), ByLength()).search("q", k=0)


def test_ties_keep_input_order() -> None:
    base = Base([hit(1, "same"), hit(2, "same"), hit(3, "same")])
    assert [h.chunk_id for h in RerankingRetriever(base, ByLength()).search("q", k=3)] == [1, 2, 3]


def test_sigmoid_is_stable_and_monotone() -> None:
    assert sigmoid(0) == 0.5
    assert sigmoid(-1000) == 0.0 and sigmoid(1000) == 1.0
    assert sigmoid(-2) < sigmoid(-1) < sigmoid(1) < sigmoid(2)
    assert math.isclose(sigmoid(2) + sigmoid(-2), 1.0)

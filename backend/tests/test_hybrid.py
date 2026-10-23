import math

import pytest

from app.retrieval.hybrid import HybridRetriever, rrf
from app.retrieval.types import Filters, Hit


def hit(i: int) -> Hit:
    return Hit(i, 0.0, f"c{i}", "AAA", "Alpha", 2025, "Item 1", 1, "acc")


def ids(hits: list[Hit]) -> list[int]:
    return [h.chunk_id for h in hits]


def test_rrf_scores_match_formula() -> None:
    fused = rrf({"v": [hit(1), hit(2), hit(3)], "k": [hit(3), hit(1), hit(4)]}, k=60)
    by = {h.chunk_id: h for h in fused}
    assert math.isclose(by[1].score, 1 / 61 + 1 / 62)
    assert math.isclose(by[3].score, 1 / 63 + 1 / 61)
    assert math.isclose(by[2].score, 1 / 62)
    assert math.isclose(by[4].score, 1 / 63)
    assert ids(fused) == [1, 3, 2, 4]


def test_rrf_records_ranks_and_dedupes() -> None:
    fused = rrf({"vector": [hit(1), hit(2)], "keyword": [hit(2)]})
    assert ids(fused) == [2, 1]
    assert fused[0].debug == {"vector_rank": 2.0, "keyword_rank": 1.0}
    assert fused[1].debug == {"vector_rank": 1.0}


def test_agreement_beats_a_single_list_winner() -> None:
    # chunk 9 is only 1st in one list; chunk 5 is 2nd in both
    fused = rrf({"v": [hit(9), hit(5)], "k": [hit(8), hit(5)]})
    assert fused[0].chunk_id == 5


def test_ties_are_deterministic() -> None:
    a = rrf({"v": [hit(2)], "k": [hit(1)]})  # equal scores, equal best rank -> lower chunk_id first
    b = rrf({"k": [hit(1)], "v": [hit(2)]})
    assert ids(a) == ids(b) == [1, 2]


def test_empty_inputs() -> None:
    assert rrf({}) == []
    assert rrf({"v": [], "k": []}) == []
    assert ids(rrf({"v": [hit(1)], "k": []})) == [1]


class Canned:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits, self.calls = hits, []  # type: ignore[var-annotated]

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        self.calls.append((query, filters, k))
        return self.hits[:k]


def test_hybrid_fuses_truncates_and_passes_through_filters() -> None:
    v, kw = Canned([hit(1), hit(2), hit(3)]), Canned([hit(3), hit(4)])
    f = Filters(tickers=("AAA",))
    out = HybridRetriever(v, kw, candidates=10).search("q", f, k=2)
    assert len(out) == 2 and out[0].chunk_id == 3
    assert v.calls == [("q", f, 10)] and kw.calls == [("q", f, 10)]


def test_candidates_never_below_k() -> None:
    v, kw = Canned([hit(1)]), Canned([hit(2)])
    HybridRetriever(v, kw, candidates=3).search("q", k=7)
    assert v.calls[0][2] == 7


def test_validation() -> None:
    with pytest.raises(ValueError):
        HybridRetriever(Canned([]), Canned([]), candidates=0)
    with pytest.raises(ValueError):
        HybridRetriever(Canned([]), Canned([])).search("q", k=0)

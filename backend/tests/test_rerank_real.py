"""Real cross-encoder (downloads ~1GB once). Skipped when the embed group isn't installed."""

import pytest

pytest.importorskip("sentence_transformers")

from app.retrieval.rerank import LocalBgeReranker  # noqa: E402


@pytest.fixture(scope="module")
def reranker() -> LocalBgeReranker:
    return LocalBgeReranker()


def test_relevant_passage_scores_highest(reranker: LocalBgeReranker) -> None:
    passages = [
        "We are exposed to interest rate risk on our fixed income investment portfolio.",
        "The Company's headquarters is located in Cupertino, California.",
        "Research and development expense increased due to higher headcount.",
    ]
    scores = reranker.score("Where is the company headquartered?", passages)
    assert len(scores) == 3
    assert scores.index(max(scores)) == 1
    assert max(scores) > 0 > min(scores)  # logits separate relevant from irrelevant


def test_empty_input(reranker: LocalBgeReranker) -> None:
    assert reranker.score("q", []) == []

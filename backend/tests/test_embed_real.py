"""Runs the real bge model (downloads ~440MB once). Skipped when the embed group isn't installed."""

import math

import pytest

pytest.importorskip("sentence_transformers")

from app.embed.local_bge import LocalBgeProvider  # noqa: E402


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def provider() -> LocalBgeProvider:
    return LocalBgeProvider()


def test_dimension_and_normalisation(provider: LocalBgeProvider) -> None:
    v = provider.embed_documents(["Apple designs the iPhone."])[0]
    assert len(v) == provider.dim == 768
    assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-3)


def test_query_retrieves_relevant_passage(provider: LocalBgeProvider) -> None:
    docs = [
        "Our headquarters are located in Cupertino, California, where we own campus buildings.",
        "We are exposed to interest rate risk on our fixed income investment portfolio.",
        "Research and development expense increased due to higher headcount-related costs.",
    ]
    d = provider.embed_documents(docs)
    q = provider.embed_query("Where is the company headquartered?")
    scores = [cos(q, x) for x in d]
    assert scores.index(max(scores)) == 0


def test_deterministic(provider: LocalBgeProvider) -> None:
    a = provider.embed_documents(["same text"])[0]
    b = provider.embed_documents(["same text"])[0]
    assert max(abs(x - y) for x, y in zip(a, b, strict=True)) < 1e-5

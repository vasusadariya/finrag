"""Retrieval confidence: how sure are we that the retrieved passages can answer the question?

Score = sigmoid(w_top * top_logit + w_gap * gap + bias) where top_logit is the best reranker logit
and gap is how far it is ahead of the runner-up. The defaults are a reasonable starting point
for bge-reranker logits (about > 0 relevant, about < -5 irrelevant); `fit` learns the weights
from labelled examples (answerable / not) and is run on the eval set, so that the threshold
means something measurable.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.retrieval.rerank import sigmoid
from app.retrieval.types import Hit


@dataclass(frozen=True)
class Features:
    top_logit: float
    gap: float


@dataclass(frozen=True)
class Params:
    w_top: float = 1.0
    w_gap: float = 0.15
    bias: float = 0.0


@dataclass(frozen=True)
class Confidence:
    score: float  # in [0, 1]
    answerable: bool  # score >= threshold
    reason: str  # short, human-readable; also used in refusal messages


DEFAULT_THRESHOLD = 0.5
DEFAULT_PARAMS = Params()


def features(hits: Sequence[Hit]) -> Features | None:
    """Reranker-based features, or None when the hits carry no reranker logits."""
    if not hits or "rerank_logit" not in hits[0].debug:
        return None
    top = hits[0].debug["rerank_logit"]
    second = hits[1].debug.get("rerank_logit") if len(hits) > 1 else None
    return Features(top_logit=top, gap=0.0 if second is None else top - second)


def score(f: Features, params: Params = DEFAULT_PARAMS) -> float:
    return sigmoid(params.w_top * f.top_logit + params.w_gap * f.gap + params.bias)


def assess(
    hits: Sequence[Hit], threshold: float = DEFAULT_THRESHOLD, params: Params = DEFAULT_PARAMS
) -> Confidence:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    if not hits:
        return Confidence(0.0, False, "no passages retrieved")
    f = features(hits)
    if f is None:
        return Confidence(0.0, False, "reranker scores unavailable")
    s = score(f, params)
    ok = s >= threshold
    return Confidence(
        s, ok, "top passage is relevant" if ok else "no passage looks relevant enough"
    )


def fit(
    examples: Sequence[tuple[Features, bool]],
    l2: float = 0.01,
    steps: int = 2000,
    lr: float = 0.05,
) -> Params:
    """Logistic regression (L2-regularised gradient descent) on (features, was_answerable) pairs."""
    if not examples:
        raise ValueError("need at least one example")
    if len({y for _, y in examples}) < 2:
        raise ValueError("need both answerable and unanswerable examples")
    w_top, w_gap, bias = 0.0, 0.0, 0.0
    n = len(examples)
    for _ in range(steps):
        g_top = g_gap = g_bias = 0.0
        for f, y in examples:
            err = sigmoid(w_top * f.top_logit + w_gap * f.gap + bias) - (1.0 if y else 0.0)
            g_top += err * f.top_logit
            g_gap += err * f.gap
            g_bias += err
        w_top -= lr * (g_top / n + l2 * w_top)
        w_gap -= lr * (g_gap / n + l2 * w_gap)
        bias -= lr * (g_bias / n)
    if not all(math.isfinite(v) for v in (w_top, w_gap, bias)):
        raise ValueError("fit diverged")
    return Params(w_top, w_gap, bias)

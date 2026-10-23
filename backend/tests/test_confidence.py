import pytest

from app.retrieval.confidence import (
    Features,
    Params,
    assess,
    features,
    fit,
    score,
)
from app.retrieval.types import Hit


def hit(i: int, logit: float | None) -> Hit:
    debug = {} if logit is None else {"rerank_logit": logit}
    return Hit(i, 0.0, "t", "AAA", "Alpha", 2025, "Item 1", 1, "acc", debug)


def test_good_query_above_and_gibberish_below_threshold() -> None:
    good = [hit(1, 3.0), hit(2, -4.0), hit(3, -6.0)]  # clear winner
    gibberish = [hit(1, -9.5), hit(2, -10.2), hit(3, -10.4)]  # everything irrelevant
    assert assess(good).answerable and assess(good).score > 0.9
    assert not assess(gibberish).answerable and assess(gibberish).score < 0.05


def test_threshold_is_configurable() -> None:
    borderline = [hit(1, -1.0), hit(2, -5.0)]
    c = assess(borderline, threshold=0.5)
    assert not c.answerable
    assert assess(borderline, threshold=0.05).answerable
    assert assess(borderline, threshold=0.05).score == c.score


def test_score_increases_with_top_logit_and_gap() -> None:
    a, b = Features(-2.0, 1.0), Features(0.0, 1.0)
    assert score(b) > score(a)
    assert score(Features(0.0, 5.0)) > score(Features(0.0, 0.0))


def test_missing_inputs_are_not_answerable() -> None:
    assert assess([]).score == 0.0 and not assess([]).answerable
    c = assess([hit(1, None)])
    assert not c.answerable and "unavailable" in c.reason


def test_single_hit_has_zero_gap() -> None:
    assert features([hit(1, 2.0)]) == Features(2.0, 0.0)
    assert features([hit(1, 2.0), hit(2, 0.5)]) == Features(2.0, 1.5)


def test_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        assess([hit(1, 1.0)], threshold=1.5)


def test_fit_learns_a_separating_boundary() -> None:
    answerable = [(Features(t, g), True) for t, g in [(2, 3), (0.5, 2), (1, 5), (3, 1), (-0.5, 4)]]
    unanswerable = [
        (Features(t, g), False) for t, g in [(-9, 0.5), (-6, 1), (-7, 0), (-4, 0.2), (-8, 2)]
    ]
    params = fit(answerable + unanswerable)
    assert params.w_top > 0
    right = sum((score(f, params) >= 0.5) == y for f, y in answerable + unanswerable)
    assert right == 10
    assert score(Features(1.5, 2.0), params) > 0.5 > score(Features(-7.0, 0.5), params)


def test_fit_validates_inputs() -> None:
    with pytest.raises(ValueError):
        fit([])
    with pytest.raises(ValueError, match="both"):
        fit([(Features(1, 1), True), (Features(2, 1), True)])


def test_default_params_are_stable() -> None:
    assert Params() == Params(1.0, 0.15, 0.0)

import math

import pytest

from app.evalkit.dataset import Question
from app.evalkit.metrics import auc, by_type, score_query, summarize
from app.evalkit.retrieval_eval import confidence_report, has_text_gold, markdown, run_variant
from app.retrieval.types import Filters, Hit


def gold(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ticker": "AAPL",
        "fiscal_year": 2025,
        "section": "Item 2",
        "contains": "Cupertino",
    }
    return {**base, **kw}


def question(
    qid: str = "q001", type_: str = "factual", sources: list[dict[str, object]] | None = None
) -> Question:
    return Question.model_validate(
        {"id": qid, "question": "Where is Apple headquartered?", "type": type_, "tickers": ["AAPL"],
         "gold_sources": sources if sources is not None else [gold()], "gold_answer": "Cupertino",
         "tools": ["filing_search"]}
    )  # fmt: skip


def hit(
    i: int,
    content: str = "x",
    ticker: str = "AAPL",
    fy: int = 2025,
    section: str = "Item 2",
    logit: float | None = None,
) -> Hit:
    debug = {} if logit is None else {"rerank_logit": logit}
    return Hit(i, 0.0, content, ticker, "Apple", fy, section, 1, "acc", debug)


GOOD = "HQ is in Cupertino, California"


def test_first_rank_and_coverage() -> None:
    q = question(sources=[gold(), gold(ticker="MSFT", contains="Redmond")])
    hits = [hit(1), hit(2, GOOD), hit(3, "Redmond, WA", ticker="MSFT")]
    r = score_query(q, hits)
    assert (r.first_rank, r.covered, r.n_gold) == (2, 2, 2)
    miss = score_query(q, [hit(1), hit(2)])
    assert (miss.first_rank, miss.covered) == (None, 0)


def test_wrong_year_or_section_does_not_match() -> None:
    q = question()
    assert score_query(q, [hit(1, GOOD, fy=2024)]).first_rank is None
    assert score_query(q, [hit(1, GOOD, section="Item 1")]).first_rank is None
    assert score_query(q, [hit(1, GOOD)]).first_rank == 1


def test_summarize_toy_data() -> None:
    q = question()
    runs = [
        score_query(q, [hit(1, GOOD)], 1.0),  # rank 1
        score_query(q, [hit(1), hit(2), hit(3, GOOD)], 2.0),  # rank 3
        score_query(q, [hit(i) for i in range(1, 8)] + [hit(8, GOOD)], 3.0),  # rank 8
        score_query(q, [hit(1)], 4.0),  # miss
    ]
    s = summarize(runs)
    assert s["n"] == 4
    assert s["hit@1"] == 0.25 and s["hit@5"] == 0.5 and s["hit@10"] == 0.75
    assert math.isclose(s["mrr@10"], (1 + 1 / 3 + 1 / 8) / 4)
    assert s["coverage"] == 0.75
    assert s["p50_s"] == 2.5 and s["p95_s"] == 4.0
    assert summarize([]) == {"n": 0}


def test_by_type_groups() -> None:
    a = score_query(question("q001", "factual"), [hit(1, GOOD)])
    b = score_query(
        question("q002", "multi_hop", [gold(), gold(contains="Cupertino, C")]), [hit(1)]
    )
    g = by_type([a, b])
    assert g["factual"]["hit@5"] == 1.0 and g["multi_hop"]["hit@5"] == 0.0


def test_auc() -> None:
    assert auc([3, 4], [1, 2]) == 1.0
    assert auc([1, 2], [3, 4]) == 0.0
    assert auc([1, 1], [1, 1]) == 0.5
    assert math.isclose(auc([2, 4], [1, 3]), 0.75)
    with pytest.raises(ValueError):
        auc([], [1])


class Canned:
    def __init__(self, table: dict[str, list[Hit]]) -> None:
        self.table = table
        self.filters: list[Filters | None] = []

    def search(self, query: str, filters: Filters | None = None, k: int = 5) -> list[Hit]:
        self.filters.append(filters)
        return self.table.get(query, [])[:k]


def test_run_variant_respects_filter_setting() -> None:
    q = question()
    r = Canned({q.question: [hit(1, GOOD)]})
    res, hits = run_variant(r, [q], use_filters=True)
    assert res[0].first_rank == 1 and hits[q.id][0].chunk_id == 1
    assert r.filters[-1] == Filters(tickers=("AAPL",))
    run_variant(r, [q], use_filters=False)
    assert r.filters[-1] == Filters()


def test_has_text_gold_excludes_unanswerable_and_numeric_only() -> None:
    ref = {"ticker": "AAPL", "concept": "Assets", "fiscal_year": 2025, "value": 1}
    numeric = Question.model_validate(
        {
            "id": "q009",
            "question": "What was Apple's revenue?",
            "type": "factual",
            "gold_answer": "x",
            "xbrl_refs": [ref],
        }
    )
    un = Question.model_validate(
        {
            "id": "q010",
            "question": "Should I buy NVIDIA stock?",
            "type": "unanswerable",
            "unanswerable_reason": "advice",
        }
    )
    assert has_text_gold(question()) and not has_text_gold(numeric) and not has_text_gold(un)


def test_confidence_report_separates_and_handles_missing_logits() -> None:
    ans = {f"a{i}": [hit(1, logit=2.0 + i), hit(2, logit=-3.0)] for i in range(4)}
    una = {f"u{i}": [hit(1, logit=-8.0 - i), hit(2, logit=-9.0)] for i in range(4)}
    c = confidence_report(ans, una, 0.5)
    assert c["auc_top_logit"] == 1.0
    assert c["answerable_answered"] == 1.0 and c["unanswerable_refused"] == 1.0
    assert c["fit_w_top"] > 0
    assert confidence_report({"a": [hit(1)]}, {"u": [hit(1)]}, 0.5) == {}  # no logits -> no report


def test_markdown_renders_all_sections() -> None:
    q = question()
    res = [score_query(q, [hit(1, GOOD)], 0.1)]
    md = markdown({"With company filter": {"A vector": res}}, {"With company filter": {}}, 1)
    assert (
        "| A vector | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |" in md and "hit@5 by question type" in md
    )

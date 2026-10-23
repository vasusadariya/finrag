"""Why does retrieval miss? Classifies misses and measures the ceiling of filter routing.

    python -m app.evalkit.retrieval_diagnostics [--out eval/reports/diagnostics.md]

The "oracle" settings hand the retriever the gold section/year as filters. A real system cannot
know them, so those numbers are an upper bound showing how much a routing step (agent tool
arguments, a question-to-section router) could gain, not an achievable result.
"""

import argparse
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from app.evalkit.dataset import Question, load_questions
from app.evalkit.retrieval_eval import ROOT, has_text_gold
from app.retrieval.types import Filters, Hit, Retriever

FilterFn = Callable[[Question], Filters]


def classify_miss(q: Question, hits: list[Hit]) -> str:
    """hit | wrong_year_only | right_section_wrong_chunk | wrong_section"""
    if any(g.matches(h) for g in q.gold_sources for h in hits):
        return "hit"
    for g in q.gold_sources:
        for h in hits:
            if (
                g.ticker == h.ticker
                and g.section == h.section
                and g.contains.lower() in h.content.lower()
            ):
                return "wrong_year_only"
    if any(g.ticker == h.ticker and g.section == h.section for g in q.gold_sources for h in hits):
        return "right_section_wrong_chunk"
    return "wrong_section"


def oracle(sections: bool, years: bool) -> FilterFn:
    def make(q: Question) -> Filters:
        return Filters(
            tickers=tuple(q.tickers),
            sections=tuple(sorted({g.section for g in q.gold_sources})) if sections else (),
            fiscal_years=tuple(sorted({g.fiscal_year for g in q.gold_sources})) if years else (),
        )

    return make


SETTINGS: dict[str, FilterFn] = {
    "tickers only (what the eval uses)": lambda q: q.filters(),
    "ORACLE sections": oracle(True, False),
    "ORACLE sections + years": oracle(True, True),
}


def hit_rates(
    r: Retriever, qs: list[Question], mk: FilterFn, k: int = 5
) -> tuple[float, float, Counter[str]]:
    h1 = h5 = 0
    cats: Counter[str] = Counter()
    for q in qs:
        hits = r.search(q.question, mk(q), k)
        matches = [any(g.matches(h) for g in q.gold_sources) for h in hits]
        h1 += bool(matches and matches[0])
        h5 += any(matches)
        cats[classify_miss(q, hits)] += 1
    return h1 / len(qs), h5 / len(qs), cats


def main() -> None:
    from app.db.conn import connect
    from app.embed.local_bge import LocalBgeProvider
    from app.retrieval.hybrid import HybridRetriever
    from app.retrieval.keyword import KeywordRetriever
    from app.retrieval.vector import VectorRetriever

    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "eval" / "dataset" / "questions.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "reports" / "diagnostics.md"))
    args = ap.parse_args()
    qs = [q for q in load_questions(args.questions) if has_text_gold(q)]
    dsn = os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")
    with connect(dsn) as conn:
        vec = VectorRetriever(conn, LocalBgeProvider())
        variants: dict[str, Retriever] = {
            "A vector": vec,
            "B hybrid": HybridRetriever(vec, KeywordRetriever(conn)),
        }
        lines = [
            "# Retrieval diagnostics",
            "",
            f"{len(qs)} questions with text evidence. ORACLE rows give the retriever the gold "
            "section/year as filters: an upper bound for a routing step, not an achievable result.",
            "",
            "| filters | variant | hit@1 | hit@5 | miss: wrong section "
            "| miss: right section, wrong chunk | miss: wrong year only |",
            "|---|---|---|---|---|---|---|",
        ]
        for sname, mk in SETTINGS.items():
            for vname, r in variants.items():
                h1, h5, c = hit_rates(r, qs, mk)
                lines.append(
                    f"| {sname} | {vname} | {h1:.2f} | {h5:.2f} | {c['wrong_section']} | "
                    f"{c['right_section_wrong_chunk']} | {c['wrong_year_only']} |"
                )
    text = "\n".join(lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()

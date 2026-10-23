"""Retrieval ablation: vector (A) vs hybrid (B) vs hybrid+rerank (C) on the eval set.

python -m app.evalkit.retrieval_eval [--variants A,B,C] [--limit N] [--out eval/reports]
"""

import argparse
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

from app.evalkit.dataset import Question, load_questions
from app.evalkit.metrics import KS, QueryResult, auc, by_type, score_query, summarize
from app.retrieval.confidence import Features, assess, features, fit
from app.retrieval.types import Filters, Hit, Retriever

K = 10  # results fetched per query (hit@1/5/10 and MRR@10 are derived from this)
ROOT = Path(__file__).resolve().parents[3]


def has_text_gold(q: Question) -> bool:
    return q.answerable and bool(q.gold_sources)


def run_variant(
    retriever: Retriever, questions: Sequence[Question], use_filters: bool
) -> tuple[list[QueryResult], dict[str, list[Hit]]]:
    results: list[QueryResult] = []
    hits_by_q: dict[str, list[Hit]] = {}
    for q in questions:
        flt = q.filters() if use_filters else Filters()
        start = time.perf_counter()
        hits = retriever.search(q.question, flt, K)
        elapsed = time.perf_counter() - start
        hits_by_q[q.id] = hits
        results.append(score_query(q, hits, elapsed))
    return results, hits_by_q


def confidence_report(
    answerable: dict[str, list[Hit]], unanswerable: dict[str, list[Hit]], threshold: float
) -> dict[str, float]:
    """How well reranker confidence separates answerable from unanswerable questions."""
    pos = [f for hits in answerable.values() if (f := features(hits))]
    neg = [f for hits in unanswerable.values() if (f := features(hits))]
    if not pos or not neg:
        return {}
    out: dict[str, float] = {
        "auc_top_logit": auc([f.top_logit for f in pos], [f.top_logit for f in neg])
    }
    answered = [assess(h, threshold).answerable for h in answerable.values()]
    refused = [not assess(h, threshold).answerable for h in unanswerable.values()]
    out["threshold"] = threshold
    out["answerable_answered"] = sum(answered) / len(answered)
    out["unanswerable_refused"] = sum(refused) / len(refused)
    examples: list[tuple[Features, bool]] = [(f, True) for f in pos] + [(f, False) for f in neg]
    p = fit(examples)
    out.update({"fit_w_top": p.w_top, "fit_w_gap": p.w_gap, "fit_bias": p.bias})
    return out


def _row(name: str, res: Sequence[QueryResult]) -> str:
    s = summarize(res)
    hits = " | ".join(f"{s[f'hit@{k}']:.2f}" for k in KS)
    tail = f"{s['mrr@10']:.2f} | {s['coverage']:.2f} | {s['p50_s']:.2f} | {s['p95_s']:.2f}"
    return f"| {name} | {hits} | {tail} |"


def _confidence_text(c: dict[str, float]) -> str:
    return (
        f"AUC of the top reranker logit, answerable vs unanswerable: **{c['auc_top_logit']:.3f}**. "
        f"At threshold {c['threshold']}: answered {c['answerable_answered']:.0%} of answerable "
        f"questions, refused {c['unanswerable_refused']:.0%} of unanswerable ones. "
        f"Fitted weights (in-sample, small n): w_top={c['fit_w_top']:.2f}, "
        f"w_gap={c['fit_w_gap']:.2f}, bias={c['fit_bias']:.2f}."
    )


def markdown(
    tables: dict[str, dict[str, list[QueryResult]]], conf: dict[str, dict[str, float]], n_text: int
) -> str:
    lines = [
        "# Retrieval ablation",
        "",
        f"{n_text} questions with text evidence. hit@k = a gold passage is in the top k; "
        "coverage = share of a question's gold passages found in the top 10.",
        "",
    ]
    header = (
        "| variant | "
        + " | ".join(f"hit@{k}" for k in KS)
        + " | MRR@10 | coverage | p50 s | p95 s |"
    )
    for setting, variants in tables.items():
        lines += [f"## {setting}", "", header, "|---|" + "---|" * (len(KS) + 4)]
        lines += [_row(name, res) for name, res in variants.items()]
        lines += [
            "",
            "hit@5 by question type, last variant:",
            "",
            "| type | n | hit@5 |",
            "|---|---|---|",
        ]
        last = list(variants.values())[-1]
        lines += [f"| {t} | {int(s['n'])} | {s['hit@5']:.2f} |" for t, s in by_type(last).items()]
        lines.append("")
    for setting, c in conf.items():
        if c:
            lines += [f"## Confidence ({setting})", "", _confidence_text(c), ""]
    return "\n".join(lines)


def main() -> None:
    from app.db.conn import connect
    from app.embed.local_bge import LocalBgeProvider
    from app.retrieval.hybrid import HybridRetriever
    from app.retrieval.keyword import KeywordRetriever
    from app.retrieval.rerank import LocalBgeReranker, RerankingRetriever
    from app.retrieval.vector import VectorRetriever

    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "eval" / "dataset" / "questions.jsonl"))
    ap.add_argument("--variants", default="A,B,C")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--out", default=str(ROOT / "eval" / "reports"))
    args = ap.parse_args()

    qs = load_questions(args.questions)
    text_qs = [q for q in qs if has_text_gold(q)]
    unans = [q for q in qs if not q.answerable]
    if args.limit:
        text_qs, unans = text_qs[: args.limit], unans[: max(3, args.limit // 3)]
    dsn = os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")
    with connect(dsn) as conn:
        emb = LocalBgeProvider()
        vec, kw = VectorRetriever(conn, emb), KeywordRetriever(conn)
        hyb = HybridRetriever(vec, kw)
        all_variants: dict[str, Retriever] = {
            "A vector": vec,
            "B hybrid (RRF)": hyb,
            "C hybrid + rerank": RerankingRetriever(
                hyb, LocalBgeReranker(), pool=30, timeout_s=600
            ),
        }
        wanted = {v.strip() for v in args.variants.split(",")}
        variants = {n: r for n, r in all_variants.items() if n[0] in wanted}
        tables: dict[str, dict[str, list[QueryResult]]] = {}
        conf: dict[str, dict[str, float]] = {}
        details: dict[str, object] = {}
        for setting, use_filters in (("With company filter", True), ("No filter", False)):
            tables[setting] = {}
            for name, r in variants.items():
                print(f"[{setting}] {name} ...", flush=True)
                res, hits = run_variant(r, text_qs, use_filters)
                tables[setting][name] = res
                details[f"{setting}/{name}"] = [vars(x) for x in res]
                if name.startswith("C"):
                    _, un_hits = run_variant(r, unans, use_filters)
                    conf[setting] = confidence_report(hits, un_hits, args.threshold)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "retrieval.md").write_text(markdown(tables, conf, len(text_qs)) + "\n")
    (out / "retrieval_details.json").write_text(json.dumps(details, indent=1, default=str))
    print((out / "retrieval.md").read_text())


if __name__ == "__main__":
    main()

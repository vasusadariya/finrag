"""CLI: ``python -m app.evalkit.agent_cli run|score [--limit N] [--ids q001,q002]``."""

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.db.conn import connect
from app.evalkit.agent_eval import dump_details, render_report, run_questions, score, summarize
from app.evalkit.dataset import load_questions
from app.llm.gemini import build_gemini

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = ROOT / "eval" / "dataset" / "questions.jsonl"
RECORDS = ROOT / "eval" / "reports" / "agent_runs.jsonl"
REPORT = ROOT / "eval" / "reports" / "agent.md"
DETAILS = ROOT / "eval" / "reports" / "agent_details.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "score"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma separated question ids")
    ap.add_argument("--records", type=Path, default=RECORDS)
    ap.add_argument("--fresh", action="store_true", help="ignore existing records")
    ap.add_argument("--title", default="Agent evaluation (variant D)")
    args = ap.parse_args(argv)

    settings = get_settings()
    questions = load_questions(QUESTIONS)
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q.id in wanted]
    if args.limit:
        questions = questions[: args.limit]

    if args.cmd == "run":
        from app.api.service import LiveService

        records = run_questions(
            LiveService(settings), questions, args.records, resume=not args.fresh
        )
        print(f"ran {len(records)} questions -> {args.records}")
        return 0

    import json

    records = [json.loads(line) for line in args.records.read_text().splitlines() if line.strip()]
    wanted_ids = {q.id for q in questions}
    records = [r for r in records if r["id"] in wanted_ids]
    judge_llm = build_gemini(settings, cache_path=ROOT / ".cache" / "judge.sqlite")
    with connect(settings.database_url) as conn:
        scored = score(questions, records, conn, judge_llm)
    summary = summarize(scored)
    REPORT.write_text(render_report(summary, scored, args.title))
    dump_details(DETAILS, scored)
    print(REPORT.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())

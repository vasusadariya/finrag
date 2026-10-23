"""CLI: validate the eval dataset.  python -m app.evalkit.validate [--db] [path]"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from app.db.conn import connect
from app.evalkit.dataset import load_questions, validate_against_db, validate_set

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "eval" / "dataset" / "questions.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT_PATH))
    ap.add_argument(
        "--db", action="store_true", help="also resolve gold sources/facts in the database"
    )
    args = ap.parse_args()
    qs = load_questions(args.path)
    problems = validate_set(qs)
    print(f"{len(qs)} questions: {dict(Counter(q.type for q in qs))}")
    if args.db:
        dsn = os.environ.get("DATABASE_URL", "postgresql://finrag:finrag@localhost:5432/finrag")
        with connect(dsn) as conn:
            problems += validate_against_db(conn, qs)
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

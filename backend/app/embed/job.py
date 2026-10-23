"""Batch embedding job.

Reads JSON lines ``{"hash": ..., "text": ...}``
and writes JSON lines ``{"hash": ..., "embedding": [...]}``.
Vectors are cached on disk, so re-running (or resuming after a crash) only embeds new text.

    finrag-ingest chunks AAPL aapl.htm | python -m app.embed.job > emb.jsonl
"""

import argparse
import json
import sys
from typing import TextIO

from app.embed.cache import CachedEmbedder
from app.embed.local_bge import LocalBgeProvider


def run(embedder: CachedEmbedder, src: TextIO, dst: TextIO) -> int:
    rows = [json.loads(line) for line in src if line.strip()]
    vecs = embedder.embed_documents(
        [r["text"] for r in rows],
        on_batch=lambda done, total: print(f"embedded {done}/{total}", file=sys.stderr),
    )
    for row, vec in zip(rows, vecs, strict=True):
        dst.write(json.dumps({"hash": row["hash"], "embedding": vec}) + "\n")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".cache/embeddings.sqlite")
    ap.add_argument("--batch-size", type=int, default=16)
    args = ap.parse_args()
    embedder = CachedEmbedder(
        LocalBgeProvider(batch_size=args.batch_size), args.cache, args.batch_size
    )
    n = run(embedder, sys.stdin, sys.stdout)
    print(f"{n} chunks, {embedder.hits} cache hits, {embedder.misses} embedded", file=sys.stderr)


if __name__ == "__main__":
    main()

"""SQLite cache of embeddings keyed by (provider, dimension, sha256 of text)."""

import hashlib
import sqlite3
import struct
from collections.abc import Callable
from pathlib import Path

from app.embed.base import EmbeddingProvider, Vector


class DimensionError(ValueError):
    pass


def text_key(provider: EmbeddingProvider, text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return f"{provider.name}:{provider.dim}:{digest}"


def _pack(vec: Vector) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> Vector:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class CachedEmbedder:
    """Embeds documents in batches, skipping texts already in the cache (idempotent re-runs)."""

    def __init__(self, provider: EmbeddingProvider, path: str | Path, batch_size: int = 32) -> None:
        self.provider = provider
        self.batch_size = batch_size
        self._db = sqlite3.connect(str(path))
        self._db.execute("CREATE TABLE IF NOT EXISTS emb (key TEXT PRIMARY KEY, vec BLOB NOT NULL)")
        self.hits = 0
        self.misses = 0

    def close(self) -> None:
        self._db.close()

    def _get(self, key: str) -> Vector | None:
        row = self._db.execute("SELECT vec FROM emb WHERE key = ?", (key,)).fetchone()
        return _unpack(row[0]) if row else None

    def embed_documents(
        self, texts: list[str], on_batch: Callable[[int, int], None] | None = None
    ) -> list[Vector]:
        keys = [text_key(self.provider, t) for t in texts]
        found: dict[str, Vector] = {}
        todo: list[tuple[str, str]] = []
        for key, text in zip(keys, texts, strict=True):
            if key in found:
                continue
            cached = self._get(key)
            if cached is not None:
                found[key] = cached
                self.hits += 1
            elif all(key != k for k, _ in todo):
                todo.append((key, text))
        self.misses += len(todo)
        for i in range(0, len(todo), self.batch_size):
            batch = todo[i : i + self.batch_size]
            vecs = self.provider.embed_documents([t for _, t in batch])
            if len(vecs) != len(batch):
                raise DimensionError(
                    f"provider returned {len(vecs)} vectors for {len(batch)} texts"
                )
            for (key, _), vec in zip(batch, vecs, strict=True):
                if len(vec) != self.provider.dim:
                    raise DimensionError(f"expected {self.provider.dim} dims, got {len(vec)}")
                found[key] = vec
                self._db.execute("INSERT OR REPLACE INTO emb VALUES (?, ?)", (key, _pack(vec)))
            self._db.commit()
            if on_batch:
                on_batch(min(i + self.batch_size, len(todo)), len(todo))
        return [found[k] for k in keys]

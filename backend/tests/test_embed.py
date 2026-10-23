import io
import json
from pathlib import Path

import pytest

from app.embed.base import Vector
from app.embed.cache import CachedEmbedder, DimensionError
from app.embed.job import run


class FakeProvider:
    """Deterministic 4-d embedding; counts calls so tests can prove caching."""

    def __init__(self, name: str = "fake", dim: int = 4, wrong_dim: bool = False) -> None:
        self._name, self._dim, self._wrong = name, dim, wrong_dim
        self.calls: list[list[str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def dim(self) -> int:
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        self.calls.append(list(texts))
        n = self._dim + 1 if self._wrong else self._dim
        return [[float(len(t)) + i for i in range(n)] for t in texts]

    def embed_query(self, text: str) -> Vector:
        return self.embed_documents([text])[0]


def test_embeds_and_returns_in_order(tmp_path: Path) -> None:
    p = FakeProvider()
    e = CachedEmbedder(p, tmp_path / "c.sqlite", batch_size=2)
    out = e.embed_documents(["aa", "b", "cccc"])
    assert [v[0] for v in out] == [2.0, 1.0, 4.0]
    assert [len(c) for c in p.calls] == [2, 1]  # batched


def test_rerun_is_idempotent_and_hits_cache(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    p1 = FakeProvider()
    first = CachedEmbedder(p1, path).embed_documents(["x", "yy"])
    p2 = FakeProvider()
    e2 = CachedEmbedder(p2, path)
    second = e2.embed_documents(["x", "yy"])
    assert second == first
    assert p2.calls == []
    assert (e2.hits, e2.misses) == (2, 0)


def test_only_new_texts_are_embedded(tmp_path: Path) -> None:
    p = FakeProvider()
    e = CachedEmbedder(p, tmp_path / "c.sqlite")
    e.embed_documents(["old"])
    e.embed_documents(["old", "new"])
    assert p.calls[-1] == ["new"]


def test_duplicate_texts_embedded_once(tmp_path: Path) -> None:
    p = FakeProvider()
    out = CachedEmbedder(p, tmp_path / "c.sqlite").embed_documents(["same", "same", "same"])
    assert len(out) == 3 and sum(len(c) for c in p.calls) == 1


def test_provider_name_and_dim_isolate_cache(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    CachedEmbedder(FakeProvider("a"), path).embed_documents(["t"])
    other = FakeProvider("b")
    CachedEmbedder(other, path).embed_documents(["t"])
    assert other.calls == [["t"]]


def test_dimension_mismatch_rejected_and_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with pytest.raises(DimensionError):
        CachedEmbedder(FakeProvider(wrong_dim=True), path).embed_documents(["t"])
    ok = FakeProvider()
    CachedEmbedder(ok, path).embed_documents(["t"])
    assert ok.calls == [["t"]]


def test_job_jsonl_roundtrip(tmp_path: Path) -> None:
    src = io.StringIO('{"hash":"h1","text":"abc"}\n\n{"hash":"h2","text":"de"}\n')
    dst = io.StringIO()
    n = run(CachedEmbedder(FakeProvider(), tmp_path / "c.sqlite"), src, dst)
    rows = [json.loads(x) for x in dst.getvalue().splitlines()]
    assert n == 2
    assert [r["hash"] for r in rows] == ["h1", "h2"]
    assert all(len(r["embedding"]) == 4 for r in rows)

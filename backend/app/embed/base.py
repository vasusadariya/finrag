from typing import Protocol

Vector = list[float]


class EmbeddingProvider(Protocol):
    """Anything that can turn text into fixed-size, L2-normalised vectors."""

    @property
    def name(self) -> str:
        """Stable id; part of the cache key, so a different model never reuses old vectors."""
        ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...

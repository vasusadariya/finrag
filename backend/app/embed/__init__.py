"""Text embeddings: provider interface, local bge model, content-hash cache, batch job."""

from app.embed.base import EmbeddingProvider
from app.embed.cache import CachedEmbedder

__all__ = ["CachedEmbedder", "EmbeddingProvider"]

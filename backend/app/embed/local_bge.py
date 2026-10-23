from typing import Any

from app.embed.base import Vector

# bge models are trained with this instruction on the query side only.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class LocalBgeProvider:
    """BAAI bge-base-en-v1.5 on CPU via sentence-transformers (768-d, 512-token window)."""

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", batch_size: int = 16) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model: Any = None

    @property
    def name(self) -> str:
        return f"local:{self._model_name}"

    @property
    def dim(self) -> int:
        return 768

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self._model_name, device="cpu")
            model.max_seq_length = 512
            self._model = model
        return self._model

    def embed_documents(self, texts: list[str]) -> list[Vector]:
        vecs = self._load().encode(texts, batch_size=self._batch_size, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vecs]

    def embed_query(self, text: str) -> Vector:
        return self.embed_documents([QUERY_INSTRUCTION + text])[0]

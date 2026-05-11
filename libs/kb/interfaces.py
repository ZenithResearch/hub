from __future__ import annotations

from typing import Protocol, Sequence

from .models import Document, SearchHit


class EmbeddingProvider(Protocol):
    """Produces vector embeddings for text.

    Implementations should be deterministic for the same input, unless explicitly
    documented otherwise.
    """

    vector_dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def upsert_documents(self, docs: list[Document]) -> None: ...

    def search(
        self, query: str, doc_types: list[str] | None = None, k: int = 5
    ) -> list[SearchHit]: ...


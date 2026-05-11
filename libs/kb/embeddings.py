from __future__ import annotations

import hashlib
import math
from typing import Sequence

from .interfaces import EmbeddingProvider


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """Local, deterministic embedding stub.

    This is intentionally *not* semantically meaningful; it exists so the stack
    runs without external API keys. Replace with a real provider for production.
    """

    def __init__(self, *, vector_dim: int = 256):
        if vector_dim <= 0:
            raise ValueError("vector_dim must be positive")
        self.vector_dim = vector_dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        base = hashlib.sha256(text.encode("utf-8")).digest()
        vec: list[float] = []
        for i in range(self.vector_dim):
            h = hashlib.sha256(base + i.to_bytes(4, "little")).digest()
            u32 = int.from_bytes(h[:4], "little", signed=False)
            # Map [0, 2^32-1] -> [-1, 1]
            vec.append((u32 / 2**32) * 2.0 - 1.0)

        # Normalize to unit length to make cosine distance stable.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


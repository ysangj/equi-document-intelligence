"""Test doubles for the embedder (not a test module)."""

from __future__ import annotations

import hashlib
import math
import re

from app.models import EMBEDDING_DIM


class FakeEmbedder:
    """Deterministic bag-of-words hashing into 384 dims (unit length). Shared words ->
    higher cosine similarity, which is enough for ordering assertions."""

    def __init__(self, model_name: str = "test-fake-embedder", fail: bool = False):
        self.model_name = model_name
        self.dim = EMBEDDING_DIM
        self.fail = fail
        self.calls = 0
        self.texts_embedded = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v] if any(v) else [1.0 / math.sqrt(self.dim)] * self.dim

    def embed_passages(self, texts):
        from app.search.embedding import EmbeddingError

        self.calls += 1
        if self.fail:
            raise EmbeddingError("fake embedder failure")
        self.texts_embedded += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        from app.search.embedding import EmbeddingError

        if self.fail:
            raise EmbeddingError("fake embedder failure")
        return self._vec(text)

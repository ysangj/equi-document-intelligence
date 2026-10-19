"""Local embeddings (FR-7, §8): fastembed, ONNX on CPU, no API key.

- Model: `EMBEDDING_MODEL` (default `BAAI/bge-small-en-v1.5`, 384-dim, cosine).
- Weights are downloaded on first use into `./.cache/fastembed` (git-ignored) and
  reused afterwards.
- The model is loaded once per process and kept in memory: that is a cache, not
  durable state (PRD §2). Loading and inference are serialized by a lock, so the
  worker's job threads can share one instance.
- `embed_query(text)` is the query-side helper for Ask (T8); passages use
  `embed_passages`. fastembed applies the model's query/passage conventions.
- Tests swap in a deterministic fake with `set_embedder_override()`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from app.config import PROJECT_ROOT, get_settings
from app.models import EMBEDDING_DIM

log = logging.getLogger("app.search.embedding")

CACHE_DIR = PROJECT_ROOT / ".cache" / "fastembed"
LOAD_RETRY_SECONDS = 60.0
BATCH_SIZE = 32


class EmbeddingError(RuntimeError):
    """The model could not be loaded or run. Messages never contain document text."""


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir=CACHE_DIR):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.dim = EMBEDDING_DIM
        self._model = None
        self._lock = threading.Lock()
        self._failed_at: float | None = None

    def _load(self):
        if self._model is not None:
            return self._model
        if self._failed_at is not None and time.monotonic() - self._failed_at < LOAD_RETRY_SECONDS:
            raise EmbeddingError(f"embedding model {self.model_name} unavailable (load failed recently)")
        try:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            model = TextEmbedding(model_name=self.model_name, cache_dir=str(self.cache_dir))
        except Exception as e:  # noqa: BLE001
            self._failed_at = time.monotonic()
            raise EmbeddingError(f"could not load embedding model {self.model_name} ({type(e).__name__})") from e
        log.info("embedding model loaded", extra={"event": "embedding_model_loaded", "model": self.model_name,
                                                   "latency_ms": int((time.monotonic() - t0) * 1000)})
        self._model, self._failed_at = model, None
        return model

    def _check(self, vectors) -> list[list[float]]:
        out = [[float(x) for x in v] for v in vectors]
        for v in out:
            if len(v) != self.dim:
                raise EmbeddingError(f"model {self.model_name} returned {len(v)}-dim vectors; the schema stores {self.dim}")
        return out

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            model = self._load()
            try:
                return self._check(model.passage_embed(texts, batch_size=BATCH_SIZE))
            except EmbeddingError:
                raise
            except Exception as e:  # noqa: BLE001
                raise EmbeddingError(f"embedding failed ({type(e).__name__})") from e

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            model = self._load()
            try:
                return self._check(model.query_embed(text))[0]
            except EmbeddingError:
                raise
            except Exception as e:  # noqa: BLE001
                raise EmbeddingError(f"embedding failed ({type(e).__name__})") from e


_embedders: dict[str, FastEmbedEmbedder] = {}
_registry_lock = threading.Lock()
_override: Embedder | None = None


def set_embedder_override(embedder: Embedder | None) -> None:
    """Tests: make `get_embedder()` return `embedder` (None restores fastembed)."""
    global _override
    _override = embedder


def get_embedder(model_name: str | None = None) -> Embedder:
    """The process-wide embedder for `model_name` (default: settings.embedding_model)."""
    if _override is not None:
        return _override
    name = model_name or get_settings().embedding_model
    with _registry_lock:
        e = _embedders.get(name)
        if e is None:
            e = _embedders[name] = FastEmbedEmbedder(name)
        return e


def embed_query(text: str) -> list[float]:
    """384-dim query vector for hybrid retrieval (T8). Raises EmbeddingError."""
    return get_embedder().embed_query(text)

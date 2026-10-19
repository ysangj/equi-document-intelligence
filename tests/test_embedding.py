"""Embeddings (FR-7, §8). The `real_*` tests run fastembed with the cached
BAAI/bge-small-en-v1.5 model in ./.cache/fastembed (downloaded on first use)."""

from __future__ import annotations

import math
import subprocess
import sys
import types

import pytest
from sqlalchemy import text

from app.config import PROJECT_ROOT
from app.ingest import postprocess
from app.search import embedding
from app.search.embedding import CACHE_DIR, EmbeddingError, FastEmbedEmbedder, embed_query, get_embedder
from tests.pipeline_helpers import FIXED_TODAY, ingest_samples

MODEL = "BAAI/bge-small-en-v1.5"


@pytest.fixture(scope="module")
def real():
    return FastEmbedEmbedder(MODEL)


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_real_fastembed_vectors_and_semantics(real):
    passages = real.embed_passages([
        "Portfolio rate sensitivity remains low; a 25bp parallel move in Treasuries would move NAV by roughly 0.5%.",
        "January gains came primarily from short-term reversal signals in US large caps.",
    ])
    assert len(passages) == 2 and all(len(v) == 384 for v in passages)
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-3 for v in passages)
    q = real.embed_query("How exposed are the funds to interest-rate moves?")
    assert len(q) == 384
    assert _cos(q, passages[0]) > _cos(q, passages[1])
    assert real.embed_passages([]) == []


def test_real_cache_dir_is_populated_and_git_ignored(real):
    real.embed_query("warm up")
    assert CACHE_DIR == PROJECT_ROOT / ".cache" / "fastembed"
    assert CACHE_DIR.is_dir() and any(CACHE_DIR.rglob("*.onnx"))
    r = subprocess.run(["git", "check-ignore", "-q", str(CACHE_DIR)], cwd=PROJECT_ROOT)
    assert r.returncode == 0


def test_real_pipeline_embeddings_rank_synonyms(clean_db, tmp_path, monkeypatch, real):
    monkeypatch.setattr(postprocess, "_today", lambda: FIXED_TODAY)
    embedding.set_embedder_override(real)
    names = ["meridian_pm_note_feb2026.eml", "meridian_rv_tearsheet_2026-02.pdf", "kestrel_factsheet_2026-01.pdf",
             "alder_asia_fund_january2026.pdf"]
    ingest_samples(clean_db, tmp_path, names)
    with clean_db.connect() as c:
        stats = c.execute(text("SELECT count(*), count(embedding), min(embedding_model), max(embedding_model) "
                               "FROM document_chunks")).one()
        assert stats[0] == stats[1] > 0 and stats[2] == stats[3] == MODEL
        assert c.execute(text("SELECT bool_and(search_indexed) FROM documents")).scalar() is True
        qv = embed_query("What did managers say about interest-rate exposure?")
        top = c.execute(text("""SELECT d.name FROM document_chunks ch JOIN documents d ON d.id = ch.document_id
                                ORDER BY ch.embedding <=> CAST(:q AS vector) LIMIT 1"""), {"q": str(qv)}).scalar()
    # the PM note ("our interest-rate exposure ... very little outright rate sensitivity") ranks first
    assert top == "meridian_pm_note_feb2026.eml"


def test_embed_query_uses_the_process_embedder(fake_embedder):
    v = embed_query("duration")
    assert len(v) == 384 and v == fake_embedder.embed_query("duration")


def test_get_embedder_loads_once_per_model():
    embedding.set_embedder_override(None)
    try:
        a, b = get_embedder(MODEL), get_embedder(MODEL)
        assert a is b and isinstance(a, FastEmbedEmbedder)
        assert get_embedder("other/model") is not a
    finally:
        embedding._embedders.pop("other/model", None)


def test_load_failure_raises_and_cools_down(monkeypatch, tmp_path):
    calls = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append(1)
            raise OSError("no network")

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=Boom))
    e = FastEmbedEmbedder("x/model", cache_dir=tmp_path / "c")
    with pytest.raises(EmbeddingError, match="could not load embedding model x/model"):
        e.embed_passages(["a"])
    with pytest.raises(EmbeddingError, match="load failed recently"):
        e.embed_query("a")
    assert len(calls) == 1  # no retry storm within LOAD_RETRY_SECONDS
    monkeypatch.setattr(embedding, "LOAD_RETRY_SECONDS", 0.0)
    with pytest.raises(EmbeddingError):
        e.embed_query("a")
    assert len(calls) == 2


def test_wrong_dimension_is_an_embedding_error(monkeypatch, tmp_path):
    class Small:
        def __init__(self, *a, **k):
            pass

        def passage_embed(self, texts, batch_size=32):
            return [[0.1, 0.2] for _ in texts]

        def query_embed(self, text):
            raise RuntimeError("inference crashed")

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=Small))
    e = FastEmbedEmbedder("tiny/model", cache_dir=tmp_path / "c")
    with pytest.raises(EmbeddingError, match="2-dim"):
        e.embed_passages(["a"])
    with pytest.raises(EmbeddingError, match="embedding failed \\(RuntimeError\\)"):
        e.embed_query("a")

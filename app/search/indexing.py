"""Chunk storage and embedding (FR-7, §8).

Design: two steps, so search indexing can never fail or slow down structured
extraction.

1. `prepare_chunks()` runs INSIDE the job transaction (from `process_extraction`, in a
   savepoint). It chunks the document text and writes `document_chunks` rows with the
   text (so the generated `tsv` makes them keyword-searchable at commit) and
   `embedding = NULL`. If the chunk texts are identical to the stored ones (same
   document version re-extracted, same text), the rows are kept with their
   embeddings: re-embedding happens only when the version's text or the embedding
   model changes. Any error rolls back the savepoint only; the document still commits
   `done`/`needs_review`, with `search_indexed = false`.
2. `index_document()` runs AFTER the commit (the job thread calls it right away; the
   worker's index thread retries via `index_pending()`). It embeds chunks whose
   embedding is missing or was made by another model, outside any transaction, then
   writes the vectors in a short transaction that locks the document row and only
   updates chunks whose text is unchanged. `search_indexed = true` once every chunk
   has a vector from the current model.

Row lock (BUG-Q2-04): another job's transaction can hold this document's row for a
moment, e.g. a concurrent postprocess refreshing its conflict status. The lock is
tried with SKIP LOCKED first; on contention an `index_skipped_locked` line is logged
and the lock is retried, waiting at most `LOCK_WAIT_SECONDS` (Postgres
`lock_timeout`). Every value transaction is short, so the wait normally succeeds at
once; if it does not, a second `index_skipped_locked` line (`gave_up: true`) is
logged and the worker's index thread retries the document. After the wait the
document is re-checked, so a job that reprocessed it in the meantime still wins.

`index_pending()` selects visible, processed documents that are not indexed, or
whose chunks carry another `embedding_model` (a model change), so changing
EMBEDDING_MODEL re-embeds everything without re-extraction.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, exists, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.extraction.schema import Extraction as ExtractionSchema
from app.models import Document, DocumentChunk, Extraction
from app.search.chunking import Chunk, chunk_document
from app.search.embedding import Embedder, EmbeddingError, get_embedder

log = logging.getLogger("app.search.indexing")

INDEXABLE_STATUSES = ("done", "needs_review")
LOCK_WAIT_SECONDS = 5.0  # bounded wait for a document row held by another job's transaction
_LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE lock_not_available (lock_timeout expired)


def _lock_document(s: Session, document_id: int, *, step: str, wait_seconds: float) -> Document | None:
    """Lock the document row inside the current transaction. Tries SKIP LOCKED, then on
    contention logs `index_skipped_locked` and waits up to `wait_seconds`. Returns
    None when the row is gone, or still locked after the wait (logged, `gave_up`)."""
    stmt = select(Document).where(Document.id == document_id)
    doc = s.execute(stmt.with_for_update(skip_locked=True)).scalar_one_or_none()
    if doc is not None:
        return doc
    if not s.execute(select(exists().where(Document.id == document_id))).scalar():
        return None
    ctx = {"event": "index_skipped_locked", "document_id": document_id, "step": step}
    log.info("document row locked by another transaction; waiting", extra={
        **ctx, "gave_up": False, "wait_seconds": wait_seconds})
    if wait_seconds > 0:
        try:
            with s.begin_nested():
                s.execute(select(func.set_config("lock_timeout", f"{int(wait_seconds * 1000)}ms", True)))
                doc = s.execute(stmt.with_for_update()).scalar_one_or_none()
                s.execute(select(func.set_config("lock_timeout", "0", True)))
            return doc
        except OperationalError as e:
            if getattr(e.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
                raise
    log.warning("document row still locked; left to the index retry pass", extra={
        **ctx, "gave_up": True, "wait_seconds": wait_seconds})
    return None


def _not_current(model_name: str):
    return or_(DocumentChunk.embedding.is_(None), DocumentChunk.embedding_model.is_distinct_from(model_name))


def prepare_chunks(session: Session, doc: Document, transcribed_pages: list[str], model_name: str) -> bool:
    """Write this document's chunks (text only) in the current transaction. Returns
    the resulting `search_indexed` value (true only when nothing needs embedding)."""
    chunks: list[Chunk] = chunk_document(doc.text_content, doc.mime_type, transcribed_pages)
    existing = list(session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id).order_by(DocumentChunk.chunk_index)
    ).scalars())
    if [(c.page, c.text) for c in existing] == [(c.page, c.text) for c in chunks]:
        return all(c.embedding is not None and c.embedding_model == model_name for c in existing)
    for c in existing:
        session.delete(c)
    session.flush()
    session.add_all(DocumentChunk(document_id=doc.id, page=c.page, chunk_index=c.chunk_index, text=c.text)
                    for c in chunks)
    session.flush()
    return not chunks


def _transcription(session: Session, document_id: int) -> list[str]:
    raw = session.execute(
        select(Extraction.raw_json).where(Extraction.document_id == document_id, Extraction.status == "ok")
        .order_by(Extraction.created_at.desc(), Extraction.id.desc()).limit(1)
    ).scalar_one_or_none()
    if not raw:
        return []
    ex = ExtractionSchema.model_validate(raw)
    return [p.text for p in sorted(ex.pages, key=lambda p: p.page)]


def index_document(engine: Engine, document_id: int, *, embedder: Embedder | None = None,
                   lock_wait_seconds: float | None = None) -> bool:
    """Embed this document's pending chunks (post-commit step). Returns True when the
    document ends up fully indexed. Raises EmbeddingError if the model fails; the
    document then stays `search_indexed = false` for a later retry. A row lock held by
    another transaction is waited for up to `lock_wait_seconds` (default
    LOCK_WAIT_SECONDS); see the module docstring."""
    wait = LOCK_WAIT_SECONDS if lock_wait_seconds is None else lock_wait_seconds
    embedder = embedder or get_embedder()
    model = embedder.model_name
    with Session(engine) as s:
        doc = s.get(Document, document_id)
        if doc is None or not doc.is_latest or doc.status not in INDEXABLE_STATUSES:
            return False
        has_chunks = s.execute(select(exists().where(DocumentChunk.document_id == document_id))).scalar()
        if not has_chunks and not doc.search_indexed:
            # Chunking failed inside the job transaction (or never ran): redo it now.
            locked = _lock_document(s, document_id, step="chunk", wait_seconds=wait)
            if locked is None:
                s.rollback()
                return False
            s.refresh(locked)
            if not locked.is_latest or locked.status not in INDEXABLE_STATUSES:
                s.rollback()
                return False
            prepare_chunks(s, locked, _transcription(s, document_id), model)
            s.commit()
        pending = s.execute(
            select(DocumentChunk.id, DocumentChunk.text).where(DocumentChunk.document_id == document_id,
                                                               _not_current(model))
            .order_by(DocumentChunk.chunk_index)
        ).all()
        s.rollback()
    vectors = embedder.embed_passages([t for _, t in pending]) if pending else []
    with Session(engine) as s, s.begin():
        doc = _lock_document(s, document_id, step="embed", wait_seconds=wait)
        if doc is not None:
            s.refresh(doc)  # the row may have changed while we waited for its lock
        if doc is None or not doc.is_latest or doc.status not in INDEXABLE_STATUSES:
            return False  # still locked, reprocessed meanwhile, or no longer indexable; retried later
        for (chunk_id, text), vec in zip(pending, vectors, strict=True):
            s.execute(update(DocumentChunk).where(DocumentChunk.id == chunk_id, DocumentChunk.text == text)
                      .values(embedding=vec, embedding_model=model))
        remaining = s.execute(select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_id == document_id, _not_current(model))).scalar_one()
        doc.search_indexed = remaining == 0
        indexed = doc.search_indexed
    log.info("document indexed" if indexed else "document partially indexed",
             extra={"event": "search_indexed", "document_id": document_id, "chunks_embedded": len(pending),
                    "search_indexed": indexed, "model": model})
    return indexed


def pending_document_ids(session: Session, model_name: str, *, limit: int = 20,
                         min_age_seconds: float = 0.0) -> list[int]:
    stale_chunks = exists().where(DocumentChunk.document_id == Document.id, _not_current(model_name))
    stmt = (select(Document.id).where(
        Document.is_latest, Document.status.in_(INDEXABLE_STATUSES),
        or_(Document.search_indexed.is_(False), stale_chunks))
        .order_by(Document.id).limit(limit))
    if min_age_seconds > 0:
        stmt = stmt.where(Document.processed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, min_age_seconds))
    return list(session.execute(stmt).scalars())


def index_pending(engine: Engine, *, embedder: Embedder | None = None, limit: int = 20,
                  min_age_seconds: float = 0.0) -> int:
    """Retry step: index documents left `search_indexed = false` (embedding failure,
    crash between commit and indexing) or embedded with another model. Returns how
    many documents became fully indexed. Raises EmbeddingError if the model is
    unavailable (the caller backs off)."""
    embedder = embedder or get_embedder()
    with Session(engine) as s:
        ids = pending_document_ids(s, embedder.model_name, limit=limit, min_age_seconds=min_age_seconds)
    done = 0
    for doc_id in ids:
        try:
            done += bool(index_document(engine, doc_id, embedder=embedder))
        except EmbeddingError:
            raise
        except Exception as e:  # noqa: BLE001 - one bad document never blocks the others
            log.warning("indexing failed", extra={"event": "search_index_error", "document_id": doc_id,
                                                  "error_type": type(e).__name__})
    return done

"""BUG-Q2-04: post-commit indexing when another transaction holds the document row
(e.g. a concurrent postprocess refreshing the document's conflict status). The skip is
logged (`index_skipped_locked`) and the job's own indexing waits briefly instead of
leaving the document out of Search until the retry pass."""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import text

from app.search import indexing
from tests.pipeline_helpers import ingest_samples

NAME = "northwind_flash_update_jan2026.eml"


def _unindexed_doc(engine, tmp_path, fake_embedder) -> int:
    fake_embedder.fail = True
    ingest_samples(engine, tmp_path, [NAME])
    fake_embedder.fail = False
    with engine.connect() as c:
        doc_id, indexed = c.execute(text("SELECT id, search_indexed FROM documents")).one()
    assert indexed is False
    return doc_id


class _RowLock:
    """Hold `SELECT ... FOR UPDATE` on the document row in another connection."""

    def __init__(self, engine, doc_id: int):
        self.conn = engine.connect()
        self.tx = self.conn.begin()
        self.conn.execute(text("SELECT id FROM documents WHERE id = :i FOR UPDATE"), {"i": doc_id})

    def release(self):
        self.tx.commit()
        self.conn.close()


def _events(caplog, doc_id):
    return [r for r in caplog.records if getattr(r, "event", None) == "index_skipped_locked"
            and getattr(r, "document_id", None) == doc_id]


def _indexed(engine) -> bool:
    with engine.connect() as c:
        return c.execute(text("SELECT search_indexed FROM documents")).scalar()


def test_locked_row_is_logged_and_waited_for(clean_db, tmp_path, fake_embedder, caplog):
    doc_id = _unindexed_doc(clean_db, tmp_path, fake_embedder)
    caplog.set_level(logging.INFO, logger="app.search.indexing")
    lock = _RowLock(clean_db, doc_id)
    timer = threading.Timer(0.5, lock.release)
    timer.start()
    started = time.monotonic()
    try:
        assert indexing.index_document(clean_db, doc_id, embedder=fake_embedder, lock_wait_seconds=5) is True
    finally:
        timer.join()
    assert 0.4 <= time.monotonic() - started < 5
    assert _indexed(clean_db) is True
    ev = _events(caplog, doc_id)
    assert [(e.step, e.gave_up) for e in ev] == [("embed", False)]


def test_lock_held_past_the_wait_gives_up_logged_and_retry_indexes(clean_db, tmp_path, fake_embedder, caplog):
    doc_id = _unindexed_doc(clean_db, tmp_path, fake_embedder)
    caplog.set_level(logging.INFO, logger="app.search.indexing")
    lock = _RowLock(clean_db, doc_id)
    try:
        started = time.monotonic()
        assert indexing.index_document(clean_db, doc_id, embedder=fake_embedder, lock_wait_seconds=0.3) is False
        assert time.monotonic() - started < 3
    finally:
        lock.release()
    assert [e.gave_up for e in _events(caplog, doc_id)] == [False, True]
    assert _indexed(clean_db) is False
    # the index thread's retry pass picks it up
    assert indexing.index_pending(clean_db, embedder=fake_embedder) == 1
    assert _indexed(clean_db) is True


def test_zero_wait_skips_immediately_but_logs(clean_db, tmp_path, fake_embedder, caplog):
    doc_id = _unindexed_doc(clean_db, tmp_path, fake_embedder)
    caplog.set_level(logging.INFO, logger="app.search.indexing")
    lock = _RowLock(clean_db, doc_id)
    try:
        assert indexing.index_document(clean_db, doc_id, embedder=fake_embedder, lock_wait_seconds=0) is False
    finally:
        lock.release()
    assert [e.gave_up for e in _events(caplog, doc_id)] == [False, True]


def test_rechunk_step_also_waits_for_the_lock(clean_db, tmp_path, fake_embedder, caplog):
    """No chunks yet (chunking failed in the job): the redo-chunks step locks the row too."""
    doc_id = _unindexed_doc(clean_db, tmp_path, fake_embedder)
    with clean_db.begin() as c:
        c.execute(text("DELETE FROM document_chunks WHERE document_id = :i"), {"i": doc_id})
    caplog.set_level(logging.INFO, logger="app.search.indexing")
    lock = _RowLock(clean_db, doc_id)
    timer = threading.Timer(0.3, lock.release)
    timer.start()
    try:
        assert indexing.index_document(clean_db, doc_id, embedder=fake_embedder, lock_wait_seconds=5) is True
    finally:
        timer.join()
    assert [e.step for e in _events(caplog, doc_id)] == ["chunk"]
    with clean_db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL")).scalar() > 0


def test_document_reprocessed_while_waiting_is_not_indexed(clean_db, tmp_path, fake_embedder):
    """The job holding the lock re-claimed the document: after the wait it is no longer
    indexable, so its own job will index it."""
    doc_id = _unindexed_doc(clean_db, tmp_path, fake_embedder)
    lock = _RowLock(clean_db, doc_id)
    lock.conn.execute(text("UPDATE documents SET status = 'processing' WHERE id = :i"), {"i": doc_id})
    timer = threading.Timer(0.3, lock.release)
    timer.start()
    try:
        assert indexing.index_document(clean_db, doc_id, embedder=fake_embedder, lock_wait_seconds=5) is False
    finally:
        timer.join()
    assert _indexed(clean_db) is False

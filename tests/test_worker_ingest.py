"""Worker orchestration (FR-4, FR-5, FR-14): boot reconcile, request latency, interval
sync, job isolation, graceful shutdown, and the end-to-end demo path with fixtures."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import worker as wk
from app.config import Settings
from app.extraction.base import FunctionExtractor
from app.extraction.content import is_supported, text_content_for
from app.extraction.fixture import FIXTURES_DIR, FixtureExtractor
from app.models import Document, Extraction, SyncState
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource
from tests.ingest_helpers import make_extraction, write_fixture


def settings(clean_env, **env) -> Settings:
    for k, v in env.items():
        clean_env.setenv(k, str(v))
    return Settings(_env_file=None)


def wait_until(pred, timeout=10.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


class Running:
    """A Worker running in a background thread."""

    def __init__(self, s, engine, folder, extractor, **kw):
        self.stop = threading.Event()
        self.worker = wk.Worker(s, engine, self.stop, extractor=extractor,
                                source_resolver=kw.pop("resolver", lambda st, sess: LocalFolderSource(folder)),
                                job_idle_seconds=0.1, **kw)
        self.thread = threading.Thread(target=self.worker.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.thread.join(timeout=10)
        assert not self.thread.is_alive()


def statuses(engine) -> dict[str, str]:
    """Latest status per file, with T5's `needs_review` reported as `done`: both mean
    processed. These tests are about orchestration; T5 validates the generic test
    extraction against dummy files (its snippets are not in them) and flags it."""
    with Session(engine) as s:
        return {d.drive_file_id: ("done" if d.status == "needs_review" else d.status)
                for d in s.execute(select(Document).where(Document.is_latest)).scalars()}


def sync_row(engine) -> SyncState | None:
    with Session(engine) as s:
        return s.get(SyncState, 1)


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.csv").write_bytes(b"Fund,Ret\nA,0.01\n")
    (d / "b.csv").write_bytes(b"Fund,Ret\nB,0.02\n")
    return d


def ok_extractor():
    return FunctionExtractor(lambda content, mime: make_extraction())


def test_boot_reconcile_processes_files_added_while_stopped(clean_db, folder, clean_env):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600, WORKER_CONCURRENCY=2)
    with Running(s, clean_db, folder, ok_extractor()):
        assert wait_until(lambda: statuses(clean_db) == {"a.csv": "done", "b.csv": "done"})
    # worker stopped; files change; next start catches up without any request
    (folder / "c.csv").write_bytes(b"Fund,Ret\nC,0.03\n")
    (folder / "a.csv").unlink()
    with Running(s, clean_db, folder, ok_extractor()):
        assert wait_until(lambda: statuses(clean_db) == {"a.csv": "removed", "b.csv": "done", "c.csv": "done"})
    with Session(clean_db) as sess:
        assert sess.execute(select(func.count()).select_from(Document)).scalar() == 3  # no duplicates


def test_sync_and_reconcile_requests_honoured_within_2s(clean_db, folder, clean_env):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600)
    with Running(s, clean_db, folder, ok_extractor()):
        assert wait_until(lambda: (sync_row(clean_db) or SyncState()).last_reconciled_at is not None)
        time.sleep(0.2)
        (folder / "new.csv").write_bytes(b"Fund,Ret\nN,0.04\n")
        with clean_db.begin() as c:
            c.execute(text("UPDATE sync_state SET sync_requested_at = now()"))
        t0 = time.monotonic()
        assert wait_until(lambda: sync_row(clean_db).sync_requested_at is None, timeout=5)
        assert time.monotonic() - t0 <= 2.0
        assert "new.csv" in statuses(clean_db)
        assert sync_row(clean_db).last_synced_at is not None

        (folder / "b.csv").unlink()
        before = sync_row(clean_db).last_reconciled_at
        with clean_db.begin() as c:
            c.execute(text("UPDATE sync_state SET reconcile_requested_at = now()"))
        t0 = time.monotonic()
        assert wait_until(lambda: sync_row(clean_db).reconcile_requested_at is None, timeout=5)
        assert time.monotonic() - t0 <= 2.0
        assert sync_row(clean_db).last_reconciled_at > before
        assert statuses(clean_db)["b.csv"] == "removed"


def test_interval_sync_without_request(clean_db, folder, clean_env):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=1)
    with Running(s, clean_db, folder, ok_extractor()):
        assert wait_until(lambda: (sync_row(clean_db) or SyncState()).last_reconciled_at is not None)
        (folder / "late.csv").write_bytes(b"x\n")
        assert wait_until(lambda: statuses(clean_db).get("late.csv") == "done", timeout=5)


def test_failing_job_does_not_stop_the_worker(clean_db, folder, clean_env, monkeypatch):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600, WORKER_CONCURRENCY=1)
    real = wk.run_job
    calls = []

    def sometimes_crash(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bug outside the pipeline's own handling")
        return real(*a, **k)

    monkeypatch.setattr(wk, "run_job", sometimes_crash)

    def extractor_fn(content, mime):
        if b"B,0.02" in content:
            raise ZeroDivisionError("extractor bug")
        return make_extraction()

    with Running(s, clean_db, folder, FunctionExtractor(extractor_fn), stale_after=0.5) as r:
        # The crashed job is reclaimed after the (short) stale timeout; b.csv retries.
        assert wait_until(lambda: statuses(clean_db).get("a.csv") == "done", timeout=10)
        assert wait_until(lambda: statuses(clean_db).get("b.csv") in ("pending", "failed"), timeout=10)
        assert r.thread.is_alive() and all(t.is_alive() for t in r.worker._threads)
        assert len(calls) >= 3  # the job loop kept claiming after the crash


def test_iteration_errors_do_not_stop_the_main_loop(clean_db, folder, clean_env, monkeypatch):
    s = settings(clean_env)
    calls = []

    def flaky(worker):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(wk, "run_iteration", flaky)
    w = wk.Worker(s, clean_db, threading.Event(), extractor=ok_extractor(), concurrency=1, tick_seconds=0,
                  source_resolver=lambda st, sess: None)
    assert w.run(max_iterations=3) == 0
    assert len(calls) == 3


def test_drive_mode_without_source_idles(clean_db, folder, clean_env):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=1)
    w = wk.Worker(s, clean_db, threading.Event(), extractor=ok_extractor(), source_resolver=lambda st, sess: None)
    assert w.sync_tick() is None and w.claim_one() is None
    assert statuses(clean_db) == {}


def test_failed_sync_records_error_and_backs_off(clean_db, tmp_path, clean_env):
    s = settings(clean_env)
    w = wk.Worker(s, clean_db, threading.Event(), extractor=ok_extractor(),
                  source_resolver=lambda st, sess: LocalFolderSource(tmp_path / "missing"))
    assert w.sync_tick() is None
    assert sync_row(clean_db).last_error == "local folder missing/ does not exist"
    assert w.sync_tick() is None  # backing off, no busy retry


def test_shutdown_hands_in_flight_jobs_back(clean_db, folder, clean_env):
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600, WORKER_CONCURRENCY=2)
    gate = threading.Event()

    def blocking(content, mime):
        gate.wait(10)
        return make_extraction()

    r = Running(s, clean_db, folder, FunctionExtractor(blocking))
    with r:
        assert wait_until(lambda: list(statuses(clean_db).values()) == ["processing", "processing"])
        r.stop.set()  # SIGTERM path
        r.thread.join(timeout=10)
        with Session(clean_db) as sess:
            rows = list(sess.execute(select(Document)).scalars())
        assert all(d.status == "pending" and d.attempts == 0 for d in rows)
        gate.set()
    # the abandoned job threads finish but are fenced out
    time.sleep(0.5)
    with Session(clean_db) as sess:
        assert sess.execute(select(func.count()).select_from(Extraction)).scalar() == 0
        assert all(d.status == "pending" for d in sess.execute(select(Document)).scalars())


def test_crashed_worker_jobs_are_recovered_without_duplicates(clean_db, folder, clean_env):
    """Simulate kill -9 mid-processing: rows left `processing` with a stale claim."""
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600)
    from app.ingest.sync import reconcile

    with Session(clean_db) as sess, sess.begin():
        reconcile(sess, LocalFolderSource(folder))
        sess.execute(text("UPDATE documents SET status='processing', attempts=1, claimed_at = now() - interval '5 minutes'"))
    with Running(s, clean_db, folder, ok_extractor(), stale_after=60):
        assert wait_until(lambda: set(statuses(clean_db).values()) == {"done"})
    with Session(clean_db) as sess:
        assert sess.execute(select(func.count()).select_from(Document)).scalar() == 2
        per_doc = sess.execute(select(Extraction.document_id, func.count()).group_by(Extraction.document_id)).all()
        assert sorted(n for _, n in per_doc) == [1, 1]
        assert {d.attempts for d in sess.execute(select(Document)).scalars()} == {2}


# ---- end to end: demo mode with FixtureExtractor ------------------------------------------------


def _demo_fixture_dir(tmp_path: Path, docs: Path) -> Path:
    """Committed fixtures when present; otherwise synthetic ones for every supported file."""
    committed = sorted(FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.is_dir() else []
    if committed:
        return FIXTURES_DIR
    fx = tmp_path / "fixtures"
    for f in LocalFolderSource(docs).list_files():
        if is_supported(f.mime_type):
            content = (docs / f.name).read_bytes()
            doc_type = "account_statement" if "statement" in f.name else "factsheet"
            write_fixture(fx, f.name, content, make_extraction(doc_type, text=f"transcribed {f.name}",
                                                               statement=doc_type == "account_statement"),
                          mime=f.mime_type)
    return fx


def test_end_to_end_demo_path_with_fixture_extractor(clean_db, tmp_path, clean_env):
    docs = tmp_path / "sample_docs"
    shutil.copytree(DEFAULT_LOCAL_DIR, docs)
    (docs / "notes.txt").write_text("not a supported type")
    fx = _demo_fixture_dir(tmp_path, docs)
    s = settings(clean_env, SYNC_INTERVAL_SECONDS=3600)  # demo mode, no key -> fixtures
    assert s.demo_mode and s.use_fixtures

    with Running(s, clean_db, docs, FixtureExtractor(fx)):
        expected = {f.file_id for f in LocalFolderSource(docs).list_files()}
        assert wait_until(
            lambda: set(statuses(clean_db)) == expected
            and set(statuses(clean_db).values()) <= {"done", "unsupported"},
            timeout=30,
        ), statuses(clean_db)

    st = statuses(clean_db)
    assert st.pop("notes.txt") == "unsupported"
    assert "MANIFEST.md" not in st and len(st) == 21 and set(st.values()) == {"done"}
    with Session(clean_db) as sess:
        rows = sess.execute(select(Document, Extraction).join(Extraction, Extraction.document_id == Document.id)).all()
        assert len(rows) == 21
        for doc, ex in rows:
            assert ex.source == "fixture" and ex.status == "ok"
            assert ex.input_tokens is not None and ex.output_tokens is not None and ex.latency_ms is not None
            assert ex.model and ex.prompt_version and ex.raw_json["document_type"] == doc.document_type
            assert doc.text_content and doc.document_type_confidence is not None and doc.processed_at
        scanned = next(d for d, _ in rows if d.name == "northwind_dec25_scanned.pdf")
        # no text layer: text_content is the extractor's page transcription
        raw = next(e for d, e in rows if d.id == scanned.id).raw_json
        assert scanned.text_content == text_content_for(
            (docs / scanned.name).read_bytes(), "application/pdf", [p["text"] for p in raw["pages"]])
        state = sess.get(SyncState, 1)
        assert state.source == "local" and state.folder_name == "sample_docs" and state.last_reconciled_at
        # page token is the local snapshot cursor
        assert set(json.loads(state.page_token)["files"]) == set(st) | {"notes.txt"}

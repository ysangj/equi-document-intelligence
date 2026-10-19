"""FR-12 / FR-4 endpoints: /api/progress, /api/documents, reprocess, sync and reconcile."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest

from app.schemas import DocumentItem, DocumentsPage, ProgressResponse, ReprocessResponse
from app.schemas.ingestion import SyncRequestResponse
from tests.api_helpers import (
    api_client,
    assert_error,
    demo_settings,
    drive_settings,
    exec_sql,
    ingest_into,
    q1,
    rows,
    truncate,
    validate,
)

T0 = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)


@pytest.fixture
def db(test_engine):
    truncate(test_engine)
    yield test_engine
    truncate(test_engine)


def add_doc(engine, name, status, *, latest=True, version=1, source="local", doc_type="factsheet", indexed=False,
            attempts=0, last_error=None, file_id=None) -> int:
    return q1(engine, """
        INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, version, is_latest, status,
                               document_type, search_indexed, attempts, last_error, created_at)
        VALUES (:src, :fid, :n, 'application/pdf', md5(:n || :v), :v, :latest, :st, :dt, :ix, :att, :err,
                :created)
        RETURNING id""", src=source, fid=file_id or name, n=name, v=version, latest=latest, st=status,
              dt=doc_type, ix=indexed, att=attempts, err=last_error, created=T0 + timedelta(minutes=version))


def add_extraction(engine, doc_id, *, status="ok", tin=100, tout=10, model="claude-opus-5-5", source="claude",
                   created=None, latency=1500):
    exec_sql(engine, """
        INSERT INTO extractions (document_id, source, model, prompt_version, status, input_tokens, output_tokens,
                                 latency_ms, created_at)
        VALUES (:d, :src, :m, 'v1', :st, :i, :o, :lat, :c)""",
             d=doc_id, src=source, m=model, st=status, i=tin, o=tout, lat=latency, c=created or T0)


# ---- progress --------------------------------------------------------------------------------


def test_progress_counts(db):
    for i, st in enumerate(["pending", "processing", "processing", "done", "done", "done", "needs_review",
                            "failed", "unsupported", "removed"]):
        add_doc(db, f"d{i}.pdf", st)
    add_doc(db, "old.pdf", "done", latest=False, file_id="d3.pdf", version=0 + 2)  # superseded version
    done_id = q1(db, "SELECT id FROM documents WHERE name = 'd3.pdf'")
    add_extraction(db, done_id, tin=1000, tout=100, created=T0)
    add_extraction(db, done_id, tin=2000, tout=200, created=T0 + timedelta(minutes=1))  # latest ok wins
    add_extraction(db, done_id, status="failed", tin=None, tout=None, created=T0 + timedelta(minutes=2))
    nr_id = q1(db, "SELECT id FROM documents WHERE name = 'd6.pdf'")
    add_extraction(db, nr_id, tin=500, tout=50)
    with api_client() as c:
        body = c.get("/api/progress").json()
    validate(ProgressResponse, body)
    assert body["counts"] == {"pending": 1, "processing": 2, "done": 3, "needs_review": 1, "failed": 1,
                              "unsupported": 1}
    assert (body["total"], body["processed"], body["finished"], body["in_flight"], body["active"]) == (9, 4, 6, 3, True)
    assert body["tokens"] == {"input_tokens": 2500, "output_tokens": 250}
    assert body["mode"] == "demo" and body["connection"] == {"connected": False, "google_email": None}
    assert body["sync"]["source"] == "local" and body["sync"]["folder_name"] == "sample_docs"
    assert body["generated_at"].endswith("Z")


def test_progress_empty_and_idle(db):
    with api_client() as c:
        body = c.get("/api/progress").json()
    validate(ProgressResponse, body)
    assert body["total"] == 0 and body["active"] is False and body["review_open"] == 0
    assert body["sync"]["last_success_at"] is None and body["sync"]["sync_pending"] is False


def test_progress_sync_info_and_drive_connection(db):
    exec_sql(db, """INSERT INTO sync_state (id, source, folder_id, folder_name, last_synced_at, last_reconciled_at,
                    last_error, sync_requested_at) VALUES (1, 'drive', 'F1', 'Fund Docs', :s, :r, 'boom', :s)""",
             s=T0 + timedelta(seconds=30), r=T0)
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted) VALUES (1, 'a@b.c', 'x')")
    with api_client(drive_settings()) as c:
        body = c.get("/api/progress").json()
    validate(ProgressResponse, body)
    s = body["sync"]
    assert body["mode"] == "drive" and body["connection"] == {"connected": True, "google_email": "a@b.c"}
    assert (s["source"], s["folder_id"], s["folder_name"]) == ("drive", "F1", "Fund Docs")
    assert s["last_success_at"] == "2026-10-16T18:00:30Z" and s["last_reconciled_at"] == "2026-10-16T18:00:00Z"
    assert s["sync_pending"] is True and s["reconcile_pending"] is False and s["last_error"] == "boom"


def test_progress_review_open_matches_review_total(test_engine, tmp_path):
    ingest_into(test_engine, tmp_path)
    try:
        with api_client() as c:
            progress = c.get("/api/progress").json()
            review = c.get("/api/review").json()
        assert progress["review_open"] == review["total"] > 0
        assert progress["processed"] == progress["total"] == 21
    finally:
        truncate(test_engine)


# ---- documents -------------------------------------------------------------------------------


def test_documents_tokens_from_latest_ok_extraction(db):
    a = add_doc(db, "a.pdf", "done", indexed=True)
    b = add_doc(db, "b.pdf", "failed", attempts=3, last_error="Anthropic API 429")
    add_doc(db, "gone.pdf", "removed")
    add_extraction(db, a, tin=6000, tout=800, created=T0, source="fixture")
    add_extraction(db, a, tin=6420, tout=812, created=T0 + timedelta(minutes=1), source="fixture", latency=14820)
    add_extraction(db, a, status="failed", tin=None, tout=None, created=T0 + timedelta(minutes=5))
    add_extraction(db, b, status="failed", tin=None, tout=None)
    with api_client() as c:
        body = c.get("/api/documents").json()
    validate(DocumentsPage, body)
    assert body["total"] == 2 and body["sort"] == "-created_at"
    by = {i["document_name"]: i for i in body["items"]}
    assert set(by) == {"a.pdf", "b.pdf"}
    assert (by["a.pdf"]["input_tokens"], by["a.pdf"]["output_tokens"], by["a.pdf"]["total_tokens"]) == (6420, 812, 7232)
    assert by["a.pdf"]["extraction_source"] == "fixture" and by["a.pdf"]["latency_ms"] == 14820
    assert by["a.pdf"]["extraction_model"] == "claude-opus-5-5" and by["a.pdf"]["can_reprocess"] is True
    fb = by["b.pdf"]
    assert (fb["input_tokens"], fb["total_tokens"], fb["extraction_model"]) == (None, None, None)
    assert fb["attempts"] == 3 and fb["last_error"] == "Anthropic API 429" and fb["can_reprocess"] is True
    assert list(by["a.pdf"]) == list(DocumentItem.model_fields)


def test_documents_can_reprocess_flags(db):
    for st in ("pending", "processing", "unsupported", "done", "needs_review", "failed"):
        add_doc(db, f"{st}.pdf", st)
    with api_client() as c:
        items = c.get("/api/documents").json()["items"]
    assert {i["document_name"]: i["can_reprocess"] for i in items} == {
        "pending.pdf": False, "processing.pdf": False, "unsupported.pdf": False,
        "done.pdf": True, "needs_review.pdf": True, "failed.pdf": True}


def test_documents_filters_sort_and_csv(db):
    for i, (st, dt, ix) in enumerate([("done", "factsheet", True), ("failed", "email", False),
                                      ("done", "email", True), ("needs_review", "factsheet", False)]):
        d = add_doc(db, f"Doc_{i}%.pdf", st, doc_type=dt, indexed=ix)
        add_extraction(db, d, tin=100 * (i + 1), tout=10)
    with api_client() as c:
        assert c.get("/api/documents?status=done&status=failed").json()["total"] == 3
        assert c.get("/api/documents?status=done&document_type=email").json()["total"] == 1
        assert c.get("/api/documents?search_indexed=false").json()["total"] == 2
        assert c.get("/api/documents?q=doc_2").json()["total"] == 1  # case-insensitive
        assert c.get("/api/documents?q=%25").json()["total"] == 4  # literal %, not a wildcard
        assert c.get("/api/documents?q=_").json()["total"] == 4
        assert c.get("/api/documents?q=zzz").json()["total"] == 0
        tokens = [i["total_tokens"] for i in c.get("/api/documents?sort=-total_tokens").json()["items"]]
        assert tokens == [410, 310, 210, 110]
        page = c.get("/api/documents?sort=document_name&page_size=3&page=2").json()
        assert [i["document_name"] for i in page["items"]] == ["Doc_3%.pdf"] and page["total"] == 4
        assert_error(c.get("/api/documents?sort=name"), 422, "validation_error", "query.sort")
        r = c.get("/api/documents?format=csv&sort=document_name")
        assert r.headers["content-disposition"].startswith('attachment; filename="documents_')
        parsed = list(csv.DictReader(io.StringIO(r.text)))
        assert [p["document_name"] for p in parsed] == ["Doc_0%.pdf", "Doc_1%.pdf", "Doc_2%.pdf", "Doc_3%.pdf"]
        assert parsed[0]["search_indexed"] == "true" and parsed[1]["search_indexed"] == "false"
        assert parsed[0]["last_error"] == "" and parsed[0]["created_at"].endswith("Z")


def test_documents_value_and_review_counts(test_engine, tmp_path):
    ingest_into(test_engine, tmp_path)
    try:
        with api_client() as c:
            items = c.get("/api/documents?page_size=100").json()["items"]
        by = {i["document_name"]: i for i in items}
        acme = by["acme_family_office_statement_2026-01.pdf"]
        n = q1(test_engine, """SELECT (SELECT count(*) FROM statements WHERE document_id = :d)
                                      + (SELECT count(*) FROM statement_holdings h JOIN statements s
                                         ON s.id = h.statement_id WHERE s.document_id = :d)""", d=acme["document_id"])
        assert acme["value_count"] == n == 6 and acme["review_count"] == 1
        assert by["kestrel_factsheet_2026-01.pdf"]["review_count"] == 0
        assert by["northwind_monthly_jan26.pdf"]["review_count"] >= 1  # conflict group member
    finally:
        truncate(test_engine)


# ---- reprocess -------------------------------------------------------------------------------


def test_reprocess_requeues(db):
    d = add_doc(db, "x.pdf", "failed", attempts=3, last_error="boom")
    exec_sql(db, "UPDATE documents SET claimed_at = now() WHERE id = :d", d=d)
    with api_client() as c:
        r = c.post(f"/api/documents/{d}/reprocess")
    assert r.status_code == 202, r.text
    body = validate(ReprocessResponse, r.json())
    assert (body.document_id, body.status, body.attempts) == (d, "pending", 0)
    row = rows(db, "SELECT status, attempts, last_error, next_attempt_at, claimed_at FROM documents WHERE id = :d",
               d=d)[0]
    assert (row["status"], row["attempts"], row["last_error"], row["claimed_at"]) == ("pending", 0, None, None)
    assert row["next_attempt_at"] is not None and r.json()["queued_at"].endswith("Z")


def test_reprocess_errors(db):
    busy = add_doc(db, "busy.pdf", "processing")
    queued = add_doc(db, "queued.pdf", "pending")
    unsup = add_doc(db, "x.docx", "unsupported")
    gone = add_doc(db, "gone.pdf", "removed")
    old = add_doc(db, "v1.pdf", "done", latest=False, file_id="v.pdf", version=1)
    add_doc(db, "v2.pdf", "done", file_id="v.pdf", version=2)
    with api_client() as c:
        body = assert_error(c.post(f"/api/documents/{busy}/reprocess"), 409, "document_busy")
        assert body["error"]["message"] == "busy.pdf is already queued for processing."
        assert_error(c.post(f"/api/documents/{queued}/reprocess"), 409, "document_busy")
        assert_error(c.post(f"/api/documents/{unsup}/reprocess"), 409, "unsupported_document")
        for d in (gone, old, 999999):
            assert_error(c.post(f"/api/documents/{d}/reprocess"), 404, "not_found")
        assert_error(c.post("/api/documents/0/reprocess"), 422, "validation_error", "path.document_id")
    assert q1(db, "SELECT status FROM documents WHERE id = :d", d=busy) == "processing"


def test_reprocess_is_picked_up_by_pipeline(test_engine, tmp_path):
    """End to end: reprocess -> the queue claims it again -> re-extracted (T4 drain)."""
    from tests.pipeline_helpers import drain
    from app.extraction.fixture import FixtureExtractor

    ingest_into(test_engine, tmp_path)
    try:
        d = q1(test_engine, "SELECT id FROM documents WHERE name = 'kestrel_factsheet_2026-01.pdf'")
        n_ext = q1(test_engine, "SELECT count(*) FROM extractions WHERE document_id = :d", d=d)
        with api_client() as c:
            assert c.post(f"/api/documents/{d}/reprocess").status_code == 202
        out = drain(test_engine, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))
        assert out == {"kestrel_factsheet_2026-01.pdf": "done"}
        assert q1(test_engine, "SELECT count(*) FROM extractions WHERE document_id = :d", d=d) == n_ext + 1
        assert q1(test_engine, "SELECT attempts FROM documents WHERE id = :d", d=d) == 1
    finally:
        truncate(test_engine)


# ---- sync requests ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,kind,col", [("/api/sync", "sync", "sync_requested_at"),
                                           ("/api/sync/reconcile", "reconcile", "reconcile_requested_at")])
def test_sync_requests_set_column_demo(db, path, kind, col):
    with api_client(demo_settings()) as c:
        r1 = c.post(path)
        assert r1.status_code == 202, r1.text
        b1 = validate(SyncRequestResponse, r1.json())
        assert (b1.kind, b1.mode, b1.already_pending, b1.last_success_at) == (kind, "demo", False, None)
        first = q1(db, f"SELECT {col} FROM sync_state WHERE id = 1")
        assert first is not None
        r2 = c.post(path)
        assert r2.status_code == 202 and r2.json()["already_pending"] is True
        assert q1(db, f"SELECT {col} FROM sync_state WHERE id = 1") >= first
        other = "reconcile_requested_at" if kind == "sync" else "sync_requested_at"
        assert q1(db, f"SELECT {other} FROM sync_state WHERE id = 1") is None
        # completion: the worker clears the request -> progress shows it is no longer pending
        exec_sql(db, f"UPDATE sync_state SET {col} = NULL, last_synced_at = :t WHERE id = 1", t=T0)
        assert c.post(path).json()["already_pending"] is False
        assert c.post(path).json()["last_success_at"] == "2026-10-16T18:00:00Z"
        assert c.get("/api/progress").json()["sync"][f"{kind}_pending"] is True


def test_sync_drive_mode_needs_credentials_and_folder(db):
    with api_client(drive_settings()) as c:
        for path in ("/api/sync", "/api/sync/reconcile"):
            assert_error(c.post(path), 409, "not_connected")
        exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted) VALUES (1, 'a@b', 'x')")
        for path in ("/api/sync", "/api/sync/reconcile"):
            assert_error(c.post(path), 409, "no_folder")
        assert q1(db, "SELECT count(*) FROM sync_state WHERE sync_requested_at IS NOT NULL") == 0
        exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name) VALUES (1, 'drive', 'F', 'Docs')")
        r = c.post("/api/sync")
        assert r.status_code == 202 and r.json()["mode"] == "drive"
        assert q1(db, "SELECT sync_requested_at IS NOT NULL FROM sync_state WHERE id = 1") is True


def test_sync_request_is_honoured_by_worker_tick(db, tmp_path):
    """The column the api sets is the one the T4 worker reacts to."""
    from app.worker import Worker
    from app.sources.local import LocalFolderSource
    from tests.pipeline_helpers import copy_samples

    folder = copy_samples(tmp_path / "docs", ["kestrel_factsheet_2026-01.pdf"])
    settings = demo_settings()
    import threading

    w = Worker(settings, db, threading.Event(), source_resolver=lambda s, sess: LocalFolderSource(folder))
    assert w.sync_tick() == "reconcile"  # boot reconcile
    assert w.sync_tick() is None
    with api_client(settings) as c:
        assert c.post("/api/sync").status_code == 202
    assert w.sync_tick() == "sync"
    assert q1(db, "SELECT sync_requested_at FROM sync_state WHERE id = 1") is None
    with api_client(settings) as c:
        assert c.post("/api/sync/reconcile").status_code == 202
    assert w.sync_tick() == "reconcile"
    assert q1(db, "SELECT reconcile_requested_at FROM sync_state WHERE id = 1") is None

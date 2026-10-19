"""DriveSource (FR-4, T7) against the in-memory Drive, and through the real sync, queue,
pipeline and worker: listing, changes filtering, upsert rules, Sheets export, atomic page
token, boot reconcile, auth errors, and no secrets in the logs."""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app import worker as wk
from app.extraction.base import FunctionExtractor
from app.google.credentials import encrypt_token
from app.google.drive_api import SHEET_MIME
from app.ingest import pipeline, queue, sync
from app.ingest.sync import incremental_sync, reconcile
from app.logging_setup import JsonFormatter
from app.sources import resolve_source
from app.sources.base import SourceAuthError, SourceError, SourceFileGone
from app.sources.drive import DriveSource, drive_query_literal, to_source_file
from tests.api_helpers import exec_sql, q1, rows, truncate
from tests.drive_fakes import ROOT_ID, FactoryRecorder, FakeDrive
from tests.ingest_helpers import make_extraction, minimal_pdf
from tests.test_auth_api import REFRESH, t7_settings

AUTH_MSG = "Google Drive access was revoked or has expired. Reconnect Google Drive to resume syncing."


@pytest.fixture
def db(test_engine):
    truncate(test_engine)
    yield test_engine
    truncate(test_engine)


@pytest.fixture
def s():
    return t7_settings()


@pytest.fixture
def drive():
    d = FakeDrive()
    d.folder = d.add_folder("Fund Docs")
    return d


def connect(db, s, folder_id, *, token=REFRESH):
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, 'a@b.com', :e, '{}') ON CONFLICT (id) DO UPDATE SET refresh_token_encrypted = :e",
             e=encrypt_token(s, token))
    exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name) VALUES (1, 'drive', :f, 'Fund Docs') "
                 "ON CONFLICT (id) DO UPDATE SET folder_id = :f", f=folder_id)


def source_for(db, s, drive) -> DriveSource:
    with Session(db) as sess:
        src = DriveSource.from_db(s, sess, api_factory=FactoryRecorder(drive))
    assert src is not None
    return src


def run_reconcile(db, src):
    with Session(db) as sess, sess.begin():
        return reconcile(sess, src)


def run_sync(db, src):
    with Session(db) as sess, sess.begin():
        return incremental_sync(sess, src)


def docs(db):
    return rows(db, "SELECT drive_file_id, name, mime_type, md5_checksum, version, is_latest, status, source, "
                    "web_view_link FROM documents ORDER BY id")


# ---- pure conversion ---------------------------------------------------------------------------


def test_to_source_file_types():
    base = {"id": "f1", "name": "a.pdf", "mimeType": "application/pdf", "md5Checksum": "a" * 32, "version": "7",
            "modifiedTime": "2026-10-15T09:20:03.123Z", "webViewLink": "https://drive.google.com/x"}
    sf = to_source_file(base)
    assert (sf.mime_type, sf.md5, sf.web_view_link) == ("application/pdf", "a" * 32, "https://drive.google.com/x")
    assert sf.modified_at == datetime(2026, 10, 15, 9, 20, 3, 123000, tzinfo=UTC)
    sheet = to_source_file({**base, "name": "nav", "mimeType": SHEET_MIME, "md5Checksum": None})
    assert (sheet.mime_type, sheet.md5) == ("text/csv", "drive-version:7")
    eml = to_source_file({**base, "name": "flash.eml", "mimeType": "application/octet-stream"})
    assert eml.mime_type == "message/rfc822"
    assert to_source_file({**base, "name": "x.eml", "mimeType": "message/rfc822"}).mime_type == "message/rfc822"
    doc = to_source_file({**base, "name": "memo", "mimeType": "application/vnd.google-apps.document",
                          "md5Checksum": None})
    assert doc.mime_type == "application/vnd.google-apps.document" and doc.md5 == "drive-version:7"
    assert to_source_file({**base, "name": "notes.txt", "mimeType": "text/plain"}).mime_type == "text/plain"
    assert to_source_file({**base, "mimeType": "application/vnd.google-apps.folder"}) is None


def test_drive_query_literal_escapes():
    assert drive_query_literal("ab") == "'ab'"
    assert drive_query_literal("a'b\\c") == "'a\\'b\\\\c'"


# ---- construction / resolve_source ------------------------------------------------------------


def test_from_db_needs_credentials_and_folder_and_is_lazy(db, s, drive):
    rec = FactoryRecorder(drive)
    with Session(db) as sess:
        assert DriveSource.from_db(s, sess, api_factory=rec) is None
    exec_sql(db, "INSERT INTO oauth_credentials (id, refresh_token_encrypted) VALUES (1, :e)", e=encrypt_token(s, REFRESH))
    with Session(db) as sess:
        assert DriveSource.from_db(s, sess, api_factory=rec) is None  # connected, no folder
    exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name) VALUES (1, 'drive', :f, 'Fund Docs')",
             f=drive.folder)
    with Session(db) as sess:
        src = DriveSource.from_db(s, sess, api_factory=rec)
    assert (src.folder_id, src.folder_name, src.name) == (drive.folder, "Fund Docs", "drive")
    assert rec.tokens == []  # nothing decrypted / no client until the first Drive call
    assert REFRESH not in repr(src) and REFRESH not in str(vars(src))
    src.start_cursor()
    assert rec.tokens == [REFRESH]


def test_resolve_source_drive_mode(db, s, drive):
    with Session(db) as sess:
        assert resolve_source(s, sess) is None
    connect(db, s, drive.folder)
    with Session(db) as sess:
        src = resolve_source(s, sess)
    assert isinstance(src, DriveSource) and src.folder_id == drive.folder


def test_undecryptable_token_is_an_auth_error(db, s, drive):
    connect(db, t7_settings(), drive.folder)  # other FERNET_KEY
    src = source_for(db, s, drive)
    with pytest.raises(SourceAuthError, match="FERNET_KEY"):
        src.list_files()


# ---- list_files ---------------------------------------------------------------------------------


def test_list_files_paginates_and_is_non_recursive(db, s, drive):
    connect(db, s, drive.folder)
    names = [f"doc{i}.pdf" for i in range(7)]
    for n in names:
        drive.add_file(n, drive.folder, n.encode())
    sub = drive.add_folder("sub", parent=drive.folder)
    drive.add_file("deep.pdf", sub)
    drive.add_file("elsewhere.pdf", ROOT_ID)
    trashed = drive.add_file("binned.pdf", drive.folder)
    drive.trash(trashed)
    drive.add_sheet("nav sheet", drive.folder)
    drive.max_page = 3
    src = source_for(db, s, drive)
    files = src.list_files()
    assert sorted(f.name for f in files) == sorted(names + ["nav sheet"])
    assert drive.calls.count("files_list") == 3  # 8 files, pages of 3


def test_list_files_folder_checks(db, s, drive):
    connect(db, s, drive.folder)
    src = source_for(db, s, drive)
    drive.files[drive.folder]["name"] = "Renamed"
    src.list_files()
    assert src.folder_name == "Renamed"
    drive.trash(drive.folder)
    with pytest.raises(SourceError, match="trash"):
        source_for(db, s, drive).list_files()
    del drive.files[drive.folder]
    with pytest.raises(SourceError, match="no longer exists"):
        source_for(db, s, drive).list_files()


def test_reconcile_records_renamed_folder_name(db, s, drive):
    connect(db, s, drive.folder)
    drive.files[drive.folder]["name"] = "Fund Docs 2026"
    run_reconcile(db, source_for(db, s, drive))
    assert q1(db, "SELECT folder_name FROM sync_state") == "Fund Docs 2026"


# ---- changes_since ------------------------------------------------------------------------------


def test_changes_since_filters_to_watched_folder(db, s, drive):
    connect(db, s, drive.folder)
    other = drive.add_folder("Other")
    keep = drive.add_file("keep.pdf", drive.folder, b"k")
    gone_trash = drive.add_file("trash.pdf", drive.folder, b"t")
    gone_move = drive.add_file("move.pdf", drive.folder, b"m")
    gone_delete = drive.add_file("delete.pdf", drive.folder, b"d")
    src = source_for(db, s, drive)
    cursor = src.start_cursor()
    new = drive.add_file("new.pdf", drive.folder, b"n")
    drive.update(keep, b"k2")
    drive.trash(gone_trash)
    drive.move(gone_move, other)
    drive.delete(gone_delete)
    drive.add_file("unrelated.pdf", other, b"u")
    drive.add_folder("subfolder", parent=drive.folder)
    moved_in = drive.add_file("moved-in.pdf", other, b"i")
    drive.move(moved_in, drive.folder)
    drive.max_page = 2  # several changes.list pages
    cs = src.changes_since(cursor)
    assert {f.name for f in cs.changed} == {"new.pdf", "keep.pdf", "moved-in.pdf"}
    assert next(f for f in cs.changed if f.file_id == keep).md5 == hashlib.md5(b"k2").hexdigest()
    assert {gone_trash, gone_move, gone_delete} <= set(cs.removed_ids)
    assert new not in cs.removed_ids and moved_in not in cs.removed_ids
    assert cs.cursor == drive.next_token and cs.complete is False
    # nothing new since: empty change set, same cursor
    again = src.changes_since(cs.cursor)
    assert again.changed == [] and again.removed_ids == [] and again.cursor == cs.cursor


def test_changes_since_invalid_cursor_falls_back_to_listing(db, s, drive):
    connect(db, s, drive.folder)
    drive.add_file("a.pdf", drive.folder)
    cs = source_for(db, s, drive).changes_since("expired-token")
    assert cs.complete is True and [f.name for f in cs.changed] == ["a.pdf"] and cs.cursor == drive.next_token


# ---- fetch ----------------------------------------------------------------------------------


def test_fetch_binary_sheet_and_errors(db, s, drive):
    connect(db, s, drive.folder)
    pdf = drive.add_file("a.pdf", drive.folder, b"%PDF-1.4 bytes")
    eml = drive.add_file("flash.eml", drive.folder, b"From: x\r\n\r\nhi", mime="message/rfc822")
    sheet = drive.add_sheet("nav", drive.folder, b"fund,ret\nA,1.2\n")
    gdoc = drive.add_file("memo", drive.folder, b"", mime="application/vnd.google-apps.document")
    src = source_for(db, s, drive)
    assert src.fetch(pdf) == b"%PDF-1.4 bytes"
    assert src.fetch(eml).startswith(b"From:")
    assert src.fetch(sheet) == b"fund,ret\nA,1.2\n"
    assert "export" in drive.calls
    with pytest.raises(SourceError, match="not supported"):
        src.fetch(gdoc)
    with pytest.raises(SourceFileGone):
        src.fetch("missing")
    drive.trash(pdf)
    with pytest.raises(SourceFileGone):
        src.fetch(pdf)


# ---- through the real sync (upsert rules, §15 rows) -------------------------------------------


def test_add_change_unchanged_readd_trash_and_move_out(db, s, drive):
    connect(db, s, drive.folder)
    other = drive.add_folder("Other")
    a = drive.add_file("a.pdf", drive.folder, b"%PDF a")
    b = drive.add_file("b.pdf", drive.folder, b"%PDF b")
    c = drive.add_file("c.pdf", drive.folder, b"%PDF c")
    rep = run_reconcile(db, source_for(db, s, drive))
    assert rep.new == 3
    d = docs(db)
    assert {x["status"] for x in d} == {"pending"} and {x["source"] for x in d} == {"drive"}
    assert d[0]["web_view_link"].startswith("https://drive.google.com/file/d/")
    assert d[0]["md5_checksum"] == hashlib.md5(b"%PDF a").hexdigest()
    exec_sql(db, "UPDATE documents SET status = 'done'")

    # new file added -> picked up by the incremental sync
    n = drive.add_file("new.pdf", drive.folder, b"%PDF new")
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.kind == "sync" and rep.new == 1
    # unchanged re-add: same content uploaded as a new revision of the same file -> no-op
    drive.update(a, b"%PDF a")
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.unchanged == 1 and rep.new == rep.versions == 0
    assert q1(db, "SELECT count(*) FROM documents WHERE drive_file_id = :f", f=a) == 1
    # edited file -> new version, old one not latest
    drive.update(a, b"%PDF a edited")
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.versions == 1
    av = rows(db, "SELECT version, is_latest, status FROM documents WHERE drive_file_id = :f ORDER BY version", f=a)
    assert av == [{"version": 1, "is_latest": False, "status": "done"},
                  {"version": 2, "is_latest": True, "status": "pending"}]
    # trash and move out -> removed
    drive.trash(b)
    drive.move(c, other)
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.removed == 2
    st = {r["drive_file_id"]: r["status"] for r in docs(db) if r["is_latest"]}
    assert st[b] == st[c] == "removed" and st[n] == "pending"
    # restored from the trash with the same content -> back, no duplicate
    drive.trash(b, trashed=False)
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.restored == 1
    assert q1(db, "SELECT count(*) FROM documents WHERE drive_file_id = :f", f=b) == 1
    # a full reconcile afterwards finds nothing new
    rep = run_reconcile(db, source_for(db, s, drive))
    assert rep.new == rep.versions == rep.restored == rep.removed == 0


def test_sheet_ingested_as_csv_with_stand_in_md5_and_versions_on_edit(db, s, drive):
    connect(db, s, drive.folder)
    sh = drive.add_sheet("Harbor NAV", drive.folder, b"fund,nav\nHarbor,1\n")
    gdoc = drive.add_file("memo", drive.folder, b"", mime="application/vnd.google-apps.document")
    run_reconcile(db, source_for(db, s, drive))
    by_id = {r["drive_file_id"]: r for r in docs(db)}
    assert (by_id[sh]["mime_type"], by_id[sh]["md5_checksum"], by_id[sh]["status"]) == (
        "text/csv", "drive-version:1", "pending")
    assert by_id[gdoc]["status"] == "unsupported"
    drive.update(sh, b"fund,nav\nHarbor,2\n")
    run_sync(db, source_for(db, s, drive))
    assert rows(db, "SELECT md5_checksum, is_latest FROM documents WHERE drive_file_id = :f ORDER BY version", f=sh) == [
        {"md5_checksum": "drive-version:1", "is_latest": False}, {"md5_checksum": "drive-version:2", "is_latest": True}]


def test_page_token_and_upserts_are_atomic(db, s, drive, monkeypatch):
    connect(db, s, drive.folder)
    drive.add_file("a.pdf", drive.folder, b"a")
    run_reconcile(db, source_for(db, s, drive))
    token_before = q1(db, "SELECT page_token FROM sync_state")
    drive.add_file("b.pdf", drive.folder, b"b")
    drive.add_file("c.pdf", drive.folder, b"c")
    real = sync.upsert_file
    calls = {"n": 0}

    def flaky(session, sf, source_name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-sync")
        return real(session, sf, source_name)

    monkeypatch.setattr(sync, "upsert_file", flaky)
    with pytest.raises(RuntimeError):
        run_sync(db, source_for(db, s, drive))
    assert q1(db, "SELECT page_token FROM sync_state") == token_before  # no advance
    assert [r["name"] for r in docs(db)] == ["a.pdf"]  # the first upsert rolled back too
    monkeypatch.setattr(sync, "upsert_file", real)
    rep = run_sync(db, source_for(db, s, drive))  # the retry replays the same changes
    assert rep.new == 2 and q1(db, "SELECT page_token FROM sync_state") == drive.next_token


def test_invalid_cursor_fallback_marks_missing_removed(db, s, drive):
    connect(db, s, drive.folder)
    a = drive.add_file("a.pdf", drive.folder, b"a")
    b = drive.add_file("b.pdf", drive.folder, b"b")
    run_reconcile(db, source_for(db, s, drive))
    exec_sql(db, "UPDATE sync_state SET page_token = 'expired'")
    drive.delete(b)
    rep = run_sync(db, source_for(db, s, drive))
    assert rep.removed == 1
    st = {r["drive_file_id"]: r["status"] for r in docs(db)}
    assert st == {a: "pending", b: "removed"}


def test_sync_is_skipped_when_folder_changed_meanwhile(db, s, drive):
    connect(db, s, drive.folder)
    drive.add_file("a.pdf", drive.folder)
    src = source_for(db, s, drive)  # built for the old folder
    exec_sql(db, "UPDATE sync_state SET folder_id = 'another', reconcile_requested_at = now()")
    assert run_reconcile(db, src).kind == "stale"
    assert docs(db) == [] and q1(db, "SELECT reconcile_requested_at FROM sync_state") is not None


# ---- worker: boot reconcile, interval sync, extraction --------------------------------------


def drive_resolver(drive):
    return lambda st, sess: DriveSource.from_db(st, sess, api_factory=FactoryRecorder(drive))


def test_files_added_while_worker_down_are_picked_up_on_boot(db, s, drive):
    connect(db, s, drive.folder)
    drive.add_file("first.pdf", drive.folder, b"1")
    w1 = wk.Worker(s, db, threading.Event(), source_resolver=drive_resolver(drive), indexing=False)
    assert w1.sync_tick() == "reconcile"
    # worker stopped; meanwhile a file is added and one is deleted
    drive.add_file("while-down.pdf", drive.folder, b"2")
    first_id = next(fid for fid, f in drive.files.items() if f["name"] == "first.pdf")
    drive.delete(first_id)
    w2 = wk.Worker(s, db, threading.Event(), source_resolver=drive_resolver(drive), indexing=False)
    assert w2.sync_tick() == "reconcile"  # boot reconcile
    st = {r["name"]: r["status"] for r in docs(db)}
    assert st == {"first.pdf": "removed", "while-down.pdf": "pending"}


def test_new_file_picked_up_within_one_sync_interval(db, clean_env, drive):
    s = t7_settings(sync_interval_seconds=1)
    connect(db, s, drive.folder)
    w = wk.Worker(s, db, threading.Event(), source_resolver=drive_resolver(drive), indexing=False)
    assert w.sync_tick() == "reconcile"
    assert w.sync_tick() is None  # nothing due yet
    drive.add_file("dropped.pdf", drive.folder, b"d")
    exec_sql(db, "UPDATE sync_state SET last_reconciled_at = now() - interval '2 seconds'")
    assert w.sync_tick() == "sync"
    assert [r["name"] for r in docs(db)] == ["dropped.pdf"]
    assert q1(db, "SELECT last_synced_at IS NOT NULL AND last_error IS NULL FROM sync_state")


def test_drive_documents_are_fetched_and_extracted(db, s, drive):
    """Claim -> DriveSource.fetch -> extractor, for a PDF and a Sheet (exported CSV)."""
    connect(db, s, drive.folder)
    pdf_bytes = minimal_pdf("Kestrel page one text")
    drive.add_file("kestrel.pdf", drive.folder, pdf_bytes)
    drive.add_sheet("harbor nav", drive.folder, b"fund,ret\nHarbor,0.71\n")
    run_reconcile(db, source_for(db, s, drive))
    seen = []

    def extract(content, mime):
        seen.append((mime, content))
        return make_extraction(text="page one text")

    ex = FunctionExtractor(extract)
    while True:
        with Session(db) as sess, sess.begin():
            src = DriveSource.from_db(s, sess, api_factory=FactoryRecorder(drive))
            c = queue.claim_next(sess, max_attempts=3, source_name="drive")
        if c is None:
            break
        pipeline.run_job(db, c, src, ex, max_attempts=3)
    assert sorted(seen) == [("application/pdf", pdf_bytes), ("text/csv", b"fund,ret\nHarbor,0.71\n")]
    assert {r["status"] for r in docs(db)} <= {"done", "needs_review"}, rows(db, "SELECT name, status, last_error FROM documents")


# ---- auth errors --------------------------------------------------------------------------


def test_sync_auth_error_drops_credentials_and_worker_idles(db, s, drive, caplog):
    connect(db, s, drive.folder)
    rec = FactoryRecorder(drive)
    w = wk.Worker(s, db, threading.Event(), indexing=False,
                  source_resolver=lambda st, sess: DriveSource.from_db(st, sess, api_factory=rec))
    drive.fail["start_page_token"] = [SourceAuthError(AUTH_MSG)]
    caplog.set_level(logging.INFO)
    assert w.sync_tick() is None
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0
    assert q1(db, "SELECT last_error FROM sync_state") == AUTH_MSG
    assert q1(db, "SELECT folder_id FROM sync_state") == drive.folder  # kept for a reconnect
    calls = len(drive.calls)
    for _ in range(3):
        assert w.sync_tick() is None  # idle: no source, no Google calls
    assert len(drive.calls) == calls
    assert any(getattr(r, "event", None) == "sync_auth_error" for r in caplog.records)
    # after reconnecting, syncing resumes and last_error clears
    connect(db, s, drive.folder, token="1//new-refresh-token")
    assert w.sync_tick() == "reconcile"
    assert q1(db, "SELECT last_error FROM sync_state") is None
    assert rec.tokens[-1] == "1//new-refresh-token"


def test_auth_error_does_not_drop_newer_credentials(db, s, drive):
    connect(db, s, drive.folder)
    src = source_for(db, s, drive)
    connect(db, s, drive.folder, token="1//reconnected")  # user reconnected meanwhile
    with Session(db) as sess, sess.begin():
        assert src.on_auth_failure(sess, AUTH_MSG) is False
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 1


def test_job_auth_error_hands_job_back_uncharged(db, s, drive):
    connect(db, s, drive.folder)
    drive.add_file("a.pdf", drive.folder, b"a")
    run_reconcile(db, source_for(db, s, drive))
    with Session(db) as sess, sess.begin():
        c = queue.claim_next(sess, max_attempts=3, source_name="drive")
    src = source_for(db, s, drive)
    drive.fail["files_get"] = [SourceAuthError(AUTH_MSG)]
    out = pipeline.run_job(db, c, src, FunctionExtractor(lambda b, m: make_extraction()), max_attempts=3)
    assert out == "pending"
    (d,) = rows(db, "SELECT status, attempts, last_error FROM documents")
    assert d == {"status": "pending", "attempts": 0, "last_error": AUTH_MSG}
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0
    with Session(db) as sess:
        assert resolve_source(s, sess) is None  # nothing is claimed until reconnect


def test_transient_sync_error_sets_last_error_and_retries(db, s, drive):
    from app.sources.base import TransientSourceError

    connect(db, s, drive.folder)
    w = wk.Worker(s, db, threading.Event(), source_resolver=drive_resolver(drive), indexing=False)
    drive.fail["files_list"] = [TransientSourceError("Drive files.list failed after 4 attempts: HTTP 503")]
    assert w.sync_tick() is None
    assert "HTTP 503" in q1(db, "SELECT last_error FROM sync_state")
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 1  # transient: credentials kept
    w._sync_retry_at = 0
    assert w.sync_tick() == "reconcile"
    assert q1(db, "SELECT last_error FROM sync_state") is None


# ---- no tokens in logs -------------------------------------------------------------------------


def test_worker_drive_logs_contain_no_tokens(db, s, drive, caplog):
    connect(db, s, drive.folder)
    drive.add_file("a.pdf", drive.folder, minimal_pdf("page one text secret content"))
    caplog.set_level(logging.DEBUG)
    w = wk.Worker(s, db, threading.Event(), source_resolver=drive_resolver(drive), indexing=False,
                  extractor=FunctionExtractor(lambda b, m: make_extraction()))
    w.sync_tick()
    got = w.claim_one()
    claim, src = got
    pipeline.run_job(db, claim, src, w.extractor, max_attempts=3)
    exec_sql(db, "UPDATE sync_state SET sync_requested_at = now()")
    drive.fail["changes_list"] = [SourceAuthError(AUTH_MSG)]
    assert w.sync_tick() is None
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0
    assert w.sync_tick() is None
    encrypted = encrypt_token(s, REFRESH)  # (a fresh ciphertext; the stored one is gone)
    fmt = JsonFormatter(s.secret_values())
    out = "\n".join(fmt.format(r) for r in caplog.records) + caplog.text
    for secret in (REFRESH, "secret content", s.fernet_key.get_secret_value(), encrypted[:20]):
        assert secret not in out
    assert any(getattr(r, "event", None) == "job_done" for r in caplog.records)

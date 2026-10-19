"""Reconcile and incremental sync against a temp folder (FR-4, §14 Reconcile/Idempotency)."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ingest import sync as sy
from app.models import Document, SyncState
from app.sources.local import LocalFolderSource

@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.pdf").write_bytes(b"pdf-a")
    (d / "b.csv").write_bytes(b"x,y\n1,2\n")
    return d


def run(engine, fn, source):
    with Session(engine) as s, s.begin():
        return fn(s, source)


def docs(engine, file_id=None):
    with Session(engine) as s:
        stmt = select(Document).order_by(Document.drive_file_id, Document.version)
        if file_id:
            stmt = stmt.where(Document.drive_file_id == file_id)
        return list(s.execute(stmt).scalars())


def state(engine) -> SyncState:
    with Session(engine) as s:
        return s.get(SyncState, 1)


def test_new_files_become_pending_with_local_source(clean_db, folder):
    r = run(clean_db, sy.reconcile, LocalFolderSource(folder))
    assert (r.new, r.seen, r.removed) == (2, 2, 0)
    rows = docs(clean_db)
    assert [(d.drive_file_id, d.status, d.version, d.is_latest, d.source) for d in rows] == [
        ("a.pdf", "pending", 1, True, "local"), ("b.csv", "pending", 1, True, "local"),
    ]
    assert rows[0].md5_checksum == hashlib.md5(b"pdf-a").hexdigest()
    assert rows[0].mime_type == "application/pdf" and rows[1].mime_type == "text/csv"
    st = state(clean_db)
    assert st.source == "local" and st.folder_name == "docs" and st.folder_id is None
    assert st.page_token and st.last_reconciled_at is not None and st.last_error is None


def test_unchanged_rerun_creates_no_rows(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    with Session(clean_db) as s, s.begin():
        s.execute(text("UPDATE documents SET status = 'done'"))
    before = [(d.id, d.status, d.version) for d in docs(clean_db)]
    for fn in (sy.reconcile, sy.incremental_sync, sy.reconcile):
        r = run(clean_db, fn, src)
        assert r.new == r.versions == r.removed == r.restored == 0
    assert [(d.id, d.status, d.version) for d in docs(clean_db)] == before


def test_changed_file_creates_new_version_and_flips_is_latest(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    (folder / "a.pdf").write_bytes(b"pdf-a edited")
    r = run(clean_db, sy.incremental_sync, src)
    assert r.versions == 1 and r.new == 0
    v1, v2 = docs(clean_db, "a.pdf")
    assert (v1.version, v1.is_latest) == (1, False)
    assert (v2.version, v2.is_latest, v2.status) == (2, True, "pending")
    assert v2.md5_checksum == hashlib.md5(b"pdf-a edited").hexdigest()
    # the same change seen again by a full reconcile is a no-op
    assert run(clean_db, sy.reconcile, src).versions == 0
    assert len(docs(clean_db, "a.pdf")) == 2


def test_deleted_file_becomes_removed_and_readd_restores(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    content = (folder / "b.csv").read_bytes()
    (folder / "b.csv").unlink()
    r = run(clean_db, sy.incremental_sync, src)
    assert r.removed == 1
    assert docs(clean_db, "b.csv")[0].status == "removed"
    (folder / "b.csv").write_bytes(content)  # unchanged re-add: same row, no duplicate
    r = run(clean_db, sy.reconcile, src)
    assert r.restored == 1 and r.new == 0
    (row,) = docs(clean_db, "b.csv")
    assert row.status == "pending" and row.version == 1


def test_reconcile_marks_missing_files_removed(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    (folder / "a.pdf").unlink()
    assert run(clean_db, sy.reconcile, src).removed == 1
    assert {d.drive_file_id: d.status for d in docs(clean_db)} == {"a.pdf": "removed", "b.csv": "pending"}


def test_files_added_and_removed_while_down_are_reconciled(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    # "worker stopped": change the folder with no sync running
    (folder / "c.html").write_bytes(b"<p>new</p>")
    (folder / "a.pdf").unlink()
    r = run(clean_db, sy.reconcile, src)  # what the worker runs on boot
    assert (r.new, r.removed) == (1, 1)
    assert {d.drive_file_id: d.status for d in docs(clean_db)} == {
        "a.pdf": "removed", "b.csv": "pending", "c.html": "pending"}


def test_unsupported_files_are_recorded_unsupported(clean_db, folder):
    (folder / "notes.txt").write_bytes(b"hello")
    (folder / "MANIFEST.md").write_text("ignored")
    r = run(clean_db, sy.reconcile, LocalFolderSource(folder))
    assert r.unsupported == 1
    by_id = {d.drive_file_id: d for d in docs(clean_db)}
    assert "MANIFEST.md" not in by_id
    assert by_id["notes.txt"].status == "unsupported" and by_id["notes.txt"].mime_type == "text/plain"


def test_revert_to_earlier_checksum_reactivates_that_version(clean_db, folder):
    src = LocalFolderSource(folder)
    run(clean_db, sy.reconcile, src)
    (folder / "a.pdf").write_bytes(b"v2")
    run(clean_db, sy.reconcile, src)
    (folder / "a.pdf").write_bytes(b"pdf-a")
    r = run(clean_db, sy.reconcile, src)
    assert r.restored == 1
    v1, v2 = docs(clean_db, "a.pdf")
    assert (v1.is_latest, v1.status, v2.is_latest) == (True, "pending", False)


def test_incremental_sync_without_cursor_reconciles(clean_db, folder):
    r = run(clean_db, sy.incremental_sync, LocalFolderSource(folder))
    assert r.kind == "reconcile" and r.new == 2


def test_removal_is_scoped_to_the_active_source(clean_db, folder):
    with Session(clean_db) as s, s.begin():
        s.add(Document(source="drive", drive_file_id="1DriveId", name="x.pdf", mime_type="application/pdf",
                       md5_checksum="abc", status="done"))
    run(clean_db, sy.reconcile, LocalFolderSource(folder))
    assert docs(clean_db, "1DriveId")[0].status == "done"


def test_cursor_and_upserts_commit_atomically(clean_db, folder, monkeypatch):
    src = LocalFolderSource(folder)
    real = sy.upsert_file
    calls = []

    def boom(session, sf, name):
        calls.append(sf.file_id)
        if len(calls) == 2:
            raise RuntimeError("crash mid-reconcile")
        return real(session, sf, name)

    monkeypatch.setattr(sy, "upsert_file", boom)
    with pytest.raises(RuntimeError):
        run(clean_db, sy.reconcile, src)
    assert docs(clean_db) == []
    st = state(clean_db)
    assert st is None or (st.page_token is None and st.last_reconciled_at is None)


def test_requests_are_cleared_unless_refreshed(clean_db, folder):
    src = LocalFolderSource(folder)
    with Session(clean_db) as s, s.begin():
        sy.get_sync_state(s)
        s.execute(text("UPDATE sync_state SET sync_requested_at = now(), reconcile_requested_at = now()"))
    run(clean_db, sy.reconcile, src)
    st = state(clean_db)
    assert st.sync_requested_at is None and st.reconcile_requested_at is None

    # A request refreshed by the api while the sync ran (value differs from the one
    # read at the start) is kept for the next tick.
    with Session(clean_db) as s, s.begin():
        st = sy.get_sync_state(s, lock=True)
        st.sync_requested_at = st.last_reconciled_at  # "refreshed"
        sy._finish(st, src, "c", seen_sync=None, seen_reconcile=None, reconcile=False, now=st.last_reconciled_at)
        assert st.sync_requested_at is not None

"""FR-3 folder selection: GET /api/drive/folders, POST /api/drive/watch (T7). The Drive
API is the in-memory fake from tests/drive_fakes.py."""

from __future__ import annotations

import threading

import pytest

from app import worker as wk
from app.api.drive import drive_api_factory_dep
from app.google.credentials import encrypt_token
from app.main import app
from app.schemas.auth import DriveFoldersResponse, WatchResponse
from app.sources.base import SourceAuthError, TransientSourceError
from app.sources.drive import DriveSource
from tests.api_helpers import api_client, assert_error, demo_settings, exec_sql, q1, rows, truncate, validate
from tests.drive_fakes import ROOT_ID, FactoryRecorder, FakeDrive
from tests.test_auth_api import REFRESH, t7_settings


@pytest.fixture
def db(test_engine):
    truncate(test_engine)
    yield test_engine
    truncate(test_engine)


@pytest.fixture
def drive():
    d = FakeDrive()
    rec = FactoryRecorder(d)
    app.dependency_overrides[drive_api_factory_dep] = lambda: rec
    d.recorder = rec
    yield d
    app.dependency_overrides.pop(drive_api_factory_dep, None)


@pytest.fixture
def s():
    return t7_settings()


def connect(db, s, email="sj.demo@gmail.com"):
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, :m, :e, '{}')", m=email, e=encrypt_token(s, REFRESH))


# ---- folders -------------------------------------------------------------------------------


def test_folders_root_lists_only_direct_child_folders_sorted(db, s, drive):
    connect(db, s)
    fund = drive.add_folder("Fund Docs")
    drive.add_folder("alpha")
    drive.add_folder("Tax archive")
    drive.add_folder("Old", trashed=True)
    drive.add_folder("nested", parent=fund)
    drive.add_file("loose.pdf", ROOT_ID)
    with api_client(s) as c:
        body = c.get("/api/drive/folders").json()
    validate(DriveFoldersResponse, body)
    assert body["parent"] == {"folder_id": "root", "folder_name": "My Drive", "parent_id": None, "is_root": True}
    assert [i["folder_name"] for i in body["items"]] == ["alpha", "Fund Docs", "Tax archive"]
    assert body["items"][1]["folder_id"] == fund
    assert body["items"][1]["modified_at"] == "2026-10-15T09:12:44Z"
    assert body["next_page_token"] is None
    assert drive.recorder.tokens == [REFRESH]  # decrypted from the DB for this request


def test_folders_pagination(db, s, drive):
    connect(db, s)
    for i in range(5):
        drive.add_folder(f"f{i}")
    drive.max_page = 2
    seen = []
    with api_client(s) as c:
        token = None
        for _ in range(5):
            params = {"page_token": token} if token else {}
            body = c.get("/api/drive/folders", params=params).json()
            seen += [i["folder_name"] for i in body["items"]]
            token = body["next_page_token"]
            if token is None:
                break
    assert seen == ["f0", "f1", "f2", "f3", "f4"]


def test_folders_non_root_parent_and_up_link(db, s, drive):
    connect(db, s)
    fund = drive.add_folder("Fund Docs")
    sub = drive.add_folder("2026", parent=fund)
    drive.add_folder("Q1", parent=sub)
    with api_client(s) as c:
        body = c.get("/api/drive/folders", params={"parent": fund}).json()
        assert body["parent"] == {"folder_id": fund, "folder_name": "Fund Docs", "parent_id": "root",
                                  "is_root": False}
        assert [i["folder_name"] for i in body["items"]] == ["2026"]
        body = c.get("/api/drive/folders", params={"parent": sub}).json()
        assert body["parent"]["parent_id"] == fund
        # the real root id is treated as root
        assert c.get("/api/drive/folders", params={"parent": ROOT_ID}).json()["parent"]["is_root"] is True


def test_folders_errors(db, s, drive):
    with api_client(s) as c:
        assert_error(c.get("/api/drive/folders"), 409, "not_connected")
    connect(db, s)
    pdf = drive.add_file("a.pdf", ROOT_ID)
    with api_client(s) as c:
        assert_error(c.get("/api/drive/folders", params={"parent": "nope"}), 404, "not_found")
        assert_error(c.get("/api/drive/folders", params={"parent": pdf}), 404, "not_found")
        assert_error(c.get("/api/drive/folders", params={"parent": "bad'id"}), 404, "not_found")
        body = assert_error(c.get("/api/drive/folders", params={"page_token": "zzz"}), 422, "validation_error")
        assert body["error"]["details"][0]["loc"] == "query.page_token"
        assert_error(c.get("/api/drive/folders", params={"x": 1}), 422, "validation_error")
        drive.fail["files_list"] = [TransientSourceError("Drive files.list failed after 4 attempts: HTTP 503")]
        assert_error(c.get("/api/drive/folders"), 502, "drive_error")
    with api_client(demo_settings()) as c:
        assert_error(c.get("/api/drive/folders"), 409, "demo_mode")


def test_folders_auth_error_drops_credentials(db, s, drive):
    connect(db, s)
    drive.fail["files_get"] = [SourceAuthError("Google Drive access was revoked or has expired. Reconnect.")]
    with api_client(s) as c:
        assert_error(c.get("/api/drive/folders"), 409, "not_connected")
        assert c.get("/api/auth/status").json()["connected"] is False
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0
    assert "revoked" in q1(db, "SELECT last_error FROM sync_state")


def test_folders_undecryptable_token_is_not_connected(db, s, drive):
    connect(db, t7_settings())  # encrypted with another FERNET_KEY
    with api_client(s) as c:
        body = assert_error(c.get("/api/drive/folders"), 409, "not_connected")
    assert "FERNET_KEY" in body["error"]["message"]
    assert drive.recorder.tokens == []


# ---- watch -----------------------------------------------------------------------------------


def test_watch_sets_folder_resets_token_and_requests_reconcile(db, s, drive):
    connect(db, s)
    fund = drive.add_folder("Fund Docs")
    exec_sql(db, "INSERT INTO sync_state (id, source, page_token, last_error) VALUES (1, 'drive', '99', 'old error')")
    with api_client(s) as c:
        r = c.post("/api/drive/watch", json={"folder_id": fund})
        assert r.status_code == 200
        body = r.json()
        validate(WatchResponse, body)
        assert body["folder"] == {"source": "drive", "folder_id": fund, "folder_name": "Fund Docs"}
        status = c.get("/api/auth/status").json()
        assert status["folder"] == body["folder"]
        prog = c.get("/api/progress").json()
        assert prog["sync"]["reconcile_pending"] is True and prog["sync"]["folder_name"] == "Fund Docs"
    (st,) = rows(db, "SELECT * FROM sync_state")
    assert st["folder_id"] == fund and st["page_token"] is None and st["reconcile_requested_at"] is not None
    assert st["last_error"] is None and st["source"] == "drive"


def test_watch_root_stores_real_id(db, s, drive):
    connect(db, s)
    with api_client(s) as c:
        body = c.post("/api/drive/watch", json={"folder_id": "root"}).json()
    assert body["folder"]["folder_id"] == ROOT_ID and body["folder"]["folder_name"] == "My Drive"


def test_watch_errors(db, s, drive):
    with api_client(s) as c:
        assert_error(c.post("/api/drive/watch", json={"folder_id": "x"}), 409, "not_connected")
    connect(db, s)
    pdf = drive.add_file("a.pdf", ROOT_ID)
    gone = drive.add_folder("Old", trashed=True)
    with api_client(s) as c:
        assert_error(c.post("/api/drive/watch", json={"folder_id": "missing"}), 404, "not_found")
        assert_error(c.post("/api/drive/watch", json={"folder_id": gone}), 404, "not_found")
        body = assert_error(c.post("/api/drive/watch", json={"folder_id": pdf}), 422, "validation_error")
        assert body["error"]["details"][0]["loc"] == "body.folder_id"
        assert_error(c.post("/api/drive/watch", json={}), 422, "validation_error")
        assert_error(c.post("/api/drive/watch", json={"folder_id": "a", "x": 1}), 422, "validation_error")
        drive.fail["files_get"] = [TransientSourceError("Drive files.get failed after 4 attempts")]
        assert_error(c.post("/api/drive/watch", json={"folder_id": pdf}), 502, "drive_error")
    with api_client(demo_settings()) as c:
        assert_error(c.post("/api/drive/watch", json={"folder_id": "x"}), 409, "demo_mode")
    assert q1(db, "SELECT folder_id FROM sync_state") is None


def test_watch_is_picked_up_by_worker_and_switching_removes_old_docs(db, s, drive):
    """Watch -> the worker's next tick reconciles that folder; switching folders marks the
    old folder's documents removed on the following reconcile (T3 decision 7)."""
    connect(db, s)
    a = drive.add_folder("A")
    b = drive.add_folder("B")
    drive.add_file("a1.pdf", a, b"%PDF a1")
    drive.add_file("b1.pdf", b, b"%PDF b1")
    resolver = lambda st, sess: DriveSource.from_db(st, sess, api_factory=FactoryRecorder(drive))  # noqa: E731
    w = wk.Worker(s, db, threading.Event(), source_resolver=resolver, indexing=False)
    assert w.sync_tick() is None  # no folder yet: idle
    with api_client(s) as c:
        c.post("/api/drive/watch", json={"folder_id": a})
    assert w.sync_tick() == "reconcile"
    assert rows(db, "SELECT name, status, source FROM documents") == [
        {"name": "a1.pdf", "status": "pending", "source": "drive"}]
    with api_client(s) as c:
        c.post("/api/drive/watch", json={"folder_id": b})
    assert w.sync_tick() == "reconcile"
    got = {r["name"]: r["status"] for r in rows(db, "SELECT name, status FROM documents")}
    assert got == {"a1.pdf": "removed", "b1.pdf": "pending"}
    st = rows(db, "SELECT reconcile_requested_at, page_token FROM sync_state")[0]
    assert st["reconcile_requested_at"] is None and st["page_token"] == drive.next_token

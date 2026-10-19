import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app import worker
from app.config import Settings
from app.db import current_revision
from app.migrations_info import head_revisions
from tests.conftest import TEST_DATABASE_URL


def test_head_revision_chain_has_single_head():
    assert len(head_revisions()) == 1


def test_wait_returns_immediately_when_at_head():
    stop = threading.Event()
    assert worker.wait_for_migrations(lambda: "0001", {"0001"}, stop, poll_seconds=0) is True


def test_wait_polls_until_migrations_applied():
    seq = iter([OperationalError("x", {}, Exception()), None, "0000", "0001"])
    calls = []

    def get_rev():
        calls.append(1)
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    stop = threading.Event()
    assert worker.wait_for_migrations(get_rev, {"0001"}, stop, poll_seconds=0) is True
    assert len(calls) == 4


def test_wait_aborts_when_stopped():
    stop = threading.Event()
    stop.set()
    assert worker.wait_for_migrations(lambda: None, {"0001"}, stop, poll_seconds=0) is False


def test_current_revision_at_head(test_engine):
    assert current_revision(test_engine) in head_revisions()


def test_current_revision_none_before_migrations(test_engine):
    with test_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS t1_empty CASCADE; CREATE SCHEMA t1_empty"))
    eng = create_engine(TEST_DATABASE_URL, connect_args={"options": "-csearch_path=t1_empty"})
    try:
        assert current_revision(eng) is None
    finally:
        eng.dispose()
        with test_engine.begin() as conn:
            conn.execute(text("DROP SCHEMA t1_empty CASCADE"))


def test_pgvector_extension_installed(test_engine):
    with test_engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'")).scalar() == 1


def test_run_waits_then_iterates_and_survives_errors(test_engine, monkeypatch, clean_env):
    clean_env.setenv("SYNC_INTERVAL_SECONDS", "1")
    s = Settings(_env_file=None)
    monkeypatch.setattr(worker, "get_engine", lambda: test_engine)
    monkeypatch.setattr(worker, "resolve_source", lambda settings, session: None)  # no source: threads idle
    calls = []

    def flaky(w):
        calls.append(w)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(worker, "run_iteration", flaky)
    stop = threading.Event()
    assert worker.run(s, stop, max_iterations=3, source_resolver=lambda settings, session: None,
                      tick_seconds=0) == 0
    assert len(calls) == 3 and stop.is_set()


def test_main_exits_nonzero_on_missing_config(monkeypatch, clean_env):
    clean_env.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(worker, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(worker, "run", lambda *a, **k: pytest.fail("must not run"))
    assert worker.main() == 2

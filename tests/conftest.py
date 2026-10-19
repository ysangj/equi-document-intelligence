"""Shared fixtures.

Tests never touch the dev database. At import time DATABASE_URL and
DATABASE_READONLY_URL are re-pointed at a separate test database
(TEST_DATABASE_NAME, default `docintel_test`) on the same Postgres server; the
`test_engine` fixture drops/recreates it once per session and runs
`alembic upgrade head`. `db_session` wraps each test in a rolled-back transaction.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings, get_settings

TEST_DB_NAME = os.environ.get("TEST_DATABASE_NAME", "docintel_test")

_base = Settings()  # env + .env, read once to learn the server location
_dev_url = make_url(_base.database_url)
if _dev_url.database == TEST_DB_NAME:
    raise RuntimeError("TEST_DATABASE_NAME must differ from the dev database name")
TEST_DATABASE_URL = _dev_url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)
TEST_READONLY_URL = (
    make_url(_base.database_readonly_url).set(database=TEST_DB_NAME).render_as_string(hide_password=False)
)
MAINTENANCE_URL = _dev_url.set(database="postgres").render_as_string(hide_password=False)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DATABASE_READONLY_URL"] = TEST_READONLY_URL
get_settings.cache_clear()


def _recreate_test_database() -> None:
    admin = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    except Exception as e:  # noqa: BLE001
        pytest.exit(
            f"Cannot reach Postgres to create the test database ({type(e).__name__}). "
            "Start it with `docker compose up -d postgres` or run scripts/test.sh.",
            returncode=2,
        )
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def test_engine():
    from alembic import command

    from app.migrations_info import alembic_config

    _recreate_test_database()
    cfg = alembic_config(TEST_DATABASE_URL)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    conn = test_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


_TRUNCATE_INGEST = "TRUNCATE documents, extractions, sync_state, funds, accounts RESTART IDENTITY CASCADE"


@pytest.fixture
def clean_db(test_engine):
    """For tests that commit (multiple sessions/threads): empty the ingestion tables
    (and everything that cascades from documents) before and after the test."""
    with test_engine.begin() as conn:
        conn.execute(text(_TRUNCATE_INGEST))
    yield test_engine
    with test_engine.begin() as conn:
        conn.execute(text(_TRUNCATE_INGEST))


# Every §11 variable, so config tests can start from a clean slate.
CONFIG_ENV_VARS = (
    "ANTHROPIC_API_KEY", "CLAUDE_MODEL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI", "OAUTHLIB_INSECURE_TRANSPORT", "DATABASE_URL",
    "DATABASE_READONLY_URL", "FERNET_KEY", "SESSION_SECRET", "SYNC_INTERVAL_SECONDS",
    "WORKER_CONCURRENCY", "MAX_ATTEMPTS", "REVIEW_CONFIDENCE_THRESHOLD",
    "EMBEDDING_MODEL", "ASK_MAX_STEPS", "DEMO_MODE",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all config vars from the environment. Use with Settings(_env_file=None)
    so the developer's real .env is never read by config tests."""
    for k in CONFIG_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture(autouse=True)
def fake_embedder():
    """Every test embeds with a fast deterministic fake unless it opts out (the real
    fastembed test uses `FastEmbedEmbedder` directly)."""
    from app.search import embedding
    from tests.embedding_fakes import FakeEmbedder

    fake = FakeEmbedder()
    embedding.set_embedder_override(fake)
    yield fake
    embedding.set_embedder_override(None)

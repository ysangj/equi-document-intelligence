import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from sqlalchemy.engine import make_url

import app.main as main_mod
from app.config import ConfigError, Settings, get_settings
from app.db import get_engine
from app.main import app, build_config
from app.migrations_info import head_revisions
from tests.conftest import TEST_DB_NAME


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok_reports_db_and_revision(client, test_engine):
    app.dependency_overrides[get_engine] = lambda: test_engine
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "db": "ok", "alembic_revision": body["alembic_revision"]}
    assert body["alembic_revision"] in head_revisions()


def test_health_db_down_returns_503(client):
    dead = create_engine(
        "postgresql+psycopg://nobody:nopass@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    app.dependency_overrides[get_engine] = lambda: dead
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "db": "error", "alembic_revision": None}
    assert "nopass" not in r.text


def test_health_uses_configured_database(client, test_engine):
    # No override: the app's engine must point at the test DB (conftest), never the dev DB.
    assert make_url(get_settings().database_url).database == TEST_DB_NAME
    assert client.get("/api/health").status_code == 200


def _settings(env, **values):
    for k, v in values.items():
        env.setenv(k, v)
    return Settings(_env_file=None)


def test_config_demo_without_keys(clean_env, client):
    app.dependency_overrides[get_settings] = lambda: _settings(clean_env)
    body = client.get("/api/config").json()
    assert body == {
        "demo_mode": True,
        "demo_reason": "GOOGLE_CLIENT_ID_UNSET",
        "extraction_source": "fixture",
        "claude_model": "claude-opus-5-5",
        "sync_interval_seconds": 30,
        "features": {
            "browse": {"enabled": True, "reason": None},
            "search": {"enabled": True, "reason": None},
            "review": {"enabled": True, "reason": None},
            "ask": {"enabled": False, "reason": "Ask is disabled because ANTHROPIC_API_KEY is not set."},
            "drive": {
                "enabled": False,
                "reason": "Demo mode: documents are read from ./sample_docs instead of Google Drive.",
            },
        },
    }


def test_config_drive_mode_with_key(clean_env, client):
    s = _settings(
        clean_env,
        ANTHROPIC_API_KEY="sk-ant-abcdefgh",
        GOOGLE_CLIENT_ID="cid",
        GOOGLE_CLIENT_SECRET="csecret",
        FERNET_KEY="k" * 44,
        SESSION_SECRET="s" * 40,
    )
    app.dependency_overrides[get_settings] = lambda: s
    r = client.get("/api/config")
    body = r.json()
    assert body["demo_mode"] is False
    assert body["demo_reason"] is None
    assert body["extraction_source"] == "claude"
    assert body["features"]["ask"] == {"enabled": True, "reason": None}
    assert body["features"]["drive"] == {"enabled": True, "reason": None}
    for secret in ("sk-ant-abcdefgh", "csecret", "k" * 44, "s" * 40):
        assert secret not in r.text


def test_config_demo_flag_with_key_enables_ask(clean_env):
    c = build_config(_settings(clean_env, DEMO_MODE="true", GOOGLE_CLIENT_ID="cid", ANTHROPIC_API_KEY="sk-ant-x"))
    assert c.demo_mode and c.demo_reason == "DEMO_MODE"
    assert c.features.ask.enabled is True
    assert c.extraction_source == "claude"


@pytest.fixture
def lifespan_settings(clean_env):
    """Make the lifespan use isolated settings (never the developer's .env)."""
    holder = {}
    clean_env.setattr(main_mod, "get_settings", lambda: holder["s"])
    clean_env.setattr(main_mod, "configure_logging", lambda *a, **k: None)
    app.dependency_overrides[get_settings] = lambda: holder["s"]
    yield lambda **values: holder.__setitem__("s", _settings(clean_env, **values))
    app.dependency_overrides.clear()


def test_startup_fails_fast_on_missing_config(lifespan_settings):
    lifespan_settings(GOOGLE_CLIENT_ID="cid")  # drive mode, everything else missing
    with pytest.raises(ConfigError) as ei:
        with TestClient(app):
            pass
    assert ei.value.missing == ["ANTHROPIC_API_KEY", "GOOGLE_CLIENT_SECRET", "FERNET_KEY", "SESSION_SECRET"]


def test_startup_succeeds_in_demo_mode(lifespan_settings):
    lifespan_settings()
    with TestClient(app) as c:
        assert c.get("/api/config").json()["demo_mode"] is True

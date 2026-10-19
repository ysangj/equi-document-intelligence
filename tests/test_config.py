import pytest
from pydantic import ValidationError

from app.config import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_DATABASE_READONLY_URL,
    DEFAULT_DATABASE_URL,
    ConfigError,
    Settings,
    require_settings,
)

DRIVE_VARS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-0000000000",
    "GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "client-secret-xyz",
    "FERNET_KEY": "f" * 44,
    "SESSION_SECRET": "s" * 40,
}


def make(env, **values):
    for k, v in values.items():
        env.setenv(k, v)
    return Settings(_env_file=None)


def test_defaults_match_prd(clean_env):
    s = make(clean_env)
    assert s.claude_model == DEFAULT_CLAUDE_MODEL == "claude-opus-5-5"
    assert s.database_url == DEFAULT_DATABASE_URL
    assert s.database_readonly_url == DEFAULT_DATABASE_READONLY_URL
    assert s.google_redirect_uri == "http://localhost:8000/api/auth/google/callback"
    assert s.sync_interval_seconds == 30
    assert s.worker_concurrency == 4
    assert s.max_attempts == 3
    assert s.review_confidence_threshold == 0.7
    assert s.embedding_model == "BAAI/bge-small-en-v1.5"
    assert s.ask_max_steps == 6
    assert s.demo_mode_flag is False


def test_demo_mode_when_google_client_id_unset(clean_env):
    s = make(clean_env)
    assert s.demo_mode is True
    assert s.demo_reason == "GOOGLE_CLIENT_ID_UNSET"


def test_empty_values_treated_as_unset(clean_env):
    s = make(clean_env, GOOGLE_CLIENT_ID="", DEMO_MODE="", SYNC_INTERVAL_SECONDS="")
    assert s.demo_mode is True
    assert s.demo_mode_flag is False
    assert s.sync_interval_seconds == 30


@pytest.mark.parametrize("flag", ["true", "TRUE", "1", "yes", "on"])
def test_demo_mode_flag_forces_demo_even_with_google(clean_env, flag):
    s = make(clean_env, DEMO_MODE=flag, **DRIVE_VARS)
    assert s.demo_mode is True
    assert s.demo_reason == "DEMO_MODE"


@pytest.mark.parametrize("flag", ["false", "0", "no", "off"])
def test_demo_mode_flag_false_with_google_is_drive_mode(clean_env, flag):
    s = make(clean_env, DEMO_MODE=flag, **DRIVE_VARS)
    assert s.demo_mode is False
    assert s.demo_reason is None


def test_invalid_demo_flag_rejected(clean_env):
    with pytest.raises(ValidationError):
        make(clean_env, DEMO_MODE="maybe")


def test_drive_mode_complete_config_passes(clean_env):
    s = make(clean_env, **DRIVE_VARS)
    assert s.missing_required() == []
    assert require_settings(s) is s
    assert s.ask_enabled is True
    assert s.use_fixtures is False


def test_drive_mode_fails_fast_naming_missing_vars(clean_env):
    s = make(clean_env, GOOGLE_CLIENT_ID="cid")
    with pytest.raises(ConfigError) as ei:
        require_settings(s)
    err = ei.value
    assert err.missing == [
        "ANTHROPIC_API_KEY",
        "GOOGLE_CLIENT_SECRET",
        "FERNET_KEY",
        "SESSION_SECRET",
    ]
    assert err.demo_mode is False
    for name in err.missing:
        assert name in str(err)


def test_drive_mode_error_never_contains_values(clean_env):
    s = make(clean_env, GOOGLE_CLIENT_ID="cid-secretish-value", SESSION_SECRET="supersecretvalue123")
    with pytest.raises(ConfigError) as ei:
        require_settings(s)
    assert "supersecretvalue123" not in str(ei.value)
    assert "cid-secretish-value" not in str(ei.value)


def test_demo_mode_requires_no_keys(clean_env):
    s = make(clean_env)
    assert s.missing_required() == []
    require_settings(s)
    assert s.ask_enabled is False
    assert s.use_fixtures is True


def test_demo_mode_with_anthropic_key_uses_claude(clean_env):
    s = make(clean_env, ANTHROPIC_API_KEY="sk-ant-x")
    assert s.demo_mode is True
    assert s.ask_enabled is True
    assert s.use_fixtures is False


def test_blank_anthropic_key_disables_ask(clean_env):
    s = make(clean_env, ANTHROPIC_API_KEY="   ")
    assert s.ask_enabled is False


def test_secrets_are_masked_in_repr(clean_env):
    s = make(clean_env, **DRIVE_VARS)
    r = repr(s)
    for name in ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_SECRET", "FERNET_KEY", "SESSION_SECRET"):
        assert DRIVE_VARS[name] not in r
    assert set(s.secret_values()) == {
        DRIVE_VARS[k] for k in ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_SECRET", "FERNET_KEY", "SESSION_SECRET")
    }


@pytest.mark.parametrize(
    "var,value",
    [("SYNC_INTERVAL_SECONDS", "0"), ("WORKER_CONCURRENCY", "-1"), ("REVIEW_CONFIDENCE_THRESHOLD", "1.5")],
)
def test_invalid_tuning_values_rejected(clean_env, var, value):
    with pytest.raises(ValidationError):
        make(clean_env, **{var: value})


def test_reads_dotenv_file(clean_env, tmp_path):
    f = tmp_path / ".env"
    f.write_text("GOOGLE_CLIENT_ID=abc\nSYNC_INTERVAL_SECONDS=5\nDEMO_MODE=\n")
    s = Settings(_env_file=f)
    assert s.google_client_id == "abc"
    assert s.sync_interval_seconds == 5
    assert s.demo_mode is False


def test_environment_overrides_dotenv(clean_env, tmp_path):
    f = tmp_path / ".env"
    f.write_text("SYNC_INTERVAL_SECONDS=5\n")
    clean_env.setenv("SYNC_INTERVAL_SECONDS", "7")
    assert Settings(_env_file=f).sync_interval_seconds == 7


# ---- BUG-Q1-01 / BUG-Q1-02: env-over-.env precedence and blank values ----------
# Throwaway .env files in tmp_path only; the real .env is never read.

DRIVE_DOTENV = "".join(f"{k}={v}\n" for k, v in DRIVE_VARS.items())

BLANKS = ["", " ", "   ", "\t"]


def dotenv(tmp_path, text=DRIVE_DOTENV):
    f = tmp_path / ".env"
    f.write_text(text)
    return f


def test_dotenv_only_is_drive_mode(clean_env, tmp_path):
    s = Settings(_env_file=dotenv(tmp_path))
    assert s.demo_mode is False
    assert s.missing_required() == []


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_env_secret_overrides_dotenv_and_fails_fast(clean_env, tmp_path, blank):
    """BUG-Q1-01: `GOOGLE_CLIENT_SECRET= python -m app.config` must fail naming the key."""
    clean_env.setenv("GOOGLE_CLIENT_SECRET", blank)
    s = Settings(_env_file=dotenv(tmp_path))
    assert s.google_client_secret is None
    assert s.demo_mode is False
    with pytest.raises(ConfigError) as ei:
        require_settings(s)
    assert ei.value.missing == ["GOOGLE_CLIENT_SECRET"]


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_env_google_client_id_forces_demo(clean_env, tmp_path, blank):
    """BUG-Q1-01 + BUG-Q1-02: blank GOOGLE_CLIENT_ID in the env -> demo mode, nothing missing."""
    clean_env.setenv("GOOGLE_CLIENT_ID", blank)
    s = Settings(_env_file=dotenv(tmp_path))
    assert s.google_client_id is None
    assert s.demo_mode is True
    assert s.demo_reason == "GOOGLE_CLIENT_ID_UNSET"
    assert s.missing_required() == []


@pytest.mark.parametrize("value", ['" "', "' '", "   ", '""'])
def test_blank_google_client_id_in_dotenv_is_demo(clean_env, tmp_path, value):
    s = Settings(_env_file=dotenv(tmp_path, DRIVE_DOTENV + f"GOOGLE_CLIENT_ID={value}\n"))
    assert s.demo_mode is True
    assert s.demo_reason == "GOOGLE_CLIENT_ID_UNSET"
    assert s.missing_required() == []


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_init_google_client_id_is_demo(clean_env, blank):
    s = Settings(_env_file=None, google_client_id=blank)
    assert s.demo_mode is True and s.demo_reason == "GOOGLE_CLIENT_ID_UNSET"


def test_blank_env_demo_flag_overrides_dotenv_true(clean_env, tmp_path):
    clean_env.setenv("DEMO_MODE", " ")
    s = Settings(_env_file=dotenv(tmp_path, DRIVE_DOTENV + "DEMO_MODE=true\n"))
    assert s.demo_mode_flag is False
    assert s.demo_mode is False


def test_blank_env_anthropic_key_overrides_dotenv_and_disables_ask(clean_env, tmp_path):
    clean_env.setenv("GOOGLE_CLIENT_ID", "")
    clean_env.setenv("ANTHROPIC_API_KEY", "")
    s = Settings(_env_file=dotenv(tmp_path))
    assert s.demo_mode is True
    assert s.ask_enabled is False
    assert s.use_fixtures is True


@pytest.mark.parametrize("blank", BLANKS)
def test_blank_env_tuning_value_uses_default_not_dotenv(clean_env, tmp_path, blank):
    clean_env.setenv("SYNC_INTERVAL_SECONDS", blank)
    clean_env.setenv("CLAUDE_MODEL", blank)
    s = Settings(_env_file=dotenv(tmp_path, "SYNC_INTERVAL_SECONDS=5\nCLAUDE_MODEL=other\n"))
    assert s.sync_interval_seconds == 30
    assert s.claude_model == DEFAULT_CLAUDE_MODEL


def test_nonblank_env_still_overrides_dotenv(clean_env, tmp_path):
    clean_env.setenv("GOOGLE_CLIENT_ID", "env-cid")
    s = Settings(_env_file=dotenv(tmp_path, DRIVE_DOTENV.replace(DRIVE_VARS["GOOGLE_CLIENT_ID"], "file-cid")))
    assert s.google_client_id == "env-cid"


@pytest.mark.parametrize("blank", ["", " "])
def test_api_config_reports_demo_for_blank_google_client_id(clean_env, tmp_path, blank):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    clean_env.setenv("GOOGLE_CLIENT_ID", blank)
    s = Settings(_env_file=dotenv(tmp_path))
    app.dependency_overrides[get_settings] = lambda: s
    try:
        body = TestClient(app).get("/api/config").json()
    finally:
        app.dependency_overrides.clear()
    assert body["demo_mode"] is True
    assert body["demo_reason"] == "GOOGLE_CLIENT_ID_UNSET"
    assert body["features"]["drive"]["enabled"] is False


def test_cli_blank_env_secret_exits_1_naming_key_only(clean_env, tmp_path, capsys):
    """`python -m app.config` path (main()) for BUG-Q1-01, without the real .env."""
    import app.config as cfg

    clean_env.setenv("GOOGLE_CLIENT_SECRET", "")
    s = Settings(_env_file=dotenv(tmp_path))
    clean_env.setattr(cfg, "get_settings", lambda: s)
    assert cfg.main() == 1
    out = capsys.readouterr()
    assert "GOOGLE_CLIENT_SECRET" in out.err
    assert "mode: drive" in out.out
    for v in DRIVE_VARS.values():
        assert v not in out.out + out.err


def test_cli_blank_google_client_id_is_demo(clean_env, tmp_path, capsys):
    import app.config as cfg

    clean_env.setenv("GOOGLE_CLIENT_ID", " ")
    s = Settings(_env_file=dotenv(tmp_path))
    clean_env.setattr(cfg, "get_settings", lambda: s)
    assert cfg.main() == 0
    out = capsys.readouterr()
    assert "mode: demo (reason: GOOGLE_CLIENT_ID_UNSET)" in out.out
    assert "missing" not in out.err

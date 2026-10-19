"""scripts/lib.sh: Postgres host-port resolution (environment > .env > 5432) and the
env_is_set / env_is_true / check_env_vars rules shared with app/config.py
(BUG-Q1-01, BUG-Q1-02). Every test uses a throwaway ROOT/.env in tmp_path."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib.sh"


def resolve_port(root: Path, env_port: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "POSTGRES_PORT"}
    if env_port is not None:
        env["POSTGRES_PORT"] = env_port
    env["ROOT"] = str(root)
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; printf "%s" "$PG_PORT"'],
        env=env, capture_output=True, text=True, timeout=10,
    )


def test_default_port_without_env_file(tmp_path):
    assert resolve_port(tmp_path).stdout == "5432"


@pytest.mark.parametrize(
    "line",
    ["POSTGRES_PORT=55432", "POSTGRES_PORT = 55432", 'POSTGRES_PORT="55432"', "export POSTGRES_PORT=55432",
     "POSTGRES_PORT=55432  # local override"],
)
def test_port_read_from_env_file(tmp_path, line):
    (tmp_path / ".env").write_text(f"OTHER=1\nPOSTGRES_PORT=1111\n{line}\n")
    assert resolve_port(tmp_path).stdout == "55432"  # last assignment wins


def test_environment_wins_over_env_file(tmp_path):
    (tmp_path / ".env").write_text("POSTGRES_PORT=55432\n")
    assert resolve_port(tmp_path, "6000").stdout == "6000"


@pytest.mark.parametrize("blank", ["", " ", "  "])
def test_blank_environment_port_hides_env_file_and_uses_default(tmp_path, blank):
    (tmp_path / ".env").write_text("POSTGRES_PORT=55432\n")
    assert resolve_port(tmp_path, blank).stdout == "5432"


def test_environment_port_is_trimmed(tmp_path):
    assert resolve_port(tmp_path, " 6000 ").stdout == "6000"


def test_resolved_port_is_exported_for_compose(tmp_path):
    (tmp_path / ".env").write_text("POSTGRES_PORT=55432\n")
    env = {k: v for k, v in os.environ.items() if k != "POSTGRES_PORT"}
    env["ROOT"] = str(tmp_path)
    r = subprocess.run(["bash", "-c", f'source "{LIB}"; printenv POSTGRES_PORT'],
                       env=env, capture_output=True, text=True, timeout=10)
    assert r.stdout.strip() == "55432"


def test_empty_value_falls_back_to_default(tmp_path):
    (tmp_path / ".env").write_text("POSTGRES_PORT=\n")
    assert resolve_port(tmp_path).stdout == "5432"


def test_non_numeric_port_fails(tmp_path):
    (tmp_path / ".env").write_text("POSTGRES_PORT=abc\n")
    r = resolve_port(tmp_path)
    assert r.returncode != 0 and "POSTGRES_PORT must be a number" in r.stderr


def test_other_env_values_never_printed(tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-secret-value\nPOSTGRES_PORT=55432\n")
    r = resolve_port(tmp_path)
    assert "sk-secret-value" not in r.stdout + r.stderr


def test_env_example_documents_postgres_port():
    example = (LIB.parent.parent / ".env.example").read_text().splitlines()
    i = example.index("POSTGRES_PORT=5432")
    assert example[i - 1].startswith("#")


# ---- env_is_set / env_is_true / check_env_vars ---------------------------------

from tests.conftest import CONFIG_ENV_VARS  # noqa: E402

DRIVE_VARS = {
    "ANTHROPIC_API_KEY": "sk-ant-test-0000000000",
    "GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "client-secret-xyz",
    "FERNET_KEY": "f" * 44,
    "SESSION_SECRET": "s" * 40,
}
DRIVE_DOTENV = "".join(f"{k}={v}\n" for k, v in DRIVE_VARS.items())


def run_lib(root: Path, script: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in CONFIG_ENV_VARS and k != "POSTGRES_PORT"}
    env.update(env_overrides or {})
    env["ROOT"] = str(root)
    return subprocess.run(["bash", "-c", f'source "{LIB}"; {script}'],
                          env=env, capture_output=True, text=True, timeout=10)


def is_set(root: Path, key: str, env: dict[str, str] | None = None) -> bool:
    return run_lib(root, f"env_is_set {key}", env).returncode == 0


def is_true(root: Path, key: str, env: dict[str, str] | None = None) -> bool:
    return run_lib(root, f"env_is_true {key}", env).returncode == 0


@pytest.mark.parametrize("blank", ["", " ", "\t "])
def test_env_is_set_blank_environment_overrides_env_file(tmp_path, blank):
    (tmp_path / ".env").write_text("GOOGLE_CLIENT_SECRET=from-file\n")
    assert is_set(tmp_path, "GOOGLE_CLIENT_SECRET")
    assert not is_set(tmp_path, "GOOGLE_CLIENT_SECRET", {"GOOGLE_CLIENT_SECRET": blank})


@pytest.mark.parametrize("value", ["", " ", '" "', "' '", '""', "   # just a comment"])
def test_env_is_set_blank_env_file_value_is_unset(tmp_path, value):
    (tmp_path / ".env").write_text(f"GOOGLE_CLIENT_ID={value}\n")
    assert not is_set(tmp_path, "GOOGLE_CLIENT_ID")


@pytest.mark.parametrize("line", ["K=v", "export K=v", "K = v", 'K="v"', "K='v'", "K=v # c", "K=#v", "K=v#x"])
def test_env_is_set_nonblank_env_file_value(tmp_path, line):
    (tmp_path / ".env").write_text(f"{line}\n")
    assert is_set(tmp_path, "K")


def test_env_is_set_nonblank_environment_without_env_file(tmp_path):
    assert is_set(tmp_path, "K", {"K": "x"})
    assert not is_set(tmp_path, "K")


@pytest.mark.parametrize("value", ["true", "TRUE", " true ", "1", "yes", "on", '"true"', "'on'", "export"])
def test_env_is_true_from_env_file(tmp_path, value):
    line = "export DEMO_MODE=true" if value == "export" else f"DEMO_MODE={value}"
    (tmp_path / ".env").write_text(line + "\n")
    assert is_true(tmp_path, "DEMO_MODE")


@pytest.mark.parametrize("blank", ["", " "])
def test_env_is_true_blank_environment_overrides_env_file(tmp_path, blank):
    (tmp_path / ".env").write_text("DEMO_MODE=true\n")
    assert not is_true(tmp_path, "DEMO_MODE", {"DEMO_MODE": blank})
    assert is_true(tmp_path, "DEMO_MODE", {"DEMO_MODE": " TRUE "})


def check(root: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return run_lib(root, "check_env_vars", env)


def test_check_env_vars_drive_mode_passes(tmp_path):
    (tmp_path / ".env").write_text(DRIVE_DOTENV)
    r = check(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Google Drive mode" in r.stdout


@pytest.mark.parametrize("blank", ["", " "])
def test_check_env_vars_blank_secret_in_environment_fails(tmp_path, blank):
    """BUG-Q1-01 for start.sh: an empty env var hides the .env value."""
    (tmp_path / ".env").write_text(DRIVE_DOTENV)
    r = check(tmp_path, {"GOOGLE_CLIENT_SECRET": blank})
    assert r.returncode != 0
    assert "missing required variables in .env: GOOGLE_CLIENT_SECRET " in r.stderr


@pytest.mark.parametrize("blank", ["", " ", "   "])
def test_check_env_vars_blank_google_client_id_in_environment_is_demo(tmp_path, blank):
    """BUG-Q1-01 + BUG-Q1-02: `GOOGLE_CLIENT_ID= ./start.sh` forces demo mode."""
    (tmp_path / ".env").write_text(DRIVE_DOTENV)
    r = check(tmp_path, {"GOOGLE_CLIENT_ID": blank})
    assert r.returncode == 0, r.stderr
    assert "Demo mode" in r.stdout
    assert "Google Drive mode" not in r.stdout


@pytest.mark.parametrize("value", [" ", '" "', "''"])
def test_check_env_vars_blank_google_client_id_in_env_file_is_demo(tmp_path, value):
    (tmp_path / ".env").write_text(DRIVE_DOTENV + f"GOOGLE_CLIENT_ID={value}\n")
    r = check(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Demo mode" in r.stdout


def test_check_env_vars_never_prints_values(tmp_path):
    (tmp_path / ".env").write_text(DRIVE_DOTENV)
    for env in ({}, {"GOOGLE_CLIENT_SECRET": ""}, {"GOOGLE_CLIENT_ID": " "}, {"DEMO_MODE": "true"}):
        r = check(tmp_path, env)
        for v in DRIVE_VARS.values():
            assert v not in r.stdout + r.stderr


# Parity: start.sh's check and app/config.py must agree on mode and fatal keys.
PARITY_CASES = [
    ({}, ""),
    ({"GOOGLE_CLIENT_SECRET": ""}, ""),
    ({"GOOGLE_CLIENT_SECRET": "  "}, ""),
    ({"GOOGLE_CLIENT_ID": ""}, ""),
    ({"GOOGLE_CLIENT_ID": " "}, ""),
    ({"GOOGLE_CLIENT_ID": " ", "ANTHROPIC_API_KEY": ""}, ""),
    ({"DEMO_MODE": "true"}, ""),
    ({"DEMO_MODE": " "}, "DEMO_MODE=true\n"),
    ({"DEMO_MODE": "TRUE "}, ""),
    ({}, "GOOGLE_CLIENT_ID=' '\n"),
    ({}, "FERNET_KEY=\nSESSION_SECRET=\"  \"\n"),
    ({"FERNET_KEY": "x"}, "FERNET_KEY=\n"),
]
SOFT_DEFAULTS = {"DATABASE_URL", "DATABASE_READONLY_URL", "GOOGLE_REDIRECT_URI"}


@pytest.mark.parametrize("env,extra", PARITY_CASES)
def test_lib_sh_and_settings_agree(tmp_path, clean_env, env, extra):
    from app.config import Settings

    (tmp_path / ".env").write_text(DRIVE_DOTENV + extra)
    for k, v in env.items():
        clean_env.setenv(k, v)
    s = Settings(_env_file=tmp_path / ".env")
    py_missing = [k for k in s.missing_required() if k not in SOFT_DEFAULTS]

    r = check(tmp_path, env)
    sh_demo = "Demo mode" in r.stdout
    assert sh_demo == s.demo_mode, (r.stdout, r.stderr)
    assert (r.returncode == 0) == (not py_missing), (r.stderr, py_missing)
    if py_missing:
        assert f"missing required variables in .env: {' '.join(py_missing)} " in r.stderr
    sh_ask = "fixture extractions will be used" not in r.stdout
    if s.demo_mode:
        assert sh_ask == s.ask_enabled

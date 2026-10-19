"""scripts/init_env.sh and scripts/env_tool.py (FR-0, FR-1, FR-14, §11).

The shell script runs via subprocess in a throwaway git repo under tmp_path with
copies of .env.example, .gitignore and the scripts. Every value is fake. `lsof`,
`docker` and `curl` are stubbed on PATH, so nothing touches the network, the dev
stack or the real .env. stdin is /dev/null, so the script runs non-interactively.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parent.parent
SCRIPT_FILES = ("init_env.sh", "env_tool.py", "gen_keys.py")
REDIRECT = "http://localhost:8000/api/auth/google/callback"

FAKE_KEY = "sk-ant-api03-FAKEKEY/abc+def=&x|y-0123456789"
FAKE_ID = "123456-fakeclient.apps.googleusercontent.com"
FAKE_SECRET = "GOCSPX-fake/se+cr=et&a|b"
FAKE_SECRET_QUOTED = "GOCSPX-fake$HOME #not-a-comment"


def _load_tool():
    spec = importlib.util.spec_from_file_location("env_tool", REPO / "scripts" / "env_tool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


# --------------------------------------------------------------------------- sandbox
class Sandbox:
    def __init__(self, base: Path):
        self.root = base / "repo"
        self.stub = base / "stub"
        self.files = base / "files"
        (self.root / "scripts").mkdir(parents=True)
        self.stub.mkdir()
        self.files.mkdir()
        shutil.copy(REPO / ".env.example", self.root / ".env.example")
        shutil.copy(REPO / ".gitignore", self.root / ".gitignore")
        for name in SCRIPT_FILES:
            shutil.copy2(REPO / "scripts" / name, self.root / "scripts" / name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self._stubs()

    def _stubs(self):
        # lsof: "listening" only for ports in FAKE_BUSY_PORTS (space separated).
        (self.stub / "lsof").write_text(
            '#!/bin/sh\nport=""\nfor a in "$@"; do case "$a" in -iTCP:*) port="${a#-iTCP:}";; esac; done\n'
            'for p in $FAKE_BUSY_PORTS; do [ "$p" = "$port" ] && exit 0; done\nexit 1\n'
        )
        # docker: compose port prints FAKE_COMPOSE_PORT (this project's container) if set.
        (self.stub / "docker").write_text(
            '#!/bin/sh\n[ -n "$FAKE_COMPOSE_PORT" ] && [ "$2" = port ] && { echo "0.0.0.0:$FAKE_COMPOSE_PORT"; exit 0; }\nexit 1\n'
        )
        # curl: records argv and the stdin config, prints FAKE_CURL_CODE.
        (self.stub / "curl").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_CURL_LOG.argv"\ncat > "$FAKE_CURL_LOG.stdin"\n'
            'printf "%s" "${FAKE_CURL_CODE:-200}"\n'
        )
        for p in self.stub.iterdir():
            p.chmod(0o755)

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    def env_values(self) -> dict:
        return dotenv_values(self.env_path)

    def key_file(self, content: str = FAKE_KEY + "\n") -> Path:
        p = self.files / "anthropic key.txt"
        p.write_text(content)
        return p

    def client_json(self, kind="web", redirect_uris=(REDIRECT,), secret=FAKE_SECRET, name="client_secret_x.json") -> Path:
        p = self.files / name
        p.write_text(json.dumps({kind: {
            "client_id": FAKE_ID, "project_id": "demo", "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "redirect_uris": list(redirect_uris),
        }}))
        return p

    def run(self, *args, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items()
             if k not in {"ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DEMO_MODE"}}
        e["PATH"] = f"{self.stub}:{e.get('PATH', '/usr/bin:/bin')}"
        e["FAKE_CURL_LOG"] = str(self.files / "curl")
        e.update(env or {})
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "init_env.sh"), *map(str, args)],
            cwd=str(cwd or self.files), env=e, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60,
        )


@pytest.fixture
def sb(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def output(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


def assert_no_secrets(r: subprocess.CompletedProcess, *secrets: str):
    out = output(r)
    for s in secrets:
        if s:
            assert s not in out, "a secret value appeared in the script output"


def status_of(r: subprocess.CompletedProcess, key: str) -> str:
    summary = r.stdout.split("\nSummary\n", 1)[1]
    m = re.search(rf"^  {key} +(\S.*)$", summary, re.M)
    assert m, f"{key} missing from summary"
    return m.group(1).strip()


def env_keys_in_order(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^([A-Z_]+)=", text, re.M)]


def comment_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("#") or not ln.strip()]


# --------------------------------------------------------------------------- script: creation + writing
def test_script_is_executable():
    assert os.access(REPO / "scripts" / "init_env.sh", os.X_OK)


def test_help_documents_every_option(sb):
    r = sb.run("--help")
    assert r.returncode == 0
    for opt in ("--anthropic-key-file", "--google-client-json", "--demo", "--yes", "--no-validate", "--rotate-keys"):
        assert opt in r.stdout
    flat = " ".join(r.stdout.split())
    assert "configured Postgres port (POSTGRES_PORT, default 5432)" in flat
    assert "if 5432 is used" not in flat
    assert "clears GOOGLE_CLIENT_ID/SECRET" in flat and "backup keeps the old values" in flat
    assert not sb.env_path.exists()


def test_fresh_env_created_from_example_with_comments_and_order(sb):
    r = sb.run("--yes", "--no-validate", "--demo")
    assert r.returncode == 0, output(r)
    example = (sb.root / ".env.example").read_text()
    created = sb.env_path.read_text()
    assert env_keys_in_order(created) == env_keys_in_order(example)
    assert comment_lines(created) == comment_lines(example)
    assert "Created .env from .env.example" in r.stdout
    assert stat.S_IMODE(sb.env_path.stat().st_mode) == 0o600
    assert not list(sb.root.glob(".env.bak.*"))  # nothing to back up
    vals = sb.env_values()
    assert vals["CLAUDE_MODEL"] == "claude-opus-5-5"
    assert vals["GOOGLE_REDIRECT_URI"] == REDIRECT


def test_runs_from_any_directory(sb, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = sb.run("--yes", "--no-validate", "--demo", cwd=elsewhere)
    assert r.returncode == 0, output(r)
    assert sb.env_path.exists() and not (elsewhere / ".env").exists()


def test_values_written_exactly_including_special_characters(sb):
    r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file(),
               "--google-client-json", sb.client_json(secret=FAKE_SECRET_QUOTED))
    assert r.returncode == 0, output(r)
    vals = sb.env_values()
    assert vals["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert vals["GOOGLE_CLIENT_ID"] == FAKE_ID
    assert vals["GOOGLE_CLIENT_SECRET"] == FAKE_SECRET_QUOTED  # `$` and ` #` kept literally
    text = sb.env_path.read_text()
    assert f"ANTHROPIC_API_KEY={FAKE_KEY}\n" in text  # / + = & | stay unquoted
    assert f"GOOGLE_CLIENT_SECRET='{FAKE_SECRET_QUOTED}'\n" in text
    assert status_of(r, "ANTHROPIC_API_KEY") == "set"
    assert status_of(r, "GOOGLE_CLIENT_ID") == "set"
    assert "Mode: Google Drive" in r.stdout and "Test users" in r.stdout and "Drive API" in r.stdout
    assert_no_secrets(r, FAKE_KEY, FAKE_ID, FAKE_SECRET_QUOTED, vals["FERNET_KEY"], vals["SESSION_SECRET"])


def test_written_values_read_back_by_app_settings(sb, monkeypatch):
    from app.config import Settings
    r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file(), "--google-client-json", sb.client_json())
    assert r.returncode == 0, output(r)
    for k in ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FERNET_KEY", "SESSION_SECRET", "DEMO_MODE"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=sb.env_path)
    assert s.anthropic_api_key.get_secret_value() == FAKE_KEY
    assert s.google_client_secret.get_secret_value() == FAKE_SECRET
    assert not s.demo_mode
    from cryptography.fernet import Fernet
    Fernet(s.fernet_key.get_secret_value().encode())


def test_key_file_accepts_assignment_form(sb):
    r = sb.run("--yes", "--no-validate", "--demo", "--anthropic-key-file",
               sb.key_file(f"# my key\nexport ANTHROPIC_API_KEY=\"{FAKE_KEY}\"\n"))
    assert r.returncode == 0, output(r)
    assert sb.env_values()["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert_no_secrets(r, FAKE_KEY)


def test_anthropic_key_from_environment(sb):
    r = sb.run("--yes", "--no-validate", "--demo", env={"ANTHROPIC_API_KEY": FAKE_KEY})
    assert r.returncode == 0, output(r)
    assert sb.env_values()["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert "from the environment" in r.stdout
    assert "also set in your shell environment" in r.stderr  # it would override .env at runtime
    assert "Mode: demo with Claude" in r.stdout
    assert_no_secrets(r, FAKE_KEY)


def test_existing_env_updated_in_place_with_backup(sb):
    original = (
        "# my own header\nANTHROPIC_API_KEY=sk-ant-old-value\nCUSTOM_THING=keep me  # note\n"
        "GOOGLE_CLIENT_ID=\nexport GOOGLE_CLIENT_SECRET=\nFERNET_KEY=\n"
    )
    sb.env_path.write_text(original)
    sb.env_path.chmod(0o644)
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json())
    assert r.returncode == 0, output(r)
    backups = list(sb.root.glob(".env.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == original
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(sb.env_path.stat().st_mode) == 0o600
    text = sb.env_path.read_text()
    assert text.startswith("# my own header\nANTHROPIC_API_KEY=sk-ant-old-value\nCUSTOM_THING=keep me  # note\n")
    assert f"export GOOGLE_CLIENT_SECRET={FAKE_SECRET}\n" in text
    assert "# Added by scripts/init_env.sh\nSESSION_SECRET=" in text  # missing key appended
    assert status_of(r, "ANTHROPIC_API_KEY") == "kept"
    assert status_of(r, "FERNET_KEY") == "generated"
    assert_no_secrets(r, "sk-ant-old-value", FAKE_SECRET, FAKE_ID)
    assert subprocess.run(["git", "-C", str(sb.root), "check-ignore", "-q", backups[0].name]).returncode == 0


def test_rerun_without_changes_leaves_env_untouched(sb):
    assert sb.run("--yes", "--no-validate", "--demo").returncode == 0
    before = sb.env_path.read_text()
    r = sb.run("--yes", "--no-validate")
    assert r.returncode == 0, output(r)
    assert "Nothing to change" in r.stdout
    assert sb.env_path.read_text() == before
    assert not list(sb.root.glob(".env.bak.*"))


# --------------------------------------------------------------------------- FERNET_KEY handling
def _existing_keys(sb):
    sb.run("--yes", "--no-validate", "--demo")
    v = sb.env_values()
    return v["FERNET_KEY"], v["SESSION_SECRET"]


def test_existing_fernet_key_is_kept_by_default(sb):
    fernet, session = _existing_keys(sb)
    r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file())
    assert r.returncode == 0, output(r)
    v = sb.env_values()
    assert (v["FERNET_KEY"], v["SESSION_SECRET"]) == (fernet, session)
    assert status_of(r, "FERNET_KEY") == "kept"
    assert "invalidates the stored Google connection" in r.stdout
    assert_no_secrets(r, fernet, session, FAKE_KEY)


def test_rotate_keys_replaces_both_with_warning(sb):
    fernet, session = _existing_keys(sb)
    r = sb.run("--yes", "--no-validate", "--rotate-keys")
    assert r.returncode == 0, output(r)
    v = sb.env_values()
    assert v["FERNET_KEY"] != fernet and v["SESSION_SECRET"] != session
    assert status_of(r, "FERNET_KEY") == "generated"
    assert "Connect Google Drive" in r.stderr
    assert_no_secrets(r, fernet, session, v["FERNET_KEY"], v["SESSION_SECRET"])
    backup = next(sb.root.glob(".env.bak.*"))
    assert dotenv_values(backup)["FERNET_KEY"] == fernet  # the old key survives in the backup


# --------------------------------------------------------------------------- Google
def test_demo_leaves_google_client_id_empty(sb):
    r = sb.run("--yes", "--no-validate", "--demo")
    assert r.returncode == 0, output(r)
    v = sb.env_values()
    assert v["GOOGLE_CLIENT_ID"] == "" and v["GOOGLE_CLIENT_SECRET"] == ""
    assert status_of(r, "GOOGLE_CLIENT_ID") == "empty (demo)"
    assert status_of(r, "ANTHROPIC_API_KEY") == "empty (fixtures)"
    assert "Mode: demo with fixtures" in r.stdout
    assert "sample_docs" in r.stdout


def test_demo_clears_existing_google_client(sb):
    sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json())
    r = sb.run("--yes", "--no-validate", "--demo")
    assert r.returncode == 0, output(r)
    assert sb.env_values()["GOOGLE_CLIENT_ID"] == ""
    assert dotenv_values(next(sb.root.glob(".env.bak.*")))["GOOGLE_CLIENT_ID"] == FAKE_ID
    assert_no_secrets(r, FAKE_ID, FAKE_SECRET)


def test_yes_without_google_flags_keeps_existing_client(sb):
    sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json())
    r = sb.run("--yes", "--no-validate")
    assert r.returncode == 0, output(r)
    assert sb.env_values()["GOOGLE_CLIENT_ID"] == FAKE_ID
    assert status_of(r, "GOOGLE_CLIENT_ID") == "kept"


@pytest.mark.parametrize("uris", [[REDIRECT + "/"], ["http://127.0.0.1:8000/api/auth/google/callback"], [], ["https://example.com/cb"]])
def test_wrong_redirect_uri_warns_with_fix(sb, uris):
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json(redirect_uris=uris))
    assert r.returncode == 0, output(r)
    assert "redirect_uri_mismatch" in r.stdout
    assert "Authorized redirect URIs" in r.stderr + r.stdout
    assert REDIRECT in r.stdout
    assert sb.env_values()["GOOGLE_CLIENT_ID"] == FAKE_ID  # still written, the warning says what to fix
    assert_no_secrets(r, FAKE_ID, FAKE_SECRET)


def test_correct_redirect_uri_is_confirmed(sb):
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json(redirect_uris=["http://x/cb", REDIRECT]))
    assert r.returncode == 0
    assert "Redirect URI matches" in r.stdout
    assert "redirect_uri_mismatch" not in output(r)


def test_installed_client_warns_web_application_required(sb):
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json(kind="installed"))
    assert r.returncode == 0, output(r)
    assert "Web application" in r.stderr
    assert "Desktop app" in r.stderr
    assert_no_secrets(r, FAKE_ID, FAKE_SECRET)


def test_client_json_path_with_quotes_tilde_and_trailing_space(sb, tmp_path):
    p = sb.client_json(name="client secret (1).json")
    home = tmp_path / "home"
    home.mkdir()
    shutil.copy(p, home / p.name)
    r = sb.run("--yes", "--no-validate", "--google-client-json", f"'~/{p.name}'  ", env={"HOME": str(home)})
    assert r.returncode == 0, output(r)
    assert sb.env_values()["GOOGLE_CLIENT_ID"] == FAKE_ID


def test_client_json_drag_and_drop_escaped_path(sb):
    p = sb.client_json(name="client secret (1).json")
    escaped = str(p).replace(" ", "\\ ").replace("(", "\\(").replace(")", "\\)") + " "
    r = sb.run("--yes", "--no-validate", "--google-client-json", escaped)
    assert r.returncode == 0, output(r)
    assert sb.env_values()["GOOGLE_CLIENT_ID"] == FAKE_ID


@pytest.mark.parametrize("content,msg", [
    ("not json", "not valid JSON"),
    ('{"other": {}}', 'no "web" client'),
    ('{"web": {"client_id": "x.apps.googleusercontent.com"}}', 'no "client_secret"'),
])
def test_unusable_client_json_aborts_without_writing(sb, content, msg):
    p = sb.files / "bad.json"
    p.write_text(content)
    r = sb.run("--yes", "--no-validate", "--google-client-json", p)
    assert r.returncode != 0
    assert msg in r.stderr
    assert not sb.env_path.exists()


def test_missing_client_json_file_aborts(sb):
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.files / "nope.json")
    assert r.returncode != 0 and "file not found" in r.stderr
    assert not sb.env_path.exists()


def test_demo_and_client_json_conflict(sb):
    r = sb.run("--demo", "--google-client-json", sb.client_json())
    assert r.returncode != 0 and "can't be combined" in r.stderr


def test_demo_mode_true_is_cleared_when_google_configured(sb):
    sb.run("--yes", "--no-validate", "--demo")
    text = sb.env_path.read_text().replace("DEMO_MODE=\n", "DEMO_MODE=true\n")
    sb.env_path.write_text(text)
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json())
    assert r.returncode == 0, output(r)
    assert sb.env_values()["DEMO_MODE"] == ""
    assert "Mode: Google Drive" in r.stdout


# --------------------------------------------------------------------------- secrets never as arguments
@pytest.mark.parametrize("args", [
    ["--anthropic-key", FAKE_KEY],
    [f"--anthropic-key={FAKE_KEY}"],
    [FAKE_KEY],
    ["--anthropic-key-file", FAKE_KEY],
    ["--google-client-secret=GOCSPX-zzz"],
])
def test_secret_values_rejected_as_arguments(sb, args):
    r = sb.run(*args)
    assert r.returncode != 0
    assert "never accepted" in r.stderr or "not accepted" in r.stderr
    assert_no_secrets(r, FAKE_KEY, "GOCSPX-zzz")
    assert not sb.env_path.exists()


def test_unknown_option_does_not_echo_its_value(sb):
    r = sb.run("--wat=hunter2")
    assert r.returncode != 0 and "--wat" in r.stderr and "hunter2" not in output(r)


# --------------------------------------------------------------------------- validation (stub curl, no network)
@pytest.mark.parametrize("code,expected", [("200", "valid"), ("401", "rejected (401)"), ("500", "could not check")])
def test_key_validation_uses_stdin_config(sb, code, expected):
    r = sb.run("--yes", "--demo", "--anthropic-key-file", sb.key_file(), env={"FAKE_CURL_CODE": code})
    assert r.returncode == 0, output(r)
    assert expected in output(r)
    argv = (sb.files / "curl.argv").read_text()
    config = (sb.files / "curl.stdin").read_text()
    assert FAKE_KEY not in argv and "--config" in argv
    assert f'header = "x-api-key: {FAKE_KEY}"' in config
    assert "https://api.anthropic.com/v1/models" in config
    assert sb.env_values()["ANTHROPIC_API_KEY"] == FAKE_KEY  # saved even if rejected (non-interactive)
    assert_no_secrets(r, FAKE_KEY)
    if code == "200":
        assert "still needs credits" in r.stdout


def test_no_validate_makes_no_curl_call(sb):
    r = sb.run("--yes", "--no-validate", "--demo", "--anthropic-key-file", sb.key_file())
    assert r.returncode == 0
    assert not (sb.files / "curl.argv").exists()


# --------------------------------------------------------------------------- git safety
def test_aborts_when_env_not_ignored(sb):
    (sb.root / ".gitignore").write_text("__pycache__/\n")
    r = sb.run("--yes", "--no-validate", "--demo")
    assert r.returncode != 0
    assert "would NOT be ignored" in r.stderr
    assert not sb.env_path.exists()


def test_aborts_when_env_tracked(sb):
    sb.env_path.write_text("ANTHROPIC_API_KEY=\n")
    g = ["git", "-C", str(sb.root), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*g, "add", "-f", ".env"], check=True)
    subprocess.run([*g, "commit", "-qm", "x"], check=True)
    r = sb.run("--yes", "--no-validate", "--demo")
    assert r.returncode != 0
    assert "git rm --cached .env" in r.stderr
    assert sb.env_path.read_text() == "ANTHROPIC_API_KEY=\n"
    assert not list(sb.root.glob(".env.bak.*"))


# --------------------------------------------------------------------------- port
def test_busy_foreign_port_switches_port_and_both_urls(sb):
    r = sb.run("--yes", "--no-validate", "--demo", env={"FAKE_BUSY_PORTS": "5432 55432"})
    assert r.returncode == 0, output(r)
    v = sb.env_values()
    assert v["POSTGRES_PORT"] == "55433"
    assert v["DATABASE_URL"] == "postgresql+psycopg://app:app@localhost:55433/docintel"
    assert v["DATABASE_READONLY_URL"] == "postgresql+psycopg://app_readonly:app_readonly@localhost:55433/docintel"
    assert "already used by another program" in r.stderr
    assert status_of(r, "POSTGRES_PORT") == "set"


def test_port_used_by_own_container_is_left_alone(sb):
    r = sb.run("--yes", "--no-validate", "--demo", env={"FAKE_BUSY_PORTS": "5432", "FAKE_COMPOSE_PORT": "5432"})
    assert r.returncode == 0, output(r)
    assert sb.env_values()["POSTGRES_PORT"] == "5432"
    assert "this project's own Postgres" in r.stdout


def test_real_listener_detected_without_lsof(sb):
    """Python fallback when lsof is unavailable: a real socket on a free port."""
    (sb.stub / "lsof").unlink()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        sb.run("--yes", "--no-validate", "--demo")
        text = sb.env_path.read_text().replace("POSTGRES_PORT=5432", f"POSTGRES_PORT={port}")
        sb.env_path.write_text(text)
        hide_lsof = {"PATH": f"{sb.stub}:" + ":".join(
            d for d in os.environ["PATH"].split(":") if not (Path(d) / "lsof").exists())}
        r = sb.run("--yes", "--no-validate", env=hide_lsof)
    assert r.returncode == 0, output(r)
    assert sb.env_values()["POSTGRES_PORT"] != str(port)


# --------------------------------------------------------------------------- env_tool unit tests
def test_parse_env_matches_dotenv_rules():
    text = "A=1\nexport B = two\nC='x # y'\nD=\"q\\\"uote\"\nE=val  # comment\nA=last\n# F=no\n"
    assert tool.parse_env(text) == {"A": "last", "B": "two", "C": "x # y", "D": 'q"uote', "E": "val"}


@pytest.mark.parametrize("value", ["", "abc", "a/b+c=d&e|f", "sk-ant-x_y.z:1@2,3~4%5"])
def test_format_value_unquoted_when_safe(value):
    assert tool.format_value(value) == value


@pytest.mark.parametrize("value", ["has space", "dollar$x", "$HOME", "hash#x", 'dq"x', "semi;colon",
                                   "a{b}c", "$}{", "unicodé ✓ 秘密", " lead", "trail "])
def test_format_value_single_quotes_and_round_trips(tmp_path, value):
    rendered = tool.format_value(value)
    assert rendered == f"'{value}'"
    p = tmp_path / ".env"
    p.write_text(f"K={rendered}\n", encoding="utf-8")
    assert dotenv_values(p)["K"] == value
    assert tool.parse_env(p.read_text(encoding="utf-8"))["K"] == value


@pytest.mark.parametrize("value, reason", [
    ("line\nbreak", "line break"), ("cr\rx", "line break"), ("tab\tx", "control"), ("nul\0x", "control"),
    ("ls\u2028x", "line break"), ("nel\x85x", "control"),
    ("it's got $", "single quote"), ("it's", "single quote"),
    ("back\\slash", "backslash"), ("GOCSPX-a\\\\b", "backslash"), ("lit\\n", "backslash"), ("end\\", "backslash"),
    ("x${HOME}y", "expand"), ("GOCSPX-${unclosed", "expand"),
])
def test_format_value_rejects_unrepresentable(value, reason):
    with pytest.raises(tool.EnvToolError) as exc:
        tool.format_value(value, "GOOGLE_CLIENT_SECRET")
    msg = str(exc.value)
    assert reason in msg and msg.startswith("GOOGLE_CLIENT_SECRET ")
    assert value not in msg


# Fake values only. Each one is either refused cleanly or read back exactly by the
# app's own Settings (pydantic-settings -> python-dotenv, with ${} interpolation).
_ROUND_TRIP_VALUES = [
    "", "plain", "sk-ant-api03-FAKE_x.y/z+w=v&u|t,s~r%q:p@o-0", "GOCSPX-fake",
    "\\", "\\\\", "GOCSPX-a\\b", "GOCSPX-a\\\\b", "lit\\n", "lit\\t", "end\\", "\\'", "a\\\"b",
    '"', 'a"b', '""', "'", "a'b", "''", "'quoted'", '"dq"',
    "$", "$HOME", "a$b", "${HOME}", "x${HOME}y", "${UNSET_FAKE:-d}", "$}", "${", "{}",
    "#", "a#b", "a #b", " #x", "# lead", "trail #",
    " ", "   ", " lead", "trail ", "mid dle", "a  b",
    "unicodé", "秘密-ключ", "emoji-🔑", "zero\u200bwidth", "\u00a0nbsp",
    "line\nbreak", "cr\rx", "tab\tx", "nul\0x", "ls\u2028x", "ps\u2029x", "nel\x85x", "del\x7fx",
    "a=b", "=", "a;b", "a`b`", "$(cmd)", "a!b", "*?[]", "<>", "a,b", "~/x", "%s",
]


_REFUSAL_RE = re.compile(
    r"(ANTHROPIC_API_KEY|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET) contains (a line break or control character, "
    r"which \.env can't hold|a backslash, which \.env readers don't all read the same way|'\$\{', which the app "
    r"would expand as a variable|a single quote plus characters that need quoting)"
)


def _random_values(n: int = 300) -> list[str]:
    import random
    rng = random.Random(1122)
    alphabet = list("aZ09_-./+=:@&|,~%") + list(" #$'\"\\{}`!;*?()[]<>") + ["\n", "\t", "é", "秘", "\u2028"]
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))) for _ in range(n)]


def test_every_accepted_value_round_trips_through_settings(tmp_path, monkeypatch):
    from app.config import Settings
    keys = ("ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
    for k in (*keys, "DEMO_MODE", "FERNET_KEY", "SESSION_SECRET", "UNSET_FAKE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", "/fake-home")  # so a stray ${HOME} expansion would show
    base = (REPO / ".env.example").read_text(encoding="utf-8")
    accepted = rejected = 0
    for i, value in enumerate(_ROUND_TRIP_VALUES + _random_values()):
        try:
            text = tool.update_env_text(base, {k: value for k in keys})
        except tool.EnvToolError as exc:
            rejected += 1
            # The message is a fixed template plus the key name, so it can't carry the value.
            assert _REFUSAL_RE.fullmatch(str(exc)), (repr(value), str(exc))
            continue
        accepted += 1
        p = tmp_path / f"{i}.env"
        p.write_text(text, encoding="utf-8")
        s = Settings(_env_file=p)
        expected = value if value.strip() else None  # blank counts as unset (BUG-Q1-02)
        got_key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
        got_secret = s.google_client_secret.get_secret_value() if s.google_client_secret else None
        assert (got_key, s.google_client_id, got_secret) == (expected, expected, expected), repr(value)
        # env_tool's own reader (used for re-runs) agrees, and the rest of the file is intact.
        parsed = tool.parse_env(text)
        assert all(parsed[k] == value for k in keys), repr(value)
        assert dotenv_values(p, interpolate=False)["GOOGLE_REDIRECT_URI"] == REDIRECT
    assert accepted > 50 and rejected > 50


def test_backslash_secret_from_client_json_is_refused_without_echo(sb):
    secret = "GOCSPX-a\\\\b"  # the value GOCSPX-a\\b (BUG-Q7-02 repro)
    r = sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json(secret=secret))
    assert r.returncode != 0
    assert "GOOGLE_CLIENT_SECRET contains a backslash" in output(r)
    assert "Traceback" not in output(r)
    assert not sb.env_path.exists()
    assert_no_secrets(r, secret)


def test_parse_env_decodes_like_dotenv(tmp_path):
    text = "A='a\\\\b\\'c\\d'\nB=\"x\\\\y\\\"z\\n\\q'\"\nC=plain # c\nD='x # y'\n"
    p = tmp_path / ".env"
    p.write_text(text)
    assert tool.parse_env(text) == dict(dotenv_values(p, interpolate=False))


def test_update_env_text_in_place_and_append():
    text = "# c\nA=1\n\nexport B=2  # keep\nA=dup\nC=3"
    out = tool.update_env_text(text, {"A": "x", "B": "y", "D": "z"})
    assert out == "# c\nA=x\n\nexport B=y  # keep\nA=x\nC=3\n\n# Added by scripts/init_env.sh\nD=z\n"
    assert tool.update_env_text(out, {"E": "w"}).endswith("D=z\nE=w\n")  # header added once


def test_url_port_helpers():
    u = "postgresql+psycopg://app:p@ss@localhost:5432/docintel?sslmode=disable"
    assert tool.url_port(u) == 5432
    assert tool.set_url_port(u, 55432) == "postgresql+psycopg://app:p@ss@localhost:55432/docintel?sslmode=disable"
    assert tool.set_url_port("postgresql://u@db/x", 6000) == "postgresql://u@db:6000/x"
    assert tool.set_url_port("postgresql://u@[::1]:5432/x", 7) == "postgresql://u@[::1]:7/x"
    assert tool.url_is_local(u) and not tool.url_is_local("postgresql://u@db.example.com/x")


def test_resolve_updates_port_uses_base_urls():
    base = {"DATABASE_URL": "postgresql://a:a@localhost:5432/d", "DATABASE_READONLY_URL": ""}
    up = tool.resolve_updates(base, [("port", "-", "6001")])
    assert up == {"POSTGRES_PORT": "6001", "DATABASE_URL": "postgresql://a:a@localhost:6001/d"}


def test_resolve_updates_generate_and_unknown():
    up = tool.resolve_updates({}, [("gen", "FERNET_KEY", ""), ("gen", "SESSION_SECRET", "")])
    from cryptography.fernet import Fernet
    Fernet(up["FERNET_KEY"].encode())
    assert len(up["SESSION_SECRET"]) >= 32
    with pytest.raises(tool.EnvToolError):
        tool.resolve_updates({}, [("gen", "OTHER", "")])
    with pytest.raises(tool.EnvToolError):
        tool.resolve_updates({}, [("nope", "K", "v")])


def test_read_client_json_variants(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"web": {"client_id": FAKE_ID, "client_secret": FAKE_SECRET, "redirect_uris": [REDIRECT]}}))
    info = tool.read_client_json(p)
    assert (info["kind"], info["redirect_status"], info["client_id_looks_ok"]) == ("web", "ok", True)
    p.write_text(json.dumps({"installed": {"client_id": "abc", "client_secret": "s", "redirect_uris": ["http://localhost"]}}))
    info = tool.read_client_json(p)
    assert (info["kind"], info["redirect_status"], info["client_id_looks_ok"]) == ("installed", "mismatch", False)
    p.write_text(json.dumps({"web": {"client_id": "a", "client_secret": "s"}}))
    assert tool.read_client_json(p)["redirect_status"] == "missing"


def test_client_json_cli_prints_no_secrets(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"web": {"client_id": FAKE_ID, "client_secret": FAKE_SECRET, "redirect_uris": [REDIRECT]}}))
    assert tool.main(["client-json", str(p), REDIRECT]) == 0
    out = capsys.readouterr().out
    assert out == "kind web\nclient_id_looks_ok 1\nredirect ok\n"


def test_get_refuses_secret_keys(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\nPOSTGRES_PORT=6000\n")
    assert tool.main(["get", str(p), "ANTHROPIC_API_KEY"]) == 2
    assert tool.main(["get", str(p), "POSTGRES_PORT"]) == 0
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out + out.err and out.out == "6000\n"
    assert tool.main(["status", str(p), "ANTHROPIC_API_KEY", "FERNET_KEY"]) == 0
    assert capsys.readouterr().out == "ANTHROPIC_API_KEY 1\nFERNET_KEY 0\n"


def test_write_env_atomic_and_private(tmp_path):
    base = tmp_path / ".env.example"
    base.write_text("# hi\nK=\n")
    out = tmp_path / ".env"
    tool.write_env(base, out, [("set", "K", "v a")])
    assert out.read_text() == "# hi\nK='v a'\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env.tmp")] == []
    with pytest.raises(tool.EnvToolError):
        tool.write_env(base, out, [("set", "K", "bad\nvalue")])
    assert out.read_text() == "# hi\nK='v a'\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".env.tmp")] == []


def test_parse_ops_round_trip():
    data = b"set\0K\0a\0b\0gen\0FERNET_KEY\0\0"
    with pytest.raises(tool.EnvToolError):
        tool.parse_ops(data)
    assert tool.parse_ops(b"set\0K\0v=1&|\0gen\0FERNET_KEY\0\0") == [("set", "K", "v=1&|"), ("gen", "FERNET_KEY", "")]


def test_port_in_use_detects_listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert tool.port_in_use(port)
    assert not tool.port_in_use(port)


# --------------------------------------------------------------------------- BUG-Q7-03: refuse before any backup/write
BAD_SECRET = "GOCSPX-a\\\\b"  # the value GOCSPX-a\\b: refused (backslash)
BAD_KEYS = {  # fake Anthropic keys that .env can't hold exactly -> expected reason
    "sk-ant-fake-${HOME}": "contains '${'",
    "sk-ant-fake-a\\b": "contains a backslash",
    "sk-ant-fake 'q": "single quote",
    "sk-ant-fake-\tx": "line break or control character",
}


def _snapshot(sb) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sb.root.iterdir() if p.name.startswith(".env") and p.is_file()}


def _assert_refused_cleanly(sb, r, before: dict[str, bytes], *secrets: str):
    assert r.returncode == 1, output(r)
    assert _snapshot(sb) == before  # .env byte-identical (or still absent), no .env.bak.*, no temp file
    assert "Backed up" not in r.stdout
    assert "Saving .env" not in r.stdout  # refused when collected, before the write step
    assert ".env was not changed" in r.stderr
    assert "Traceback" not in output(r)
    assert_no_secrets(r, *secrets)


@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "existing"])
def test_refused_client_json_value_makes_no_backup_and_no_change(sb, existing):
    if existing:
        assert sb.run("--yes", "--no-validate", "--demo").returncode == 0
    before = _snapshot(sb)
    assert (".env" in before) == existing
    r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file(),
               "--google-client-json", sb.client_json(secret=BAD_SECRET))
    _assert_refused_cleanly(sb, r, before, BAD_SECRET, "GOCSPX-a", FAKE_KEY)
    assert "GOOGLE_CLIENT_SECRET contains a backslash" in r.stderr


@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "existing"])
@pytest.mark.parametrize("source", ["env", "file"])
@pytest.mark.parametrize("bad", list(BAD_KEYS), ids=["dollar-brace", "backslash", "quote", "tab"])
def test_refused_anthropic_key_makes_no_backup_and_no_change(sb, existing, source, bad):
    if existing:
        assert sb.run("--yes", "--no-validate", "--google-client-json", sb.client_json()).returncode == 0
    before = _snapshot(sb)
    if source == "env":
        r = sb.run("--yes", "--no-validate", env={"ANTHROPIC_API_KEY": bad})
    else:
        r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file(bad + "\n"))
    _assert_refused_cleanly(sb, r, before, bad, "sk-ant-fake", FAKE_SECRET)
    assert "ANTHROPIC_API_KEY contains" in r.stderr and BAD_KEYS[bad] in r.stderr


def test_accepted_change_still_backs_up_once(sb):
    assert sb.run("--yes", "--no-validate", "--demo").returncode == 0
    before = sb.env_path.read_bytes()
    r = sb.run("--yes", "--no-validate", "--anthropic-key-file", sb.key_file())
    assert r.returncode == 0, output(r)
    backups = list(sb.root.glob(".env.bak.*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    out = r.stdout
    assert out.index("Backed up the old .env") < out.index("Updated .env in place")


def test_check_cli_refuses_without_echo(capsys, monkeypatch):
    import io

    class _In:
        def __init__(self, data: bytes):
            self.buffer = io.BytesIO(data)

    monkeypatch.setattr(tool.sys, "stdin", _In(b"sk-ant-fake-${X}"))
    assert tool.main(["check", "ANTHROPIC_API_KEY"]) == 1
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY contains '${'" in err and "sk-ant-fake" not in err
    monkeypatch.setattr(tool.sys, "stdin", _In(FAKE_SECRET_QUOTED.encode()))
    assert tool.main(["check", "GOOGLE_CLIENT_SECRET"]) == 0
    monkeypatch.setattr(tool.sys, "stdin", _In(b"sk-ant-\xff"))  # not UTF-8: refused, no traceback
    assert tool.main(["check", "ANTHROPIC_API_KEY"]) == 1
    capsys.readouterr()


def test_client_json_cli_refuses_unwritable_values(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"web": {"client_id": FAKE_ID, "client_secret": BAD_SECRET, "redirect_uris": [REDIRECT]}}))
    assert tool.main(["client-json", str(p), REDIRECT]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "GOOGLE_CLIENT_SECRET contains a backslash" in out.err and "GOCSPX-a" not in out.err
    p.write_text(json.dumps({"web": {"client_id": "id 'x", "client_secret": FAKE_SECRET}}))
    assert tool.main(["client-json", str(p), REDIRECT]) == 1
    assert "GOOGLE_CLIENT_ID contains a single quote" in capsys.readouterr().err


def test_write_env_backstop_refuses_before_backup(tmp_path):
    out = tmp_path / ".env"
    out.write_text("# mine\nK=old\n")
    original = out.read_bytes()
    backup = tmp_path / ".env.bak.1"
    with pytest.raises(tool.EnvToolError):
        tool.write_env(out, out, [("set", "A", "ok"), ("set", "K", "bad\\value")], backup)
    assert out.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # no backup, no temp file


def test_write_env_backup_only_when_content_changes(tmp_path):
    out = tmp_path / ".env"
    out.write_text("K=v\n")
    out.chmod(0o644)
    backup = tmp_path / ".env.bak.1"
    assert tool.write_env(out, out, [("set", "K", "v")], backup) == "unchanged"
    assert not backup.exists() and stat.S_IMODE(out.stat().st_mode) == 0o644  # not rewritten
    assert tool.write_env(out, out, [("set", "K", "w")], backup) == "backup"
    assert backup.read_text() == "K=v\n" and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert out.read_text() == "K=w\n"
    with pytest.raises(FileExistsError):  # an existing backup is never overwritten...
        tool.write_env(out, out, [("set", "K", "x")], backup)
    assert out.read_text() == "K=w\n" and backup.read_text() == "K=v\n"  # ...and .env is left alone
    fresh = tmp_path / "new.env"
    assert tool.write_env(out, fresh, [("set", "K", "y")], tmp_path / ".env.bak.2") == "written"
    assert not (tmp_path / ".env.bak.2").exists() and fresh.read_text() == "K=y\n"


def test_write_cli_prints_result(tmp_path, monkeypatch, capsys):
    import io

    class _In:
        def __init__(self, data: bytes):
            self.buffer = io.BytesIO(data)

    out = tmp_path / ".env"
    out.write_text("K=v\n")
    bak = tmp_path / ".env.bak.x"
    monkeypatch.setattr(tool.sys, "stdin", _In(b"set\0K\0v\0"))
    assert tool.main(["write", str(out), str(out), str(bak)]) == 0
    assert capsys.readouterr().out == "unchanged\n"
    monkeypatch.setattr(tool.sys, "stdin", _In(b"set\0K\0w\0"))
    assert tool.main(["write", str(out), str(out), str(bak)]) == 0
    assert capsys.readouterr().out == "backup\n" and bak.read_text() == "K=v\n"
    monkeypatch.setattr(tool.sys, "stdin", _In(b"set\0K\0a\\b\0"))
    assert tool.main(["write", str(out), str(out), str(tmp_path / ".env.bak.y")]) == 1
    assert not (tmp_path / ".env.bak.y").exists() and out.read_text() == "K=w\n"
    capsys.readouterr()


# --------------------------------------------------------------------------- interactive re-prompts (pty)
def pty_run(sb, args, steps, env=None, timeout=30):
    """Run the script on a pty. steps: [(regex, reply)]: wait for regex, then type reply.
    Returns (exit code, transcript, index of the first step that never matched or None)."""
    import pty
    import select
    import time

    e = {k: v for k, v in os.environ.items()
         if k not in {"ANTHROPIC_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "DEMO_MODE"}}
    e["PATH"] = f"{sb.stub}:{e.get('PATH', '/usr/bin:/bin')}"
    e["FAKE_CURL_LOG"] = str(sb.files / "curl")
    e["TERM"] = "dumb"
    e.update(env or {})
    argv = ["bash", str(sb.root / "scripts" / "init_env.sh"), *map(str, args)]
    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            os.chdir(str(sb.files))
            os.execvpe("bash", argv, e)
        finally:
            os._exit(127)
    buf, pos, stuck = b"", 0, None
    deadline = time.time() + timeout

    def pump(t=0.2) -> bool:
        nonlocal buf
        ready, _, _ = select.select([fd], [], [], t)
        if not ready:
            return True
        try:
            data = os.read(fd, 4096)
        except OSError:
            return False
        buf += data
        return bool(data)

    alive = True
    for n, (pat, reply) in enumerate(steps):
        rx = re.compile(pat.encode())
        while True:
            m = rx.search(buf, pos)
            if m:
                pos = m.end()
                time.sleep(0.05)
                os.write(fd, reply.encode())
                break
            if time.time() > deadline or not alive:
                stuck = n
                break
            alive = pump()
        if stuck is not None:
            break
    while alive and time.time() < deadline:
        alive = pump()
    if alive:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), buf.decode("utf-8", "replace"), stuck


KEY_PROMPT = r"Paste your Anthropic API key"
CHOOSE = r"Choose \[\d\]: "
PORT_FREE = r"Port \d+ is free"


def test_interactive_refused_key_is_reprompted_and_earlier_answers_kept(sb):
    bad = "sk-ant-fake-${HOME}-x"
    rc, t, stuck = pty_run(sb, ["--no-validate"], [
        (KEY_PROMPT, bad + "\r"),
        (r"Please paste the key again", ""),
        (KEY_PROMPT, FAKE_KEY + "\r"),
        (CHOOSE, "3\r"),
    ])
    assert stuck is None and rc == 0, t
    assert "ANTHROPIC_API_KEY contains '${'" in t
    assert t.count("Paste your Anthropic API key") == 2
    assert bad not in t and "sk-ant-fake" not in t and FAKE_KEY not in t
    v = sb.env_values()
    assert v["ANTHROPIC_API_KEY"] == FAKE_KEY and v["GOOGLE_CLIENT_ID"] == ""
    assert not list(sb.root.glob(".env.bak.*"))


def test_interactive_refused_manual_secret_is_reprompted_on_existing_env(sb):
    assert sb.run("--yes", "--no-validate", "--demo", "--anthropic-key-file", sb.key_file()).returncode == 0
    before = sb.env_path.read_text()
    rc, t, stuck = pty_run(sb, ["--no-validate"], [
        (KEY_PROMPT, "\r"),                       # keep the current key
        (CHOOSE, "2\r"),
        (r"Client ID \(", "bad'id x\r"),           # refused, asked again
        (r"GOOGLE_CLIENT_ID contains a single quote", ""),
        (r"Client ID \(", FAKE_ID + "\r"),
        (r"Client secret \(input hidden\): ", BAD_SECRET + "\r"),  # refused, asked again
        (r"GOOGLE_CLIENT_SECRET contains a backslash", ""),
        (r"Client secret \(input hidden\): ", FAKE_SECRET + "\r"),
    ])
    assert stuck is None and rc == 0, t
    assert t.count("Client secret (input hidden)") == 2
    assert_no_secrets(subprocess.CompletedProcess([], 0, t, ""), BAD_SECRET, "GOCSPX-a", FAKE_SECRET, FAKE_KEY)
    v = sb.env_values()
    assert (v["ANTHROPIC_API_KEY"], v["GOOGLE_CLIENT_ID"], v["GOOGLE_CLIENT_SECRET"]) == (FAKE_KEY, FAKE_ID, FAKE_SECRET)
    backups = list(sb.root.glob(".env.bak.*"))
    assert len(backups) == 1 and backups[0].read_text() == before


def test_interactive_refused_client_json_returns_to_menu(sb):
    bad_json = sb.client_json(secret=BAD_SECRET, name="bad.json")
    good_json = sb.client_json(name="good.json")
    rc, t, stuck = pty_run(sb, ["--no-validate", "--google-client-json", bad_json], [
        (KEY_PROMPT, FAKE_KEY + "\r"),
        (r"GOOGLE_CLIENT_SECRET contains a backslash", ""),
        (CHOOSE, "1\r"),
        (r"Path to the client JSON", str(bad_json) + "\r"),  # refused again from the menu
        (r"GOOGLE_CLIENT_SECRET contains a backslash", ""),
        (CHOOSE, "1\r"),
        (r"Path to the client JSON", str(good_json) + "\r"),
        (PORT_FREE, ""),
    ])
    assert stuck is None and rc == 0, t
    assert "GOCSPX-a" not in t and FAKE_KEY not in t
    v = sb.env_values()
    assert (v["ANTHROPIC_API_KEY"], v["GOOGLE_CLIENT_SECRET"]) == (FAKE_KEY, FAKE_SECRET)  # step 1 answer kept


def test_interactive_cancel_after_refusal_leaves_no_backup(sb):
    assert sb.run("--yes", "--no-validate", "--demo").returncode == 0
    before = _snapshot(sb)
    rc, t, stuck = pty_run(sb, ["--no-validate"], [
        (KEY_PROMPT, "sk-ant-fake-a\\b\r"),
        (r"Please paste the key again", ""),
        (KEY_PROMPT, "\x03"),
    ])
    assert stuck is None and rc == 130, t
    assert "Cancelled. .env was not changed." in t
    assert _snapshot(sb) == before

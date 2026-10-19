#!/usr/bin/env python3
"""Helper for scripts/init_env.sh (PRD FR-0, FR-1, §11). Standard library only.

Secrets rule (FR-0): nothing here prints a secret value. Secret values reach the
writer on stdin (never argv), and only allow-listed, non-secret keys can be read
back with `get`.

Subcommands:
  status FILE KEY...            "KEY 1" if set (non-blank), else "KEY 0"
  get FILE KEY                  value of a NON-secret key (allow-listed)
  client-json PATH REDIRECT     check a Google OAuth client JSON; prints only
                                non-secret findings ("kind web", "redirect ok", ...)
  check KEY                     exit 0 if the value on stdin can be written to .env
                                exactly; else exit 1 with a reason (never the value)
  port-in-use PORT              exit 0 if something accepts TCP connections on PORT
  write BASE OUT [BACKUP]       apply ops read from stdin, write OUT atomically (0600).
                                Every value is checked before anything touches disk.
                                If OUT exists and its content would change, it is first
                                copied to BACKUP (new file, 0600). Prints "unchanged"
                                (nothing written), or "backup" when BACKUP was made.

Ops for `write` are NUL-separated triples `op\\0key\\0value\\0`:
  set KEY VALUE        replace KEY's assignment in place (append if missing)
  gen KEY              generate FERNET_KEY / SESSION_SECRET (scripts/gen_keys.py)
  clientjson - PATH    set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from the JSON
  port - PORT          set POSTGRES_PORT and the port in both DATABASE URLs
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_REDIRECT_URI = "http://localhost:8000/api/auth/google/callback"
NON_SECRET_KEYS = frozenset(
    {"GOOGLE_REDIRECT_URI", "POSTGRES_PORT", "DATABASE_URL", "DATABASE_READONLY_URL", "DEMO_MODE"}
)
DB_URL_KEYS = ("DATABASE_URL", "DATABASE_READONLY_URL")
APPEND_HEADER = "# Added by scripts/init_env.sh"

_ASSIGN_RE = re.compile(r"^(\s*)(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
# Encoding rule (BUG-Q7-02). Readers of .env: the app (pydantic-settings, which
# calls python-dotenv's dotenv_values with interpolate=True), docker compose and
# scripts/lib.sh. python-dotenv (1.2.x) does this:
#   * unquoted: taken as is up to an ` #` comment, trailing spaces dropped;
#   * '...': `\\` -> `\` and `\'` -> `'` are still decoded, everything else literal;
#     a trailing `\` swallows the closing quote and the line fails to parse;
#   * "...": `\\ \' \" \a \b \f \n \r \t \v` are decoded;
#   * `${NAME}` / `${NAME:-x}` is expanded in EVERY value, quoted or not, and there
#     is no escape for it. docker compose reads '...' literally.
# So: values matching _SAFE_VALUE_RE are written unquoted; any other value is
# single-quoted, which python-dotenv and docker compose decode identically as long
# as it has no `\`, no `'` and no `${`. Values containing `\`, `'` or `${` (outside
# the unquoted set), or a control/line-separator character, can't be written so that
# both get them back exactly, and are refused (see format_value).
#
# scripts/lib.sh is NOT an exact reader, and doesn't need to be (BUG-Q7-04). When
# start.sh, stop.sh or scripts/test.sh source it, _env_file_raw takes the last
# `KEY=` line of .env (when KEY isn't set in the environment), strips everything
# from the first whitespace + `#` onward, and only THEN removes one pair of
# surrounding quotes. So a quoted value containing ` #` (e.g. 'GOCSPX-x #c') reads
# as `'GOCSPX-x` there. That is harmless because of what lib.sh reads:
#   * POSTGRES_PORT (sourcing lib.sh) and DEMO_MODE (start.sh's check_env_vars) are
#     the only values it uses. This tool writes them unquoted (digits, or empty).
#   * ANTHROPIC_API_KEY, GOOGLE_CLIENT_ID/SECRET, GOOGLE_REDIRECT_URI, FERNET_KEY,
#     SESSION_SECRET and DATABASE_(READONLY_)URL are only checked for being set
#     (non-blank) by check_env_vars. A quoted value always starts with `'`, so it is
#     never blank to lib.sh, and set/unset comes out the same as for the app.
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./+=:@&|,~%-]*$")
_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")


class EnvToolError(Exception):
    """A user-facing error. Its message never contains a secret value."""


# --------------------------------------------------------------------------- parsing
_SQ_ESCAPES_RE = re.compile(r"\\([\\'])")
_DQ_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f",
               "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_DQ_ESCAPES_RE = re.compile(r"\\([\\'\"abfnrtv])")


def parse_value(raw: str) -> str:
    """Value part of `KEY=raw`, read like python-dotenv does (without `${}` expansion)."""
    v = raw.strip()
    if v[:1] == "'":
        m = re.match(r"'((?:\\.|[^'\\])*)'", v)
        if m:
            return _SQ_ESCAPES_RE.sub(r"\1", m.group(1))
    elif v[:1] == '"':
        m = re.match(r'"((?:\\.|[^"\\])*)"', v)
        if m:
            return _DQ_ESCAPES_RE.sub(lambda e: _DQ_ESCAPES[e.group(1)], m.group(1))
    return _TRAILING_COMMENT_RE.sub("", v).strip()


def parse_env(text: str) -> dict[str, str]:
    """KEY -> value; the last assignment wins (same as lib.sh and python-dotenv)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _ASSIGN_RE.match(line)
        if m:
            out[m.group(3)] = parse_value(m.group(4))
    return out


def is_set(values: dict[str, str], key: str) -> bool:
    return bool(values.get(key, "").strip())


def _unsafe_char(c: str) -> bool:
    """Control characters (\\n, \\r, \\t, NUL, ...), surrogates, and the Unicode line
    and paragraph separators, which str.splitlines() would treat as line breaks."""
    return unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp")


def format_value(value: str, key: str = "") -> str:
    """Render a value for .env so the app's Settings (python-dotenv) and docker compose
    both read back exactly `value` (scripts/lib.sh only checks set/unset for quoted
    values; see the encoding comment above). Raises EnvToolError for values that
    can't be written that way; the message names the key, never the value."""
    what = key or "a value"
    if any(_unsafe_char(c) for c in value):
        raise EnvToolError(f"{what} contains a line break or control character, which .env can't hold")
    if _SAFE_VALUE_RE.match(value):
        return value
    if "\\" in value:
        raise EnvToolError(f"{what} contains a backslash, which .env readers don't all read the same way")
    if "${" in value:
        raise EnvToolError(f"{what} contains '${{', which the app would expand as a variable")
    if "'" in value:
        raise EnvToolError(f"{what} contains a single quote plus characters that need quoting")
    return f"'{value}'"


def check_value(key: str, value: str) -> None:
    """Raise EnvToolError if `value` can't be written for `key` (see format_value)."""
    format_value(value, key)


def _trailing_comment(raw: str) -> str:
    v = raw.strip()
    if v[:1] in ("'", '"'):
        return ""
    m = _TRAILING_COMMENT_RE.search(v)
    return m.group(0) if m else ""


def update_env_text(text: str, updates: dict[str, str]) -> str:
    """Replace every `KEY=` line for KEY in updates, in place; append missing keys.

    Comments, blank lines, order, `export ` prefixes and unquoted trailing
    `# comments` are preserved.
    """
    rendered = {k: format_value(v, k) for k, v in updates.items()}
    seen: set[str] = set()
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        m = _ASSIGN_RE.match(body)
        if m and m.group(3) in rendered:
            indent, export, key, raw = m.group(1), m.group(2) or "", m.group(3), m.group(4)
            out.append(f"{indent}{export}{key}={rendered[key]}{_trailing_comment(raw)}{ending}")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        result = "".join(out)
        if result and not result.endswith("\n"):
            result += "\n"
        if APPEND_HEADER not in result:
            result += ("\n" if result else "") + APPEND_HEADER + "\n"
        result += "".join(f"{k}={rendered[k]}\n" for k in missing)
        return result
    return "".join(out)


# --------------------------------------------------------------------------- URLs / ports
def _split_hostport(hostport: str) -> tuple[str, str]:
    if hostport.startswith("["):
        end = hostport.index("]") + 1
        return hostport[:end], hostport[end + 1:] if hostport[end:end + 1] == ":" else ""
    host, _, port = hostport.partition(":")
    return host, port


def url_port(url: str) -> int | None:
    netloc = urlsplit(url).netloc
    _, port = _split_hostport(netloc.rpartition("@")[2])
    return int(port) if port.isdigit() else None


def url_is_local(url: str) -> bool:
    netloc = urlsplit(url).netloc
    host, _ = _split_hostport(netloc.rpartition("@")[2])
    return host.strip("[]") in {"localhost", "127.0.0.1", "::1", ""}


def set_url_port(url: str, port: int) -> str:
    parts = urlsplit(url)
    userinfo, at, hostport = parts.netloc.rpartition("@")
    host, _ = _split_hostport(hostport)
    return urlunsplit(parts._replace(netloc=f"{userinfo}{at}{host}:{port}"))


def port_in_use(port: int, timeout: float = 0.3) -> bool:
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((addr, port)) == 0:
                    return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- Google client JSON
def read_client_json(path: str | os.PathLike, expected_redirect: str = DEFAULT_REDIRECT_URI) -> dict:
    """Parse a downloaded OAuth client JSON. Raises EnvToolError (no values in messages).

    Returns {kind, client_id, client_secret, redirect_status, redirect_hint}, where
    redirect_status is "ok", "missing" (no redirect_uris at all) or "mismatch".
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise EnvToolError(f"file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise EnvToolError("the file is not UTF-8 text; is it the downloaded client JSON?") from exc
    except json.JSONDecodeError as exc:
        raise EnvToolError(f"the file is not valid JSON (line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise EnvToolError('the JSON has no "web" client in it')
    if isinstance(data.get("web"), dict):
        kind, client = "web", data["web"]
    elif isinstance(data.get("installed"), dict):
        kind, client = "installed", data["installed"]
    else:
        raise EnvToolError('the JSON has no "web" client in it; download it from Google Auth Platform → Clients')
    client_id = str(client.get("client_id") or "").strip()
    client_secret = str(client.get("client_secret") or "").strip()
    if not client_id:
        raise EnvToolError('the JSON has no "client_id"')
    if not client_secret:
        raise EnvToolError('the JSON has no "client_secret"')
    uris = client.get("redirect_uris") or []
    if not isinstance(uris, list):
        uris = []
    uris = [str(u) for u in uris]
    hint = ""
    if expected_redirect in uris:
        status = "ok"
    elif not uris:
        status = "missing"
    else:
        status = "mismatch"
        norm = expected_redirect.rstrip("/").replace("://localhost", "://127.0.0.1")
        for u in uris:
            if u != expected_redirect and u.rstrip("/").replace("://localhost", "://127.0.0.1") == norm:
                hint = "close, but not exact: check for a trailing slash, or 127.0.0.1 instead of localhost"
                break
    return {
        "kind": kind,
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_looks_ok": client_id.endswith(".apps.googleusercontent.com"),
        "redirect_status": status,
        "redirect_hint": hint,
    }


# --------------------------------------------------------------------------- key generation
def _gen_keys_module():
    path = Path(__file__).resolve().parent / "gen_keys.py"
    spec = importlib.util.spec_from_file_location("gen_keys", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def generate(key: str) -> str:
    mod = _gen_keys_module()
    if key == "FERNET_KEY":
        return mod.generate_fernet_key()
    if key == "SESSION_SECRET":
        return mod.generate_session_secret()
    raise EnvToolError(f"don't know how to generate {key}")


# --------------------------------------------------------------------------- write
def parse_ops(data: bytes) -> list[tuple[str, str, str]]:
    parts = data.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 3:
        raise EnvToolError("malformed op stream")
    fields = [p.decode("utf-8") for p in parts]
    return [tuple(fields[i:i + 3]) for i in range(0, len(fields), 3)]  # type: ignore[misc]


def resolve_updates(base_values: dict[str, str], ops: list[tuple[str, str, str]]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for op, key, value in ops:
        if op == "set":
            updates[key] = value
        elif op == "gen":
            updates[key] = generate(key)
        elif op == "clientjson":
            info = read_client_json(value)
            updates["GOOGLE_CLIENT_ID"] = info["client_id"]
            updates["GOOGLE_CLIENT_SECRET"] = info["client_secret"]
        elif op == "port":
            port = int(value)
            updates["POSTGRES_PORT"] = str(port)
            for k in DB_URL_KEYS:
                current = updates.get(k, base_values.get(k, ""))
                if current.strip():
                    updates[k] = set_url_port(current, port)
        else:
            raise EnvToolError(f"unknown op {op!r}")
    return updates


def _make_backup(src: bytes, backup: Path) -> None:
    """Create BACKUP (must not exist yet) holding `src`, mode 0600."""
    fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(src)
    except BaseException:
        try:
            os.unlink(backup)
        except FileNotFoundError:
            pass
        raise


def write_env(base: Path, out: Path, ops: list[tuple[str, str, str]], backup: Path | None = None) -> str:
    """Apply ops to BASE's text and write OUT atomically with mode 0600.

    All values are validated (format_value) before anything is written, so a refused
    value leaves OUT untouched and creates no backup. Returns "unchanged" if OUT
    already holds exactly the new text (nothing is written), "backup" if OUT existed
    and was first copied to BACKUP, otherwise "written".
    """
    text = base.read_text(encoding="utf-8") if base.exists() else ""
    updates = resolve_updates(parse_env(text), ops)
    new_text = update_env_text(text, updates)  # raises EnvToolError on a refused value
    old = out.read_bytes() if out.exists() else None
    if old is not None and old == new_text.encode("utf-8"):
        return "unchanged"
    made_backup = False
    if old is not None and backup is not None:
        _make_backup(old, backup)
        made_backup = True
    try:
        _atomic_write(out, new_text)
    except BaseException:
        if made_backup:
            try:
                os.unlink(backup)
            except FileNotFoundError:
                pass
        raise
    return "backup" if made_backup else "written"


def _atomic_write(out: Path, new_text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".env.tmp.", dir=str(out.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.chmod(out, 0o600)


# --------------------------------------------------------------------------- CLI
def _read_values(path: str) -> dict[str, str]:
    p = Path(path)
    return parse_env(p.read_text(encoding="utf-8")) if p.exists() else {}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "status":
            values = _read_values(args[0])
            for key in args[1:]:
                print(f"{key} {1 if is_set(values, key) else 0}")
        elif cmd == "get":
            key = args[1]
            if key not in NON_SECRET_KEYS:
                print(f"refusing to read {key}: not an allow-listed non-secret key", file=sys.stderr)
                return 2
            print(_read_values(args[0]).get(key, ""))
        elif cmd == "client-json":
            info = read_client_json(args[0], args[1] if len(args) > 1 else DEFAULT_REDIRECT_URI)
            # Refuse here, when the file is chosen, rather than at write time (BUG-Q7-03).
            check_value("GOOGLE_CLIENT_ID", info["client_id"])
            check_value("GOOGLE_CLIENT_SECRET", info["client_secret"])
            print(f"kind {info['kind']}")
            print(f"client_id_looks_ok {1 if info['client_id_looks_ok'] else 0}")
            print(f"redirect {info['redirect_status']}")
            if info["redirect_hint"]:
                print(f"redirect_hint {info['redirect_hint']}")
        elif cmd == "check":
            check_value(args[0], sys.stdin.buffer.read().decode("utf-8", "surrogateescape"))
        elif cmd == "port-in-use":
            return 0 if port_in_use(int(args[0])) else 1
        elif cmd == "write":
            ops = parse_ops(sys.stdin.buffer.read())
            result = write_env(Path(args[0]), Path(args[1]), ops, Path(args[2]) if len(args) > 2 else None)
            if result != "written":
                print(result)
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2
    except EnvToolError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

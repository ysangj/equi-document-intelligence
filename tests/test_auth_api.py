"""FR-2 Google OAuth endpoints: login, callback, status, disconnect (T7).

Google is never contacted: the login URL is built locally by the real Flow, and the
code exchange goes through a stubbed `OAuthClient.exchange` (plus one test of the real
`exchange` with `Flow.fetch_token` patched).
"""

from __future__ import annotations

import base64
import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from app.api.auth import oauth_client_dep
from app.google import oauth
from app.google.credentials import decrypt_token, encrypt_token
from app.google.oauth import (
    DRIVE_READONLY_SCOPE,
    EMAIL_SCOPE,
    SCOPES,
    STATE_COOKIE,
    OAuthClient,
    OAuthExchangeError,
    TokenResult,
    dump_state_cookie,
    email_from_id_token,
    load_state_cookie,
)
from app.logging_setup import JsonFormatter
from app.main import app
from app.schemas.auth import AuthStatusResponse
from tests.api_helpers import api_client, assert_error, demo_settings, exec_sql, q1, rows, truncate, validate

UI = "http://localhost:4200"
REFRESH = "1//fake-refresh-token-DO-NOT-LOG-abcdef123456"
ACCESS = "ya29.fake-access-token-DO-NOT-LOG"
CODE = "4/0fake-auth-code-DO-NOT-LOG"
CLIENT_ID = "1234-test.apps.googleusercontent.com"


def t7_settings(**kw):
    from app.config import Settings

    base = dict(google_client_id=CLIENT_ID, google_client_secret="test-client-secret-xyz",
                fernet_key=Fernet.generate_key().decode(), session_secret="s" * 48,
                anthropic_api_key="sk-ant-test-key-123456")
    base.update(kw)
    return Settings(_env_file=None, **base)


def id_token(email: str | None) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    claims = {"iss": "https://accounts.google.com", "sub": "123"}
    if email:
        claims["email"] = email
    return f"{b64({'alg': 'RS256'})}.{b64(claims)}.c2ln"


class StubClient(OAuthClient):
    """Real URL building; the exchange returns `result` (or raises it)."""

    def __init__(self, settings, result=None):
        super().__init__(settings)
        self.result = result
        self.exchanged: list[tuple[str, str | None]] = []

    def exchange(self, code, code_verifier):
        self.exchanged.append((code, code_verifier))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def db(test_engine):
    truncate(test_engine)
    yield test_engine
    truncate(test_engine)


@pytest.fixture
def stub():
    holder = {}

    def install(settings, result=None):
        client = StubClient(settings, result)
        app.dependency_overrides[oauth_client_dep] = lambda: client
        holder["c"] = client
        return client

    yield install
    app.dependency_overrides.pop(oauth_client_dep, None)


def login(c):
    r = c.get("/api/auth/google/login", follow_redirects=False)
    assert r.status_code == 302
    return r


def callback(c, **params):
    return c.get("/api/auth/google/callback", params=params, follow_redirects=False)


def ok_result(email="sj.demo@gmail.com", scopes=SCOPES, refresh=REFRESH):
    return TokenResult(refresh_token=refresh, scopes=list(scopes), email=email)


# ---- login ---------------------------------------------------------------------------------


def test_login_redirects_to_google_with_exact_params(db, stub):
    s = t7_settings()
    stub(s)
    with api_client(s) as c:
        r = login(c)
    url = urlparse(r.headers["location"])
    assert (url.scheme, url.netloc, url.path) == ("https", "accounts.google.com", "/o/oauth2/auth")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == s.google_redirect_uri == "http://localhost:8000/api/auth/google/callback"
    assert q["response_type"] == "code"
    assert set(q["scope"].split()) == {"openid", EMAIL_SCOPE, DRIVE_READONLY_SCOPE}
    assert [x for x in q["scope"].split() if "drive" in x] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert "include_granted_scopes" not in q
    # state is in the signed cookie, with the PKCE verifier
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"{STATE_COOKIE}=")
    low = set_cookie.lower()
    for attr in ("httponly", "samesite=lax", "path=/api/auth", "max-age=600"):
        assert attr in low
    cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1].strip('"')
    state, verifier = load_state_cookie(s, cookie_value)
    assert state == q["state"] and len(state) >= 32
    assert verifier and 43 <= len(verifier) <= 128
    assert r.headers["cache-control"] == "no-store"


def test_login_each_time_new_state(db, stub):
    s = t7_settings()
    stub(s)
    with api_client(s) as c:
        a = parse_qs(urlparse(login(c).headers["location"]).query)["state"][0]
        b = parse_qs(urlparse(login(c).headers["location"]).query)["state"][0]
    assert a != b


def test_login_in_demo_mode_redirects_to_connect_error(db):
    with api_client(demo_settings()) as c:
        r = login(c)
    assert r.headers["location"] == f"{UI}/connect?error=demo_mode"


def test_login_rejects_unknown_params(db, stub):
    s = t7_settings()
    stub(s)
    with api_client(s) as c:
        assert_error(c.get("/api/auth/google/login?x=1"), 422, "validation_error")


# ---- callback ---------------------------------------------------------------------------------


def test_callback_success_stores_encrypted_token(db, stub):
    s = t7_settings()
    client = stub(s, ok_result())
    with api_client(s) as c:
        r0 = login(c)
        state = parse_qs(urlparse(r0.headers["location"]).query)["state"][0]
        verifier = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[1]
        r = callback(c, state=state, code=CODE, scope=" ".join(SCOPES))
    assert r.status_code == 302
    assert r.headers["location"] == f"{UI}/connect/folder"
    assert client.exchanged == [(CODE, verifier)]
    # cookie cleared
    assert f'{STATE_COOKIE}=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()
    (row,) = rows(db, "SELECT * FROM oauth_credentials")
    assert row["refresh_token_encrypted"] != REFRESH
    assert REFRESH not in row["refresh_token_encrypted"]
    assert decrypt_token(s, row["refresh_token_encrypted"]) == REFRESH
    assert row["google_email"] == "sj.demo@gmail.com"
    assert set(row["scopes"]) == set(SCOPES)
    # the ciphertext is not decryptable with another key
    with pytest.raises(Exception):
        decrypt_token(t7_settings(), row["refresh_token_encrypted"])


@pytest.mark.parametrize("variant", ["no_cookie", "wrong_state", "tampered_cookie", "missing_state", "expired"])
def test_callback_state_mismatch(db, stub, variant, monkeypatch):
    s = t7_settings()
    client = stub(s, ok_result())
    with api_client(s) as c:
        login(c)
        good = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[0]
        params = {"state": good, "code": CODE}
        if variant == "no_cookie":
            c.cookies.clear()
        elif variant == "wrong_state":
            params["state"] = good[:-2] + "xx"
        elif variant == "tampered_cookie":
            forged = dump_state_cookie(t7_settings(session_secret="other" * 10), good, None)
            c.cookies.clear()
            c.cookies.set(STATE_COOKIE, forged, path="/api/auth")
        elif variant == "missing_state":
            params.pop("state")
        elif variant == "expired":
            monkeypatch.setattr(oauth, "STATE_MAX_AGE_SECONDS", -1)
        r = callback(c, **params)
    assert r.headers["location"] == f"{UI}/connect?error=state_mismatch"
    assert client.exchanged == []
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0


def _connected_callback(db, stub, s, result, **extra):
    client = stub(s, result)
    with api_client(s) as c:
        login(c)
        state = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[0]
        r = callback(c, state=state, code=CODE, **extra)
    return r, client


def test_callback_missing_refresh_token_is_an_error_and_keeps_credentials(db, stub):
    s = t7_settings()
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, 'old@x.com', :e, '{}')", e=encrypt_token(s, "old-token"))
    r, _ = _connected_callback(db, stub, s, ok_result(refresh=None))
    assert r.headers["location"] == f"{UI}/connect?error=missing_refresh_token"
    assert decrypt_token(s, q1(db, "SELECT refresh_token_encrypted FROM oauth_credentials")) == "old-token"


def test_callback_exchange_failure(db, stub):
    s = t7_settings()
    r, _ = _connected_callback(db, stub, s, OAuthExchangeError("token exchange failed (InvalidGrantError)"))
    assert r.headers["location"] == f"{UI}/connect?error=token_exchange_failed"
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0


def test_callback_insufficient_scope(db, stub):
    s = t7_settings()
    r, _ = _connected_callback(db, stub, s, ok_result(scopes=["openid", EMAIL_SCOPE]))
    assert r.headers["location"] == f"{UI}/connect?error=insufficient_scope"
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0


def test_callback_access_denied_and_missing_code(db, stub):
    s = t7_settings()
    client = stub(s, ok_result())
    with api_client(s) as c:
        login(c)
        state = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[0]
        r = callback(c, state=state, error="access_denied")
        assert r.headers["location"] == f"{UI}/connect?error=access_denied"
        login(c)
        state = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[0]
        r = callback(c, state=state)
        assert r.headers["location"] == f"{UI}/connect?error=token_exchange_failed"
    assert client.exchanged == []


def test_callback_in_demo_mode(db):
    with api_client(demo_settings()) as c:
        r = callback(c, state="x", code=CODE)
    assert r.headers["location"] == f"{UI}/connect?error=demo_mode"


def test_reconnect_same_account_keeps_folder_and_requests_reconcile(db, stub):
    s = t7_settings()
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, 'sj.demo@gmail.com', :e, '{}')", e=encrypt_token(s, "old-token"))
    exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name, page_token) "
                 "VALUES (1, 'drive', 'F1', 'Fund Docs', '42')")
    r, _ = _connected_callback(db, stub, s, ok_result())
    assert r.headers["location"] == f"{UI}/connect/folder"
    (st,) = rows(db, "SELECT * FROM sync_state")
    assert st["folder_id"] == "F1" and st["page_token"] == "42" and st["reconcile_requested_at"] is not None
    assert decrypt_token(s, q1(db, "SELECT refresh_token_encrypted FROM oauth_credentials")) == REFRESH


def test_reconnect_other_account_resets_folder(db, stub):
    s = t7_settings()
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, 'someone.else@gmail.com', :e, '{}')", e=encrypt_token(s, "old-token"))
    exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name, page_token) "
                 "VALUES (1, 'drive', 'F1', 'Fund Docs', '42')")
    _connected_callback(db, stub, s, ok_result())
    (st,) = rows(db, "SELECT * FROM sync_state")
    assert st["folder_id"] is None and st["page_token"] is None
    assert q1(db, "SELECT google_email FROM oauth_credentials") == "sj.demo@gmail.com"


# ---- the real OAuthClient.exchange (Flow.fetch_token patched; no network) -------------------


def test_oauth_client_exchange_parses_token(monkeypatch):
    s = t7_settings()
    seen = {}

    def fake_fetch_token(self, **kw):
        seen.update(kw)
        return {"access_token": ACCESS, "refresh_token": REFRESH, "id_token": id_token("a@b.com"),
                "scope": " ".join(SCOPES), "expires_in": 3599}

    monkeypatch.setattr(oauth.Flow, "fetch_token", fake_fetch_token)
    res = OAuthClient(s).exchange(CODE, "v" * 50)
    assert seen == {"code": CODE}  # the verifier is sent by the Flow itself
    assert res.refresh_token == REFRESH and res.email == "a@b.com" and set(res.scopes) == set(SCOPES)
    assert REFRESH not in repr(res)


def test_oauth_client_exchange_error_hides_details(monkeypatch):
    def boom(self, **kw):
        raise ValueError(f"(invalid_grant) Bad Request for code {CODE}")

    monkeypatch.setattr(oauth.Flow, "fetch_token", boom)
    with pytest.raises(OAuthExchangeError) as ei:
        OAuthClient(t7_settings()).exchange(CODE, None)
    assert CODE not in str(ei.value) and "ValueError" in str(ei.value)


def test_oauth_client_sends_pkce_verifier(monkeypatch):
    captured = {}

    def fake_fetch(self, token_url, **kw):  # requests_oauthlib.OAuth2Session.fetch_token
        captured.update(kw, token_url=token_url)
        return {"access_token": ACCESS, "refresh_token": REFRESH, "scope": DRIVE_READONLY_SCOPE}

    import requests_oauthlib

    monkeypatch.setattr(requests_oauthlib.OAuth2Session, "fetch_token", fake_fetch)
    res = OAuthClient(t7_settings()).exchange(CODE, "v" * 50)
    assert captured["token_url"] == "https://oauth2.googleapis.com/token"
    assert captured["code"] == CODE and captured["code_verifier"] == "v" * 50
    assert res.scopes == [DRIVE_READONLY_SCOPE] and res.email is None


@pytest.mark.parametrize("tok,expected", [
    (None, None), ("", None), ("not-a-jwt", None), ("a.!!!.c", None), (id_token(None), None),
    (id_token("x@y.org"), "x@y.org"),
])
def test_email_from_id_token(tok, expected):
    assert email_from_id_token(tok) == expected


# ---- status / disconnect ----------------------------------------------------------------------


def test_status_not_connected_connected_and_with_folder(db):
    s = t7_settings()
    with api_client(s) as c:
        body = c.get("/api/auth/status").json()
        validate(AuthStatusResponse, body)
        assert body == {"mode": "drive", "connected": False, "google_email": None, "scopes": [],
                        "connected_at": None, "folder": None}
        exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                     "VALUES (1, 'sj.demo@gmail.com', :e, :sc)", e=encrypt_token(s, REFRESH), sc=list(SCOPES))
        body = c.get("/api/auth/status").json()
        validate(AuthStatusResponse, body)
        assert body["connected"] is True and body["google_email"] == "sj.demo@gmail.com"
        assert body["scopes"] == sorted(SCOPES) and body["connected_at"].endswith("Z") and body["folder"] is None
        exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name) VALUES (1, 'drive', 'F1', 'Fund Docs')")
        body = c.get("/api/auth/status").json()
        assert body["folder"] == {"source": "drive", "folder_id": "F1", "folder_name": "Fund Docs"}
        assert REFRESH not in json.dumps(body)


def test_status_demo_mode(db):
    with api_client(demo_settings()) as c:
        body = c.get("/api/auth/status").json()
    assert body == {"mode": "demo", "connected": False, "google_email": None, "scopes": [], "connected_at": None,
                    "folder": {"source": "local", "folder_id": None, "folder_name": "sample_docs"}}


def test_disconnect_deletes_credentials_and_sync_state_but_keeps_documents(db):
    s = t7_settings()
    exec_sql(db, "INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes) "
                 "VALUES (1, 'a@b.com', :e, '{}')", e=encrypt_token(s, REFRESH))
    exec_sql(db, "INSERT INTO sync_state (id, source, folder_id, folder_name, page_token, last_synced_at, "
                 "last_reconciled_at, last_error, sync_requested_at, reconcile_requested_at) "
                 "VALUES (1, 'drive', 'F1', 'Fund Docs', '42', now(), now(), 'x', now(), now())")
    exec_sql(db, "INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status) "
                 "VALUES ('drive', 'f1', 'a.pdf', 'application/pdf', 'abc', 'done')")
    with api_client(s) as c:
        r = c.post("/api/auth/disconnect")
        assert r.status_code == 200 and r.json() == {"connected": False}
        assert c.post("/api/auth/disconnect").status_code == 200  # idempotent
        assert c.get("/api/auth/status").json()["connected"] is False
        prog = c.get("/api/progress").json()
        assert prog["connection"] == {"connected": False, "google_email": None}
        assert_error(c.post("/api/sync"), 409, "not_connected")
    assert q1(db, "SELECT count(*) FROM oauth_credentials") == 0
    (st,) = rows(db, "SELECT * FROM sync_state")
    for col in ("folder_id", "folder_name", "page_token", "last_synced_at", "last_reconciled_at", "last_error",
                "sync_requested_at", "reconcile_requested_at"):
        assert st[col] is None, col
    assert q1(db, "SELECT status FROM documents") == "done"


def test_disconnect_demo_mode(db):
    with api_client(demo_settings()) as c:
        assert_error(c.post("/api/auth/disconnect"), 409, "demo_mode")


# ---- removed endpoint (Part A) ----------------------------------------------------------------


def test_document_file_endpoint_is_gone(db):
    with api_client(demo_settings()) as c:
        assert_error(c.get("/api/documents/1/file"), 404, "not_found")


# ---- no secrets in logs -----------------------------------------------------------------------


def test_oauth_flow_never_logs_code_state_or_tokens(db, stub, caplog):
    s = t7_settings()
    stub(s, ok_result())
    caplog.set_level(logging.DEBUG)
    with api_client(s) as c:
        r0 = login(c)
        state = parse_qs(urlparse(r0.headers["location"]).query)["state"][0]
        verifier = load_state_cookie(s, c.cookies.get(STATE_COOKIE))[1]
        callback(c, state=state, code=CODE, scope=" ".join(SCOPES))
        callback(c, state="bad", code=CODE)
    fmt = JsonFormatter(s.secret_values())
    # What the app writes (every record through the JSON formatter), plus the raw text of
    # every non-test-client record (the httpx lines are the TestClient's own requests).
    app_records = [r for r in caplog.records if not r.name.startswith("httpx")]
    text_logs = "\n".join(fmt.format(rec) for rec in caplog.records) + "\n".join(
        r.getMessage() + json.dumps({k: str(v) for k, v in r.__dict__.items()}) for r in app_records)
    for secret in (REFRESH, CODE, state, verifier, "sj.demo@gmail.com"):
        assert secret not in text_logs
    assert any(getattr(r, "event", None) == "oauth_connected" for r in caplog.records)


def test_access_log_lines_have_oauth_params_redacted():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5", "GET", f"/api/auth/google/callback?state=abc123&code={CODE}&scope=x",
                             "1.1", 302), None)
    out = JsonFormatter().format(rec)
    assert CODE not in out and "abc123" not in out
    assert "code=[REDACTED]" in out and "state=[REDACTED]" in out and "scope=x" in out

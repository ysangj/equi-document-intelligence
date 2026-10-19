"""Google OAuth 2.0 web flow (FR-2) on `google-auth-oauthlib`.

Scopes (minimal, §12):
- `https://www.googleapis.com/auth/drive.readonly`: the only Drive scope; no write access.
- `openid` + `https://www.googleapis.com/auth/userinfo.email`: only so the token
  response carries an id_token with the account email for `/api/auth/status`. Full
  URIs are used (not the `email` shorthand) because Google echoes the full URIs back,
  and a mismatch would trip oauthlib's scope-change check.

State + PKCE: the login handler generates a random `state` and lets the Flow generate a
PKCE `code_verifier`; both go into the signed (`SESSION_SECRET`, itsdangerous) cookie
`oauth_state` (HttpOnly, SameSite=Lax, Path=/api/auth, Max-Age=600). The callback
checks the state in constant time and sends the verifier with the code exchange.
The cookie is signed, not encrypted: the verifier is only useful together with the
authorization code and the client secret, and it is single-use.

`OAuthClient` is the seam tests mock: `authorization_url()` builds the URL locally (no
network); `exchange()` is the only call to Google's token endpoint.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from dataclasses import dataclass

from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import Settings

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
OPENID_SCOPE = "openid"
SCOPES = (OPENID_SCOPE, EMAIL_SCOPE, DRIVE_READONLY_SCOPE)

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

STATE_COOKIE = "oauth_state"
STATE_COOKIE_PATH = "/api/auth"
STATE_MAX_AGE_SECONDS = 600
_COOKIE_SALT = "docintel.oauth-state.v1"

# requests_oauthlib/oauthlib log the state, the token request and the token response
# at DEBUG; keep them quiet whatever the root level is (§12: never log tokens).
for _name in ("requests_oauthlib", "oauthlib", "google_auth_oauthlib", "urllib3.connectionpool"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class OAuthExchangeError(Exception):
    """The code exchange failed. The message is generic and safe to log."""


@dataclass(frozen=True)
class TokenResult:
    refresh_token: str | None
    scopes: list[str]
    email: str | None

    def __repr__(self) -> str:  # never print the token
        return f"TokenResult(refresh_token={'***' if self.refresh_token else None}, scopes={self.scopes!r})"


# ---- state cookie ----------------------------------------------------------------------


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    if settings.session_secret is None:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt=_COOKIE_SALT)


def dump_state_cookie(settings: Settings, state: str, code_verifier: str | None) -> str:
    return _serializer(settings).dumps({"s": state, "v": code_verifier})


def load_state_cookie(settings: Settings, value: str | None) -> tuple[str, str | None] | None:
    """(state, code_verifier) from a valid, unexpired cookie, else None."""
    if not value:
        return None
    try:
        data = _serializer(settings).loads(value, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature:  # includes SignatureExpired
        return None
    if not isinstance(data, dict) or not isinstance(data.get("s"), str):
        return None
    verifier = data.get("v")
    return data["s"], verifier if isinstance(verifier, str) else None


def states_match(expected: str, got: str | None) -> bool:
    return bool(got) and secrets.compare_digest(expected.encode(), got.encode())  # type: ignore[union-attr]


# ---- id_token ---------------------------------------------------------------------------


def email_from_id_token(id_token: str | None) -> str | None:
    """The `email` claim. The id_token comes straight from Google's token endpoint over
    TLS, so its signature is not re-verified (OpenID Connect Core §3.1.3.7)."""
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (binascii.Error, ValueError):
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    return email if isinstance(email, str) and email else None


def _scope_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [s for s in raw.split() if s]
    return [str(s) for s in raw]


# ---- client -------------------------------------------------------------------------------


class OAuthClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.oauthlib_insecure_transport:
            # PRD §11: plain-http localhost redirect URI in development.
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", settings.oauthlib_insecure_transport)
        # Google may return the scopes in another order or (granular consent) fewer of
        # them; we check drive.readonly ourselves instead of letting oauthlib raise.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

    def _flow(self, *, state: str | None = None, code_verifier: str | None = None) -> Flow:
        s = self.settings
        config = {"web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value() if s.google_client_secret else None,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [s.google_redirect_uri],
        }}
        flow = Flow.from_client_config(config, scopes=list(SCOPES), state=state, code_verifier=code_verifier,
                                       autogenerate_code_verifier=True)
        flow.redirect_uri = s.google_redirect_uri
        return flow

    def authorization_url(self) -> tuple[str, str, str | None]:
        """(url, state, code_verifier). No network."""
        state = secrets.token_urlsafe(32)
        flow = self._flow(state=state)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return url, state, flow.code_verifier

    def exchange(self, code: str, code_verifier: str | None) -> TokenResult:
        """Exchange the authorization code. Raises OAuthExchangeError (generic message)."""
        flow = self._flow(code_verifier=code_verifier)
        try:
            token = flow.fetch_token(code=code)
        except Exception as e:  # noqa: BLE001 - oauthlib/requests errors; never echo them (may quote the code)
            raise OAuthExchangeError(f"token exchange failed ({type(e).__name__})") from None
        return TokenResult(
            refresh_token=token.get("refresh_token") or None,
            scopes=_scope_list(token.get("scope")),
            email=email_from_id_token(token.get("id_token")),
        )


def get_oauth_client(settings: Settings) -> OAuthClient:
    return OAuthClient(settings)

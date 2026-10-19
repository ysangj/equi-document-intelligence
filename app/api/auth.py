"""FR-2 Google OAuth: `/api/auth/google/login`, `/callback`, `/api/auth/status`,
`/api/auth/disconnect`.

- Login and callback are full-page navigations, so every outcome is a 302: to Google,
  to the folder picker (`/connect/folder`), or to `/connect?error=<code>`.
- Only the refresh token is kept, Fernet-encrypted, in `oauth_credentials` (id 1).
- Never logged: the authorization code, `state`, the PKCE verifier, any token, the
  account email.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.api.listing import utc
from app.api.params import NoQuery
from app.config import Settings, get_settings
from app.db import get_session
from app.google.credentials import delete_credentials, load_credentials, save_credentials
from app.google.oauth import (
    DRIVE_READONLY_SCOPE,
    STATE_COOKIE,
    STATE_COOKIE_PATH,
    STATE_MAX_AGE_SECONDS,
    OAuthClient,
    OAuthExchangeError,
    dump_state_cookie,
    get_oauth_client,
    load_state_cookie,
    states_match,
)
from app.schemas.auth import (
    UI_BASE_URL,
    UI_CONNECT_ERROR_PATH,
    UI_CONNECT_SUCCESS_PATH,
    AuthStatusResponse,
    DisconnectResponse,
)
from app.schemas.common import ErrorResponse

log = logging.getLogger("app.api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

DEMO_FOLDER = {"source": "local", "folder_id": None, "folder_name": "sample_docs"}


def oauth_client_dep(settings: Settings = Depends(get_settings)) -> OAuthClient:
    return get_oauth_client(settings)


def _no_store(resp: RedirectResponse) -> RedirectResponse:
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _ui_error(code: str) -> RedirectResponse:
    resp = RedirectResponse(f"{UI_BASE_URL}{UI_CONNECT_ERROR_PATH}?{urlencode({'error': code})}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return _no_store(resp)


@router.get("/google/login", status_code=302, response_class=RedirectResponse)
def google_login(_: NoQuery, settings: Settings = Depends(get_settings),
                 client: OAuthClient = Depends(oauth_client_dep)):
    if settings.demo_mode:
        return _ui_error("demo_mode")
    url, state, verifier = client.authorization_url()
    resp = _no_store(RedirectResponse(url, status_code=302))
    resp.set_cookie(STATE_COOKIE, dump_state_cookie(settings, state, verifier), max_age=STATE_MAX_AGE_SECONDS,
                    path=STATE_COOKIE_PATH, httponly=True, samesite="lax", secure=False)
    log.info("oauth login started", extra={"event": "oauth_login"})
    return resp


@router.get("/google/callback", status_code=302, response_class=RedirectResponse)
def google_callback(request: Request, settings: Settings = Depends(get_settings),
                    client: OAuthClient = Depends(oauth_client_dep), session: Session = Depends(get_session)):
    """Google's redirect target. Query params are Google's (code, state, scope, error,
    authuser, prompt, ...), so unknown params are accepted here."""

    def fail(code: str) -> RedirectResponse:
        log.warning("oauth callback failed", extra={"event": "oauth_callback_error", "code": code})
        return _ui_error(code)

    if settings.demo_mode:
        return fail("demo_mode")
    params = request.query_params
    if params.get("error"):  # the user cancelled, or Google refused (e.g. not a test user)
        return fail("access_denied")
    saved = load_state_cookie(settings, request.cookies.get(STATE_COOKIE))
    if saved is None or not states_match(saved[0], params.get("state")):
        return fail("state_mismatch")
    code = params.get("code")
    if not code:
        return fail("token_exchange_failed")
    try:
        token = client.exchange(code, saved[1])
    except OAuthExchangeError as e:
        log.warning("oauth token exchange failed", extra={"event": "oauth_exchange_error", "error": str(e)})
        return fail("token_exchange_failed")
    if DRIVE_READONLY_SCOPE not in token.scopes:
        return fail("insufficient_scope")
    if not token.refresh_token:
        return fail("missing_refresh_token")
    save_credentials(session, settings, refresh_token=token.refresh_token, email=token.email, scopes=token.scopes)
    session.commit()
    log.info("google drive connected", extra={"event": "oauth_connected", "has_email": token.email is not None,
                                              "scopes": sorted(token.scopes)})
    resp = _no_store(RedirectResponse(f"{UI_BASE_URL}{UI_CONNECT_SUCCESS_PATH}", status_code=302))
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return resp


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(_: NoQuery, settings: Settings = Depends(get_settings), session: Session = Depends(get_session)):
    if settings.demo_mode:
        return {"mode": "demo", "connected": False, "google_email": None, "scopes": [], "connected_at": None,
                "folder": DEMO_FOLDER}
    creds = load_credentials(session)
    if creds is None:
        return {"mode": "drive", "connected": False, "google_email": None, "scopes": [], "connected_at": None,
                "folder": None}
    row = session.execute(text("SELECT folder_id, folder_name FROM sync_state WHERE id = 1")).first()
    folder = ({"source": "drive", "folder_id": row.folder_id, "folder_name": row.folder_name}
              if row is not None and row.folder_id else None)
    return {"mode": "drive", "connected": True, "google_email": creds.google_email, "scopes": sorted(creds.scopes),
            "connected_at": utc(creds.created_at), "folder": folder}


@router.post("/disconnect", response_model=DisconnectResponse, responses={409: {"model": ErrorResponse}})
def disconnect(_: NoQuery, settings: Settings = Depends(get_settings), session: Session = Depends(get_session)):
    if settings.demo_mode:
        raise ApiError(409, "demo_mode", "Google Drive is not used in demo mode.")
    delete_credentials(session)
    session.commit()
    log.info("google drive disconnected", extra={"event": "oauth_disconnected"})
    return {"connected": False}


__all__ = ["router", "oauth_client_dep"]

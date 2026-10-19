"""Thin Drive v3 client: the only code that talks to the Drive API.

`DriveApi` wraps a `googleapiclient` service and turns every failure into a
`SourceError` the worker and the api understand:

| failure                                              | raised                                   |
|------------------------------------------------------|------------------------------------------|
| 429, 5xx, 403 rate-limit reasons, network/timeouts   | retried here (short backoff, Retry-After), then `TransientSourceError` |
| refresh token rejected (`invalid_grant`, revoked), 401 | `SourceAuthError` (not retried)          |
| 404                                                  | `DriveNotFound`                          |
| 400 (e.g. an invalid page token)                     | `DriveInvalidArgument`                   |
| other 4xx                                            | `DriveApiError` (not retryable)          |

Longer outages fall through to the callers' own retry: job attempts use the T4 queue
backoff (`queue.backoff_seconds`), syncs retry after `SYNC_ERROR_RETRY_SECONDS`.

Messages never contain tokens, URLs with credentials, or response bodies.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Settings
from app.google.oauth import SCOPES, TOKEN_URI
from app.sources.base import SourceAuthError, SourceError, TransientSourceError

log = logging.getLogger("app.google.drive")

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."

HTTP_TIMEOUT_SECONDS = 60
RETRIES = 3  # 4 attempts in all
RETRY_BASE_SECONDS = 0.5
RETRY_MAX_SECONDS = 8.0
RETRY_AFTER_CAP_SECONDS = 10.0
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}

AUTH_ERROR_MESSAGE = ("Google Drive access was revoked or has expired. Reconnect Google Drive to resume syncing.")

# Quiet the client's own INFO/WARNING chatter (request URLs, retry notices); our own
# log lines carry what matters.
for _name in ("googleapiclient.discovery", "googleapiclient.http", "googleapiclient.discovery_cache"):
    logging.getLogger(_name).setLevel(logging.ERROR)


class DriveApiError(SourceError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class DriveNotFound(DriveApiError):
    pass


class DriveInvalidArgument(DriveApiError):
    pass


def _reason(err: HttpError) -> str | None:
    try:
        data = json.loads(err.content.decode("utf-8"))
        errors = data["error"].get("errors") or []
        if errors and isinstance(errors[0], dict):
            return errors[0].get("reason")
        return data["error"].get("status")
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeDecodeError):
        return None


def _retry_after(err: HttpError) -> float | None:
    try:
        v = err.resp.get("retry-after")
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


class DriveApi:
    def __init__(self, service: Any, *, retries: int = RETRIES, sleep: Callable[[float], None] = time.sleep):
        self.service = service
        self.retries = retries
        self.sleep = sleep

    @classmethod
    def from_refresh_token(cls, settings: Settings, refresh_token: str) -> DriveApi:
        """A client whose access tokens are minted from `refresh_token` on demand. No
        network until the first call."""
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value() if settings.google_client_secret else None,
            scopes=list(SCOPES),
        )
        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=HTTP_TIMEOUT_SECONDS))
        service = build("drive", "v3", http=http, cache_discovery=False, static_discovery=True)
        return cls(service)

    # ---- the retry/translate loop ----------------------------------------------------------
    def _call(self, what: str, make_request: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            retry_after = None
            try:
                return make_request().execute()
            except HttpError as e:
                status = int(getattr(e.resp, "status", 0) or 0)
                reason = _reason(e)
                if status == 401:
                    raise SourceAuthError(AUTH_ERROR_MESSAGE) from None
                if status == 404:
                    raise DriveNotFound(f"Drive {what}: not found", 404) from None
                retryable = status == 429 or status >= 500 or (status == 403 and reason in _RATE_LIMIT_REASONS)
                if not retryable:
                    cls = DriveInvalidArgument if status == 400 else DriveApiError
                    raise cls(f"Drive {what} failed: HTTP {status}" + (f" ({reason})" if reason else ""),
                              status) from None
                failure = f"HTTP {status}" + (f" ({reason})" if reason else "")
                retry_after = _retry_after(e)
            except RefreshError as e:
                if not getattr(e, "retryable", False):
                    raise SourceAuthError(AUTH_ERROR_MESSAGE) from None
                failure = "token refresh failed (transient)"
            except (TransportError, httplib2.HttpLib2Error, socket.timeout, ssl.SSLError, ConnectionError,
                    TimeoutError, OSError) as e:
                failure = f"network error ({type(e).__name__})"
            if attempt >= self.retries:
                raise TransientSourceError(f"Drive {what} failed after {attempt + 1} attempts: {failure}")
            delay = min(RETRY_BASE_SECONDS * 2 ** attempt, RETRY_MAX_SECONDS) * random.uniform(0.8, 1.2)
            if retry_after:
                delay = max(delay, min(retry_after, RETRY_AFTER_CAP_SECONDS))
            log.info("drive call retry", extra={"event": "drive_retry", "call": what, "attempt": attempt + 1,
                                                "error": failure, "delay_s": round(delay, 2)})
            self.sleep(delay)
            attempt += 1

    # ---- endpoints -----------------------------------------------------------------------
    def files_list(self, *, q: str, fields: str, page_token: str | None = None, page_size: int = 1000,
                   order_by: str | None = None) -> dict:
        kw: dict[str, Any] = {"q": q, "fields": fields, "pageSize": page_size, "spaces": "drive",
                              "supportsAllDrives": True, "includeItemsFromAllDrives": True}
        if page_token:
            kw["pageToken"] = page_token
        if order_by:
            kw["orderBy"] = order_by
        return self._call("files.list", lambda: self.service.files().list(**kw))

    def files_get(self, file_id: str, *, fields: str) -> dict:
        return self._call("files.get", lambda: self.service.files().get(fileId=file_id, fields=fields,
                                                                         supportsAllDrives=True))

    def get_media(self, file_id: str) -> bytes:
        return self._call("files.get_media", lambda: self.service.files().get_media(fileId=file_id,
                                                                                     supportsAllDrives=True))

    def export(self, file_id: str, mime_type: str) -> bytes:
        return self._call("files.export", lambda: self.service.files().export(fileId=file_id, mimeType=mime_type))

    def start_page_token(self) -> str:
        resp = self._call("changes.getStartPageToken",
                          lambda: self.service.changes().getStartPageToken(supportsAllDrives=True))
        return resp["startPageToken"]

    def changes_list(self, *, page_token: str, fields: str, page_size: int = 1000) -> dict:
        return self._call("changes.list", lambda: self.service.changes().list(
            pageToken=page_token, fields=fields, pageSize=page_size, spaces="drive", includeRemoved=True,
            supportsAllDrives=True, includeItemsFromAllDrives=True))


DriveApiFactory = Callable[[Settings, str], DriveApi]


def default_api_factory(settings: Settings, refresh_token: str) -> DriveApi:
    return DriveApi.from_refresh_token(settings, refresh_token)

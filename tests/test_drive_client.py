"""`app.google.drive_api.DriveApi`: the real googleapiclient service over
`HttpMockSequence` (no network). Retries, error translation, request parameters."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from app.google.drive_api import (
    AUTH_ERROR_MESSAGE,
    DriveApi,
    DriveApiError,
    DriveInvalidArgument,
    DriveNotFound,
    default_api_factory,
)
from app.sources.base import SourceAuthError, TransientSourceError
from tests.test_auth_api import REFRESH, t7_settings


def err(status: int, reason: str | None = None, **headers) -> tuple[dict, bytes]:
    body = {"error": {"code": status, "message": "x"}}
    if reason:
        body["error"]["errors"] = [{"reason": reason, "message": "x"}]
    return {"status": str(status), **headers}, json.dumps(body).encode()


def ok(body: dict | bytes) -> tuple[dict, bytes]:
    return {"status": "200"}, body if isinstance(body, bytes) else json.dumps(body).encode()


class Recording(HttpMockSequence):
    def __init__(self, seq):
        super().__init__(seq)
        self.uris: list[str] = []

    def request(self, uri, method="GET", body=None, headers=None, redirections=1, connection_type=None):
        self.uris.append(uri)
        return super().request(uri, method, body, headers, redirections, connection_type)


def api(seq, retries=3):
    http = Recording(seq)
    sleeps: list[float] = []
    service = build("drive", "v3", http=http, static_discovery=True, cache_discovery=False)
    return DriveApi(service, retries=retries, sleep=sleeps.append), http, sleeps


def test_files_list_params_and_result():
    d, http, _ = api([ok({"files": [{"id": "a"}], "nextPageToken": "t2"})])
    out = d.files_list(q="'F' in parents and trashed = false", fields="nextPageToken, files(id)", page_token="t1",
                       page_size=100, order_by="name")
    assert out == {"files": [{"id": "a"}], "nextPageToken": "t2"}
    q = {k: v[0] for k, v in parse_qs(urlparse(http.uris[0]).query).items()}
    assert q["q"] == "'F' in parents and trashed = false"
    assert (q["pageToken"], q["pageSize"], q["orderBy"], q["spaces"]) == ("t1", "100", "name", "drive")


def test_media_export_and_changes():
    d, http, _ = api([ok(b"%PDF raw"), ok(b"a,b\n"), ok({"startPageToken": "77"}),
                      ok({"changes": [], "newStartPageToken": "78"})])
    assert d.get_media("f1") == b"%PDF raw"
    assert "alt=media" in http.uris[0]
    assert d.export("s1", "text/csv") == b"a,b\n"
    assert "/files/s1/export" in http.uris[1] and "mimeType=text%2Fcsv" in http.uris[1]
    assert d.start_page_token() == "77"
    assert d.changes_list(page_token="77", fields="newStartPageToken")["newStartPageToken"] == "78"
    assert "includeRemoved=true" in http.uris[3]


@pytest.mark.parametrize("first", [err(503), err(500), err(429), err(403, "userRateLimitExceeded"),
                                   err(403, "rateLimitExceeded")])
def test_transient_errors_are_retried(first):
    d, http, sleeps = api([first, ok({"id": "f"})])
    assert d.files_get("f", fields="id") == {"id": "f"}
    assert len(http.uris) == 2 and len(sleeps) == 1


def test_retries_exhausted_raise_transient():
    d, http, sleeps = api([err(503)] * 4, retries=3)
    with pytest.raises(TransientSourceError, match="after 4 attempts: HTTP 503") as ei:
        d.files_get("f", fields="id")
    assert ei.value.retryable is True
    assert len(sleeps) == 3 and sleeps[0] < sleeps[1] < sleeps[2]


def test_retry_after_is_honoured():
    d, _, sleeps = api([err(429, **{"retry-after": "7"}), ok({"id": "f"})])
    d.files_get("f", fields="id")
    assert sleeps[0] >= 7


@pytest.mark.parametrize("resp,exc", [
    (err(404, "notFound"), DriveNotFound),
    (err(400, "invalid"), DriveInvalidArgument),
    (err(403, "insufficientFilePermissions"), DriveApiError),
    (err(401, "authError"), SourceAuthError),
])
def test_non_retryable_errors(resp, exc):
    d, http, sleeps = api([resp, ok({})])
    with pytest.raises(exc) as ei:
        d.files_get("f", fields="id")
    assert ei.value.retryable is False and sleeps == [] and len(http.uris) == 1


class _Req:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def execute(self):
        o = self.outcomes.pop(0)
        if isinstance(o, BaseException):
            raise o
        return o


class _Service:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def files(self):
        return self

    def get(self, **kw):
        return _Req(self.outcomes)


def test_refresh_errors():
    d = DriveApi(_Service([RefreshError("invalid_grant: Token has been expired or revoked.")]), sleep=lambda s: None)
    with pytest.raises(SourceAuthError) as ei:
        d.files_get("f", fields="id")
    assert ei.value.message == AUTH_ERROR_MESSAGE
    d = DriveApi(_Service([RefreshError("temporarily unavailable", retryable=True), {"id": "f"}]),
                 sleep=lambda s: None)
    assert d.files_get("f", fields="id") == {"id": "f"}


@pytest.mark.parametrize("exc", [TimeoutError("t"), ConnectionResetError("r"), httplib2.ServerNotFoundError("dns"),
                                 OSError("net down")])
def test_network_errors_are_transient(exc):
    d = DriveApi(_Service([exc] * 4), sleep=lambda s: None)
    with pytest.raises(TransientSourceError, match="network error"):
        d.files_get("f", fields="id")


def test_default_factory_builds_without_network():
    d = default_api_factory(t7_settings(), REFRESH)
    creds = d.service._http.credentials
    assert creds.refresh_token == REFRESH and creds.token is None
    assert "https://www.googleapis.com/auth/drive.readonly" in creds.scopes
    assert REFRESH not in repr(d)

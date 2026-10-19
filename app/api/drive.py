"""FR-3 folder selection: `GET /api/drive/folders`, `POST /api/drive/watch`.

Each request builds its Drive client from the stored (encrypted) refresh token and
drops it afterwards (FR-2). If Google rejects the token, the credentials are removed
(guarded, as in the worker) and the request answers 409 `not_connected`, so the UI
sends the user back to Connect.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ApiError, not_found, validation_error
from app.api.listing import utc
from app.config import Settings, get_settings
from app.db import get_session
from app.google.credentials import (
    CredentialsError,
    StoredCredentials,
    decrypt_token,
    invalidate_credentials,
    load_credentials,
)
from app.google.drive_api import (
    FOLDER_MIME,
    DriveApi,
    DriveApiFactory,
    DriveInvalidArgument,
    DriveNotFound,
    default_api_factory,
)
from app.schemas.auth import DriveFoldersQuery, DriveFoldersResponse, WatchRequest, WatchResponse
from app.schemas.common import ErrorResponse
from app.sources.base import SourceAuthError, SourceError
from app.sources.drive import DECRYPT_ERROR_MESSAGE, drive_query_literal, parse_time

log = logging.getLogger("app.api.drive")

router = APIRouter(prefix="/api/drive", tags=["drive"])

PAGE_SIZE = 100
ROOT = "root"
ROOT_NAME = "My Drive"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
           502: {"model": ErrorResponse}}


def drive_api_factory_dep() -> DriveApiFactory:
    """Overridden in tests with a fake Drive."""
    return default_api_factory


class _Drive:
    """The stored credentials + a lazily built client, for one request."""

    def __init__(self, settings: Settings, session: Session, creds: StoredCredentials, factory: DriveApiFactory):
        self.settings, self.session, self.creds, self.factory = settings, session, creds, factory
        self._api: DriveApi | None = None

    @property
    def api(self) -> DriveApi:
        if self._api is None:
            try:
                token = decrypt_token(self.settings, self.creds.refresh_token_encrypted)
            except CredentialsError:
                raise SourceAuthError(DECRYPT_ERROR_MESSAGE) from None
            self._api = self.factory(self.settings, token)
        return self._api

    def rejected(self, message: str) -> ApiError:
        invalidate_credentials(self.session, self.creds.refresh_token_encrypted, message)
        self.session.commit()
        log.warning("google credentials rejected", extra={"event": "drive_auth_failed"})
        return ApiError(409, "not_connected", message)


def _connected_drive(settings: Settings, session: Session, factory: DriveApiFactory) -> _Drive:
    if settings.demo_mode:
        raise ApiError(409, "demo_mode", "Google Drive is not used in demo mode.")
    creds = load_credentials(session)
    if creds is None:
        raise ApiError(409, "not_connected", "Google Drive is not connected. Connect it first.")
    return _Drive(settings, session, creds, factory)


def _drive_error(e: SourceError) -> ApiError:
    log.warning("drive api failed", extra={"event": "drive_api_error", "error": e.message})
    return ApiError(502, "drive_error", "Google Drive could not be reached or returned an error. Try again.")


def _folder_meta(drive: _Drive, folder_id: str, fields: str) -> dict:
    if not _ID_RE.match(folder_id):
        raise not_found(f"Drive folder {folder_id!r} not found.")
    try:
        return drive.api.files_get(folder_id, fields=fields)
    except DriveNotFound:
        raise not_found(f"Drive folder {folder_id} not found.") from None


@router.get("/folders", response_model=DriveFoldersResponse, responses=_ERRORS)
def folders(q: Annotated[DriveFoldersQuery, Query()], settings: Settings = Depends(get_settings),
            session: Session = Depends(get_session), factory: DriveApiFactory = Depends(drive_api_factory_dep)):
    drive = _connected_drive(settings, session, factory)
    try:
        root_id = drive.api.files_get(ROOT, fields="id")["id"]
        if q.parent in (ROOT, root_id):
            parent = {"folder_id": ROOT, "folder_name": ROOT_NAME, "parent_id": None, "is_root": True}
            query_parent = ROOT
        else:
            meta = _folder_meta(drive, q.parent, "id, name, mimeType, parents, trashed")
            if meta.get("mimeType") != FOLDER_MIME or meta.get("trashed"):
                raise not_found(f"Drive folder {q.parent} not found.")
            up = (meta.get("parents") or [None])[0]
            parent = {"folder_id": meta["id"], "folder_name": meta.get("name") or meta["id"],
                      "parent_id": ROOT if up == root_id else up, "is_root": False}
            query_parent = meta["id"]
        try:
            resp = drive.api.files_list(
                q=f"{drive_query_literal(query_parent)} in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
                fields="nextPageToken, files(id, name, modifiedTime)", page_token=q.page_token,
                page_size=PAGE_SIZE, order_by="name")
        except DriveInvalidArgument:
            if q.page_token:
                raise validation_error("query.page_token", "invalid or expired page token") from None
            raise
    except SourceAuthError as e:
        raise drive.rejected(e.message) from None
    except ApiError:
        raise
    except SourceError as e:
        raise _drive_error(e) from None
    items = [{"folder_id": f["id"], "folder_name": f.get("name") or f["id"],
              "modified_at": utc(parse_time(f.get("modifiedTime")))} for f in resp.get("files", [])]
    items.sort(key=lambda i: (i["folder_name"].casefold(), i["folder_id"]))
    return {"parent": parent, "items": items, "next_page_token": resp.get("nextPageToken") or None}


@router.post("/watch", response_model=WatchResponse, responses=_ERRORS)
def watch(body: WatchRequest, settings: Settings = Depends(get_settings), session: Session = Depends(get_session),
          factory: DriveApiFactory = Depends(drive_api_factory_dep)):
    drive = _connected_drive(settings, session, factory)
    try:
        meta = _folder_meta(drive, body.folder_id, "id, name, mimeType, trashed")
    except SourceAuthError as e:
        raise drive.rejected(e.message) from None
    except SourceError as e:
        raise _drive_error(e) from None
    if meta.get("trashed"):
        raise not_found(f"Drive folder {body.folder_id} is in the trash.")
    if meta.get("mimeType") != FOLDER_MIME:
        raise validation_error("body.folder_id", "not a folder")
    # Store Drive's real id (for "root" too): changes are matched against file parents.
    folder_id, folder_name = meta["id"], meta.get("name") or meta["id"]
    session.execute(text("INSERT INTO sync_state (id, source) VALUES (1, 'drive') ON CONFLICT (id) DO NOTHING"))
    requested_at = session.execute(text("""
        UPDATE sync_state SET source = 'drive', folder_id = :fid, folder_name = :fname, page_token = NULL,
               last_error = NULL, reconcile_requested_at = now()
        WHERE id = 1 RETURNING reconcile_requested_at
    """), {"fid": folder_id, "fname": folder_name}).scalar_one()
    session.commit()
    log.info("watched folder set", extra={"event": "drive_watch", "folder_id": folder_id})
    return {"folder": {"source": "drive", "folder_id": folder_id, "folder_name": folder_name},
            "reconcile_requested_at": utc(requested_at)}


__all__ = ["router", "drive_api_factory_dep"]

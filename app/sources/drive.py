"""DriveSource: the watched Google Drive folder (FR-4), per the `app/sources/base.py`
contract. Non-recursive: only files whose `parents` include the folder.

- `list_files`: checks the folder still exists (and refreshes its name), then
  `files.list` with `'<folder>' in parents and trashed = false`, paginated. Subfolders
  are skipped (no recursion); every other file is returned, and the sync records
  unsupported types (e.g. Google Docs) as `unsupported`.
- `start_cursor`: `changes.getStartPageToken`.
- `changes_since`: `changes.list` from the token, all pages, keeping files whose
  parents include the folder. Removed (deleted / access lost), trashed, or moved out
  of the folder = `removed_ids`. Returns `newStartPageToken` as the cursor; the caller
  commits it in the same transaction as the upserts. An invalid/expired token falls
  back to a full listing (`ChangeSet.complete`).
- `fetch`: `files.get_media`; Google Sheets via `files.export` as `text/csv` (Drive
  exports the first sheet). Other Google-native types are unsupported.
- md5: Drive's `md5Checksum`; Google-native files have none, so the stand-in
  `drive-version:<version>` is used (it changes on every edit; the pipeline's
  md5-of-bytes check only runs for 32-hex checksums).
- MIME: Drive's `mimeType` when supported; a generic type (e.g. an `.eml` uploaded as
  `application/octet-stream`) falls back to the file extension.

Stateless (PRD §2, FR-2): built per iteration by `resolve_source` from Postgres. It
keeps the refresh token encrypted and decrypts it on the first Drive call; the client
(and any access token) lives only as long as this object.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.extraction.content import is_supported, mime_for_filename
from app.google.credentials import CredentialsError, decrypt_token, invalidate_credentials
from app.google.drive_api import (
    FOLDER_MIME,
    GOOGLE_APPS_PREFIX,
    SHEET_MIME,
    DriveApi,
    DriveApiFactory,
    DriveInvalidArgument,
    DriveNotFound,
    default_api_factory,
)
from app.sources.base import ChangeSet, SourceAuthError, SourceError, SourceFile, SourceFileGone

log = logging.getLogger("app.sources.drive")

CSV_MIME = "text/csv"
FILE_FIELDS = "id, name, mimeType, md5Checksum, modifiedTime, version, webViewLink, parents, trashed"
LIST_FIELDS = f"nextPageToken, files({FILE_FIELDS})"
CHANGE_FIELDS = f"nextPageToken, newStartPageToken, changes(changeType, removed, fileId, file({FILE_FIELDS}))"
PAGE_SIZE = 1000
DECRYPT_ERROR_MESSAGE = ("The stored Google credentials cannot be decrypted (was FERNET_KEY changed?). "
                         "Reconnect Google Drive.")


def drive_query_literal(value: str) -> str:
    """A Drive query string literal: backslash and single quote escaped."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_source_file(f: dict) -> SourceFile | None:
    """A Drive file resource -> SourceFile; None for folders."""
    mime = f.get("mimeType") or "application/octet-stream"
    if mime == FOLDER_MIME:
        return None
    name = f.get("name") or f["id"]
    stand_in = f"drive-version:{f.get('version', 0)}"
    if mime == SHEET_MIME:
        mime_out, md5 = CSV_MIME, stand_in
    else:
        mime_out = mime
        if not is_supported(mime) and not mime.startswith(GOOGLE_APPS_PREFIX):
            by_ext = mime_for_filename(name)
            if is_supported(by_ext):
                mime_out = by_ext
        md5 = f.get("md5Checksum") or stand_in
    return SourceFile(file_id=f["id"], name=name, mime_type=mime_out, md5=md5,
                      modified_at=parse_time(f.get("modifiedTime")), web_view_link=f.get("webViewLink"))


class DriveSource:
    name = "drive"

    def __init__(self, settings: Settings, *, folder_id: str, folder_name: str | None, encrypted_token: str,
                 api_factory: DriveApiFactory | None = None):
        self.settings = settings
        self.folder_id = folder_id
        self.folder_name = folder_name
        self._encrypted_token = encrypted_token
        self._api_factory = api_factory or default_api_factory
        self._api: DriveApi | None = None

    def __repr__(self) -> str:
        return f"DriveSource(folder_id={self.folder_id!r})"

    @classmethod
    def from_db(cls, settings: Settings, session: Session, *,
                api_factory: DriveApiFactory | None = None) -> DriveSource | None:
        """None when not connected or no folder is watched (the worker idles)."""
        row = session.execute(text("""
            SELECT c.refresh_token_encrypted, s.folder_id, s.folder_name
            FROM oauth_credentials c LEFT JOIN sync_state s ON s.id = 1 WHERE c.id = 1
        """)).first()
        if row is None or not row.folder_id:
            return None
        return cls(settings, folder_id=row.folder_id, folder_name=row.folder_name,
                   encrypted_token=row.refresh_token_encrypted, api_factory=api_factory)

    # ---- credentials ------------------------------------------------------------------
    @property
    def credential_marker(self) -> str:
        """The encrypted token this source uses (guards `invalidate_credentials`)."""
        return self._encrypted_token

    @property
    def api(self) -> DriveApi:
        if self._api is None:
            try:
                refresh_token = decrypt_token(self.settings, self._encrypted_token)
            except CredentialsError:
                raise SourceAuthError(DECRYPT_ERROR_MESSAGE) from None
            self._api = self._api_factory(self.settings, refresh_token)
        return self._api

    def on_auth_failure(self, session: Session, message: str) -> bool:
        """Drop the rejected credentials (guarded) and record `message`. Does not commit."""
        removed = invalidate_credentials(session, self._encrypted_token, message)
        log.warning("google credentials rejected; worker idles until reconnected",
                    extra={"event": "drive_auth_failed", "credentials_removed": removed})
        return removed

    # ---- listing --------------------------------------------------------------------------
    def _check_folder(self) -> None:
        try:
            meta = self.api.files_get(self.folder_id, fields="id, name, mimeType, trashed")
        except DriveNotFound:
            raise SourceError("The watched Drive folder no longer exists or is not accessible. "
                              "Choose another folder.") from None
        if meta.get("trashed") or meta.get("mimeType") != FOLDER_MIME:
            raise SourceError("The watched Drive folder is in the trash or is not a folder. Choose another folder.")
        if meta.get("name"):
            self.folder_name = meta["name"]

    def list_files(self) -> list[SourceFile]:
        self._check_folder()
        q = f"{drive_query_literal(self.folder_id)} in parents and trashed = false"
        out: list[SourceFile] = []
        token = None
        while True:
            resp = self.api.files_list(q=q, fields=LIST_FIELDS, page_token=token, page_size=PAGE_SIZE)
            for f in resp.get("files", []):
                if self.folder_id not in (f.get("parents") or [self.folder_id]) or f.get("trashed"):
                    continue  # defensive: the query already guarantees both
                sf = to_source_file(f)
                if sf is not None:
                    out.append(sf)
            token = resp.get("nextPageToken")
            if not token:
                return out

    def start_cursor(self) -> str:
        return self.api.start_page_token()

    def changes_since(self, cursor: str) -> ChangeSet:
        changed: dict[str, SourceFile] = {}
        removed: dict[str, None] = {}  # ordered set
        token = cursor
        while True:
            try:
                resp = self.api.changes_list(page_token=token, fields=CHANGE_FIELDS, page_size=PAGE_SIZE)
            except (DriveInvalidArgument, DriveNotFound):
                if token != cursor:
                    raise
                # The stored token is invalid or expired: fall back to a full listing.
                log.warning("drive page token rejected; falling back to a full listing",
                            extra={"event": "drive_cursor_invalid"})
                new_cursor = self.start_cursor()
                return ChangeSet(changed=self.list_files(), removed_ids=[], cursor=new_cursor, complete=True)
            for ch in resp.get("changes", []):
                if ch.get("changeType", "file") != "file":
                    continue  # shared-drive metadata changes
                f = ch.get("file") or {}
                fid = ch.get("fileId") or f.get("id")
                if not fid:
                    continue
                gone = ch.get("removed") or not f or f.get("trashed") or self.folder_id not in (f.get("parents") or [])
                sf = None if gone else to_source_file(f)
                if sf is None:  # removed, trashed, moved out, or a (sub)folder
                    changed.pop(fid, None)
                    removed[fid] = None
                else:
                    removed.pop(fid, None)
                    changed[fid] = sf
            if resp.get("newStartPageToken"):
                return ChangeSet(changed=list(changed.values()), removed_ids=list(removed),
                                 cursor=resp["newStartPageToken"])
            token = resp.get("nextPageToken")
            if not token:  # should not happen; keep the old cursor rather than lose changes
                raise SourceError("Drive changes.list returned neither nextPageToken nor newStartPageToken")

    # ---- content -------------------------------------------------------------------------
    def fetch(self, file_id: str) -> bytes:
        try:
            meta = self.api.files_get(file_id, fields="id, mimeType, trashed")
            if meta.get("trashed"):
                raise SourceFileGone(f"{file_id} is in the Drive trash")
            mime = meta.get("mimeType") or ""
            if mime == SHEET_MIME:
                return self.api.export(file_id, CSV_MIME)
            if mime.startswith(GOOGLE_APPS_PREFIX):
                raise SourceError(f"Google file type {mime} is not supported")
            return self.api.get_media(file_id)
        except DriveNotFound:
            raise SourceFileGone(f"{file_id} no longer exists in Google Drive") from None


__all__ = ["DriveSource", "to_source_file", "drive_query_literal", "parse_time"]

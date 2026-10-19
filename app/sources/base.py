"""File source interface (FR-4). The worker's sync code only talks to this.

Implementations:
- `LocalFolderSource` (demo mode, FR-14): `./sample_docs`, non-recursive.
- `DriveSource` (T7): the watched Drive folder. `list_files` = `files.list` (paginated,
  non-recursive, trashed excluded); `start_cursor` = `changes.getStartPageToken`;
  `changes_since` = `changes.list` filtered to the watched folder; `fetch` =
  `files.get_media` (or `files.export` text/csv for Google Sheets, reported with
  mime_type 'text/csv' and a stand-in md5 such as `drive-version:<n>`).

Contract for every implementation:
- `SourceFile.file_id` is stable for the life of the file (stored as
  `documents.drive_file_id`); `md5` changes iff the content changes (never None).
- `changes_since(cursor)` returns the files added or changed since `cursor`, the ids of
  files removed from the folder (deleted, trashed or moved out), and the new cursor.
  Returning a file that did not actually change is harmless (upserts are idempotent).
- Raise `TransientSourceError` for retryable failures (network, 429, 5xx),
  `SourceFileGone` when `fetch` finds the file no longer exists, and `SourceAuthError`
  when the stored credentials are rejected (revoked/expired refresh token). On an auth
  error the caller calls `source.on_auth_failure(session, message)` (when the source
  has it) and stops using the source until the user reconnects.
- `ChangeSet.complete = True` means `changed` is the full current listing (the cursor
  was invalid and the source fell back to a listing), so the caller treats every file
  not in it as removed, exactly like a reconcile.
- Hold no state between calls beyond configuration (PRD §2); the cursor is persisted
  by the caller in `sync_state.page_token`, in the same transaction as the upserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class SourceError(Exception):
    """Base class. `message` is safe to store and log (no contents, no secrets)."""

    retryable = False

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TransientSourceError(SourceError):
    retryable = True


class SourceFileGone(SourceError):
    retryable = False


class SourceAuthError(SourceError):
    """The source's credentials were rejected (e.g. Google `invalid_grant`). Retrying
    cannot help until the user reconnects."""

    retryable = False


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    name: str
    mime_type: str
    md5: str
    modified_at: datetime | None = None
    web_view_link: str | None = None


@dataclass
class ChangeSet:
    changed: list[SourceFile] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    cursor: str | None = None
    complete: bool = False  # True: `changed` is the full listing (see module docstring)


@runtime_checkable
class FileSource(Protocol):
    # documents.source / sync_state.source value: 'local' or 'drive'
    name: str
    # Shown as sync_state.folder_id / folder_name
    folder_id: str | None
    folder_name: str | None

    def list_files(self) -> list[SourceFile]: ...

    def fetch(self, file_id: str) -> bytes: ...

    def start_cursor(self) -> str: ...

    def changes_since(self, cursor: str) -> ChangeSet: ...

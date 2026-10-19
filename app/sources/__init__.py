"""File sources (FR-4). `resolve_source()` picks the active source for this iteration.

Demo mode: `LocalFolderSource(./sample_docs)`. Drive mode: `DriveSource` built from
`oauth_credentials` + `sync_state.folder_id` (T7), or None when either is missing, in
which case the worker idles and re-checks each tick.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.sources.base import (
    ChangeSet,
    FileSource,
    SourceAuthError,
    SourceError,
    SourceFile,
    SourceFileGone,
    TransientSourceError,
)
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource

__all__ = [
    "ChangeSet", "FileSource", "LocalFolderSource", "SourceAuthError", "SourceError",
    "SourceFile", "SourceFileGone",
    "TransientSourceError", "resolve_source", "DEFAULT_LOCAL_DIR",
]


def resolve_source(settings: Settings, session: Session) -> FileSource | None:
    """The source to sync from, or None when there is nothing to watch yet.

    Demo mode: the local `./sample_docs` folder. Drive mode: a DriveSource built from
    `oauth_credentials` + `sync_state.folder_id`, one per iteration (called on every
    sync tick and job claim). It holds the refresh token only in its encrypted form and
    decrypts it lazily on its first Drive call, so tokens never outlive the iteration
    (FR-2) and an idle tick makes no Google request. None when not connected or no
    folder is watched.
    """
    if settings.demo_mode:
        return LocalFolderSource(DEFAULT_LOCAL_DIR)
    from app.sources.drive import DriveSource  # lazy: app.google imports app.sources.base

    return DriveSource.from_db(settings, session)

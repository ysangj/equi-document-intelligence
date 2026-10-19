"""Google OAuth (FR-2) and folder selection (FR-3)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, DocumentSource, Mode, Timestamp

# Fixed UI origin for OAuth redirects. The callback is served on :8000 (it is the
# GOOGLE_REDIRECT_URI), so redirects back to the UI must be absolute (§5: web runs on 4200).
UI_BASE_URL = "http://localhost:4200"
UI_CONNECT_SUCCESS_PATH = "/connect/folder"
UI_CONNECT_ERROR_PATH = "/connect"  # with ?error=<OAuthErrorCode>

OAuthErrorCode = Literal[
    "access_denied",  # the user cancelled on the Google consent screen
    "state_mismatch",  # missing or invalid state cookie (stale tab, cookies blocked)
    "token_exchange_failed",  # Google rejected the code exchange
    "missing_refresh_token",  # Google returned no refresh token
    "insufficient_scope",  # drive.readonly was not granted
    "demo_mode",  # OAuth is not available in demo mode
]


class FolderRef(ApiModel):
    source: DocumentSource
    folder_id: str | None  # Drive folder id; null in demo mode
    folder_name: str | None  # Drive folder name; "sample_docs" in demo mode


class AuthStatusResponse(ApiModel):
    mode: Mode
    connected: bool
    google_email: str | None
    scopes: list[str]
    connected_at: Timestamp | None
    folder: FolderRef | None  # null when connected but no folder chosen yet


class DisconnectResponse(ApiModel):
    connected: Literal[False]


class DriveFoldersQuery(ApiModel):
    parent: str = Field("root", min_length=1)  # "root" = My Drive
    page_token: str | None = None


class DriveFolderParent(ApiModel):
    folder_id: str  # "root" for My Drive
    folder_name: str  # "My Drive" for root
    parent_id: str | None  # for an "Up" button; null at root
    is_root: bool


class DriveFolder(ApiModel):
    folder_id: str
    folder_name: str
    modified_at: Timestamp | None


class DriveFoldersResponse(ApiModel):
    parent: DriveFolderParent
    items: list[DriveFolder]  # direct child folders only, sorted by name (case-insensitive)
    next_page_token: str | None


class WatchRequest(ApiModel):
    folder_id: str = Field(min_length=1)


class WatchResponse(ApiModel):
    folder: FolderRef
    reconcile_requested_at: Timestamp

"""Ingestion status (FR-12), progress, sync triggers (FR-4) and reprocess."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from app.schemas.common import (
    ApiModel,
    DocumentSource,
    DocumentStatus,
    DocumentType,
    ExtractionSource,
    ListQuery,
    Mode,
    Timestamp,
)


class DocumentsQuery(ListQuery):
    SORTABLE: ClassVar[tuple[str, ...]] = (
        "document_name", "document_type", "status", "attempts", "processed_at", "created_at",
        "drive_modified_at", "total_tokens", "search_indexed", "version",
    )
    DEFAULT_SORT: ClassVar[str] = "-created_at"

    status: list[DocumentStatus] = Field(default_factory=list)
    document_type: list[DocumentType] = Field(default_factory=list)
    q: str | None = Field(None, max_length=200)  # case-insensitive substring of document_name
    search_indexed: bool | None = None


class DocumentItem(ApiModel):
    # v_documents columns
    document_id: int
    document_name: str
    document_type: DocumentType | None
    document_type_confidence: float | None
    mime_type: str
    status: DocumentStatus
    version: int
    source: DocumentSource
    drive_file_id: str  # for local demo files, the path relative to ./sample_docs
    web_view_link: str | None
    search_indexed: bool
    attempts: int
    last_error: str | None
    drive_modified_at: Timestamp | None
    processed_at: Timestamp | None
    created_at: Timestamp
    # API-computed, from the latest extraction of this document
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    extraction_model: str | None
    extraction_source: ExtractionSource | None
    latency_ms: int | None
    # API-computed
    value_count: int  # returns + fund_facts + statements + holdings rows from this document
    review_count: int  # open review items whose document is this one
    can_reprocess: bool  # false while pending/processing, and for unsupported files


class StatusCounts(ApiModel):
    pending: int
    processing: int
    done: int
    needs_review: int
    failed: int
    unsupported: int


class TokenTotals(ApiModel):
    input_tokens: int
    output_tokens: int


class SyncInfo(ApiModel):
    source: DocumentSource
    folder_id: str | None
    folder_name: str | None
    last_synced_at: Timestamp | None
    last_reconciled_at: Timestamp | None
    last_success_at: Timestamp | None  # max(last_synced_at, last_reconciled_at): the header's "last successful sync"
    last_error: str | None
    sync_requested_at: Timestamp | None
    reconcile_requested_at: Timestamp | None
    sync_pending: bool  # sync_requested_at is set (the worker clears it when done)
    reconcile_pending: bool


class ConnectionInfo(ApiModel):
    connected: bool
    google_email: str | None


class ProgressResponse(ApiModel):
    """GET /api/progress: the global progress bar, the review badge and the Ingestion header."""

    mode: Mode
    counts: StatusCounts
    total: int  # all visible documents (removed are excluded)
    processed: int  # done + needs_review: the "14 / 20 processed" numerator
    finished: int  # processed + failed + unsupported (terminal states)
    in_flight: int  # pending + processing
    active: bool  # in_flight > 0: poll every 3 s, otherwise every 15 s
    review_open: int  # open review items (same count as GET /api/review total)
    tokens: TokenTotals  # sum over the latest extraction of each visible document
    sync: SyncInfo
    connection: ConnectionInfo
    generated_at: Timestamp


class SyncRequestResponse(ApiModel):
    """POST /api/sync and POST /api/sync/reconcile (202)."""

    kind: Literal["sync", "reconcile"]
    mode: Mode
    requested_at: Timestamp
    already_pending: bool  # a request of this kind was already waiting; requested_at is refreshed
    last_success_at: Timestamp | None


class ReprocessResponse(ApiModel):
    """POST /api/documents/{id}/reprocess (202)."""

    document_id: int
    status: Literal["pending"]
    attempts: int  # reset to 0
    queued_at: Timestamp

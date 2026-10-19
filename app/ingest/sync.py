"""Reconcile and incremental sync (FR-4), source-agnostic.

Both run inside one caller-owned transaction, so the cursor (`sync_state.page_token`)
and the document upserts commit atomically (§12). A transaction-scoped advisory lock
serialises syncs across worker processes.

Upsert rules, keyed on (drive_file_id, md5_checksum) (§12 idempotency):
- new file id                        -> version 1, `pending` (or `unsupported`)
- known id, new checksum             -> clear the old latest row's `is_latest` (flushed
                                        first, same transaction), insert version n+1
- known id, same checksum, latest    -> nothing (metadata such as a rename is updated)
- ... same checksum, but `removed`   -> back to `pending` (file re-added to the folder)
- ... same checksum, older version   -> that version becomes latest again and `pending`
                                        (a revert; (id, md5) is unique, T2 note)
- file gone from the folder          -> latest row `removed` (its ids are returned in
                                        `SyncReport.removed_document_ids`; the worker
                                        recomputes canonical names for them after the
                                        commit, `app.ingest.canonical`)

Removal is scoped to the active source (`documents.source`), so demo-mode syncs never
touch Drive documents and vice versa. Switching the watched Drive folder therefore marks
the old folder's documents removed on the next reconcile (T3 decision 7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.extraction.content import is_supported
from app.models import Document, SyncState
from app.sources.base import FileSource, SourceFile

log = logging.getLogger("app.ingest.sync")

SYNC_LOCK_KEY = 0x646F6331  # pg_advisory_xact_lock key ("doc1")


@dataclass
class SyncReport:
    kind: str  # 'reconcile' | 'sync' | 'stale' (nothing done: the watched folder changed meanwhile)
    new: int = 0
    versions: int = 0
    unchanged: int = 0
    restored: int = 0
    removed: int = 0
    unsupported: int = 0
    seen: int = 0
    document_ids: list[int] = field(default_factory=list)
    removed_document_ids: list[int] = field(default_factory=list)  # canonical names are recomputed for these

    def as_log(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("document_ids", "removed_document_ids")}


def lock_sync(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SYNC_LOCK_KEY})


def get_sync_state(session: Session, *, lock: bool = False) -> SyncState:
    """The singleton row (id = 1), created if missing."""
    session.execute(text("INSERT INTO sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
    stmt = select(SyncState).where(SyncState.id == 1)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one()


def _reset_for_queue(doc: Document, status: str) -> None:
    doc.status = status
    doc.attempts = 0
    doc.last_error = None
    doc.next_attempt_at = None
    doc.claimed_at = None


def upsert_file(session: Session, sf: SourceFile, source_name: str) -> tuple[str, Document]:
    """Apply the upsert rules for one listed file. Returns (outcome, document)."""
    rows = list(
        session.execute(
            select(Document).where(Document.drive_file_id == sf.file_id).with_for_update()
        ).scalars()
    )
    queued_status = "pending" if is_supported(sf.mime_type) else "unsupported"
    same = next((r for r in rows if r.md5_checksum == sf.md5), None)
    latest = next((r for r in rows if r.is_latest), None)

    if same is not None:
        same.name = sf.name
        same.web_view_link = sf.web_view_link if sf.web_view_link is not None else same.web_view_link
        if sf.modified_at is not None:
            same.drive_modified_at = sf.modified_at
        if same.is_latest:
            if same.status == "removed":
                _reset_for_queue(same, queued_status)
                return "restored", same
            return "unchanged", same
        if latest is not None:
            latest.is_latest = False
            session.flush()  # partial unique index: one latest row per file id
        same.is_latest = True
        _reset_for_queue(same, queued_status)
        return "restored", same

    if latest is not None:
        latest.is_latest = False
        session.flush()
    version = max((r.version for r in rows), default=0) + 1
    doc = Document(
        source=source_name,
        drive_file_id=sf.file_id,
        name=sf.name,
        mime_type=sf.mime_type,
        md5_checksum=sf.md5,
        version=version,
        is_latest=True,
        status=queued_status,
        web_view_link=sf.web_view_link,
        drive_modified_at=sf.modified_at,
    )
    session.add(doc)
    session.flush()
    return ("new" if version == 1 else "version"), doc


def _count(report: SyncReport, outcome: str, doc: Document) -> None:
    report.seen += 1
    if outcome == "new":
        report.new += 1
    elif outcome == "version":
        report.versions += 1
    elif outcome == "restored":
        report.restored += 1
    else:
        report.unchanged += 1
    if outcome != "unchanged":
        report.document_ids.append(doc.id)
        if doc.status == "unsupported":
            report.unsupported += 1


def mark_removed(session: Session, source_name: str, *, keep_ids: set[str] | None = None,
                 remove_ids: list[str] | None = None) -> list[int]:
    """Mark latest, visible documents `removed`: either every one not in `keep_ids`
    (reconcile) or exactly `remove_ids` (incremental). Returns their document ids."""
    stmt = (
        update(Document)
        .where(Document.source == source_name, Document.is_latest.is_(True), Document.status != "removed")
        .values(status="removed", claimed_at=None, next_attempt_at=None)
    )
    if keep_ids is not None:
        if keep_ids:
            stmt = stmt.where(Document.drive_file_id.not_in(sorted(keep_ids)))
    elif remove_ids:
        stmt = stmt.where(Document.drive_file_id.in_(remove_ids))
    else:
        return []
    stmt = stmt.returning(Document.id).execution_options(synchronize_session=False)
    return sorted(i for (i,) in session.execute(stmt))


def _finish(state: SyncState, source: FileSource, cursor: str | None, seen_sync: datetime | None,
            seen_reconcile: datetime | None, *, reconcile: bool, now: datetime) -> None:
    state.source = source.name
    if source.name == "local":
        state.folder_id = source.folder_id
        state.folder_name = source.folder_name
    elif source.folder_name and source.folder_id == state.folder_id:
        state.folder_name = source.folder_name  # a Drive folder renamed since it was picked
    state.page_token = cursor
    state.last_error = None
    if reconcile:
        state.last_reconciled_at = now
        # Clear the request only if the api did not refresh it while we ran.
        if state.reconcile_requested_at is not None and state.reconcile_requested_at == seen_reconcile:
            state.reconcile_requested_at = None
    else:
        state.last_synced_at = now
    # A reconcile also satisfies a pending sync request.
    if state.sync_requested_at is not None and state.sync_requested_at == seen_sync:
        state.sync_requested_at = None


def _stale(state: SyncState, source: FileSource) -> bool:
    """The api switched the watched Drive folder after this source was built (the folder
    is api-owned for Drive). The caller retries on its next tick with a fresh source."""
    return source.name != "local" and state.folder_id != source.folder_id


def reconcile(session: Session, source: FileSource) -> SyncReport:
    """Full catch-up: list everything, upsert, mark missing files removed, record a
    fresh cursor (taken before listing so no change in between is missed)."""
    lock_sync(session)
    state = get_sync_state(session, lock=True)
    if _stale(state, source):
        return SyncReport(kind="stale")
    seen_sync, seen_reconcile = state.sync_requested_at, state.reconcile_requested_at
    cursor = source.start_cursor()
    files = source.list_files()
    report = SyncReport(kind="reconcile")
    for sf in files:
        outcome, doc = upsert_file(session, sf, source.name)
        _count(report, outcome, doc)
    report.removed_document_ids = mark_removed(session, source.name, keep_ids={f.file_id for f in files})
    report.removed = len(report.removed_document_ids)
    now = session.execute(select(func.now())).scalar_one()
    _finish(state, source, cursor, seen_sync, seen_reconcile, reconcile=True, now=now)
    session.flush()
    return report


def incremental_sync(session: Session, source: FileSource) -> SyncReport:
    """Changes since the stored cursor. Falls back to a reconcile when there is no
    cursor yet or the stored cursor belongs to a different source."""
    lock_sync(session)
    state = get_sync_state(session, lock=True)
    if _stale(state, source):
        return SyncReport(kind="stale")
    if not state.page_token or state.source != source.name:
        return reconcile(session, source)
    seen_sync, seen_reconcile = state.sync_requested_at, state.reconcile_requested_at
    changes = source.changes_since(state.page_token)
    report = SyncReport(kind="sync")
    for sf in changes.changed:
        outcome, doc = upsert_file(session, sf, source.name)
        _count(report, outcome, doc)
    if changes.complete:  # the source fell back to a full listing (invalid cursor)
        report.removed_document_ids = mark_removed(session, source.name,
                                                   keep_ids={f.file_id for f in changes.changed})
    else:
        report.removed_document_ids = mark_removed(session, source.name, remove_ids=changes.removed_ids)
    report.removed = len(report.removed_document_ids)
    now = session.execute(select(func.now())).scalar_one()
    _finish(state, source, changes.cursor, seen_sync, seen_reconcile, reconcile=False, now=now)
    session.flush()
    return report


def record_sync_error(session: Session, message: str) -> None:
    state = get_sync_state(session, lock=True)
    state.last_error = message[:500]
    session.flush()

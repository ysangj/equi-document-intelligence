"""Postgres-backed job queue over `documents` (FR-5, §12).

A job is a latest-version document in `pending` whose `next_attempt_at` has passed.
- `claim_next` takes one with `SELECT ... FOR UPDATE SKIP LOCKED`, sets `processing`,
  `claimed_at = now()`, increments `attempts`. Concurrent claimers never get the same row.
- A running job refreshes `claimed_at` (heartbeat). A `processing` row whose
  `claimed_at` is older than the stale timeout belonged to a worker that died; it is
  reclaimed by the next `claim_next` (or failed, if it already used MAX_ATTEMPTS).
- Finishing is fenced: a job writes its result only if the row is still `processing`
  with the attempt number it claimed. A job that lost its claim (reclaimed after a
  stall, removed by a sync, re-queued) is discarded, so nothing is written twice.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Document

STALE_AFTER_SECONDS = 60.0
HEARTBEAT_SECONDS = 10.0
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 300.0


@dataclass(frozen=True)
class Claim:
    document_id: int
    drive_file_id: str
    name: str
    mime_type: str
    md5_checksum: str
    source: str
    attempt: int

    @property
    def job_id(self) -> str:
        return f"doc{self.document_id}-a{self.attempt}"


_CLAIM_SQL = text("""
WITH c AS (
  SELECT id FROM documents
  WHERE is_latest AND (CAST(:source AS text) IS NULL OR source = :source) AND (
        (status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
     OR (status = 'processing' AND claimed_at < now() - make_interval(secs => :stale) AND attempts < :max_attempts)
  )
  ORDER BY next_attempt_at NULLS FIRST, id
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
UPDATE documents d
SET status = 'processing', claimed_at = now(), attempts = d.attempts + 1, next_attempt_at = NULL
FROM c WHERE d.id = c.id
RETURNING d.id, d.drive_file_id, d.name, d.mime_type, d.md5_checksum, d.source, d.attempts
""")

_FAIL_EXHAUSTED_STALE_SQL = text("""
UPDATE documents
SET status = 'failed', claimed_at = NULL, next_attempt_at = NULL,
    last_error = 'processing was interrupted (worker stopped or crashed); gave up after '
                 || attempts || ' attempts'
WHERE status = 'processing' AND claimed_at < now() - make_interval(secs => :stale)
  AND attempts >= :max_attempts
""")


def fail_exhausted_stale(session: Session, *, max_attempts: int, stale_after: float = STALE_AFTER_SECONDS) -> int:
    return session.execute(_FAIL_EXHAUSTED_STALE_SQL, {"stale": stale_after, "max_attempts": max_attempts}).rowcount or 0


def claim_next(session: Session, *, max_attempts: int, stale_after: float = STALE_AFTER_SECONDS,
               source_name: str | None = None) -> Claim | None:
    """Claim one job in the caller's transaction (commit right after). With
    `source_name`, only documents from that source (the active one) are claimed."""
    row = session.execute(
        _CLAIM_SQL, {"stale": stale_after, "max_attempts": max_attempts, "source": source_name}
    ).first()
    if row is None:
        return None
    return Claim(
        document_id=row.id, drive_file_id=row.drive_file_id, name=row.name, mime_type=row.mime_type,
        md5_checksum=row.md5_checksum, source=row.source, attempt=row.attempts,
    )


def heartbeat(session: Session, claims: list[Claim]) -> int:
    if not claims:
        return 0
    n = 0
    for c in claims:
        n += session.execute(
            text("UPDATE documents SET claimed_at = now() WHERE id = :id AND status = 'processing' AND attempts = :a"),
            {"id": c.document_id, "a": c.attempt},
        ).rowcount or 0
    return n


def lock_if_still_claimed(session: Session, claim: Claim) -> Document | None:
    """Row-lock the document if this claim still owns it, else None (fencing)."""
    doc = session.get(Document, claim.document_id, with_for_update=True, populate_existing=True)
    if doc is None or doc.status != "processing" or doc.attempts != claim.attempt:
        return None
    return doc


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter: 5s, 10s, 20s, ... capped at 300s; at least
    the server's retry-after when given."""
    base = min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)), BACKOFF_MAX_SECONDS)
    delay = base * random.uniform(0.8, 1.2)
    if retry_after:
        delay = max(delay, min(float(retry_after), BACKOFF_MAX_SECONDS))
    return delay


def schedule_retry_or_fail(session: Session, doc: Document, claim: Claim, *, message: str, retryable: bool,
                           max_attempts: int, retry_after: float | None = None) -> str:
    """After a failed attempt: `pending` with next_attempt_at (retryable and attempts
    left) or `failed`. Returns the new status."""
    doc.claimed_at = None
    if retryable and claim.attempt < max_attempts:
        delay = backoff_seconds(claim.attempt, retry_after)
        doc.status = "pending"
        doc.last_error = message[:1000]
        session.flush()
        session.execute(
            text("UPDATE documents SET next_attempt_at = now() + make_interval(secs => :d) WHERE id = :id"),
            {"d": delay, "id": doc.id},
        )
        return "pending"
    doc.status = "failed"
    doc.next_attempt_at = None
    suffix = f" (gave up after {claim.attempt} attempts)" if retryable else ""
    doc.last_error = (message + suffix)[:1000]
    return "failed"


def release_claims(session: Session, claims: list[Claim]) -> int:
    """Graceful shutdown: hand in-flight jobs back without charging the attempt."""
    n = 0
    for c in claims:
        n += session.execute(
            text("UPDATE documents SET status = 'pending', claimed_at = NULL, attempts = GREATEST(attempts - 1, 0) "
                 "WHERE id = :id AND status = 'processing' AND attempts = :a"),
            {"id": c.document_id, "a": c.attempt},
        ).rowcount or 0
    return n

"""One job end to end: fetch bytes -> extract -> persist (FR-5).

The file bytes live only in memory for the duration of the job (FR-5); the extracted
text is persisted in `documents.text_content`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import traceback
from collections.abc import Callable, Iterable

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.extraction.base import ExtractionError, ExtractionResult, Extractor
from app.extraction.content import text_content_and_source
from app.ingest import postprocess, queue
from app.ingest.queue import Claim, lock_if_still_claimed, schedule_retry_or_fail
from app.models import Extraction as ExtractionRow
from app.sources.base import FileSource, SourceAuthError, SourceError

log = logging.getLogger("app.ingest.pipeline")

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def scrub(message: str, secrets: Iterable[str] = ()) -> str:
    """Remove secret values from an error message (defense in depth; messages are
    built without contents or secrets in the first place)."""
    for s in secrets:
        if s and len(s) >= 8 and s in message:
            message = message.replace(s, "[REDACTED]")
    return message


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def run_job(
    engine: Engine,
    claim: Claim,
    source: FileSource,
    extractor: Extractor,
    *,
    max_attempts: int,
    secrets: Iterable[str] = (),
    hook: Callable[..., str] | None = None,
    indexer: Callable[[Engine, int], object] | None = None,
) -> str:
    """Process one claimed document. Never raises for job-level failures; returns the
    resulting status ('done', 'needs_review', 'pending' (retry), 'failed', 'discarded').

    `indexer(engine, document_id)` runs after the commit (FR-7: embeddings). Its
    failure is logged and leaves `search_indexed = false` for the worker's retry."""
    secrets = list(secrets)
    hook = hook or postprocess.process_extraction
    t0 = time.monotonic()
    ctx = {"job_id": claim.job_id, "document_id": claim.document_id, "attempt": claim.attempt}

    # 1. bytes
    try:
        content = source.fetch(claim.drive_file_id)
    except SourceAuthError as e:
        return _hand_back_for_auth(engine, claim, source, scrub(e.message, secrets), ctx, t0)
    except SourceError as e:
        return _fail(engine, claim, scrub(e.message, secrets), e.retryable, max_attempts, ctx, t0)
    except Exception as e:  # noqa: BLE001
        return _fail(engine, claim, f"source error ({type(e).__name__})", True, max_attempts, ctx, t0, exc=e)
    if _HEX32.match(claim.md5_checksum) and hashlib.md5(content).hexdigest() != claim.md5_checksum:
        return _fail(engine, claim, "file changed after it was queued; the next sync will add the new version",
                     True, max_attempts, ctx, t0)

    # 2. extraction
    t_ex = time.monotonic()
    try:
        result = extractor.extract_with_meta(content, claim.mime_type, name=claim.name)
    except ExtractionError as e:
        msg = scrub(e.message, secrets)
        _record_failed_extraction(engine, claim, extractor, msg, _ms(t_ex))
        return _fail(engine, claim, msg, e.retryable, max_attempts, ctx, t0, retry_after=e.retry_after)
    except Exception as e:  # noqa: BLE001 - one bad job never kills the worker
        msg = f"extraction error ({type(e).__name__})"
        _record_failed_extraction(engine, claim, extractor, msg, _ms(t_ex))
        return _fail(engine, claim, msg, True, max_attempts, ctx, t0, exc=e)
    latency_ms = _ms(t_ex)

    # 3. persist (fenced), T5 hook in the same transaction
    try:
        status = _persist(engine, claim, content, result, latency_ms, hook)
    except Exception as e:  # noqa: BLE001
        return _fail(engine, claim, f"post-processing error ({type(e).__name__})", True, max_attempts, ctx, t0, exc=e)
    if status is None:
        log.warning("job lost its claim; result discarded", extra={**ctx, "event": "job_discarded", "status": "discarded"})
        return "discarded"
    if indexer is not None:
        _index_after_commit(indexer, engine, claim.document_id, ctx)
    log.info(
        "job finished",
        extra={
            **ctx, "event": "job_done", "status": status, "latency_ms": _ms(t0),
            "extraction_latency_ms": latency_ms, "extraction_source": result.source, "model": result.model,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "document_type": result.extraction.document_type,
        },
    )
    return status


def _persist(engine: Engine, claim: Claim, content: bytes, result: ExtractionResult, latency_ms: int,
             hook: Callable[..., str]) -> str | None:
    ex = result.extraction
    pages = [p.text for p in sorted(ex.pages, key=lambda p: p.page)]
    text_content, text_source = text_content_and_source(content, claim.mime_type, pages)
    text_content = text_content.replace("\x00", "")
    with Session(engine) as s, s.begin():
        doc = lock_if_still_claimed(s, claim)
        if doc is None:
            return None
        row = ExtractionRow(
            document_id=doc.id,
            source=result.source,
            model=result.model,
            prompt_version=result.prompt_version,
            status="ok",
            raw_json=ex.model_dump(mode="json"),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
        )
        s.add(row)
        s.flush()
        doc.text_content = text_content
        doc.text_source = text_source
        doc.document_type = ex.document_type
        doc.document_type_confidence = max(0.0, min(1.0, ex.document_type_confidence))
        s.flush()
        status = hook(s, doc.id, ex, extraction_id=row.id)
        if status not in ("done", "needs_review"):
            raise ValueError(f"process_extraction returned invalid status {status!r}")
        doc.status = status
        doc.processed_at = s.execute(select(func.now())).scalar_one()
        doc.last_error = None
        doc.claimed_at = None
        doc.next_attempt_at = None
        return status


def _index_after_commit(indexer: Callable[[Engine, int], object], engine: Engine, document_id: int,
                        ctx: dict) -> None:
    try:
        indexer(engine, document_id)
    except Exception as e:  # noqa: BLE001 - FR-7: never fails the job
        log.warning("search indexing failed; will retry", extra={**ctx, "event": "search_index_error",
                                                                 "error_type": type(e).__name__})


def _record_failed_extraction(engine: Engine, claim: Claim, extractor: Extractor, message: str, latency_ms: int) -> None:
    try:
        with Session(engine) as s, s.begin():
            if lock_if_still_claimed(s, claim) is None:
                return
            from app.extraction.schema import PROMPT_VERSION

            s.add(ExtractionRow(
                document_id=claim.document_id, source=getattr(extractor, "source", "claude"),
                model=getattr(extractor, "model", None), prompt_version=PROMPT_VERSION,
                status="failed", error=message[:1000], latency_ms=latency_ms,
            ))
    except Exception:  # noqa: BLE001 - bookkeeping only
        log.warning("could not record failed extraction", extra={"event": "extraction_record_error",
                                                                  "document_id": claim.document_id})


def _hand_back_for_auth(engine: Engine, claim: Claim, source: FileSource, message: str, ctx: dict,
                        t0: float) -> str:
    """The source's credentials were rejected: return the job to `pending` without
    charging the attempt (it is not the document's fault) and let the source drop its
    credentials, so no job is claimed again until the user reconnects."""
    with Session(engine) as s, s.begin():
        released = queue.release_claims(s, [claim])
        if released:
            s.execute(text("UPDATE documents SET last_error = :m WHERE id = :id"),
                      {"m": message[:1000], "id": claim.document_id})
        handler = getattr(source, "on_auth_failure", None)
        if handler is not None:
            handler(s, message)
    log.warning("job handed back: source credentials rejected",
                extra={**ctx, "event": "job_auth_error", "status": "pending", "error": message,
                       "latency_ms": _ms(t0)})
    return "pending" if released else "discarded"


def _fail(engine: Engine, claim: Claim, message: str, retryable: bool, max_attempts: int, ctx: dict, t0: float,
          *, retry_after: float | None = None, exc: BaseException | None = None) -> str:
    extra = {**ctx, "error": message, "retryable": retryable, "latency_ms": _ms(t0)}
    if exc is not None:
        # Frames only: an exception's message could quote document data.
        extra["error_type"] = type(exc).__name__
        extra["tb"] = "".join(traceback.format_tb(exc.__traceback__))[-3000:]
    with Session(engine) as s, s.begin():
        doc = lock_if_still_claimed(s, claim)
        if doc is None:
            log.warning("job failed after losing its claim", extra={**extra, "event": "job_discarded",
                                                                   "status": "discarded"})
            return "discarded"
        status = schedule_retry_or_fail(s, doc, claim, message=message, retryable=retryable,
                                        max_attempts=max_attempts, retry_after=retry_after)
    event = "job_retry" if status == "pending" else "job_failed"
    (log.info if status == "pending" else log.warning)("job attempt failed", extra={**extra, "event": event,
                                                                                    "status": status})
    return status

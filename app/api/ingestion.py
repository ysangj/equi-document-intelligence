"""FR-12 / FR-4: `/api/progress`, `/api/documents`, reprocess, sync and reconcile requests.

The api never syncs or extracts itself (stateless processes, §2): it records the
request in Postgres and the worker picks it up (sync requests within 2 s, T4;
reprocessed documents on its next claim).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Boolean, DateTime, Engine, Integer, Text, column, literal_column, select, text
from sqlalchemy.orm import Session

from app.api.errors import ApiError, not_found
from app.api.params import NoQuery
from app.api.listing import Listing, any_of, conditions, csv_response, now_utc, utc
from app.api.review_queue import open_items, review_count_by_document
from app.config import Settings, get_settings
from app.db import get_engine, get_session
from app.schemas import DocumentItem, DocumentsPage, DocumentsQuery, ProgressResponse, ReprocessResponse
from app.schemas.common import ErrorResponse
from app.schemas.ingestion import SyncRequestResponse

router = APIRouter(prefix="/api", tags=["ingestion"])

STATUS_KEYS = ("pending", "processing", "done", "needs_review", "failed", "unsupported")
DEMO_FOLDER_NAME = "sample_docs"

_ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def _mode(settings: Settings) -> str:
    return "demo" if settings.demo_mode else "drive"


def _sync_row(session: Session) -> dict | None:
    row = session.execute(text("SELECT * FROM sync_state WHERE id = 1")).mappings().first()
    return dict(row) if row else None


def _last_success(row: dict | None):
    if not row:
        return None
    times = [t for t in (row["last_synced_at"], row["last_reconciled_at"]) if t is not None]
    return utc(max(times)) if times else None


def _connection(session: Session, settings: Settings) -> dict:
    if settings.demo_mode:
        return {"connected": False, "google_email": None}
    row = session.execute(text("SELECT google_email FROM oauth_credentials WHERE id = 1")).first()
    return {"connected": row is not None, "google_email": row[0] if row else None}


# ---- progress ----------------------------------------------------------------------------

_TOKENS_SQL = text("""
SELECT COALESCE(sum(e.input_tokens), 0) AS input_tokens, COALESCE(sum(e.output_tokens), 0) AS output_tokens
FROM v_documents d
CROSS JOIN LATERAL (
  SELECT x.input_tokens, x.output_tokens FROM extractions x
  WHERE x.document_id = d.document_id AND x.status = 'ok'
  ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) e
""")


@router.get("/progress", response_model=ProgressResponse)
def progress(_: NoQuery, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    counts = dict.fromkeys(STATUS_KEYS, 0)
    for status, n in session.execute(text("SELECT status, count(*) FROM v_documents GROUP BY status")):
        if status in counts:
            counts[status] = n
    total = sum(counts.values())
    processed = counts["done"] + counts["needs_review"]
    in_flight = counts["pending"] + counts["processing"]
    tokens = session.execute(_TOKENS_SQL).mappings().one()
    row = _sync_row(session)
    demo = settings.demo_mode
    sync = {
        "source": "local" if demo else "drive",
        "folder_id": None if demo else (row or {}).get("folder_id"),
        "folder_name": DEMO_FOLDER_NAME if demo else (row or {}).get("folder_name"),
        "last_synced_at": utc((row or {}).get("last_synced_at")),
        "last_reconciled_at": utc((row or {}).get("last_reconciled_at")),
        "last_success_at": _last_success(row),
        "last_error": (row or {}).get("last_error"),
        "sync_requested_at": utc((row or {}).get("sync_requested_at")),
        "reconcile_requested_at": utc((row or {}).get("reconcile_requested_at")),
    }
    sync["sync_pending"] = sync["sync_requested_at"] is not None
    sync["reconcile_pending"] = sync["reconcile_requested_at"] is not None
    return {
        "mode": _mode(settings),
        "counts": counts,
        "total": total,
        "processed": processed,
        "finished": processed + counts["failed"] + counts["unsupported"],
        "in_flight": in_flight,
        "active": in_flight > 0,
        "review_open": len(open_items(session)),
        "tokens": {"input_tokens": int(tokens["input_tokens"]), "output_tokens": int(tokens["output_tokens"])},
        "sync": sync,
        "connection": _connection(session, settings),
        "generated_at": now_utc(),
    }


# ---- documents ---------------------------------------------------------------------------

_DOCUMENTS_SQL = """
SELECT d.document_id, d.document_name, d.document_type, d.document_type_confidence, d.mime_type, d.status,
       d.version, d.source, d.drive_file_id, d.web_view_link, d.search_indexed, d.attempts, d.last_error,
       d.drive_modified_at, d.processed_at, d.created_at,
       e.input_tokens, e.output_tokens,
       CASE WHEN e.input_tokens IS NULL AND e.output_tokens IS NULL THEN NULL
            ELSE COALESCE(e.input_tokens, 0) + COALESCE(e.output_tokens, 0) END AS total_tokens,
       e.model AS extraction_model, e.source AS extraction_source, e.latency_ms,
       ((SELECT count(*) FROM returns r WHERE r.document_id = d.document_id)
        + (SELECT count(*) FROM fund_facts f WHERE f.document_id = d.document_id)
        + (SELECT count(*) FROM statements s WHERE s.document_id = d.document_id)
        + (SELECT count(*) FROM statement_holdings h JOIN statements s2 ON s2.id = h.statement_id
           WHERE s2.document_id = d.document_id))::int AS value_count
FROM v_documents d
LEFT JOIN LATERAL (
  SELECT x.input_tokens, x.output_tokens, x.model, x.source, x.latency_ms FROM extractions x
  WHERE x.document_id = d.document_id AND x.status = 'ok'
  ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) e ON true
"""

_DOC_COLS = {
    "document_id": Integer, "document_name": Text, "document_type": Text, "status": Text, "attempts": Integer,
    "processed_at": DateTime, "created_at": DateTime, "drive_modified_at": DateTime, "total_tokens": Integer,
    "search_indexed": Boolean, "version": Integer,
}


def _documents_listing(q: DocumentsQuery, review_counts: dict[int, int]) -> Listing:
    sub = text(_DOCUMENTS_SQL).columns(*(column(n, t) for n, t in _DOC_COLS.items())).subquery("docs")
    # all columns of the subquery, by name
    base = select(literal_column("docs.*")).select_from(sub)
    c = sub.c

    def to_item(r: dict) -> dict:
        item = {k: r[k] for k in DocumentItem.model_fields if k in r}
        item["review_count"] = review_counts.get(r["document_id"], 0)
        item["can_reprocess"] = r["status"] not in ("pending", "processing", "unsupported")
        return item

    name_like = None
    if q.q:
        pattern = "%" + q.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        name_like = c.document_name.ilike(pattern, escape="\\")
    listing = Listing(base=base, sortable={k: c[k] for k in DocumentsQuery.SORTABLE}, pk=c.document_id,
                      to_item=to_item, item_model=DocumentItem, name="documents")
    return listing.where(*conditions(
        any_of(c.status, q.status), any_of(c.document_type, q.document_type), name_like,
        None if q.search_indexed is None else c.search_indexed.is_(q.search_indexed),
    ))


@router.get("/documents", response_model=DocumentsPage, responses={200: {"content": {"text/csv": {}}}, **_ERRORS})
def documents(q: Annotated[DocumentsQuery, Query()], session: Session = Depends(get_session),
              engine: Engine = Depends(get_engine)):
    listing = _documents_listing(q, review_count_by_document(open_items(session)))
    if q.format == "csv":
        return csv_response(listing, engine, q)
    return listing.run_page(session, q)


@router.post("/documents/{document_id}/reprocess", status_code=202, response_model=ReprocessResponse,
             responses=_ERRORS)
def reprocess(document_id: Annotated[int, Path(ge=1)], _: NoQuery, session: Session = Depends(get_session)):
    row = session.execute(
        text("SELECT id, name, status FROM documents WHERE id = :id AND is_latest AND status <> 'removed' "
             "FOR UPDATE"), {"id": document_id},
    ).mappings().first()
    if row is None:
        raise not_found(f"Document {document_id} not found.")
    if row["status"] in ("pending", "processing"):
        raise ApiError(409, "document_busy", f"{row['name']} is already queued for processing.")
    if row["status"] == "unsupported":
        raise ApiError(409, "unsupported_document", f"{row['name']} is not a supported file type.")
    queued_at = session.execute(text(
        "UPDATE documents SET status = 'pending', attempts = 0, last_error = NULL, next_attempt_at = now(), "
        "claimed_at = NULL WHERE id = :id RETURNING next_attempt_at"), {"id": document_id}).scalar_one()
    session.commit()
    return {"document_id": document_id, "status": "pending", "attempts": 0, "queued_at": utc(queued_at)}


# ---- sync requests -------------------------------------------------------------------------


def _request(kind: str, session: Session, settings: Settings) -> JSONResponse:
    if not settings.demo_mode:
        if session.execute(text("SELECT 1 FROM oauth_credentials WHERE id = 1")).first() is None:
            raise ApiError(409, "not_connected", "Google Drive is not connected. Connect it first.")
        folder = session.execute(text("SELECT folder_id FROM sync_state WHERE id = 1")).scalar()
        if not folder:
            raise ApiError(409, "no_folder", "No Drive folder is being watched. Choose a folder first.")
    column_name = "sync_requested_at" if kind == "sync" else "reconcile_requested_at"
    session.execute(text("INSERT INTO sync_state (id, source) VALUES (1, :src) ON CONFLICT (id) DO NOTHING"),
                    {"src": "local" if settings.demo_mode else "drive"})
    row = session.execute(text(f"SELECT {column_name} AS pending, last_synced_at, last_reconciled_at "
                               "FROM sync_state WHERE id = 1 FOR UPDATE")).mappings().one()
    requested_at = session.execute(
        text(f"UPDATE sync_state SET {column_name} = now() WHERE id = 1 RETURNING {column_name}")).scalar_one()
    session.commit()
    body = SyncRequestResponse.model_validate({
        "kind": kind, "mode": _mode(settings), "requested_at": utc(requested_at),
        "already_pending": row["pending"] is not None, "last_success_at": _last_success(dict(row)),
    })
    return JSONResponse(status_code=202, content=body.model_dump(mode="json"))


@router.post("/sync", status_code=202, response_model=SyncRequestResponse, responses=_ERRORS)
def sync_now(_: NoQuery, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    return _request("sync", session, settings)


@router.post("/sync/reconcile", status_code=202, response_model=SyncRequestResponse, responses=_ERRORS)
def reconcile_now(_: NoQuery, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    return _request("reconcile", session, settings)


__all__ = ["router"]

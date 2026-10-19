"""`GET /api/sources/{table}/{id}?field=` (FR-9 side panel, §15 "every value links to
document, page and snippet")."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import not_found, validation_error
from app.api.listing import utc
from app.api.values import (
    clean_detail,
    display_value,
    document_ref,
    extraction_ref,
    field_label,
    field_value,
    load_rows,
    provenance,
    provenance_raw,
    reason_infos,
    row_label,
    snapshot,
    snippet_highlight,
)
from app.db import get_session
from app.ingest.decisions import natural_key
from app.schemas import SourceQuery, SourceResponse
from app.schemas.common import ErrorResponse
from app.schemas.data import SOURCE_FIELDS, SourceTable

router = APIRouter(prefix="/api", tags=["data"])

_HISTORY_SQL = text("""
SELECT ra.id AS review_action_id, ra.action, ra.old_value, ra.new_value, ra.note, ra.created_at
FROM review_actions ra LEFT JOIN documents d ON d.id = ra.document_id
WHERE ra.target_table = :table
  AND (ra.target_id = :id OR (d.drive_file_id = :file AND ra.natural_key @> CAST(:key AS jsonb)))
ORDER BY ra.created_at DESC, ra.id DESC
""")

HISTORY_ACTIONS = ("accept", "edit", "choose", "confirm_alias", "reject_alias")


def row_natural_key(table: str, row: dict[str, Any]) -> dict:
    ns = SimpleNamespace(**row)
    if table == "fund_facts":
        ns.name = row["metric"]
    if table == "statement_holdings":
        st = SimpleNamespace(account_id=row["account_id"], fund_id=row["fund_id"], period_end=row["period_end"])
        return natural_key(table, ns, statement=st)
    return natural_key(table, ns)


def review_history(session: Session, table: str, row: dict[str, Any]) -> list[dict]:
    rows = session.execute(_HISTORY_SQL, {
        "table": table, "id": row["id"], "file": row["drive_file_id"], "key": json.dumps(row_natural_key(table, row)),
    }).mappings()
    return [{**dict(r), "created_at": utc(r["created_at"])} for r in rows if r["action"] in HISTORY_ACTIONS]


def conflict_snapshots(session: Session, table: str, row: dict[str, Any], field: str) -> list[dict]:
    detail = (row.get("review_detail") or {}).get("conflict") or {}
    ids = [i for i in detail.get("conflicting_ids") or [] if i != row["id"]]
    others = load_rows(session, table, ids)
    return [snapshot(table, others[i], field) for i in sorted(others)]


@router.get("/sources/{table}/{row_id}", response_model=SourceResponse,
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def source(table: SourceTable, row_id: Annotated[int, Path(ge=1)], q: Annotated[SourceQuery, Query()],
           session: Session = Depends(get_session)):
    allowed = SOURCE_FIELDS[table]
    field = q.field or allowed[0]
    if field not in allowed:
        raise validation_error("query.field", f"field {field!r} is not allowed for {table}; allowed: {', '.join(allowed)}")
    row = load_rows(session, table, [row_id]).get(row_id)
    if row is None:
        raise not_found(f"{table} row {row_id} not found.")
    label = row_label(table, row)
    value = field_value(table, row, field)
    conflicts = conflict_snapshots(session, table, row, field) if table != "statement_holdings" else []
    own = snapshot(table, row, field)
    detail = clean_detail(row["review_detail"])
    prov = provenance(table, row, field)
    return {
        "table": table, "id": row_id, "field": field, "label": label, "field_label": field_label(table, row, field),
        **value,
        "status": row["status"], "review_reasons": list(row["review_reasons"] or []),
        "reasons": reason_infos(row["review_reasons"], detail, title=label,
                                conflict_values=[display_value(s) for s in [own, *conflicts]] if conflicts else ()),
        "provenance": prov,
        "snippet_highlight": snippet_highlight(prov["source_snippet"], value["value_kind"], value["value"],
                                               raw=provenance_raw(table, row, field)),
        "document": document_ref(row),
        "extraction": extraction_ref(row),
        "conflicts": conflicts,
        "review_history": review_history(session, table, row),
    }

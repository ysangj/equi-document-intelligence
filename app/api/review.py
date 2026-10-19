"""`GET /api/review` and `POST /api/review/{id}` (FR-13)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import review_actions
from app.api.errors import ApiError, details_from_errors, invalid_action, validation_error
from app.api.listing import utc
from app.api.review_queue import ALIAS_TABLES, OpenItem, counts, open_items
from app.api.values import (
    STATEMENT_MONEY_FIELDS,
    clean_detail,
    display_value,
    load_rows,
    month_label,
    reason_infos,
    row_label,
    snapshot,
)
from app.db import get_session
from app.schemas import ReviewActionRequest, ReviewActionResponse, ReviewListResponse, ReviewQuery
from app.schemas.common import ErrorResponse
from app.schemas.review import ALLOWED_ACTIONS

router = APIRouter(prefix="/api", tags=["review"])
_ACTION_ADAPTER = TypeAdapter(ReviewActionRequest)
_ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

_KIND_ORDER = {"value": 0, "conflict": 1, "alias": 2}


def _sort_items(items: list[OpenItem], keys: list[tuple[str, bool]]) -> list[OpenItem]:
    """Stable multi-key sort: NULLs last in both directions, then (table, id) ascending."""
    out = sorted(items, key=lambda it: (it.target_table, it.target_id))
    for name, desc in reversed(keys):
        def key(it: OpenItem, name=name):
            v = {"created_at": it.created_at, "kind": _KIND_ORDER[it.kind], "document_name": it.document_name,
                 "target_table": it.target_table}[name]
            return v
        present = [it for it in out if key(it) is not None]
        missing = [it for it in out if key(it) is None]
        present.sort(key=key, reverse=desc)
        out = present + missing
    return out


def _filter(items: list[OpenItem], q: ReviewQuery) -> list[OpenItem]:
    out = items
    if q.kind:
        out = [it for it in out if it.kind in q.kind]
    if q.reason:
        out = [it for it in out if set(it.reasons) & set(q.reason)]
    if q.document_id:
        wanted = set(q.document_id)
        out = [it for it in out if it.document_ids & wanted]
    return out


# ---- hydration -----------------------------------------------------------------------------


def _subject(table: str, row: dict[str, Any] | None) -> dict[str, Any]:
    s = {k: None for k in ("fund_id", "fund_name", "account_id", "account_name", "period_end", "period_label",
                           "return_type", "net_gross", "metric", "holding_name")}
    if row is None:
        return s
    s["fund_id"], s["fund_name"] = row.get("fund_id"), row.get("fund_name")
    s["account_id"], s["account_name"] = row.get("account_id"), row.get("account_name")
    if table == "returns":
        s.update(period_end=row["period_end"], period_label=row["period_label"] or month_label(row["period_end"]),
                 return_type=row["return_type"], net_gross=row["net_gross"])
    elif table == "fund_facts":
        s.update(period_end=row["as_of_date"], period_label=month_label(row["as_of_date"]), metric=row["metric"])
    elif table == "statements":
        s.update(period_end=row["period_end"], period_label=month_label(row["period_end"]))
    else:
        s.update(period_end=row["period_end"], period_label=month_label(row["period_end"]),
                 holding_name=row["holding_name"])
    return s


_ALIAS_SQL = {
    "fund_aliases": text("""
SELECT a.id, a.alias, a.match_method, a.match_score, a.fund_id AS suggested_id, f.canonical_name AS suggested_name,
       a.source_document_id, d.name AS source_document_name,
       ((SELECT count(*) FROM returns r WHERE r.fund_alias_id = a.id)
        + (SELECT count(*) FROM fund_facts x WHERE x.fund_alias_id = a.id)
        + (SELECT count(*) FROM statements s WHERE s.fund_alias_id = a.id))::int AS affected_rows,
       f.manager
FROM fund_aliases a JOIN funds f ON f.id = a.fund_id LEFT JOIN documents d ON d.id = a.source_document_id
WHERE a.id = ANY(:ids)"""),
    "account_aliases": text("""
SELECT a.id, a.alias, a.match_method, a.match_score, a.account_id AS suggested_id,
       ac.canonical_name AS suggested_name, a.source_document_id, d.name AS source_document_name,
       (SELECT count(*) FROM statements s WHERE s.account_alias_id = a.id)::int AS affected_rows,
       NULL::text AS manager
FROM account_aliases a JOIN accounts ac ON ac.id = a.account_id LEFT JOIN documents d ON d.id = a.source_document_id
WHERE a.id = ANY(:ids)"""),
}


def hydrate(session: Session, items: list[OpenItem]) -> list[dict[str, Any]]:
    """Full `ReviewItem`s for one page of open items (one query per table)."""
    ids: dict[str, set[int]] = {}
    for it in items:
        ids.setdefault(it.target_table, set()).update(it.members or [it.target_id])
    rows = {t: (load_rows(session, t, i) if t not in ALIAS_TABLES else
                {r["id"]: dict(r) for r in session.execute(_ALIAS_SQL[t], {"ids": sorted(i)}).mappings()})
            for t, i in ids.items()}
    out = []
    for it in items:
        built = _build(it, rows.get(it.target_table, {}))
        if built is not None:
            out.append(built)
    return out


def _build(it: OpenItem, rows: dict[int, dict]) -> dict[str, Any] | None:
    detail = clean_detail(it.detail)
    base = {
        "id": it.id, "kind": it.kind, "target_table": it.target_table, "target_id": it.target_id,
        "status": it.status, "review_reasons": it.reasons, "detail": detail, "current": None, "fields": None,
        "candidates": None, "alias": None, "allowed_actions": list(ALLOWED_ACTIONS[it.kind]),
        "document_id": it.document_id, "document_name": it.document_name, "created_at": utc(it.created_at),
    }
    if it.kind == "alias":
        a = rows.get(it.target_id)
        if a is None:
            return None
        entity = "fund" if it.target_table == "fund_aliases" else "account"
        subject = _subject("alias", None)
        subject[f"{entity}_id"], subject[f"{entity}_name"] = a["suggested_id"], a["suggested_name"]
        base.update(
            title=f"“{a['alias']}” → {a['suggested_name']}", subject=subject,
            reasons=reason_infos(it.reasons, detail, title=a["suggested_name"]),
            alias={"entity": entity, "alias_id": a["id"], "alias": a["alias"], "match_method": a["match_method"],
                   "match_score": a["match_score"], "suggested_id": a["suggested_id"],
                   "suggested_name": a["suggested_name"], "source_document_id": a["source_document_id"],
                   "source_document_name": a["source_document_name"], "affected_rows": a["affected_rows"]},
        )
        return base
    row = rows.get(it.target_id)
    if row is None:
        return None
    title = row_label(it.target_table, row)
    base.update(title=title, subject=_subject(it.target_table, row))
    if it.kind == "conflict":
        candidates = [snapshot(it.target_table, rows[m]) for m in it.members if m in rows]
        if len(candidates) < 2:
            return None
        base["candidates"] = candidates
        base["reasons"] = reason_infos(it.reasons, detail, title=title,
                                       conflict_values=[display_value(c) for c in candidates])
        return base
    base["current"] = snapshot(it.target_table, row)
    if it.target_table == "statements":
        base["fields"] = [snapshot("statements", row, f) for f in (*STATEMENT_MONEY_FIELDS, "period_start",
                                                                     "period_end")]
    base["reasons"] = reason_infos(it.reasons, detail, title=title)
    return base


# ---- routes --------------------------------------------------------------------------------


@router.get("/review", response_model=ReviewListResponse, responses={422: {"model": ErrorResponse}})
def review_list(q: Annotated[ReviewQuery, Query()], session: Session = Depends(get_session)):
    if q.format == "csv":
        raise validation_error("query.format", "CSV export is not available for the review queue")
    items = open_items(session)
    selected = _sort_items(_filter(items, q), q.sort_keys())
    page = selected[(q.page - 1) * q.page_size: q.page * q.page_size]
    return {"items": hydrate(session, page), "total": len(selected), "page": q.page, "page_size": q.page_size,
            "sort": q.sort, "counts": counts(items)}


_ACTION_SCHEMA = _ACTION_ADAPTER.json_schema()


def parse_action(body: Any):
    """Validate the body against the discriminated union. A missing or contradictory
    action-specific field is `invalid_action`; anything else malformed is a
    `validation_error` (C2)."""
    try:
        return _ACTION_ADAPTER.validate_python(body)
    except ValidationError as e:
        errors = e.errors()
        if any("exactly one of value or value_pct" in str(x.get("msg")) for x in errors):
            raise invalid_action("Send exactly one of value or value_pct.", "body.value",
                                 "send exactly one of value or value_pct") from None
        if isinstance(body, dict) and body.get("action") == "choose" and \
                any(x.get("type") == "missing" and tuple(x.get("loc", ()))[-1:] == ("chosen_id",) for x in errors):
            raise invalid_action("Action 'choose' needs chosen_id.", "body.chosen_id", "field required") from None
        raise ApiError(422, "validation_error", "Invalid request parameters.",
                       details_from_errors(errors, "body")) from None


@router.post("/review/{item_id}", response_model=ReviewActionResponse, responses=_ERRORS,
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                 "schema": _ACTION_SCHEMA}}}})
def review_action(item_id: str, body: Annotated[Any, Body()] = None, session: Session = Depends(get_session)):
    action = parse_action(body)
    return review_actions.apply(session, item_id, action)

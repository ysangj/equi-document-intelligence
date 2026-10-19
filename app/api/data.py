"""FR-9 data browsing: `/api/returns`, `/api/fund-facts`, `/api/statements`,
`/api/statements/{id}/holdings`, `/api/funds`, `/api/accounts`.

Built on the T2 query views, which already hide removed documents and superseded
versions. Filtering, sorting (allowlist, NULLs last, id tie-breaker) and paging all
run in SQL (C3/C4); `format=csv` streams every matching row (C5).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import Engine, Text, column, literal, select, table, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.api.params import NoQuery
from app.api.listing import Listing, any_of, conditions, csv_response
from app.api.values import pct_of, statement_flagged_fields
from app.db import get_engine, get_session
from app.schemas import (
    AccountsList,
    FundFactItem,
    FundFactsPage,
    FundFactsQuery,
    FundsList,
    HoldingItem,
    HoldingsPage,
    HoldingsQuery,
    ReturnItem,
    ReturnsPage,
    ReturnsQuery,
    StatementItem,
    StatementsPage,
    StatementsQuery,
)
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/api", tags=["data"])
_ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_CSV = {200: {"content": {"application/json": {}, "text/csv": {}}}}


def _view(name: str, columns) -> table:
    return table(name, *(column(c, ARRAY(Text)) if c == "review_reasons" else column(c) for c in columns))


V_RETURNS = _view("v_returns", ReturnItem.model_fields)
V_FUND_FACTS = _view("v_fund_facts", [f for f in FundFactItem.model_fields if f != "value_pct"])
V_STATEMENTS = _view("v_statements", [f for f in StatementItem.model_fields if f != "flagged_fields"])
V_HOLDINGS = _view("v_holdings", HoldingItem.model_fields)
STATEMENTS_T = table("statements", column("id"), column("review_detail", JSONB))


def _reasons_filter(view, reasons):
    return view.c.review_reasons.overlap(literal(list(dict.fromkeys(reasons)), ARRAY(Text))) if reasons else None


def _common(view, q) -> list:
    return conditions(
        any_of(view.c.document_id, q.document_id), any_of(view.c.document_type, q.document_type),
        any_of(view.c.status, q.status), _reasons_filter(view, q.review_reason),
    )


def _date_range(col, q) -> list:
    return conditions(col >= q.date_from if q.date_from else None, col <= q.date_to if q.date_to else None)


def _respond(listing: Listing, q, session: Session, engine: Engine):
    if q.format == "csv":
        return csv_response(listing, engine, q)
    return listing.run_page(session, q)


# ---- returns -----------------------------------------------------------------------------


def returns_listing(q: ReturnsQuery) -> Listing:
    v = V_RETURNS
    listing = Listing(base=select(*v.c), sortable={k: v.c[k] for k in ReturnsQuery.SORTABLE}, pk=v.c.return_id,
                      to_item=lambda r: r, item_model=ReturnItem, name="returns")
    return listing.where(*_common(v, q), *_date_range(v.c.period_end, q), *conditions(
        any_of(v.c.fund_id, q.fund_id), any_of(v.c.return_type, q.return_type), any_of(v.c.net_gross, q.net_gross)))


@router.get("/returns", response_model=ReturnsPage, responses={**_CSV, **_ERRORS})
def returns(q: Annotated[ReturnsQuery, Query()], session: Session = Depends(get_session),
            engine: Engine = Depends(get_engine)):
    return _respond(returns_listing(q), q, session, engine)


# ---- fund facts --------------------------------------------------------------------------


def _fact_item(r: dict) -> dict:
    r["value_pct"] = pct_of(r["value"]) if r["unit"] == "fraction" else None
    return {k: r[k] for k in FundFactItem.model_fields}


def fund_facts_listing(q: FundFactsQuery) -> Listing:
    v = V_FUND_FACTS
    listing = Listing(base=select(*v.c), sortable={k: v.c[k] for k in FundFactsQuery.SORTABLE}, pk=v.c.fact_id,
                      to_item=_fact_item, item_model=FundFactItem, name="fund_facts")
    return listing.where(*_common(v, q), *_date_range(v.c.as_of_date, q),
                         *conditions(any_of(v.c.fund_id, q.fund_id), any_of(v.c.metric, q.metric)))


@router.get("/fund-facts", response_model=FundFactsPage, responses={**_CSV, **_ERRORS})
def fund_facts(q: Annotated[FundFactsQuery, Query()], session: Session = Depends(get_session),
               engine: Engine = Depends(get_engine)):
    return _respond(fund_facts_listing(q), q, session, engine)


# ---- statements --------------------------------------------------------------------------


def _statement_item(r: dict) -> dict:
    r["flagged_fields"] = statement_flagged_fields(r["review_reasons"], r.pop("review_detail", None))
    return {k: r[k] for k in StatementItem.model_fields}


def statements_listing(q: StatementsQuery) -> Listing:
    v, s = V_STATEMENTS, STATEMENTS_T
    base = select(*v.c, s.c.review_detail).select_from(v.join(s, s.c.id == v.c.statement_id))
    listing = Listing(base=base, sortable={k: v.c[k] for k in StatementsQuery.SORTABLE}, pk=v.c.statement_id,
                      to_item=_statement_item, item_model=StatementItem, name="statements")
    return listing.where(*_common(v, q), *_date_range(v.c.period_end, q),
                         *conditions(any_of(v.c.account_id, q.account_id), any_of(v.c.fund_id, q.fund_id)))


@router.get("/statements", response_model=StatementsPage, responses={**_CSV, **_ERRORS})
def statements(q: Annotated[StatementsQuery, Query()], session: Session = Depends(get_session),
               engine: Engine = Depends(get_engine)):
    return _respond(statements_listing(q), q, session, engine)


def holdings_listing(statement_id: int, q: HoldingsQuery) -> Listing:
    v = V_HOLDINGS
    listing = Listing(base=select(*v.c), sortable={k: v.c[k] for k in HoldingsQuery.SORTABLE}, pk=v.c.holding_id,
                      to_item=lambda r: r, item_model=HoldingItem, name=f"statement_{statement_id}_holdings")
    return listing.where(v.c.statement_id == statement_id, *conditions(any_of(v.c.status, q.status)))


@router.get("/statements/{statement_id}/holdings", response_model=HoldingsPage, responses={**_CSV, **_ERRORS})
def holdings(statement_id: Annotated[int, Path(ge=1)], q: Annotated[HoldingsQuery, Query()],
             session: Session = Depends(get_session), engine: Engine = Depends(get_engine)):
    if session.execute(text("SELECT 1 FROM v_statements WHERE statement_id = :id"), {"id": statement_id}).first() is None:
        raise not_found(f"Statement {statement_id} not found.")
    return _respond(holdings_listing(statement_id, q), q, session, engine)


# ---- funds / accounts --------------------------------------------------------------------

_FUNDS_SQL = text("""
WITH vis AS (
  SELECT fund_id, document_id, period_end AS d, 1 AS is_return FROM v_returns
  UNION ALL SELECT fund_id, document_id, as_of_date, 0 FROM v_fund_facts
  UNION ALL SELECT fund_id, document_id, period_end, 0 FROM v_statements WHERE fund_id IS NOT NULL
)
SELECT f.id AS fund_id, f.canonical_name AS fund_name, f.manager, f.strategy, f.currency,
       sum(vis.is_return)::int AS return_count, count(DISTINCT vis.document_id)::int AS document_count,
       max(vis.d) AS latest_period_end
FROM vis JOIN funds f ON f.id = vis.fund_id
GROUP BY f.id
ORDER BY lower(f.canonical_name), f.id
""")

_ACCOUNTS_SQL = text("""
SELECT ac.id AS account_id, ac.canonical_name AS account_name, ac.account_number_masked, ac.custodian, ac.currency,
       count(*)::int AS statement_count, max(v.period_end) AS latest_period_end
FROM v_statements v JOIN accounts ac ON ac.id = v.account_id
GROUP BY ac.id
ORDER BY lower(ac.canonical_name), ac.id
""")


def _aliases(session: Session, alias_table: str, owner_col: str) -> dict[int, list[dict]]:
    rows = session.execute(text(f"""
SELECT a.id AS alias_id, a.{owner_col} AS owner_id, a.alias, a.confirmed, a.match_method, a.match_score,
       a.source_document_id, d.name AS source_document_name
FROM {alias_table} a LEFT JOIN documents d ON d.id = a.source_document_id
ORDER BY lower(a.alias), a.id""")).mappings()
    out: dict[int, list[dict]] = {}
    for r in rows:
        item = dict(r)
        out.setdefault(item.pop("owner_id"), []).append(item)
    return out


def _with_aliases(entity: dict, aliases: list[dict]) -> dict:
    entity["aliases"] = aliases
    entity["has_unconfirmed_alias"] = any(a["match_method"] == "fuzzy" and not a["confirmed"] for a in aliases)
    return entity


@router.get("/funds", response_model=FundsList)
def funds(_: NoQuery, session: Session = Depends(get_session)):
    aliases = _aliases(session, "fund_aliases", "fund_id")
    items = []
    for r in session.execute(_FUNDS_SQL).mappings():
        f = dict(r)
        latest, counts = f.pop("latest_period_end"), (f.pop("return_count"), f.pop("document_count"))
        _with_aliases(f, aliases.get(f["fund_id"], []))
        f.update(return_count=counts[0], document_count=counts[1], latest_period_end=latest)
        items.append(f)
    return {"items": items, "total": len(items)}


@router.get("/accounts", response_model=AccountsList)
def accounts(_: NoQuery, session: Session = Depends(get_session)):
    aliases = _aliases(session, "account_aliases", "account_id")
    items = []
    for r in session.execute(_ACCOUNTS_SQL).mappings():
        a = dict(r)
        count, latest = a.pop("statement_count"), a.pop("latest_period_end")
        _with_aliases(a, aliases.get(a["account_id"], []))
        a.update(statement_count=count, latest_period_end=latest)
        items.append(a)
    return {"items": items, "total": len(items)}

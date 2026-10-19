"""Review decisions (`POST /api/review/{id}`, FR-13).

| action        | kind            | effect                                                          |
|---------------|-----------------|-----------------------------------------------------------------|
| accept        | value           | keep the value; clear every reason; status ok                   |
| edit          | value, conflict | set the new value; status ok (conflict: on `chosen_id`, the     |
|               |                 | other candidates are deleted)                                   |
| choose        | conflict        | `chosen_id` -> ok; candidates whose value differs from it beyond |
|               |                 | the conflict tolerance are deleted; candidates that agree with   |
|               |                 | it (corroborating sources) are kept and set to ok               |
| confirm_alias | alias           | confirmed = true; linked rows unchanged                         |
| reject_alias  | alias           | the alias moves to another (or a new) fund/account, match_method |
|               |                 | manual + confirmed; every row linked through it is relinked     |
| make_canonical| alias           | the alias's name becomes the entity's canonical name (pinned     |
|               |                 | against the automatic rule); the old name stays a confirmed     |
|               |                 | alias; the alias is confirmed                                    |

Every changed row gets one `review_actions` row with the T5 natural key
(`app.ingest.decisions.natural_key`, the PRE-change key) and the row's
`document_id`, so `app.ingest.decisions.reapply_decisions` re-applies it when the
same Drive file is reprocessed (T3 decision 8). Values are JSON objects keyed by
field; a deleted row has `new_value = {"deleted": true}`.

Dependent checks are re-run afterwards: conflicts for every natural key touched
(old and new), the YTD check for the affected fund-years, and the canonical-name rule
(`app.ingest.canonical`) for every fund/account whose rows moved or were deleted;
then done <-> needs_review is re-derived for every document involved. The whole decision runs
under the postprocess advisory lock, so it never interleaves with a worker job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import exists, extract, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import invalid_action, not_found
from app.api.review_queue import OpenItem, find_item, open_items
from app.api.values import STATEMENT_MONEY_FIELDS
from app.ingest import canonical
from app.ingest import conflicts as conflicts_mod
from app.ingest.decisions import natural_key
from app.ingest.postprocess import lock_postprocess, refresh_document_statuses
from app.ingest.review_state import add_flag, clear_flags, drop_flag
from app.ingest.validation import MonthValue, check_ytd
from app.models import (
    Account,
    AccountAlias,
    Document,
    Fund,
    FundAlias,
    FundFact,
    Return,
    ReviewAction,
    Statement,
    StatementHolding,
)
from app.normalize.dates import month_end
from app.schemas.review import ALLOWED_ACTIONS

_ID_RE = re.compile(r"^(returns|fund_facts|statements|statement_holdings|fund_aliases|account_aliases):(\d{1,18})$")
MODELS = {"returns": Return, "fund_facts": FundFact, "statements": Statement, "statement_holdings": StatementHolding}
STATEMENT_EDIT_FIELDS = (*STATEMENT_MONEY_FIELDS, "period_start", "period_end")
HOLDING_EDIT_FIELDS = ("market_value", "weight", "quantity", "holding_name", "identifier")
_HOLDING_ATTR = {"holding_name": "name"}


def _f(x: Any) -> Any:
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, date):
        return x.isoformat()
    return x


def current_values(table: str, row: Any) -> dict[str, Any]:
    """The row's values as stored, in `review_actions.old_value` form."""
    if table == "returns":
        return {"value": _f(row.value)}
    if table == "fund_facts":
        if row.value is None and row.value_text is not None:
            return {"value_text": row.value_text}
        return {"value": _f(row.value)}
    if table == "statements":
        return {f: _f(getattr(row, f)) for f in STATEMENT_MONEY_FIELDS}
    return {"market_value": _f(row.market_value), "quantity": _f(row.quantity), "weight": _f(row.weight)}


def _doc_id(table: str, row: Any) -> int:
    return row.statement.document_id if table == "statement_holdings" else row.document_id


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dec(v: Any) -> Decimal:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise invalid_action("The value must be a number.", "body.value", "not a number") from None
    if not d.is_finite():
        raise invalid_action("The value must be a finite number.", "body.value", "not a finite number")
    return d


@dataclass
class EditTarget:
    attr: str  # ORM attribute
    key: str  # key in old_value/new_value (decisions._attr understands it)
    new: Any  # python value to store
    new_json: Any


def edit_target(table: str, row: Any, action) -> EditTarget:
    """Validate an `edit` payload for this row (C6 value kinds) -> what to set."""
    fld = action.field

    def percent() -> Decimal:
        if action.value_pct is None:
            raise invalid_action("This is a percent value: send value_pct (e.g. -0.15 for -0.15%).", "body.value_pct",
                                 "required for a percent value")
        return _dec(action.value_pct) / 100

    def number() -> Decimal:
        if action.value_pct is not None or not _is_number(action.value):
            raise invalid_action("Send the new number as value.", "body.value", "a number is required")
        return _dec(action.value)

    def text_value() -> str:
        if action.value_pct is not None or action.value is None or not str(action.value).strip():
            raise invalid_action("Send the new text as value.", "body.value", "a non-empty value is required")
        return str(action.value).strip()

    if table in ("returns", "fund_facts"):
        if fld not in (None, "value"):
            raise invalid_action(f"Field {fld!r} cannot be edited on {table}; use 'value'.", "body.field",
                                 "allowed: value")
        if table == "returns" or row.unit == "fraction":
            v = percent()
            return EditTarget("value", "value", v, float(v))
        if row.value is None and row.value_text is not None and not _is_number(action.value):
            v = text_value()
            return EditTarget("value_text", "value_text", v, v)
        v = number()
        return EditTarget("value", "value", v, float(v))
    if table == "statements":
        if fld not in STATEMENT_EDIT_FIELDS:
            raise invalid_action(f"Statements need field, one of: {', '.join(STATEMENT_EDIT_FIELDS)}.", "body.field",
                                 "required for statements" if fld is None else f"{fld!r} is not editable")
        if fld in STATEMENT_MONEY_FIELDS:
            v = number()
            return EditTarget(fld, fld, v, float(v))
        raw = text_value()
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            raise invalid_action("Dates must be YYYY-MM-DD.", "body.value", "not a YYYY-MM-DD date") from None
        start = d if fld == "period_start" else row.period_start
        end = d if fld == "period_end" else row.period_end
        if start is not None and end is not None and start > end:
            raise invalid_action("period_start must be on or before period_end.", "body.value", "invalid period")
        return EditTarget(fld, fld, d, d.isoformat())
    # statement_holdings
    fld = "market_value" if fld in (None, "value") else fld
    if fld not in HOLDING_EDIT_FIELDS:
        raise invalid_action(f"Holdings fields: {', '.join(HOLDING_EDIT_FIELDS)}.", "body.field", f"{fld!r} is not editable")
    if fld == "weight":
        v = percent()
        return EditTarget("weight", "weight", v, float(v))
    if fld in ("market_value", "quantity"):
        v = number()
        return EditTarget(fld, fld, v, float(v))
    v = text_value()
    return EditTarget(_HOLDING_ATTR.get(fld, fld), fld, v, v)


# ---- context -----------------------------------------------------------------------------------


@dataclass
class _Ctx:
    session: Session
    item: OpenItem
    note: str | None
    action_ids: list[int] = field(default_factory=list)
    affected: list[dict] = field(default_factory=list)
    touched: list[tuple[str, Any]] = field(default_factory=list)  # (table, ORM row) kept, report status
    conflict_keys: dict[str, set[tuple]] = field(default_factory=lambda: {"returns": set(), "fund_facts": set(),
                                                                          "statements": set()})
    ytd_keys: set[tuple] = field(default_factory=set)  # (fund_id, net_gross, year)
    ytd_skip: set[int] = field(default_factory=set)  # rows the reviewer just decided
    doc_ids: set[int] = field(default_factory=set)
    canonical_ids: dict[str, set[int]] = field(default_factory=lambda: {"fund": set(), "account": set()})

    def record(self, table: str, row: Any, action: str, old: Any, new: Any, *, key_extra: dict | None = None,
               document_id: int | None = None, key: dict | None = None) -> None:
        nk = key if key is not None else natural_key(table, row)
        if key_extra:
            nk = {**nk, **key_extra}
        ra = ReviewAction(target_table=table, target_id=row.id,
                          document_id=document_id if document_id is not None else _doc_id(table, row),
                          natural_key=nk, action=action, reason=",".join(self.item.reasons) or None,
                          old_value=old, new_value=new, note=self.note)
        self.session.add(ra)
        self.session.flush()
        self.action_ids.append(ra.id)

    def note_keys(self, table: str, row: Any) -> None:
        """Remember the row's natural key / fund-year for the dependent checks."""
        if table == "returns":
            self.conflict_keys["returns"].add(conflicts_mod.return_key(row))
            self.ytd_keys.add((row.fund_id, row.net_gross, row.period_end.year))
        elif table == "fund_facts":
            self.conflict_keys["fund_facts"].add(conflicts_mod.fact_key(row))
        elif table == "statements":
            self.conflict_keys["statements"].add(conflicts_mod.statement_key(row))
        self.doc_ids.add(_doc_id(table, row))

    def note_entities(self, table: str, row: Any) -> None:
        """Remember the fund/account a moved or deleted row used (canonical-name rule)."""
        if table == "statement_holdings":
            return
        self.canonical_ids["fund"].add(row.fund_id)
        if table == "statements":
            self.canonical_ids["account"].add(row.account_id)

    def delete(self, table: str, row: Any) -> None:
        self.note_keys(table, row)
        self.note_entities(table, row)
        self.affected.append({"table": table, "id": row.id, "change": "deleted", "status": None})
        self.session.delete(row)


def _rows(ctx: _Ctx, table: str, ids: list[int]) -> list[Any]:
    model = MODELS[table]
    rows = list(ctx.session.execute(select(model).where(model.id.in_(ids)).order_by(model.id)).scalars())
    if table == "statement_holdings":
        for r in rows:
            _ = r.statement  # load before any delete
    return rows


# ---- actions -----------------------------------------------------------------------------------


def _accept(ctx: _Ctx, table: str, row: Any) -> None:
    values = current_values(table, row)
    ctx.record(table, row, "accept", values, values)
    clear_flags(row)
    ctx.doc_ids.add(_doc_id(table, row))
    ctx.touched.append((table, row))
    ctx.affected.append({"table": table, "id": row.id, "change": "status_changed", "status": None})


def _edit(ctx: _Ctx, table: str, row: Any, action) -> None:
    target = edit_target(table, row, action)
    old = {target.key: _f(getattr(row, target.attr))}
    extra = {"field": target.key} if table == "statements" else None
    ctx.record(table, row, "edit", old, {target.key: target.new_json}, key_extra=extra)
    ctx.note_keys(table, row)  # the old key
    setattr(row, target.attr, target.new)
    clear_flags(row)
    ctx.session.flush()
    ctx.note_keys(table, row)  # the new key (a period edit moves it)
    if table == "returns":
        ctx.ytd_skip.add(row.id)
    ctx.touched.append((table, row))
    ctx.affected.append({"table": table, "id": row.id, "change": "value_edited", "status": None})


def _chosen(ctx: _Ctx, rows: list[Any], chosen_id: int | None, action_name: str) -> Any:
    if chosen_id is None:
        raise invalid_action(f"Action '{action_name}' on a conflict needs chosen_id (one of the candidates).",
                             "body.chosen_id", "required for a conflict item")
    for r in rows:
        if r.id == chosen_id:
            return r
    raise invalid_action(f"chosen_id {chosen_id} is not a candidate of this conflict; candidates: "
                         f"{', '.join(str(r.id) for r in rows)}.", "body.chosen_id", "not a candidate")


# Same tests (and tolerances) as conflict detection: a kept row never re-conflicts.
_DIFFERS = {
    "returns": lambda a, b: conflicts_mod.returns_differ(a.value, b.value),
    "fund_facts": conflicts_mod.facts_differ,
    "statements": conflicts_mod.statements_differ,
}


def _choose_keep(ctx: _Ctx, table: str, row: Any) -> None:
    """Record a `choose` on a kept row (the winner, or a candidate that agrees with it)
    and set it ok. old_value = new_value, so reprocessing re-applies it only while the
    document still says the same thing."""
    values = current_values(table, row)
    ctx.record(table, row, "choose", values, values)
    clear_flags(row)
    ctx.note_keys(table, row)
    if table == "returns":
        ctx.ytd_skip.add(row.id)
    ctx.touched.append((table, row))
    ctx.affected.append({"table": table, "id": row.id, "change": "status_changed", "status": None})


def _resolve_group(ctx: _Ctx, table: str, action) -> None:
    rows = _rows(ctx, table, ctx.item.members)
    winner = _chosen(ctx, rows, action.chosen_id, action.action)
    losers = [r for r in rows if r.id != winner.id]
    if action.action == "edit":
        edit_target(table, winner, action)  # validate before anything is written
        _edit(ctx, table, winner, action)
    else:
        _choose_keep(ctx, table, winner)
        differ = _DIFFERS.get(table)
        agreeing = [r for r in losers if differ is not None and not differ(winner, r)]
        for r in agreeing:
            _choose_keep(ctx, table, r)
        losers = [r for r in losers if r not in agreeing]
    for loser in losers:
        ctx.record(table, loser, action.action, current_values(table, loser), {"deleted": True})
        ctx.delete(table, loser)


def _alias_models(table: str):
    return (FundAlias, Fund, "fund") if table == "fund_aliases" else (AccountAlias, Account, "account")


def _linked_rows(ctx: _Ctx, table: str, alias_id: int) -> list[tuple[str, Any]]:
    s = ctx.session
    if table == "fund_aliases":
        out = [("returns", r) for r in s.execute(select(Return).where(Return.fund_alias_id == alias_id)).scalars()]
        out += [("fund_facts", r) for r in s.execute(select(FundFact).where(FundFact.fund_alias_id == alias_id)).scalars()]
        out += [("statements", r) for r in s.execute(select(Statement).where(Statement.fund_alias_id == alias_id)).scalars()]
    else:
        out = [("statements", r) for r in
               s.execute(select(Statement).where(Statement.account_alias_id == alias_id)).scalars()]
    return sorted(out, key=lambda x: (x[0], x[1].id))


def _confirm_alias(ctx: _Ctx, table: str) -> None:
    model, _, entity = _alias_models(table)
    alias = ctx.session.get(model, ctx.item.target_id)
    owner = alias.fund_id if entity == "fund" else alias.account_id
    ctx.record(table, alias, "confirm_alias", {f"{entity}_id": owner, "confirmed": False},
               {f"{entity}_id": owner, "confirmed": True}, document_id=alias.source_document_id,
               key={"entity": entity, "alias": alias.alias.lower()})
    alias.confirmed = True
    ctx.affected.append({"table": table, "id": alias.id, "change": "alias_confirmed", "status": None})
    if alias.source_document_id:
        ctx.doc_ids.add(alias.source_document_id)
    for t, r in _linked_rows(ctx, table, alias.id):
        ctx.doc_ids.add(r.document_id)


def _new_entity(ctx: _Ctx, entity_model, entity: str, alias: Any, old_owner: Any) -> Any:
    s = ctx.session
    name = alias.alias.strip()
    stmt = select(entity_model).where(func.lower(entity_model.canonical_name) == name.lower())
    if entity == "account":
        stmt = stmt.where(Account.account_number_masked.is_(None))
    existing = s.execute(stmt).scalars().first()
    if existing is not None:
        if existing.id == old_owner.id:
            raise invalid_action(f"“{name}” is already the canonical name of this {entity}.", "body.reassign_to_id",
                                 "the alias names the suggested entity itself")
        return existing
    if entity == "fund":
        new = Fund(canonical_name=name, manager=old_owner.manager, currency=old_owner.currency)
    else:
        new = Account(canonical_name=name, custodian=old_owner.custodian, currency=old_owner.currency)
    s.add(new)
    s.flush()
    ctx.affected.append({"table": "funds" if entity == "fund" else "accounts", "id": new.id, "change": "created",
                         "status": None})
    return new


def _reject_alias(ctx: _Ctx, table: str, reassign_to_id: int | None) -> None:
    s = ctx.session
    model, entity_model, entity = _alias_models(table)
    owner_attr = f"{entity}_id"
    alias = s.get(model, ctx.item.target_id)
    old_owner = s.get(entity_model, getattr(alias, owner_attr))
    if reassign_to_id is None:
        target = _new_entity(ctx, entity_model, entity, alias, old_owner)
    else:
        target = s.get(entity_model, reassign_to_id)
        if target is None:
            raise invalid_action(f"{entity.capitalize()} {reassign_to_id} does not exist.", "body.reassign_to_id",
                                 "unknown id")
        if target.id == old_owner.id:
            raise invalid_action(f"The alias already belongs to {entity} {target.id}; use confirm_alias instead.",
                                 "body.reassign_to_id", "same as the suggested entity")
    ctx.record(table, alias, "reject_alias",
               {owner_attr: old_owner.id, "confirmed": False, "match_method": alias.match_method},
               {owner_attr: target.id, "confirmed": True, "match_method": "manual"},
               document_id=alias.source_document_id, key={"entity": entity, "alias": alias.alias.lower()})
    linked = _linked_rows(ctx, table, alias.id)
    for t, r in linked:
        ctx.note_keys(t, r)
    ctx.canonical_ids[entity] |= {old_owner.id, target.id}
    setattr(alias, owner_attr, target.id)
    alias.match_method = "manual"
    alias.match_score = None
    alias.confirmed = True
    ctx.affected.append({"table": table, "id": alias.id, "change": "alias_reassigned", "status": None})
    if alias.source_document_id:
        ctx.doc_ids.add(alias.source_document_id)
    for t, r in linked:
        setattr(r, owner_attr, target.id)
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        raise invalid_action(f"Cannot move the alias: a document already has values for {entity} {target.id} "
                             "with the same keys.", "body.reassign_to_id", "would duplicate rows") from None
    for t, r in linked:
        ctx.note_keys(t, r)
        ctx.touched.append((t, r))
        ctx.affected.append({"table": t, "id": r.id, "change": "relinked", "status": None})


def _make_canonical(ctx: _Ctx, table: str) -> None:
    """The alias's name becomes the canonical name. The `review_actions` row (natural
    key `{entity, alias: lower(alias)}`) is what pins it: `canonical.recompute` keeps
    the newest `make_canonical` name of an entity, across reprocessing and recomputes."""
    s = ctx.session
    model, entity_model, entity = _alias_models(table)
    owner_attr = f"{entity}_id"
    alias = s.get(model, ctx.item.target_id)
    owner = s.get(entity_model, getattr(alias, owner_attr))
    old_name, new_name = owner.canonical_name, canonical.name_of(entity, alias.alias, owner)
    if canonical.name_taken(s, canonical.SPECS[entity], owner, new_name):
        raise invalid_action(f"Another {entity} is already named “{new_name}”.", "body.action",
                             f"the name is another {entity}'s canonical name")
    ctx.record(table, alias, "make_canonical",
               {owner_attr: owner.id, "canonical_name": old_name, "confirmed": alias.confirmed},
               {owner_attr: owner.id, "canonical_name": new_name, "confirmed": True},
               document_id=alias.source_document_id, key={"entity": entity, "alias": alias.alias.lower()})
    before = {a.id for a in s.execute(select(model.id).where(getattr(model, owner_attr) == owner.id))}
    res = canonical.rename(s, entity, owner, alias, user=True)
    ctx.doc_ids |= res.document_ids
    ctx.affected.append({"table": "funds" if entity == "fund" else "accounts", "id": owner.id, "change": "renamed",
                         "status": None})
    ctx.affected.append({"table": table, "id": alias.id, "change": "alias_confirmed", "status": None})
    for (new_id,) in s.execute(select(model.id).where(getattr(model, owner_attr) == owner.id,
                                                      model.id.not_in(before)).order_by(model.id)):
        ctx.affected.append({"table": table, "id": new_id, "change": "created", "status": None})
    if alias.source_document_id:
        ctx.doc_ids.add(alias.source_document_id)


def _affected_renames(ctx: _Ctx, result: canonical.RecomputeResult) -> None:
    reported = {(a["table"], a["id"]) for a in ctx.affected}
    for entity, eid, _old, _new in result.renamed:
        t = "funds" if entity == "fund" else "accounts"
        if (t, eid) not in reported:
            reported.add((t, eid))
            ctx.affected.append({"table": t, "id": eid, "change": "renamed", "status": None})


# ---- dependent checks ------------------------------------------------------------------------------


def _visible(model):
    return exists().where(Document.id == model.document_id, Document.is_latest, Document.status != "removed")


def _key_rows(ctx: _Ctx) -> list[tuple[str, Any]]:
    s, out = ctx.session, []
    rk = sorted(ctx.conflict_keys["returns"], key=repr)
    if rk:
        out += [("returns", r) for r in s.execute(select(Return).where(
            _visible(Return), tuple_(Return.fund_id, Return.period_end, Return.return_type, Return.net_gross).in_(rk)))
            .scalars()]
    fk = ctx.conflict_keys["fund_facts"]
    if fk:
        out += [("fund_facts", r) for r in s.execute(select(FundFact).where(
            _visible(FundFact), FundFact.fund_id.in_(sorted({k[0] for k in fk})))).scalars()
            if conflicts_mod.fact_key(r) in fk]
    sk = ctx.conflict_keys["statements"]
    if sk:
        out += [("statements", r) for r in s.execute(select(Statement).where(
            _visible(Statement), Statement.account_id.in_(sorted({k[0] for k in sk})))).scalars()
            if conflicts_mod.statement_key(r) in sk]
    return out


def _ytd_rows(ctx: _Ctx) -> list[Return]:
    out = []
    for fund_id, net_gross, year in sorted(ctx.ytd_keys, key=repr):
        out += list(ctx.session.execute(select(Return).where(
            _visible(Return), Return.fund_id == fund_id, Return.net_gross == net_gross, Return.return_type == "YTD",
            extract("year", Return.period_end) == year).order_by(Return.id)).scalars())
    return out


def _months(ctx: _Ctx, y: Return) -> list[MonthValue] | None:
    """Jan..month MTD values for a YTD row: its own document first, then other visible
    documents (ok rows first, then higher confidence), as T5 does. None if incomplete."""
    ends = [month_end(date(y.period_end.year, m, 1)) for m in range(1, y.period_end.month + 1)]
    rows = ctx.session.execute(select(Return).where(
        _visible(Return), Return.fund_id == y.fund_id, Return.net_gross == y.net_gross, Return.return_type == "MTD",
        Return.period_end.in_(ends), Return.value.is_not(None)).order_by(Return.id)).scalars()
    best: dict[date, Return] = {}
    for r in rows:
        rank = (r.document_id == y.document_id, r.status == "ok", r.confidence or 0.0)
        cur = best.get(r.period_end)
        if cur is None or rank > (cur.document_id == y.document_id, cur.status == "ok", cur.confidence or 0.0):
            best[r.period_end] = r
    if any(e not in best for e in ends):
        return None
    return [MonthValue(e, best[e].value, best[e].id) for e in ends]


def _recheck_ytd(ctx: _Ctx, rows: list[Return]) -> None:
    for y in rows:
        if y.id in ctx.ytd_skip or y.value is None:
            continue
        months = _months(ctx, y)
        if months is None:
            continue
        detail = check_ytd(y.value, months, y.period_end.year)
        if detail is None:
            if drop_flag(y, "ytd_mismatch"):
                ctx.doc_ids.add(y.document_id)
        elif "ytd_mismatch" in (y.review_reasons or []) or not _has_decision(ctx, y):
            if add_flag(y, "ytd_mismatch", detail):
                ctx.doc_ids.add(y.document_id)


def _has_decision(ctx: _Ctx, row: Return) -> bool:
    """A reviewer already decided this YTD row: do not re-flag it from another edit."""
    return ctx.session.execute(select(ReviewAction.id).where(
        ReviewAction.target_table == "returns", ReviewAction.target_id == row.id).limit(1)).first() is not None


def _dependent_checks(ctx: _Ctx) -> None:
    s = ctx.session
    s.flush()
    candidates = _key_rows(ctx)
    ytd = _ytd_rows(ctx)
    before = {(t, r.id): r.status for t, r in candidates} | {("returns", r.id): r.status for r in ytd}
    if any(ctx.conflict_keys.values()):
        ctx.doc_ids |= conflicts_mod.refresh_conflicts(s, ctx.conflict_keys)
    s.flush()
    _recheck_ytd(ctx, ytd)
    s.flush()
    if any(ctx.canonical_ids.values()):
        renames = canonical.recompute_entities(s, ctx.canonical_ids["fund"], ctx.canonical_ids["account"])
        ctx.doc_ids |= renames.document_ids
        _affected_renames(ctx, renames)
    reported = {(a["table"], a["id"]) for a in ctx.affected}
    for t, r in [*candidates, *(("returns", y) for y in ytd)]:
        k = (t, r.id)
        if k not in reported and k in before and before[k] != r.status:
            reported.add(k)
            ctx.affected.append({"table": t, "id": r.id, "change": "status_changed", "status": r.status})
            ctx.doc_ids.add(r.document_id)


# ---- entry point ----------------------------------------------------------------------------------


def apply(session: Session, item_id: str, action) -> dict[str, Any]:
    m = _ID_RE.fullmatch(item_id)
    if m is None:
        raise not_found(f"Review item {item_id!r} not found.")
    table, target_id = m.group(1), int(m.group(2))
    lock_postprocess(session)
    item = find_item(open_items(session), table, target_id)
    if item is None:
        raise not_found(f"Review item {item_id} not found or already resolved.")
    allowed = ALLOWED_ACTIONS[item.kind]
    if action.action not in allowed:
        raise invalid_action(f"Action '{action.action}' is not allowed for a {item.kind} item; allowed: "
                             f"{', '.join(allowed)}.", "body.action", f"not allowed for kind '{item.kind}'")
    ctx = _Ctx(session, item, action.note)
    if item.kind == "alias":
        if action.action == "confirm_alias":
            _confirm_alias(ctx, table)
        elif action.action == "make_canonical":
            _make_canonical(ctx, table)
        else:
            _reject_alias(ctx, table, action.reassign_to_id)
    elif item.kind == "conflict":
        _resolve_group(ctx, table, action)
    else:
        row = _rows(ctx, table, [item.target_id])[0]
        if action.action == "accept":
            _accept(ctx, table, row)
        else:
            _edit(ctx, table, row, action)
    _dependent_checks(ctx)
    status_by_row = {(t, r.id): r.status for t, r in ctx.touched}
    for a in ctx.affected:
        if a["change"] in ("status_changed", "value_edited", "relinked") and a["status"] is None:
            a["status"] = status_by_row.get((a["table"], a["id"]))
    session.flush()
    refresh_document_statuses(session, {d for d in ctx.doc_ids if d is not None})
    after = open_items(session)
    resolved = not any(it.covers(item.target_table, mid) for it in after for mid in (item.members or [item.target_id]))
    session.commit()
    return {"id": item.id, "action": action.action, "resolved": resolved, "review_action_ids": ctx.action_ids,
            "affected": ctx.affected, "remaining_open": len(after)}


__all__ = ["apply", "current_values", "edit_target"]

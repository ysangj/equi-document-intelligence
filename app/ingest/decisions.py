"""Re-applying review decisions after reprocessing (FR-13, T3 decision 8).

Reprocessing recreates value rows with new ids, so `review_actions` are matched by
NATURAL KEY + `drive_file_id` (any version of the same file), never by row id.

What T6 writes (one `review_actions` row per changed value row):
- `target_table`, `target_id`, `document_id` (the row's document), `action`,
  `natural_key = natural_key(table, row)` (use this function), `old_value`,
  `new_value`, `note`.
- Values are JSON objects keyed by field:
  returns / fund_facts `{"value": <fraction or number>}` (fund_facts may use
  `{"value_text": ...}`); statements `{"<field>": <number or "YYYY-MM-DD">}` (accept:
  all six money fields in `old_value`); statement_holdings `{"market_value": ...}`
  etc. A row deleted by the decision (the losing candidates of `choose`, and of `edit`
  on a conflict) has `new_value = {"deleted": true}`.
- Statement edits may also put `"field"` in the natural key; it is ignored for
  matching and tells which field a `{"value": x}` edit sets.

Re-apply rules, per recreated row, over that key's decisions in time order:
- every `edit` is re-applied (its `new_value` fields are set); if the newest decision
  is an `edit` the row ends `ok` (all reasons cleared), or is deleted again if that
  edit deleted it;
- if the newest decision is `accept` or `choose`, it is re-applied only when the
  re-extracted values still equal its `old_value` (all keys, numbers within 1e-9):
  the row ends `ok`, or is deleted again for a losing candidate. Otherwise the row
  keeps its fresh flags and comes back for review;
- alias decisions are not handled here: they live on the alias rows themselves
  (see `app/ingest/resolve.py`).

Conflict detection runs after this step, so a value that still disagrees with another
document is flagged again (new information), while a resolved conflict stays resolved
(its losing rows are gone).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.review_state import clear_flags
from app.models import Document, ReviewAction, Statement

VALUE_TABLES = ("returns", "fund_facts", "statements", "statement_holdings")
_HOLDING_FIELDS = {"holding_name": "name", "name": "name", "identifier": "identifier", "quantity": "quantity",
                   "market_value": "market_value", "weight": "weight"}
_STATEMENT_FIELDS = ("opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance",
                     "period_start", "period_end", "currency")


def _d(x: date | None) -> str | None:
    return x.isoformat() if x else None


def natural_key(table: str, row: Any, *, statement: Statement | None = None) -> dict:
    """The JSON natural key stored in `review_actions.natural_key` (T6 must use this)."""
    if table == "returns":
        return {"fund_id": row.fund_id, "period_end": _d(row.period_end), "return_type": row.return_type,
                "net_gross": row.net_gross}
    if table == "fund_facts":
        return {"fund_id": row.fund_id, "name": row.name, "as_of_date": _d(row.as_of_date)}
    if table == "statements":
        return {"account_id": row.account_id, "fund_id": row.fund_id, "period_end": _d(row.period_end)}
    if table == "statement_holdings":
        st = statement if statement is not None else row.statement
        return {**natural_key("statements", st), "line_no": row.line_no}
    raise ValueError(f"no natural key for {table}")


def _match_key(key: dict | None) -> tuple:
    return tuple(sorted((k, v) for k, v in (key or {}).items() if k != "field"))


def _is_delete(v: Any) -> bool:
    return isinstance(v, dict) and v.get("deleted") is True


def _attr(table: str, field: str) -> str | None:
    if table in ("returns", "fund_facts"):
        return field if field in ("value", "value_text") else None
    if table == "statements":
        return field if field in _STATEMENT_FIELDS else None
    if table == "statement_holdings":
        return _HOLDING_FIELDS.get(field)
    return None


def _coerce(row: Any, attr: str, v: Any) -> Any:
    if v is None:
        return None
    if attr in ("period_start", "period_end"):
        return date.fromisoformat(v) if isinstance(v, str) else v
    if attr in ("value_text", "name", "identifier", "currency"):
        return str(v)
    return Decimal(str(v))


def _set_values(table: str, row: Any, new_value: dict, key: dict | None) -> None:
    field_hint = (key or {}).get("field")
    for k, v in new_value.items():
        field = field_hint if (k == "value" and field_hint and table == "statements") else k
        attr = _attr(table, field)
        if attr:
            setattr(row, attr, _coerce(row, attr, v))


def _equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (Decimal, int, float)) and not isinstance(a, bool):
        try:
            return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("1e-9")
        except Exception:  # noqa: BLE001 - a non-numeric old value never matches
            return False
    if isinstance(a, date):
        return _d(a) == (b if isinstance(b, str) else _d(b))
    return str(a) == str(b)


def _matches(table: str, row: Any, old_value: Any, key: dict | None) -> bool:
    if not isinstance(old_value, dict) or not old_value:
        return False
    field_hint = (key or {}).get("field")
    for k, v in old_value.items():
        field = field_hint if (k == "value" and field_hint and table == "statements") else k
        attr = _attr(table, field)
        if attr is None or not _equal(getattr(row, attr), v):
            return False
    return True


def load_decisions(session: Session, drive_file_id: str) -> dict[tuple[str, tuple], list[ReviewAction]]:
    """Value-row decisions for any version of this file, grouped by (table, key)."""
    rows = session.execute(
        select(ReviewAction).join(Document, Document.id == ReviewAction.document_id)
        .where(Document.drive_file_id == drive_file_id, ReviewAction.target_table.in_(VALUE_TABLES))
        .order_by(ReviewAction.created_at, ReviewAction.id)
    ).scalars()
    out: dict[tuple[str, tuple], list[ReviewAction]] = defaultdict(list)
    for ra in rows:
        out[(ra.target_table, _match_key(ra.natural_key))].append(ra)
    return out


def apply_decisions(table: str, row: Any, actions: list[ReviewAction]) -> str:
    """Apply one key's decisions to a recreated row. Returns 'deleted', 'resolved' or
    'unchanged'. The caller deletes the row for 'deleted'."""
    if not actions:
        return "unchanged"
    for a in actions:
        if a.action == "edit" and isinstance(a.new_value, dict) and not _is_delete(a.new_value):
            _set_values(table, row, a.new_value, a.natural_key)
    last = actions[-1]
    if last.action == "edit":
        if _is_delete(last.new_value):
            return "deleted"
        clear_flags(row)
        return "resolved"
    if last.action in ("accept", "choose") and _matches(table, row, last.old_value, last.natural_key):
        if _is_delete(last.new_value):
            return "deleted"
        clear_flags(row)
        return "resolved"
    return "unchanged"


def reapply_decisions(session: Session, drive_file_id: str, rows: dict[str, list[Any]]) -> dict[str, int]:
    """Re-apply decisions to this document's freshly written rows ({table: [rows]}).
    Holdings are handled before their statement, so a deleted statement's holdings
    go with it. Returns counts per outcome."""
    decisions = load_decisions(session, drive_file_id)
    counts = {"resolved": 0, "deleted": 0}
    if not decisions:
        return counts
    for table in ("statement_holdings", "returns", "fund_facts", "statements"):
        for row in list(rows.get(table, [])):
            key = _match_key(natural_key(table, row))
            outcome = apply_decisions(table, row, decisions.get((table, key), []))
            if outcome == "deleted":
                if table == "statements":  # its holdings cascade with it
                    rows["statement_holdings"] = [h for h in rows.get("statement_holdings", [])
                                                  if h.statement_id != row.id]
                session.delete(row)
                rows[table].remove(row)
            if outcome in counts:
                counts[outcome] += 1
    session.flush()
    return counts


__all__ = ["natural_key", "reapply_decisions", "apply_decisions", "load_decisions", "VALUE_TABLES"]

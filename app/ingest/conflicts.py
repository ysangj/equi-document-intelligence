"""Cross-document conflicts (FR-6): same natural key, different value beyond tolerance.

Both rows are kept (no overwrite) and each gets status 'conflict' with
`review_detail.conflict = {conflicting_ids: [other rows that differ], tolerance}`.

Natural keys (T3 "Persistence"):
- returns:    (fund_id, period_end, return_type, net_gross)
- fund_facts: (fund_id, name, as_of_date); numeric values only (text facts never conflict)
- statements: (account_id, fund_id, period_end); any of the six money fields differs

Only rows of VISIBLE documents take part (latest version, not removed), and only
rows from different documents (one document has at most one row per key).
`refresh_conflicts` recomputes whole keys, so it both adds and clears the flag; the
pipeline calls it for every key the reprocessed file had before (any version) and has
now, which also clears stale flags on the other document's rows.

Tolerances: see `app/ingest/validation.py` docstring.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.ingest.review_state import add_flag, drop_flag
from app.models import Document, FundFact, Return, Statement

RETURN_TOLERANCE = Decimal("0.00005")
MONEY_REL_TOLERANCE = Decimal("0.0005")
MONEY_ABS_TOLERANCE = Decimal("1")
NUMBER_ABS_TOLERANCE = Decimal("0.005")
STATEMENT_MONEY_FIELDS = ("opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance")

ReturnKey = tuple  # (fund_id, period_end, return_type, net_gross)


def return_key(r: Return) -> tuple:
    return (r.fund_id, r.period_end, r.return_type, r.net_gross)


def fact_key(f: FundFact) -> tuple:
    return (f.fund_id, f.name, f.as_of_date)


def statement_key(s: Statement) -> tuple:
    return (s.account_id, s.fund_id, s.period_end)


def money_tolerance(a: Decimal, b: Decimal) -> Decimal:
    return max(MONEY_ABS_TOLERANCE, max(abs(a), abs(b)) * MONEY_REL_TOLERANCE)


def fact_tolerance(unit: str | None, a: Decimal, b: Decimal) -> Decimal:
    if unit == "fraction":
        return RETURN_TOLERANCE
    if unit and len(unit) == 3 and unit.isupper():  # currency code -> money
        return money_tolerance(a, b)
    return max(NUMBER_ABS_TOLERANCE, max(abs(a), abs(b)) * MONEY_REL_TOLERANCE)


def returns_differ(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) > RETURN_TOLERANCE


def facts_differ(a: FundFact, b: FundFact) -> bool:
    if a.value is None or b.value is None:
        return False
    if (a.unit or None) != (b.unit or None):
        return True
    return abs(a.value - b.value) > fact_tolerance(a.unit, a.value, b.value)


def statements_differ(a: Statement, b: Statement) -> bool:
    for f in STATEMENT_MONEY_FIELDS:
        x, y = getattr(a, f), getattr(b, f)
        if x is not None and y is not None and abs(x - y) > money_tolerance(x, y):
            return True
    return False


def _tolerance_for(table: str, row) -> float:
    if table == "returns":
        return float(RETURN_TOLERANCE)
    if table == "fund_facts":
        v = row.value or Decimal(0)
        return float(fact_tolerance(row.unit, v, v))
    return float(MONEY_ABS_TOLERANCE)


def _rows(session: Session, model, key_cols, keys: list[tuple]):
    if not keys:
        return []
    stmt = (select(model).join(Document, Document.id == model.document_id)
            .where(Document.is_latest, Document.status != "removed")
            .where(tuple_(*key_cols).in_(keys))
            .order_by(model.id))
    return list(session.execute(stmt).scalars())


def _null_safe_groups(rows, keyfn) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    return groups


def _apply(table: str, groups: dict, differ) -> set[int]:
    changed_docs: set[int] = set()
    for rows in groups.values():
        for r in rows:
            partners = [o.id for o in rows if o.document_id != r.document_id and differ(r, o)]
            if partners:
                changed = add_flag(r, "conflict", {"conflicting_ids": partners, "tolerance": _tolerance_for(table, r)})
            else:
                changed = drop_flag(r, "conflict")
            if changed:
                changed_docs.add(r.document_id)
    return changed_docs


def refresh_conflicts(session: Session, keys: dict[str, set[tuple]]) -> set[int]:
    """Recompute conflict flags for the given natural keys ({table: {key, ...}}).
    Returns the ids of documents whose rows changed state."""
    changed: set[int] = set()
    rk = sorted(keys.get("returns", set()), key=repr)
    rows = _rows(session, Return, (Return.fund_id, Return.period_end, Return.return_type, Return.net_gross), rk)
    changed |= _apply("returns", _null_safe_groups(rows, return_key), lambda a, b: returns_differ(a.value, b.value))

    fk = keys.get("fund_facts", set())
    if fk:
        fund_ids = sorted({k[0] for k in fk})
        stmt = (select(FundFact).join(Document, Document.id == FundFact.document_id)
                .where(Document.is_latest, Document.status != "removed", FundFact.fund_id.in_(fund_ids))
                .order_by(FundFact.id))
        frows = [f for f in session.execute(stmt).scalars() if fact_key(f) in fk]  # NULL as_of_date compares equal
        changed |= _apply("fund_facts", _null_safe_groups(frows, fact_key), facts_differ)

    sk = keys.get("statements", set())
    if sk:
        account_ids = sorted({k[0] for k in sk})
        stmt = (select(Statement).join(Document, Document.id == Statement.document_id)
                .where(Document.is_latest, Document.status != "removed", Statement.account_id.in_(account_ids))
                .order_by(Statement.id))
        srows = [s for s in session.execute(stmt).scalars() if statement_key(s) in sk]
        changed |= _apply("statements", _null_safe_groups(srows, statement_key), statements_differ)
    return changed

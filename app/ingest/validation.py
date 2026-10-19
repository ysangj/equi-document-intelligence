"""Validation rules (FR-6). Pure functions; each returns a `review_detail` entry in the
shape of `app/schemas/review.py::REVIEW_DETAIL_MODELS`, or None when the check passes
(or does not apply).

Tolerances (all inclusive: a difference equal to the tolerance passes):
- monthly (MTD) return range: -50 % .. +50 % (fractions -0.5 .. 0.5).
- YTD compounding: 5 bps (0.0005). Months compound geometrically: prod(1 + r) - 1.
- statement roll-forward: max(1.00, 0.000001 x |closing|) currency units, i.e. one
  unit, or one part per million on large balances. Statement figures are compared as
  stated, so a 20 000 gap on a 13.1 mm balance (Acme) is always a mismatch.
- conflicts (see app/ingest/conflicts.py): returns and fraction-unit facts 0.5 bp
  (0.00005), so any difference visible at two decimals of a percent is a conflict,
  while 1.234 % vs 1.23 % is rounding; money 0.05 % relative (min 1 unit); other
  numbers max(0.005, 0.05 % relative).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MONTHLY_RETURN_MIN = Decimal("-0.5")
MONTHLY_RETURN_MAX = Decimal("0.5")
YTD_TOLERANCE = Decimal("0.0005")
ROLLFORWARD_ABS_TOLERANCE = Decimal("1.00")
ROLLFORWARD_REL_TOLERANCE = Decimal("0.000001")


def _f(x: Decimal | float | None) -> float | None:
    return None if x is None else float(x)


def check_monthly_range(value: Decimal | None) -> dict | None:
    if value is None or MONTHLY_RETURN_MIN <= value <= MONTHLY_RETURN_MAX:
        return None
    return {"value": float(value), "min": float(MONTHLY_RETURN_MIN), "max": float(MONTHLY_RETURN_MAX)}


def compound(values: list[Decimal]) -> Decimal:
    """Geometric compounding of period returns (fractions)."""
    acc = Decimal(1)
    for v in values:
        acc *= Decimal(1) + v
    return acc - Decimal(1)


@dataclass(frozen=True)
class MonthValue:
    period_end: date
    value: Decimal
    return_id: int | None


def check_ytd(stated: Decimal | None, months: list[MonthValue], year: int,
              tolerance: Decimal = YTD_TOLERANCE) -> dict | None:
    """`stated` YTD vs the monthly returns January..month compounded. The caller only
    passes a complete run of months; an incomplete run is not checked."""
    if stated is None or not months:
        return None
    computed = compound([m.value for m in months])
    difference = stated - computed
    if abs(difference) <= tolerance:
        return None
    return {
        "year": year,
        "stated": float(stated),
        "computed": round(float(computed), 10),
        "difference": round(float(difference), 10),
        "tolerance": float(tolerance),
        "months": [{"period_end": m.period_end.isoformat(), "value": float(m.value), "return_id": m.return_id}
                   for m in months],
    }


def rollforward_tolerance(closing: Decimal) -> Decimal:
    return max(ROLLFORWARD_ABS_TOLERANCE, abs(closing) * ROLLFORWARD_REL_TOLERANCE)


def expected_closing(opening: Decimal, contributions: Decimal | None, withdrawals: Decimal | None,
                     pnl: Decimal | None, fees: Decimal | None) -> Decimal:
    """User-approved formula: opening + contributions - withdrawals + pnl - fees.
    Missing flows count as 0. Withdrawals and fees are outflows: their magnitude is
    subtracted however they are signed in the document ("(1,000,000.00)" or "1,000,000")."""
    z = Decimal(0)
    return (opening + (contributions or z) - abs(withdrawals or z) + (pnl or z) - abs(fees or z))


def check_rollforward(*, opening: Decimal | None, contributions: Decimal | None, withdrawals: Decimal | None,
                      pnl: Decimal | None, fees: Decimal | None, closing: Decimal | None,
                      currency: str | None, unparseable_flow: bool = False) -> dict | None:
    """None when it ties, or when it can't be checked: opening, closing and P&L are
    required (FR-6 "when present"), and every flow the document states must have
    parsed (`unparseable_flow`). Missing contributions/withdrawals/fees count as 0."""
    if opening is None or closing is None or pnl is None or unparseable_flow:
        return None
    exp = expected_closing(opening, contributions, withdrawals, pnl, fees)
    tol = rollforward_tolerance(closing)
    diff = closing - exp
    if abs(diff) <= tol:
        return None
    z = Decimal(0)
    return {
        "opening_balance": float(opening),
        "contributions": float(contributions or z),
        "withdrawals": float(abs(withdrawals or z)),
        "pnl": float(pnl),
        "fees": float(abs(fees or z)),
        "expected_closing": float(exp),
        "stated_closing": float(closing),
        "difference": float(diff),
        "tolerance": float(tol),
        "currency": currency,
    }


def check_future(period_start: date | None, shown: date | None, today: date) -> dict | None:
    """Flag a period that has not started yet. Month/quarter periods ending later this
    month/quarter are fine (an MTD estimate is legitimate mid-month)."""
    if period_start is None or shown is None or period_start <= today:
        return None
    return {"date": shown.isoformat(), "today": today.isoformat()}


def check_confidence(confidence: float | None, threshold: float) -> dict | None:
    if confidence is None or confidence >= threshold:
        return None
    return {"confidence": float(confidence), "threshold": float(threshold)}

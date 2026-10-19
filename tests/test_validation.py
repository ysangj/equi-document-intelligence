"""Unit tests: validation rules (FR-6, §14): YTD compounding, roll-forward (with fees),
monthly range, future dates, confidence. Every detail must match the review_detail
shapes of app/schemas/review.py."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.ingest.review_state import add_flag, drop_flag, set_flags, status_for
from app.ingest.validation import (
    MonthValue,
    check_confidence,
    check_future,
    check_monthly_range,
    check_rollforward,
    check_ytd,
    compound,
    expected_closing,
    rollforward_tolerance,
)
from app.schemas.review import validate_review_detail

D = Decimal


def months(*pairs):
    return [MonthValue(date(2026, m, 28 if m == 2 else 31), D(v), i) for i, (m, v) in enumerate(pairs, start=1)]


def test_compound_is_geometric():
    assert compound([D("0.0284"), D("-0.0092")]) == D("1.0284") * D("0.9908") - 1
    assert compound([]) == 0


def test_ytd_meridian_sign_error_flags():
    # stated YTD 0.15%; Jan (0.36)% and Feb 0.21% compound to -0.15%
    detail = check_ytd(D("0.0015"), months((1, "-0.0036"), (2, "0.0021")), 2026)
    assert detail is not None
    assert detail["computed"] == float(D("0.9964") * D("1.0021") - 1)
    assert round(detail["computed"], 6) == -0.001508
    assert abs(detail["difference"] - 0.00300756) < 1e-9 and detail["tolerance"] == 0.0005
    assert [m["return_id"] for m in detail["months"]] == [1, 2]
    validate_review_detail({"ytd_mismatch": detail})


def test_ytd_within_5bps_passes():
    # Kestrel: Jan +2.84%, Feb -0.92% -> 1.894%; stated +1.89%
    assert check_ytd(D("0.0189"), months((1, "0.0284"), (2, "-0.0092")), 2026) is None
    # Alder Asia: 1.10, 0.40, -1.25 -> 0.2345%; stated 0.24% (0.55 bp apart)
    assert check_ytd(D("0.0024"), months((1, "0.011"), (2, "0.004"), (3, "-0.0125")), 2026) is None
    # exactly at the tolerance passes; just beyond fails
    assert check_ytd(D("0.0105"), months((1, "0.01")), 2026) is None
    assert check_ytd(D("0.01051"), months((1, "0.01")), 2026) is not None


def test_ytd_not_checked_without_inputs():
    assert check_ytd(None, months((1, "0.01")), 2026) is None
    assert check_ytd(D("0.01"), [], 2026) is None


def _rf(**kw):
    base = dict(opening=None, contributions=None, withdrawals=None, pnl=None, fees=None, closing=None, currency="USD")
    base.update({k: (D(v) if isinstance(v, str) else v) for k, v in kw.items()})
    return check_rollforward(**base)


def test_rollforward_acme_mismatch():
    # 12.40mm + 0.50mm - 0 + 0.18mm - 0 = 13.08mm != 13.10mm
    d = _rf(opening="12400000", contributions="500000", withdrawals="0", pnl="180000", fees="0", closing="13100000")
    assert d["expected_closing"] == 13080000.0 and d["stated_closing"] == 13100000.0
    assert d["difference"] == 20000.0 and d["tolerance"] == 13.1 and d["currency"] == "USD"
    validate_review_detail({"rollforward_mismatch": d})


def test_rollforward_birchwood_and_cedar_tie_with_fees():
    # Birchwood: 48.2mm + 0 - 1.0mm + 236k - 12,050 = 47,423,950 (withdrawals/fees in parentheses)
    assert _rf(opening="48200000", contributions="0", withdrawals="-1000000", pnl="236000", fees="-12050",
               closing="47423950") is None
    # Cedar Ridge: 5.0mm + 250k - 0 + 71,830 - (19,560 + 10,450) = 5,291,820
    assert _rf(opening="5000000", contributions="250000", withdrawals="0", pnl="71830", fees="30010",
               closing="5291820") is None
    # Without fees Cedar would not tie: the fees term matters
    assert _rf(opening="5000000", contributions="250000", withdrawals="0", pnl="71830", closing="5291820") is not None


def test_rollforward_missing_flows_count_as_zero():
    assert _rf(opening="100", pnl="5", closing="105") is None
    assert expected_closing(D(100), None, None, D(5), None) == D(105)
    d = _rf(opening="100", pnl="5", closing="110")
    assert d["fees"] == 0.0 and d["contributions"] == 0.0 and d["withdrawals"] == 0.0


def test_rollforward_skipped_when_not_checkable():
    assert _rf(contributions="1", pnl="1", closing="2") is None  # no opening
    assert _rf(opening="1", pnl="1") is None  # no closing
    assert _rf(opening="100", closing="999") is None  # no P&L
    assert check_rollforward(opening=D(1), contributions=None, withdrawals=None, pnl=D(1), fees=None,
                             closing=D(99), currency=None, unparseable_flow=True) is None


def test_rollforward_tolerance():
    assert rollforward_tolerance(D("100")) == D("1.00")
    assert rollforward_tolerance(D("13100000")) == D("13.1")
    assert _rf(opening="100", pnl="0", closing="101") is None  # 1.00 difference passes
    assert _rf(opening="100", pnl="0", closing="101.01") is not None


def test_monthly_range():
    assert check_monthly_range(D("0.5")) is None and check_monthly_range(D("-0.5")) is None
    d = check_monthly_range(D("0.62"))
    assert d == {"value": 0.62, "min": -0.5, "max": 0.5}
    validate_review_detail({"out_of_range": d})
    assert check_monthly_range(None) is None


def test_future_date():
    today = date(2026, 10, 16)
    assert check_future(date(2026, 10, 1), date(2026, 10, 31), today) is None  # this month: MTD estimate ok
    d = check_future(date(2026, 11, 1), date(2026, 11, 30), today)
    assert d == {"date": "2026-11-30", "today": "2026-10-16"}
    validate_review_detail({"future_date": d})


def test_confidence():
    assert check_confidence(0.7, 0.7) is None
    d = check_confidence(0.69, 0.7)
    assert d == {"confidence": 0.69, "threshold": 0.7}
    validate_review_detail({"low_confidence": d})
    assert check_confidence(None, 0.7) is None


class Row:
    status = "ok"
    review_reasons: list = []
    review_detail = None


def test_review_state_keeps_invariants():
    r = Row()
    set_flags(r, {"low_confidence": {"confidence": 0.5, "threshold": 0.7},
                  "conflict": {"conflicting_ids": [2], "tolerance": 0.00005}})
    assert r.status == "conflict" and r.review_reasons == ["low_confidence", "conflict"]  # canonical order
    assert drop_flag(r, "conflict") and r.status == "needs_review"
    assert not drop_flag(r, "conflict")
    assert add_flag(r, "ytd_mismatch", {"x": 1}) and not add_flag(r, "ytd_mismatch", {"x": 1})
    set_flags(r, {})
    assert (r.status, r.review_reasons, r.review_detail) == ("ok", [], None)
    assert status_for(["conflict"]) == "conflict" and status_for([]) == "ok"

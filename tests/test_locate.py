"""Unit tests for `app.normalize.locate` (value-in-snippet locator, BUG-Q2-05/06)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.values import snippet_highlight
from app.normalize.locate import locate_value, snippet_numbers_sum_to, value_in_snippet

HARBOR = "HCO-A,Harbor Credit Opps Fund,A,2026-03,1225.49,0.0055,937978036"


def text_at(snippet, kind, value, raw=None):
    hit = locate_value(snippet, kind, value, raw)
    return None if hit is None else snippet[hit[0]:hit[1]]


@pytest.mark.parametrize("snippet,kind,value,expected", [
    # BUG-Q2-05: CSV fields are never glued to a thousands group ("03,122" + "5.49")
    (HARBOR, "money", 1225.49, "1225.49"),
    (HARBOR, "money", Decimal("1225.4900000000"), "1225.49"),
    (HARBOR, "money", 937978036, "937978036"),
    (HARBOR, "percent", 0.0055, "0.0055"),  # a raw fraction in a CSV
    ("MER,2026-01,-0.36,1", "percent", -0.0036, "-0.36"),  # a bare percent number
    ("HCO-A,2026-01,0.0071", "percent", 0.0071, "0.0071"),  # 0.71% written as a fraction
    ("Net 0.71%", "percent", 0.0071, "0.71%"),
    ("+110 bps", "percent", 0.011, "+110 bps"),
    ("Closing balance 13,100,000.00", "money", 13100000, "13,100,000.00"),
    ("USD 5,000,000.00", "money", 5000000, "5,000,000.00"),
    ("AUM $412.6mm", "money", 412600000, "$412.6mm"),
    ("Withdrawals (1,000,000.00)", "money", 1000000, "(1,000,000.00)"),
    ("Holding A Hedge fund 14,100,000 29.7%", "percent", 0.297, "29.7%"),
    ("Redemptions -", "money", 0, "-"),  # "-" printed for nil
])
def test_locate_finds_the_value(snippet, kind, value, expected):
    assert text_at(snippet, kind, value) == expected


@pytest.mark.parametrize("snippet,kind,value", [
    # BUG-Q2-06: the weight is not in a market-value snippet; integers never read as fractions
    ("Meridian Rates Relative Value Fund Hedge fund 14,100,000", "percent", 0.297),
    ("Cash Cash 4,223,950", "percent", 0.089),
    ("Cash Cash 280,000", "percent", 0.021),
    ("Jan-26 1.92%", "percent", 0.0189),
    ("as of 2026", "money", 2026),  # a year
    ("Jan-26 1.92%", "money", 0),  # a date's dash is not a nil dash
    ("1,225", "money", 225),  # never read from inside a thousands group
])
def test_locate_rejects_values_not_written(snippet, kind, value):
    assert locate_value(snippet, kind, value) is None
    assert value_in_snippet(snippet, kind, value) is False


def test_raw_string_is_tried_first_but_only_while_it_still_reads_as_the_value():
    s = "Fees (12,050.00) and fees YTD 12,050.00"
    assert locate_value(s, "money", 12050, raw="(12,050.00)") == (5, 16)
    # a reviewer edited the value: the stale raw string is ignored
    assert locate_value(s, "money", 13000, raw="(12,050.00)") is None
    assert text_at("USD 5,000,000.00", "money", 5000000, raw="USD 5,000,000.00") == "USD 5,000,000.00"
    assert text_at("a 1.5 b 21.5%", "percent", 0.215, raw="21.5%") == "21.5%"


def test_value_in_snippet_skips_non_numeric_or_missing():
    assert value_in_snippet("Kestrel", "text", "Kestrel") is None
    assert value_in_snippet(None, "money", 1) is None
    assert value_in_snippet("x 1", "money", None) is None


def test_snippet_numbers_sum_to():
    assert snippet_numbers_sum_to("Management fee (19,560.00)\nIncentive allocation (10,450.00)", 30010)
    assert not snippet_numbers_sum_to("Management fee (19,560.00)\nIncentive allocation (10,450.00)", 30000)
    assert not snippet_numbers_sum_to("Fees (12,050.00)", 12050)  # one number: not a sum


def test_snippet_highlight_csv_utf16_offsets():
    hl = snippet_highlight(HARBOR, "money", 1225.49)
    assert hl == {"start": 40, "end": 47, "text": "1225.49"}
    s = "Fund – Class A, 2026-03, 1225.49"
    hl = snippet_highlight(s, "money", 1225.49)
    assert hl["text"] == "1225.49" and hl["start"] == len(s.encode("utf-16-le")) // 2 - 7
    assert snippet_highlight("Fees (12,050.00)", "money", 12050, raw="(12,050.00)")["text"] == "(12,050.00)"

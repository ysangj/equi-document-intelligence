"""Unit tests: normalization (FR-6, §14): percentages, amounts, currencies, numbers,
dates/periods, alias matching, snippet text matching."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.normalize.dates import (
    DAY,
    MONTH,
    QUARTER,
    YEAR,
    parse_period,
    return_period_end,
    return_period_start,
)
from app.normalize.names import (
    FUZZY_THRESHOLD,
    alias_key,
    best_fuzzy_match,
    core_tokens,
    display_name,
    distinct_identity,
    match_score,
    tokens_equivalent,
)
from app.normalize.numbers import (
    BPS,
    FRACTION,
    PERCENT,
    UnitContext,
    csv_column_header,
    has_money_marker,
    has_percent_unit,
    header_unit,
    parse_amount,
    parse_currency,
    parse_number,
    parse_percent,
    to_decimal,
)
from app.normalize.text import normalize_for_match, snippet_in_text
from app.sources.local import DEFAULT_LOCAL_DIR

D = Decimal

# ---- percentages ----------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("1.2%", "0.012"),
    ("+2.84%", "0.0284"),
    ("-0.92%", "-0.0092"),
    ("120bps", "0.012"),
    ("+110 bps", "0.011"),
    ("-125 bps", "-0.0125"),
    ("15bp", "0.0015"),
    ("24 basis points", "0.0024"),
    ("(0.36)%", "-0.0036"),
    ("(0.36%)", "-0.0036"),
    ("(36 bps)", "-0.0036"),
    ("−0.5%", "-0.005"),  # Unicode minus
    ("–0.5%", "-0.005"),  # en dash used as a minus
    ("1.25% p.a.", "0.0125"),
    ("+1.89% (est.)", "0.0189"),
    ("0.71 %", "0.0071"),
    ("3.5 percent", "0.035"),
    ("0,71%", "0.0071"),  # European decimal comma
])
def test_percent_with_explicit_unit(raw, expected):
    p = parse_percent(raw)
    assert p.value == D(expected) and p.rule == "value"


@pytest.mark.parametrize("raw", ["", "  ", None, "n/a", "15% over a 5% hurdle", "1.5% / 20%", "abc%", "((1))%",
                                 "−", "--0.3%", "1.2.3%"])
def test_percent_unparseable(raw):
    p = parse_percent(raw)
    assert p.value is None and p.error


def test_bare_decimal_without_context_is_not_guessed():
    p = parse_percent("0.36")
    assert p.value is None and p.rule == "no_unit_in_context"
    assert parse_percent(0.36).value is None


def test_bare_number_unit_attached_in_snippet():
    assert parse_percent("1.92", UnitContext(snippet="Jan-26 1.92%")).value == D("0.0192")
    p = parse_percent("110", UnitContext(snippet="Month (net of fees) +110 bps"))
    assert (p.value, p.rule) == (D("0.011"), "snippet_attached")
    assert parse_percent("(0.8)", UnitContext(snippet="Feb (0.8)%")).value == D("-0.008")


def test_bare_number_csv_header_rules():
    meridian = (DEFAULT_LOCAL_DIR / "meridian_returns_export.csv").read_text()
    harbor = (DEFAULT_LOCAL_DIR / "harbor_admin_nav_export_2026-03.csv").read_text()
    row = "Meridian Rates RV Fund,1/31/2026,-0.36,-0.15,"
    assert csv_column_header(meridian, "-0.36", row) == "Return (Net) %"
    assert csv_column_header(meridian, "-0.15", row) == "Return (Gross) %"
    h = csv_column_header(harbor, "0.0071", "HCO-A,Harbor Credit Opps Fund,A,2026-01,1211.03,0.0071,926915118")
    assert h == "NET_RET"
    # the planted unit variety: 0.36 in a "%" column is a percent, 0.0071 in a unit-less one a fraction
    p = parse_percent("-0.36", UnitContext(column_header="Return (Net) %", is_csv=True))
    assert (p.value, p.unit, p.rule) == (D("-0.0036"), PERCENT, "csv_header")
    p = parse_percent("0.0071", UnitContext(column_header="NET_RET", is_csv=True))
    assert (p.value, p.unit) == (D("0.0071"), FRACTION)
    assert parse_percent(0.0071, UnitContext(column_header="NET_RET", is_csv=True)).value == D("0.0071")
    # CSV value whose column can't be found: not guessed from the page
    p = parse_percent("0.5", UnitContext(is_csv=True, page_text="Return %"))
    assert p.value is None and p.rule == "csv_column_not_found"


def test_csv_column_header_ambiguous_or_missing():
    text = "A %,B\n0.5,0.5\n"
    assert csv_column_header(text, "0.5", None) is None  # two columns with different units
    assert csv_column_header(text, "9.9", None) is None
    assert csv_column_header("only a header\n", "1", None) is None
    assert csv_column_header(None, "1", None) is None


@pytest.mark.parametrize("header, unit", [
    ("Return (Net) %", PERCENT), ("NET_RET", FRACTION), ("ret_pct", PERCENT), ("Perf (bps)", BPS),
    ("Monthly return", FRACTION),
])
def test_header_unit(header, unit):
    assert header_unit(header) == unit


def test_bare_number_standalone_markers_in_snippet_then_page():
    p = parse_percent("1.92", UnitContext(snippet="Perf. (net) — % 2026 1.92"))
    assert (p.value, p.rule) == (D("0.0192"), "snippet_marker")
    page = "Northwind Global Macro Fund\nPerf. (net) — %\nYear Jan Feb\n2026 1.92 0.55"
    p = parse_percent("0.55", UnitContext(snippet="2026 1.92 0.55", page_text=page))
    assert (p.value, p.rule) == (D("0.0055"), "page_marker")
    p = parse_percent("110", UnitContext(page_text="Performance in basis points, net of all fees"))
    assert (p.value, p.unit) == (D("0.011"), BPS)
    # a "%" attached to another number is not a unit marker for this one
    assert parse_percent("0.3", UnitContext(page_text="a 25% investor-level gate")).value is None
    # both markers on the page: ambiguous, not guessed
    assert parse_percent("0.3", UnitContext(page_text="Return % ... shown in bps")).value is None


def test_has_percent_and_money_markers():
    assert has_percent_unit("-6.8%") and has_percent_unit("+110 bps") and not has_percent_unit("1.84")
    assert has_money_marker("$412.6mm") and has_money_marker("USD 1.18bn") and has_money_marker("12.40mm")
    assert not has_money_marker("1.84") and not has_money_marker("6.2x")


# ---- amounts, currencies, numbers ------------------------------------------------------------------


@pytest.mark.parametrize("raw, value, currency", [
    ("1.2mm", "1200000", None),
    ("12.40mm", "12400000", None),
    ("$1,200,000", "1200000", "USD"),
    ("(1,000)", "-1000", None),
    ("USD 5,000,000.00", "5000000", "USD"),
    ("(1,000,000.00)", "-1000000", None),
    ("(0.00)", "0", None),
    ("$640mm", "640000000", "USD"),
    ("$640m", "640000000", "USD"),
    ("USD 1.18bn", "1180000000", "USD"),
    ("$905 million", "905000000", "USD"),
    ("€2.5 billion", "2500000000", "EUR"),
    ("£12k", "12000", "GBP"),
    ("1.234.567,89", "1234567.89", None),
    ("937978036", "937978036", None),
    ("-", "0", None),
    ("—", "0", None),
    ("nil", "0", None),
    ("-250,000.00", "-250000", None),
])
def test_parse_amount(raw, value, currency):
    a = parse_amount(raw)
    assert a.value == D(value) and a.currency == currency


@pytest.mark.parametrize("raw", ["", "n/a", "abc", "1,2,3", "(1", "12 apples"])
def test_parse_amount_unparseable(raw):
    assert parse_amount(raw).value is None


def test_parse_amount_numbers():
    assert parse_amount(1500.5).value == D("1500.5")
    assert parse_amount(None).error == "empty"


@pytest.mark.parametrize("raw, code", [
    ("USD", "USD"), ("usd", "USD"), ("Base currency: USD", "USD"), ("US$", "USD"), ("$412.6mm", "USD"),
    ("euro", "EUR"), ("Class A USD", "USD"), ("AUM_USD", "USD"), ("EUR 1.2bn", "EUR"), ("nothing", None), (None, None),
])
def test_parse_currency(raw, code):
    assert parse_currency(raw) == code


@pytest.mark.parametrize("raw, value, unit", [
    ("1.84", "1.84", None), ("6.2x", "6.2", "x"), ("3.4 years", "3.4", "years"), ("(0.3)", "-0.3", None),
    ("-1.12", "-1.12", None), ("1,225.49", "1225.49", None),
])
def test_parse_number(raw, value, unit):
    n = parse_number(raw)
    assert (n.value, n.unit) == (D(value), unit)


def test_parse_number_rejects_text():
    assert parse_number("1.75 / 20").value is None and parse_number("Monthly").value is None


def test_to_decimal_separators():
    assert to_decimal("1,234,567.5") == D("1234567.5")
    assert to_decimal("1 234") == D("1234")
    assert to_decimal("1,5") == D("1.5")
    assert to_decimal("1,23,4") is None and to_decimal("") is None


# ---- dates ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, start, end, precision", [
    ("Jan-26", date(2026, 1, 1), date(2026, 1, 31), MONTH),
    ("Jan 26", date(2026, 1, 1), date(2026, 1, 31), MONTH),
    ("Jan '26", date(2026, 1, 1), date(2026, 1, 31), MONTH),
    ("January 2026", date(2026, 1, 1), date(2026, 1, 31), MONTH),
    ("Feb 2026", date(2026, 2, 1), date(2026, 2, 28), MONTH),
    ("2026-03", date(2026, 3, 1), date(2026, 3, 31), MONTH),
    ("2026 Mar", date(2026, 3, 1), date(2026, 3, 31), MONTH),
    ("01/31/2026", date(2026, 1, 31), date(2026, 1, 31), DAY),
    ("1/31/26", date(2026, 1, 31), date(2026, 1, 31), DAY),
    ("31/01/2026", date(2026, 1, 31), date(2026, 1, 31), DAY),  # first number > 12: day-first
    ("31.01.2026", date(2026, 1, 31), date(2026, 1, 31), DAY),
    ("2026-01-31", date(2026, 1, 31), date(2026, 1, 31), DAY),
    ("31 December 2025", date(2025, 12, 31), date(2025, 12, 31), DAY),
    ("January 20, 2026", date(2026, 1, 20), date(2026, 1, 20), DAY),
    ("Q1 2026", date(2026, 1, 1), date(2026, 3, 31), QUARTER),
    ("Q4-25", date(2025, 10, 1), date(2025, 12, 31), QUARTER),
    ("1Q26", date(2026, 1, 1), date(2026, 3, 31), QUARTER),
    ("2026 Q2", date(2026, 4, 1), date(2026, 6, 30), QUARTER),
    ("fourth quarter of 2025", date(2025, 10, 1), date(2025, 12, 31), QUARTER),
    ("2025", date(2025, 1, 1), date(2025, 12, 31), YEAR),
    ("FY2025", date(2025, 1, 1), date(2025, 12, 31), YEAR),
    ("YTD 2026", date(2026, 1, 1), date(2026, 12, 31), YEAR),
    ("As of 01/31/2026", date(2026, 1, 31), date(2026, 1, 31), DAY),
    ("Tear Sheet | As of 02/28/2026", date(2026, 2, 28), date(2026, 2, 28), DAY),
    ("For the quarter ended 31 December 2025", date(2025, 12, 31), date(2025, 12, 31), DAY),
    ("Monthly Factsheet · January 2026", date(2026, 1, 1), date(2026, 1, 31), MONTH),
    ("Investor Report — March 2026", date(2026, 3, 1), date(2026, 3, 31), MONTH),
])
def test_parse_period(raw, start, end, precision):
    p = parse_period(raw)
    assert (p.start, p.end, p.precision) == (start, end, precision)


def test_parse_period_ranges_and_prefer():
    s = "Account Summary — 01/01/2026 to 01/31/2026 (USD)"
    assert parse_period(s).end == date(2026, 1, 31)
    assert parse_period(s, prefer="start").start == date(2026, 1, 1)
    assert parse_period("1 October 2025 - 31 December 2025", prefer="start").start == date(2025, 10, 1)


@pytest.mark.parametrize("raw, end", [
    ("2025 YTD", date(2025, 12, 31)), ("2026 YTD", date(2026, 12, 31)), ("2025 Oct", date(2025, 10, 31)),
    ("quarter ended 31 December 2025", date(2025, 12, 31)), ("Wed, 18 Feb 2026 16:40:00 +0000", date(2026, 2, 18)),
])
def test_parse_period_strings_seen_in_real_fixtures(raw, end):
    assert parse_period(raw).end == end


def test_bare_quarter_with_reference():
    assert parse_period("Q1") is None
    q1 = parse_period("Q1", ref=date(2026, 3, 31))
    assert (q1.start, q1.end, q1.precision) == (date(2026, 1, 1), date(2026, 3, 31), QUARTER)
    assert parse_period("Q4", ref=date(2026, 1, 31)).end == date(2025, 12, 31)


def test_parse_period_bare_month_needs_reference():
    assert parse_period("January") is None
    assert parse_period("January", ref=date(2026, 2, 28)).end == date(2026, 1, 31)
    assert parse_period("December", ref=date(2026, 1, 31)).end == date(2025, 12, 31)  # latest Dec on/before ref


@pytest.mark.parametrize("raw", [None, "", "Year to date", "Q1 / YTD", "Since inception", "13/13/2026", "2026-13"])
def test_parse_period_unparseable(raw):
    assert parse_period(raw) is None


def test_return_period_end_snaps_by_type():
    jan15 = parse_period("2026-01-15")
    assert return_period_end(jan15, "MTD", None) == date(2026, 1, 31)
    assert return_period_end(jan15, "QTD", None) == date(2026, 3, 31)
    assert return_period_end(parse_period("Q1 2026"), "QTD", None) == date(2026, 3, 31)
    assert return_period_end(parse_period("Q1 2026"), "YTD", None) == date(2026, 3, 31)
    # a year-only YTD in the as-of year is "YTD through the as-of month"
    assert return_period_end(parse_period("YTD 2026"), "YTD", date(2026, 1, 31)) == date(2026, 1, 31)
    assert return_period_end(parse_period("2025"), "YTD", date(2026, 1, 31)) == date(2025, 12, 31)
    assert return_period_end(parse_period("2025 YTD"), "YTD", date(2026, 2, 28)) == date(2025, 12, 31)
    assert return_period_end(parse_period("2026 YTD"), "YTD", date(2026, 2, 28)) == date(2026, 2, 28)
    assert return_period_end(parse_period("Q1 2026"), "MTD", None) is None  # MTD needs a month
    assert return_period_end(parse_period("2026"), "QTD", None) is None
    assert return_period_end(parse_period("2026"), "QTD", date(2026, 5, 31)) == date(2026, 6, 30)


def test_return_period_start():
    assert return_period_start(date(2026, 3, 31), "QTD") == date(2026, 1, 1)
    assert return_period_start(date(2026, 3, 31), "MTD") == date(2026, 3, 1)


# ---- names / fuzzy alias resolution -----------------------------------------------------------------


def test_alias_key_normalizes_case_whitespace_and_dashes():
    assert alias_key("  Kestrel  Quant Equity Fund LP ") == alias_key("kestrel quant equity fund lp")
    assert alias_key("Alder Asia Fund SPC – Class A") == alias_key("Alder Asia Fund SPC - Class A")
    assert display_name("Alder  Asia Fund SPC – Class A") == "Alder Asia Fund SPC – Class A"


def test_core_tokens_drop_generic_words_and_share_class():
    assert core_tokens("Alder Asia Fund SPC – Class A") == ["alder", "asia"]
    assert core_tokens("Kestrel Quant Equity Fund L.P.") == ["kestrel", "quant", "equity"]
    assert core_tokens("Northwind Macro Fund Ltd") == ["northwind", "macro"]


@pytest.mark.parametrize("a, b", [
    ("Northwind Macro Fund Ltd", "Northwind Global Macro Fund"),  # planted alias (email vs factsheet)
    ("Alder Asia Fund SPC – Class A", "Alder Asia Fund"),  # planted alias (share class)
    ("Meridian Rates RV Fund", "Meridian Rates Relative Value Fund"),  # acronym
    ("Harbor Credit Opps Fund", "Harbor Credit Opportunities Fund"),  # abbreviation
    ("Kestrel Quant Equity Fund", "Kestrel Quant Equity Fund LP"),
])
def test_fuzzy_matches_same_fund(a, b):
    assert match_score(a, b) >= FUZZY_THRESHOLD
    assert not distinct_identity(a, b)
    assert best_fuzzy_match(a, [(7, b)]).entity_id == 7


@pytest.mark.parametrize("a, b", [
    ("Alder Asia Fund", "Alder Dubai Fund"),  # planted siblings: must NOT merge
    ("Alder Asia Fund SPC – Class A", "Alder Dubai Fund"),
    ("Northwind Global Macro Fund", "Northwind Asia Macro Fund"),
    ("Kestrel Fund II", "Kestrel Fund III"),
    ("Kestrel Quant Equity Fund LP", "Northwind Global Macro Fund"),
])
def test_fuzzy_never_merges_distinct_funds(a, b):
    assert best_fuzzy_match(a, [(1, b)]) is None
    assert best_fuzzy_match(b, [(1, a)]) is None


def test_asia_vs_dubai_both_layers_reject():
    # score alone is below the threshold, and the guard rejects it independently
    assert match_score("Alder Asia Fund", "Alder Dubai Fund") < FUZZY_THRESHOLD
    assert distinct_identity("Alder Asia Fund", "Alder Dubai Fund")
    # even at a permissive threshold the guard keeps them apart
    assert best_fuzzy_match("Alder Asia Fund", [(1, "Alder Dubai Fund")], threshold=50) is None


def test_best_fuzzy_match_prefers_higher_score_then_lower_id():
    cands = [(3, "Alder Asia Fund"), (2, "Alder Dubai Fund"), (1, "Alder Asia Fund")]
    m = best_fuzzy_match("Alder Asia Fund SPC – Class A", cands)
    assert (m.entity_id, m.score) == (1, 100.0)


def test_tokens_equivalent():
    assert tokens_equivalent("opps", "opportunities") and tokens_equivalent("intl", "international")
    assert tokens_equivalent("strategies", "strategy")
    assert not tokens_equivalent("asia", "dubai") and not tokens_equivalent("ii", "iii")


def test_empty_core_never_matches():
    assert match_score("The Fund", "Fund Ltd") == 0.0


# ---- snippet text matching ---------------------------------------------------------------------------


def test_snippet_matching_normalizes_whitespace_case_and_dashes():
    text = normalize_for_match("SHARPE (SINCE\nINCEPT.)\n1.84\nInvestor Report — January 2026")
    assert snippet_in_text("Sharpe (since incept.) 1.84", text)
    assert snippet_in_text("Investor Report - January 2026", text)
    assert not snippet_in_text("Sharpe 1.85", text)
    assert not snippet_in_text("", text) and not snippet_in_text(None, text)


def test_snippet_matching_tolerates_wrapped_words():
    text = normalize_for_match("Liquidity in our core cash-\nfutures basis positions")
    assert snippet_in_text("core cash-futures basis", text)

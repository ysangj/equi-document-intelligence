"""Period strings as written -> period start/end dates (FR-6).

`parse_period` understands, case-insensitively:
- days: "2026-01-31", "01/31/2026" and "1/31/26" (US month-first; day-first only when
  the first number is > 12), "31.01.2026" (dots: day-first), "31 Jan 2026",
  "31 December 2025", "January 31, 2026";
- months: "Jan-26", "Jan 26", "Jan '26", "January 2026", "2026-03", "2026/03",
  "2026 Jan", "2025 Oct";
- quarters: "Q1 2026", "Q1-26", "1Q26", "2026 Q1", "fourth quarter of 2025";
- halves: "H1 2026"; years: "2026", "FY2025", "YTD 2026", "2025 YTD", "full year 2025";
- with a reference date: a bare month ("January") or quarter ("Q1") is the latest one
  on or before the reference;
- prefixes such as "As of", "For the quarter ended", and ranges ("01/01/2026 to
  01/31/2026": the last part is the end, the first part the start).
Two-digit years are 20yy (yy < 70) or 19yy.

A month-precision period ends on the month's last day, a quarter on the quarter's
last day (`Period.end`). Returns use `return_period_end` to snap to month end (MTD,
YTD, ITD) or quarter end (QTD).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from app.normalize.text import clean

DAY, MONTH, QUARTER, HALF, YEAR = "day", "month", "quarter", "half", "year"

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_ORDINAL_Q = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
_PREFIXES = re.compile(
    r"^(?:as\s+(?:of|at)|for\s+the\s+(?:month|quarter|period|year|half[-\s]year)\s+(?:ended|ending)|"
    r"(?:month|quarter|period|year)\s+(?:ended|ending|end)|period|date|ended|ending|month\s+end|"
    r"statement\s+date|valuation\s+date)\s*[:,]?\s*"
)
_RANGE = re.compile(r"\s+(?:to|through|until|thru|-)\s+")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    precision: str


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def quarter_end(d: date) -> date:
    m = ((d.month - 1) // 3 + 1) * 3
    return date(d.year, m, calendar.monthrange(d.year, m)[1])


def quarter_start(d: date) -> date:
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _year(y: str) -> int:
    n = int(y)
    if len(y) == 2:
        return 2000 + n if n < 70 else 1900 + n
    return n


def _month_period(y: int, m: int) -> Period:
    return Period(date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]), MONTH)


def _quarter_period(y: int, q: int) -> Period:
    m = q * 3
    return Period(date(y, m - 2, 1), date(y, m, calendar.monthrange(y, m)[1]), QUARTER)


def _day(y: int, m: int, d: int) -> Period | None:
    try:
        x = date(y, m, d)
    except ValueError:
        return None
    return Period(x, x, DAY)


def _month(name: str) -> int | None:
    return _MONTHS.get(name.rstrip("."))


def _parse_one(s: str, ref: date | None) -> Period | None:
    s = s.strip(" .,;:")
    if not s:
        return None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return _day(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s):
        return _month_period(int(m[1]), int(m[2])) if 1 <= int(m[2]) <= 12 else None
    if m := re.fullmatch(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})", s):
        a, b, y = int(m[1]), int(m[3]), _year(m[4])
        if m[2] == "." or a > 12:  # day-first
            return _day(y, b, a)
        return _day(y, a, b)
    if (m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[\s-]+([a-z]+\.?)[\s,-]+(\d{4}|\d{2})", s)) and _month(m[2]):
        return _day(_year(m[3]), _month(m[2]), int(m[1]))
    if (m := re.fullmatch(r"([a-z]+\.?)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)) and _month(m[1]):
        return _day(int(m[3]), _month(m[1]), int(m[2]))
    if (m := re.fullmatch(r"([a-z]+\.?)[\s\-'/]*(\d{4}|\d{2})", s)) and _month(m[1]):
        return _month_period(_year(m[2]), _month(m[1]))
    if m := re.fullmatch(r"(\d{4})[\s\-/]*([a-z]+\.?)", s):
        mo = _month(m[2])
        if mo:
            return _month_period(int(m[1]), mo)
    if m := re.fullmatch(r"q([1-4])[\s\-/']*(\d{4}|\d{2})", s) or re.fullmatch(r"([1-4])q[\s\-']*(\d{4}|\d{2})", s):
        return _quarter_period(_year(m[2]), int(m[1]))
    if m := re.fullmatch(r"(\d{4})[\s\-/]*q([1-4])", s):
        return _quarter_period(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter(?:\s+of)?\s+(\d{4})", s):
        return _quarter_period(int(m[2]), _ORDINAL_Q[m[1]])
    if m := re.fullmatch(r"h([12])[\s\-/']*(\d{4}|\d{2})", s):
        y, h = _year(m[2]), int(m[1])
        return Period(date(y, 1 if h == 1 else 7, 1), date(y, 6, 30) if h == 1 else date(y, 12, 31), HALF)
    if m := (re.fullmatch(r"(?:fy|ytd|cy|year|full[\s-]year|calendar\s+year)?[\s\-]*(\d{4})", s)
             or re.fullmatch(r"(\d{4})[\s\-]*(?:ytd|fy|full[\s-]year|year)", s)):
        y = int(m[1])
        if 1900 <= y <= 2200:
            return Period(date(y, 1, 1), date(y, 12, 31), YEAR)
    if ref is not None:
        mo = _month(s)
        if mo:  # "January" -> the latest January on or before the reference date
            return _month_period(ref.year if mo <= ref.month else ref.year - 1, mo)
        if m := re.fullmatch(r"q([1-4])|([1-4])q", s):  # "Q1" -> the latest Q1 on or before the reference
            q = int(m[1] or m[2])
            return _quarter_period(ref.year if q * 3 <= ref.month else ref.year - 1, q)
    return None


def parse_period(raw: str | None, *, ref: date | None = None, prefer: str = "end") -> Period | None:
    """Parse a period/date string; None if it has no recognisable date.

    `ref` resolves a bare month name. `prefer="start"` picks the first part of a range."""
    if raw is None:
        return None
    s = clean(str(raw)).lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # "(USD)", "(est.)"
    s = re.sub(r"\s+", " ", s).strip()
    s = _PREFIXES.sub("", s)
    s = re.sub(r"^(?:mtd|qtd|ytd|itd|month|quarter|year\s+to\s+date|month\s+to\s+date)\b\s*[:,]?\s*(?=\S)", "", s) \
        if re.search(r"\d", s) else s
    parts = [p for p in _RANGE.split(s) if p.strip()]
    if not parts:
        return None
    p = _parse_one(s, ref) if len(parts) > 1 else None  # "2026-01" style strings never split
    if p is None:
        for part in parts if prefer == "start" else reversed(parts):
            p = _parse_one(part, ref)
            if p is not None:
                break
    if p is None:
        p = _search_embedded(s, ref, prefer)
    return p


_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_EMBEDDED = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.]\d{1,2}[/.](?:\d{4}|\d{2})"
    r"|\d{1,2}\s+(?:" + _MONTH_ALT + r")\.?,?\s+\d{4}"
    r"|(?:" + _MONTH_ALT + r")\.?\s+\d{1,2},\s+\d{4}"
    r"|(?:" + _MONTH_ALT + r")\.?[\s\-'/]*(?:\d{4}|\d{2})\b"
    r"|q[1-4][\s\-/']*(?:\d{4}|\d{2})\b"
    r"|(?:first|second|third|fourth)\s+quarter(?:\s+of)?\s+\d{4}"
)


def _search_embedded(s: str, ref: date | None, prefer: str) -> Period | None:
    """Fallback for phrases such as "Monthly Factsheet · January 2026"."""
    hits = [m.group(0) for m in _EMBEDDED.finditer(s)]
    for h in hits if prefer == "start" else reversed(hits):
        p = _parse_one(h, ref)
        if p is not None:
            return p
    return None


def return_period_end(period: Period, return_type: str, as_of: date | None) -> date | None:
    """Canonical `returns.period_end` for a parsed period and return type.

    MTD, YTD, ITD, annualized and unknown snap to the month end; QTD to the quarter
    end. A year-only YTD/QTD/ITD period in the as-of year uses the as-of month (e.g.
    "YTD 2026" in a January factsheet -> 2026-01-31); in another year it is the year end.
    An MTD return needs at least month precision (None otherwise)."""
    p = period
    if return_type == "MTD":
        return month_end(p.end) if p.precision in (DAY, MONTH) else None
    if return_type == "QTD":
        if p.precision in (DAY, MONTH, QUARTER):
            return quarter_end(p.end)
        if as_of and as_of.year == p.end.year:
            return quarter_end(as_of)
        return None
    if p.precision in (DAY, MONTH):
        return month_end(p.end)
    if p.precision == YEAR and as_of and as_of.year == p.end.year and as_of < p.end:
        return month_end(as_of)
    return p.end


def return_period_start(period_end: date, return_type: str) -> date:
    """First day of the period a return covers (for the future-date check)."""
    if return_type == "QTD":
        return quarter_start(period_end)
    return date(period_end.year, period_end.month, 1)

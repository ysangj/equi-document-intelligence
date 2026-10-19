"""Find where a stored value is written inside its source snippet.

Shared by the API (`snippet_highlight`, FR-9 side panel) and the pipeline (the
"value appears in its snippet" provenance check, §2 "every value is traceable").

Matching rules:
- `raw`, the value string as extracted (e.g. "(12,050.00)", "29.7%"), is tried
  first as a verbatim substring with digit boundaries.
- Otherwise every number token in the snippet is read and compared with the value:
  * percent values match with their sign, written as `%`, `bps`, a bare percent
    number ("1.92" in a "%" column) or a raw fraction ("0.0071" for 0.71%);
  * money and plain numbers match on magnitude (withdrawals "(1,000.00)" are stored
    positive), and `mm`/`bn`/`k`/`million` scales are read.
- A thousands group is exactly three digits not followed by another digit, so a CSV
  row such as "2026-03,1225.49,0.0055" reads as "2026", "-03", "1225.49", "0.0055"
  (a field is never glued to its neighbour). Only a token with a decimal point below
  1 is read as a raw fraction, so integers ("000" of "14,100,000") never match a
  percentage.
- Four-digit years (1900-2100) are never a match. A zero money value matches a
  standalone dash ("Redemptions -").
"""

from __future__ import annotations

import re
from typing import Any

NUMERIC_KINDS = ("percent", "money", "number")

_PREFIX = r"(?P<open>\()?(?P<sign>[-+−])?(?P<code>[A-Z]{3}\s?)?[$€£]?"
_SUFFIX = r"(?:\s?(?P<unit>%|bps|bp|mm|bn|million|billion|m|k)(?![A-Za-z]))?(?P<close>\))?"
# A thousands group is exactly three digits not followed by another digit, so
# "03,1225.49" is never read as "03,122" + "5.49".
_GROUPED_RE = re.compile(_PREFIX + r"(?<![\d.])(?P<num>\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?)" + _SUFFIX)
_DASH_RE = re.compile(r"(?<![\w.])[-\u2013\u2014](?![\w.])")  # "-" printed for nil / zero
_SCALE = {"mm": 1e6, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9, "k": 1e3}


def _token_values(m: re.Match, value_kind: str) -> list[tuple[float, float]]:
    """(candidate value, tolerance) pairs for one numeric token, in the value's units."""
    raw = m.group("num").replace(",", "")
    unit = (m.group("unit") or "").lower()
    x = float(raw)
    half_ulp = 0.5 * 10 ** -(len(raw.split(".")[1]) if "." in raw else 0)
    negative = bool(m.group("open") and m.group("close")) or m.group("sign") in ("-", "−")
    sign = -1.0 if negative else 1.0
    if value_kind == "percent":
        if unit in ("bps", "bp"):
            return [(sign * x / 10000, half_ulp / 10000)]
        if unit == "%":
            return [(sign * x / 100, half_ulp / 100)]
        if unit:
            return []
        out = [(sign * x / 100, half_ulp / 100)]  # a bare percent number: "2.84" in a "%" column
        if "." in raw and x < 1:
            out.append((sign * x, half_ulp))  # a raw fraction: "0.0284" (never an integer like "000")
        return out
    if unit == "%":
        return []
    scale = _SCALE.get(unit, 1.0)
    return [(sign * x * scale, half_ulp * scale)]


def _is_year(m: re.Match) -> bool:
    raw = m.group("num")
    return not m.group("unit") and raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100


def _span(m: re.Match) -> tuple[int, int]:
    start, end = m.span()
    if m.group("code"):  # "USD 5,000.00" -> the number; "YTD 0.15%" -> "0.15%"
        start = m.end("code")
    elif m.group("open") and not m.group("close"):
        start += 1
    return start, end


def _matches(m: re.Match, value_kind: str, target: float) -> bool:
    if _is_year(m):
        return False
    for cand, tol in _token_values(m, value_kind):
        a, b = (cand, target) if value_kind == "percent" else (abs(cand), abs(target))
        if abs(a - b) <= tol + 1e-12:
            return True
    return False


def _raw_span(snippet: str, raw: str | None, value_kind: str, target: float) -> tuple[int, int] | None:
    """The raw string's position, but only while it still reads as the value (a
    reviewer edit makes the extracted string stale)."""
    r = (raw or "").strip()
    if not r or not any(ch.isdigit() for ch in r):
        return None
    if not any(_matches(m, value_kind, target) for m in _GROUPED_RE.finditer(r)):
        return None
    for hay, needle in ((snippet, r), (snippet.lower(), r.lower())):
        start = 0
        while (i := hay.find(needle, start)) >= 0:
            j = i + len(needle)
            before = hay[i - 1] if i > 0 else ""
            after = hay[j] if j < len(hay) else ""
            if not (before.isdigit() or before == "." or after.isdigit()):
                return i, j
            start = i + 1
    return None


def locate_value(snippet: str | None, value_kind: str, value: Any, raw: str | None = None) -> tuple[int, int] | None:
    """(start, end) Python string indices of the value inside the snippet, or None
    when the value is not written there (or is not numeric)."""
    if not snippet or value is None or value_kind not in NUMERIC_KINDS:
        return None
    try:
        target = float(value)
    except (TypeError, ValueError):
        return None
    hit = _raw_span(snippet, raw, value_kind, target)
    if hit is not None:
        return hit
    for m in _GROUPED_RE.finditer(snippet):
        if _matches(m, value_kind, target):
            return _span(m)
    if target == 0 and value_kind in ("money", "number") and (d := _DASH_RE.search(snippet)):
        return d.span()  # "Redemptions -": a dash for nil, stored as 0
    return None


def snippet_numbers_sum_to(snippet: str | None, value: Any) -> bool:
    """True when the money amounts in the snippet add up to the value (by magnitude):
    a total the extractor summed from several printed lines, e.g. fees =
    "Management fee (19,560.00)" + "Incentive allocation (10,450.00)" (prompt rule 3)."""
    if not snippet or value is None:
        return False
    nums = [abs(c) for m in _GROUPED_RE.finditer(snippet) if not _is_year(m)
            for c, _ in _token_values(m, "money")[:1]]
    if len(nums) < 2:
        return False
    target = abs(float(value))
    return abs(sum(nums) - target) <= max(0.01, target * 1e-9)


def value_in_snippet(snippet: str | None, value_kind: str, value: Any, raw: str | None = None) -> bool | None:
    """True/False = the value is / is not written in the snippet; None = not checked
    (no value, no snippet, or a text/date value)."""
    if value is None or value_kind not in NUMERIC_KINDS or not snippet:
        return None
    return locate_value(snippet, value_kind, value, raw) is not None

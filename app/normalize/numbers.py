"""Numbers as written in documents -> canonical values (FR-6).

Returns and percentage metrics are stored as decimal FRACTIONS (0.012 = 1.2 %, T2).

Percent rules (`parse_percent`), applied to the value string as written:
- explicit unit in the value: "%"/"pct"/"percent" -> /100, "bps"/"bp"/"basis points"
  -> /10 000. "1.2%" -> 0.012, "120bps" -> 0.012, "+2.84%" -> 0.0284.
- negatives: a leading "-" or "+", the Unicode minus/dashes, or accounting parentheses
  around the number with or without the unit: "(0.8)" -> -0.8 %, "(0.36)%",
  "(0.36%)", "(36 bps)", "−0.5%".
- trailing qualifiers are ignored: "p.a.", "per annum", "net", "gross", "est.",
  "(est.)", "estimate".
- BARE numbers ("0.0071", "0.36", "1.92", or a float) carry no unit. The unit is taken
  from context, first rule that applies wins (`infer_bare_unit`):
    1. the snippet shows this number with a unit attached ("1.92%" / "110 bps");
    2. CSV documents: the header of the value's column. A header naming a unit ("%",
       "pct", "percent", "bps", "basis points") gives that unit; a header with NO
       unit means a decimal fraction (machine-export convention: Harbor `NET_RET`
       0.0071 = 0.71 %; Meridian `Return (Net) %` 0.22 = 0.22 %);
    3. a stand-alone unit marker in the snippet, i.e. a "%" (or "bps") that does not
       follow a number, as in a column label "MTD %" or "Perf. (net) — %";
    4. the same kind of stand-alone marker on the value's page (table headers such as
       "Perf. (net) — %" or "Performance in basis points").
  If none applies, or a level is ambiguous (both "%" and "bps" markers), the value is
  NOT guessed: `parse_percent` returns an error and the caller flags
  `unparseable_value`.

Amount rules (`parse_amount`): "$1,200,000", "USD 5,000,000.00", "1.2mm", "12.40mm",
"$640m", "1.18bn", "905 million", "12k", "(1,000)" -> -1000, "1.234.567,89"
(European). A lone dash ("-", "—") or "nil" is an accounting zero. Currency codes and
symbols are recognised and returned separately ("$" -> USD, "€" -> EUR, "£" -> GBP).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.normalize.text import clean, normalize_for_match

PERCENT = "percent"
BPS = "bps"
FRACTION = "fraction"
_DIVISOR = {PERCENT: Decimal(100), BPS: Decimal(10000), FRACTION: Decimal(1)}

ISO_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CHF", "SGD", "HKD", "AUD", "CAD", "CNY", "CNH", "AED", "SAR", "KRW",
    "INR", "SEK", "NOK", "DKK", "NZD", "ZAR", "BRL", "MXN", "ILS", "TWD", "QAR", "KWD",
})
_SYMBOLS = (("US$", "USD"), ("U.S.$", "USD"), ("S$", "SGD"), ("HK$", "HKD"), ("A$", "AUD"), ("C$", "CAD"),
            ("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"))
_CURRENCY_WORDS = (("us dollar", "USD"), ("u.s. dollar", "USD"), ("euro", "EUR"), ("pound sterling", "GBP"),
                   ("sterling", "GBP"), ("swiss franc", "CHF"), ("japanese yen", "JPY"))
_CODE_RE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")

_UNIT_RE = re.compile(r"(%|bps|bp|basis\s+points?|pct|per\s*cent|percent)$")
_QUALIFIER_RE = re.compile(
    r"(?:\s+|(?<=[%\d)]))(?:p\.?\s?a\.?|per\s+annum|annuali[sz]ed|net|gross|\(?est\.?\)?|estimated?|\(e\))$"
)
_NUM_RE = re.compile(r"^(?:\d[\d,.\s]*\d|\d)$|^\.\d+$")
_NIL = frozenset({"-", "--", "nil", "none", "zero"})


@dataclass(frozen=True)
class Parsed:
    value: Decimal | None
    unit: str | None = None  # percent/bps/fraction for percentages; "x"/"years"/... for numbers
    currency: str | None = None
    rule: str | None = None  # how the unit was decided (for docs/debugging)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class UnitContext:
    """Where a bare number's unit can be read from (see module docstring)."""

    snippet: str | None = None
    column_header: str | None = None  # CSV documents: header of the value's column, if found
    is_csv: bool = False
    page_text: str | None = None


# ---- low-level number text ---------------------------------------------------------------


def to_decimal(t: str) -> Decimal | None:
    """'1,234.56' / '1.234,56' / '1 234' / '0.0071' -> Decimal. None if malformed."""
    t = re.sub(r"[\s']", "", t)
    if not t or not re.fullmatch(r"[\d.,]+", t) or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):  # European: 1.234,56
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", t):
            t = t.replace(",", "")
        elif t.count(",") == 1:
            t = t.replace(",", ".")
        else:
            return None
    elif t.count(".") > 1:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
            t = t.replace(".", "")
        else:
            return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _strip_parens_and_sign(s: str) -> tuple[str, bool] | None:
    """Accounting parentheses and +/- signs. Returns (rest, negative) or None if malformed."""
    s = s.strip()
    neg = False
    m = re.fullmatch(r"\((.*)\)(.*)", s)
    if m:
        inner, rest = m.group(1).strip(), m.group(2).strip()
        if "(" in inner or ")" in inner or (rest and not _UNIT_RE.fullmatch(rest)):
            return None
        s, neg = (inner + rest).strip(), True
    elif "(" in s or ")" in s:
        return None
    if s[:1] in "+-":
        if neg and s[:1] == "-":
            return None
        neg = neg or s[0] == "-"
        s = s[1:].strip()
        if s[:1] in "+-":
            return None
    return s, neg


def _strip_qualifiers(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _QUALIFIER_RE.sub("", s).strip()
    return s


# ---- percentages -----------------------------------------------------------------------


def _split_percent(raw: str) -> tuple[str, bool, str | None] | None:
    """(number text, negative, unit or None) or None if malformed."""
    s = _strip_qualifiers(clean(raw).lower())
    got = _strip_parens_and_sign(s)
    if got is None:
        return None
    s, neg = got
    unit = None
    m = _UNIT_RE.search(s)
    if m:
        u = m.group(1)
        unit = BPS if u.startswith("b") else PERCENT
        s = s[: m.start()].strip()
        # sign or parentheses may sit after a currency-less unit split, e.g. "-(0.3)%"
        got = _strip_parens_and_sign(s)
        if got is None:
            return None
        s, neg2 = got
        if neg and neg2:
            return None
        neg = neg or neg2
    if not _NUM_RE.match(s):
        return None
    return s, neg, unit


def parse_percent(raw: str | float | int | None, context: UnitContext | None = None) -> Parsed:
    """A return or percentage metric as written -> decimal fraction (see module docstring)."""
    if raw is None or isinstance(raw, bool):
        return Parsed(None, error="empty")
    if isinstance(raw, (int, float)):
        num_text, neg, unit = format(Decimal(str(abs(raw))), "f"), raw < 0, None
    else:
        if not str(raw).strip():
            return Parsed(None, error="empty")
        got = _split_percent(str(raw))
        if got is None:
            return Parsed(None, error="not a percentage")
        num_text, neg, unit = got
    number = to_decimal(num_text)
    if number is None:
        return Parsed(None, error="not a number")
    rule = "value"
    if unit is None:
        unit, rule = infer_bare_unit(num_text, context or UnitContext())
        if unit is None:
            return Parsed(None, error="bare number with no unit in its context", rule=rule)
    value = number / _DIVISOR[unit]
    return Parsed(-value if neg else value, unit=unit, rule=rule)


def _standalone_unit(text: str | None) -> str | None:
    """'percent'/'bps' when the text has a unit marker that is not attached to a
    number ("MTD %", "Perf. (net) — %", "(%)", "in basis points"); None if there is
    none, or if both kinds appear (ambiguous)."""
    if not text:
        return None
    t = clean(text).lower()
    found = set()
    for m in re.finditer(r"%", t):
        before = t[: m.start()].rstrip()
        if not before or not (before[-1].isdigit() or before[-1] == ")"):
            found.add(PERCENT)
    for m in re.finditer(r"\b(bps|bp|basis\s+points?)\b", t):
        before = t[: m.start()].rstrip()
        if not before or not before[-1].isdigit():
            found.add(BPS)
    return found.pop() if len(found) == 1 else None


def header_unit(header: str) -> str:
    """Unit of a CSV column from its header; no unit named -> fraction."""
    h = clean(header).lower()
    if "%" in h or re.search(r"(?<![a-z])(pct|percent|per\s*cent)(?![a-z])", h):
        return PERCENT
    if re.search(r"(?<![a-z])(bps|bp|basis\s+points?)(?![a-z])", h):
        return BPS
    return FRACTION


def infer_bare_unit(num_text: str, ctx: UnitContext) -> tuple[str | None, str]:
    """(unit, rule) for a bare number; unit None when it can't be determined."""
    if ctx.snippet:
        snip = clean(ctx.snippet).lower()
        m = re.search(r"(?<![\d.])" + re.escape(num_text) + r"\s*\)?\s*(%|bps\b|bp\b|basis\s+points?)", snip)
        if m:
            return (BPS if m.group(1).startswith("b") else PERCENT), "snippet_attached"
    if ctx.column_header is not None:
        return header_unit(ctx.column_header), "csv_header"
    if ctx.is_csv:
        return None, "csv_column_not_found"
    u = _standalone_unit(ctx.snippet)
    if u:
        return u, "snippet_marker"
    u = _standalone_unit(ctx.page_text)
    if u:
        return u, "page_marker"
    return None, "no_unit_in_context"


def csv_column_header(csv_text: str | None, raw_value: str | float | None, snippet: str | None) -> str | None:
    """Header of the CSV column holding `raw_value`, or None if it can't be pinned down.

    The value's row is the row quoted by the snippet when there is one, otherwise any
    row containing the value. If the value sits under several columns with different
    units, there is no answer (None)."""
    if not csv_text or raw_value is None:
        return None
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return None
    header, body = rows[0], rows[1:]
    target = _num_key(str(raw_value))
    if target is None:
        return None
    snip = normalize_for_match(snippet)
    candidates = [r for r in body if snip and (normalize_for_match(",".join(r)) in snip or snip in normalize_for_match(",".join(r)))]
    if not candidates:
        candidates = body
    headers = {
        header[i].strip()
        for r in candidates
        for i, cell in enumerate(r)
        if i < len(header) and _num_key(cell) == target
    }
    if not headers:
        return None
    units = {header_unit(h) for h in headers}
    if len(units) != 1:
        return None
    return sorted(headers)[0]


def _num_key(s: str) -> Decimal | None:
    got = _split_percent(s) if s else None
    if got is None:
        return None
    num, neg, _ = got
    d = to_decimal(num)
    return None if d is None else (-d if neg else d)


# ---- amounts and currencies --------------------------------------------------------------------

_MAGNITUDES = (
    (re.compile(r"(billion|bln|bn|b)$"), Decimal(10) ** 9),
    (re.compile(r"(million|mio|mln|mn|mm|m)$"), Decimal(10) ** 6),
    (re.compile(r"(thousand|k)$"), Decimal(10) ** 3),
)


def _pop_currency(s: str) -> tuple[str, str | None]:
    currency = None
    m = _CODE_RE.search(s)
    if m and m.group(1) in ISO_CURRENCIES:
        currency = m.group(1)
        s = (s[: m.start()] + " " + s[m.end():]).strip()
    for sym, code in _SYMBOLS:
        if sym in s:
            currency = currency or code
            s = s.replace(sym, " ").strip()
            break
    return s, currency


def parse_amount(raw: str | float | int | None) -> Parsed:
    """Money amount as written -> Decimal in currency units (+ currency if stated)."""
    if raw is None or isinstance(raw, bool):
        return Parsed(None, error="empty")
    if isinstance(raw, (int, float)):
        return Parsed(Decimal(str(raw)))
    s, currency = _pop_currency(clean(str(raw)))
    s = s.lower().strip()
    if not s:
        return Parsed(None, currency=currency, error="empty")
    if s in _NIL:
        return Parsed(Decimal(0), currency=currency, rule="nil")
    got = _strip_parens_and_sign(s)
    if got is None:
        return Parsed(None, currency=currency, error="not an amount")
    s, neg = got
    mult = Decimal(1)
    for rx, m in _MAGNITUDES:
        mm = rx.search(s)
        if mm and s[: mm.start()].strip() and re.search(r"\d\s*$", s[: mm.start()]):
            mult, s = m, s[: mm.start()].strip()
            break
    if not _NUM_RE.match(s):
        return Parsed(None, currency=currency, error="not an amount")
    d = to_decimal(s)
    if d is None:
        return Parsed(None, currency=currency, error="not an amount")
    v = d * mult
    return Parsed(-v if neg else v, currency=currency)


def parse_currency(raw: str | None) -> str | None:
    """ISO code from "USD", "Base currency: USD", "US$", "$", "euro"; None if none."""
    if not raw:
        return None
    s = clean(str(raw))
    for m in _CODE_RE.finditer(s.upper() if len(s.strip()) == 3 else s):
        if m.group(1) in ISO_CURRENCIES:
            return m.group(1)
    low = s.lower()
    for word, code in _CURRENCY_WORDS:
        if word in low:
            return code
    for sym, code in _SYMBOLS:
        if sym in s:
            return code
    return None


# ---- plain numbers -----------------------------------------------------------------------------

_NUMBER_UNITS = ((re.compile(r"\s*(x|times)$"), "x"), (re.compile(r"\s*(years?|yrs?|y)$"), "years"))


def parse_number(raw: str | float | int | None) -> Parsed:
    """Plain number ("1.84", "(0.3)", "6.2x", "3.4 years") -> Decimal (+ unit)."""
    if raw is None or isinstance(raw, bool):
        return Parsed(None, error="empty")
    if isinstance(raw, (int, float)):
        return Parsed(Decimal(str(raw)))
    s = _strip_qualifiers(clean(str(raw)).lower())
    unit = None
    for rx, u in _NUMBER_UNITS:
        m = rx.search(s)
        if m and re.search(r"\d\)?$", s[: m.start()]):
            unit, s = u, s[: m.start()].strip()
            break
    got = _strip_parens_and_sign(s)
    if got is None:
        return Parsed(None, error="not a number")
    s, neg = got
    if not _NUM_RE.match(s):
        return Parsed(None, error="not a number")
    d = to_decimal(s)
    if d is None:
        return Parsed(None, error="not a number")
    return Parsed(-d if neg else d, unit=unit)


def has_percent_unit(raw: str | None) -> bool:
    return bool(raw) and _split_percent(str(raw)) is not None and _split_percent(str(raw))[2] is not None


def has_money_marker(raw: str | None) -> bool:
    """Currency code/symbol or a magnitude suffix (mm, bn, million, k)."""
    if not raw:
        return False
    s, cur = _pop_currency(clean(str(raw)))
    if cur:
        return True
    return bool(re.search(r"\d\s*(billion|bln|bn|million|mio|mln|mn|mm|m|thousand|k)\b", s.lower()))

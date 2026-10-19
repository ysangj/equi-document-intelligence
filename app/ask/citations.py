"""Citation markers and the number citation check (FR-11).

Markers
-------
During the loop every citable source (one per successful `query_sql` result, one
per distinct passage) gets a provisional `ref` number, and Claude cites `[ref]`.
`renumber_markers` then:
- accepts `[3]`, and also `[3, 5]` / `[3,5]` (split into `[3][5]`);
- drops markers whose ref is unknown (for example a marker copied from history);
- renumbers the known refs 1..N in order of first appearance;
- keeps markers out of code: inside fenced blocks and inline code spans, a
  `[n]` token is removed, and when it is a known ref it is re-emitted right after
  the code span (after the closing fence line for blocks).

Number check
------------
`extract_answer_numbers` finds number tokens in the answer: plain numbers,
thousands separators, decimals, signs (+, -, Unicode minus), currency prefixes
($, US$, USD, EUR, €, £), accounting parentheses `(0.36)%`, and the units %, pp,
percent, bps/bp, mm/m/million, bn/b/billion, k and x.
Not checked:
- citation markers;
- 4-digit years (1900-2100, no decimals, no unit or currency);
- numbers inside dates (ISO `2026-01-31`, `01/31/2026`, `Jan-26`, `January 31, 2026`,
  `31 Dec 2025`, `Q1 2026`, `H2`);
- numbers glued to letters (`Q1`, `v2`, `1st`, `3-month` is checked as 3);
- markdown list numbering (`1. `, `2) ` at the start of a line);
- ordinals: `1st`, `2nd`, `#2` (a bare `2` or "6 funds" is still checked);
- rank columns of markdown tables: a column headed Rank / Ranking / # / No. / Nr. /
  Position / Pos. / Place / Order whose body cells are all bare integers forming a
  ranking 1..N in row order (ties and competition ranking "1, 2, 2, 4" allowed).
  A column with any other header, a non-integer cell, or values that do not run
  from 1 is checked as usual;
- anything in code spans / fenced code.

`verify_numbers` matches each token against every value returned by the run's SQL
rows (numbers, plus numbers parsed from string cells) and passages (numbers parsed
from the text). Matching is tolerant:
- scale: fraction vs percent (0.0284 vs 2.84%), either direction;
- rounding: a token written with d decimals matches any value within half a unit
  of its last digit (2.8% matches 2.84);
- units: 1.2mm / 1.2 million / $1,200,000 all match 1200000; 13.1mm matches
  13,100,000; 110 bps matches 1.10 (%) and 0.011, and the written 110;
- sign: magnitudes are compared, so "fell 0.36%" matches -0.36;
- SQL row counts also count as values ("7 funds").
Unmatched tokens are returned as written (bare token, no markdown), de-duplicated,
in order of appearance. Derived numbers (a difference Claude computed) usually
land here, which is intended (contract).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# markers

_MARKER_GROUP_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_MARKER_WS_RE = re.compile(r"([ \t]*)\[(\d+(?:\s*,\s*\d+)*)\]")
_FENCE_RE = re.compile(r"(^|\n)([ \t]*)(```|~~~)[^\n]*\n.*?(\n[ \t]*\3[^\n]*(?=\n|$)|$)", re.S)
_INLINE_CODE_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


def _code_spans(answer: str) -> list[tuple[int, int, bool]]:
    """(start, end, is_fence) of fenced blocks and inline code spans, sorted."""
    spans: list[tuple[int, int, bool]] = []
    for m in _FENCE_RE.finditer(answer):
        start = m.start() + len(m.group(1))
        spans.append((start, m.end(), True))
    taken = [(a, b) for a, b, _ in spans]
    for m in _INLINE_CODE_RE.finditer(answer):
        if not any(a <= m.start() < b for a, b in taken):
            spans.append((m.start(), m.end(), False))
    return sorted(spans)


def code_mask(answer: str) -> list[bool]:
    mask = [False] * len(answer)
    for a, b, _ in _code_spans(answer):
        for i in range(a, b):
            mask[i] = True
    return mask


def renumber_markers(answer: str, known_refs: Iterable[int]) -> tuple[str, dict[int, int]]:
    """Returns (new answer, {ref: new id}) with ids 1..N in order of first appearance."""
    known = set(known_refs)
    mapping: dict[int, int] = {}

    def new_id(ref: int) -> int:
        if ref not in mapping:
            mapping[ref] = len(mapping) + 1
        return mapping[ref]

    def render(refs: list[int]) -> str:
        out, seen = [], set()
        for r in refs:
            if r in known and r not in seen:
                seen.add(r)
                out.append(f"[{new_id(r)}]")
        return "".join(out)

    def refs_of(group: str) -> list[int]:
        return [int(x) for x in re.split(r"\s*,\s*", group)]

    def sub_text(chunk: str) -> str:
        def one(m: re.Match) -> str:
            rendered = render(refs_of(m.group(2)))
            return m.group(1) + rendered if rendered else ""  # a dropped marker takes its leading space
        return _MARKER_WS_RE.sub(one, chunk)

    parts: list[str] = []
    pos = 0
    for a, b, is_fence in _code_spans(answer):
        parts.append(sub_text(answer[pos:a]))
        code = answer[a:b]
        moved: list[int] = []

        def strip(m: re.Match) -> str:
            moved.extend(r for r in refs_of(m.group(1)) if r in known)
            return ""

        code = _MARKER_GROUP_RE.sub(strip, code)
        parts.append(code)
        if moved:
            parts.append(("\n" if is_fence and not code.endswith("\n") else "") + render(moved))
        pos = b
    parts.append(sub_text(answer[pos:]))
    text = "".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip(), mapping


# ---------------------------------------------------------------------------
# number tokens

_MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{1,2}(?:-\d{1,2})?\b"),  # 2026-01-31, 2026-01
    re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"),  # 01/31/2026, 31.01.2026
    re.compile(rf"\b{_MONTHS}\.?[-\s]\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.I),  # Jan 31, 2026 / Jan-26
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}\b\.?(?:,?\s+\d{{4}})?", re.I),  # 31 December 2025
    re.compile(rf"\b{_MONTHS}\.?['’]\d{{2}}\b", re.I),  # Jan'26
    re.compile(r"\b[QH][1-4]\b", re.I),  # Q1, H2
    re.compile(r"\b(?:FY|CY)\s?\d{2,4}\b", re.I),
]
_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*\d{1,3}[.)](?=\s)")
# Ordinals: "1st", "2nd", "#2" (a "#" right before the number, not after a word or "&").
_ORDINAL_RE = re.compile(r"(?<![\w&#])(?:#\d{1,3}|\d{1,3}(?:st|nd|rd|th))(?!\w)", re.I)

# ---- rank columns in markdown tables
# A column is a rank column when its header (markdown emphasis stripped) is one of
# these words and every body cell is a bare integer (optionally bold, optionally
# with a trailing "."), forming a ranking 1..N in row order: the first is 1 and each
# next one is the previous (a tie), the previous + 1 (dense) or its row number
# (competition ranking "1, 2, 2, 4"). Anything else is checked as usual.
_RANK_HEADERS = frozenset({"rank", "ranking", "#", "no", "no.", "nr", "nr.", "n°", "№", "position", "pos", "pos.",
                           "place", "order"})
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_RANK_CELL_RE = re.compile(r"^(?:\*\*|__|\*|_)?(\d{1,3})\.?(?:\*\*|__|\*|_)?$")


def _row_cells(line: str, offset: int) -> list[tuple[int, int]]:
    """(start, end) absolute offsets of each cell in a `| a | b |` row (outer pipes optional)."""
    pipes = [i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")]
    edges = [-1, *pipes, len(line)]
    cells = [(x + 1, y) for x, y in zip(edges, edges[1:])]
    if cells and not line[cells[0][0]:cells[0][1]].strip():
        cells = cells[1:]  # before a leading pipe
    if cells and not line[cells[-1][0]:cells[-1][1]].strip():
        cells = cells[:-1]  # after a trailing pipe
    return [(offset + x, offset + y) for x, y in cells]


def _rank_cell_spans(answer: str) -> list[tuple[int, int]]:
    """Spans of the body cells of markdown-table rank columns (see _RANK_HEADERS)."""
    lines = answer.split("\n")
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    spans: list[tuple[int, int]] = []
    i = 0
    while i + 1 < len(lines):
        head, sep = lines[i], lines[i + 1]
        if "|" not in head or "|" not in sep or not _TABLE_SEP_RE.match(sep):
            i += 1
            continue
        header = _row_cells(head, offsets[i])
        body: list[list[tuple[int, int]]] = []
        j = i + 2
        while j < len(lines) and "|" in lines[j] and lines[j].strip():
            body.append(_row_cells(lines[j], offsets[j]))
            j += 1
        for col, (a, b) in enumerate(header):
            name = re.sub(r"[*_`]", "", answer[a:b]).strip().lower()
            if name not in _RANK_HEADERS or not body:
                continue
            ranks: list[int] = []
            for row in body:
                if col >= len(row):
                    break
                m = _RANK_CELL_RE.match(answer[row[col][0]:row[col][1]].strip())
                if not m:
                    break
                ranks.append(int(m.group(1)))
            if len(ranks) != len(body) or not _is_ranking(ranks):
                continue
            spans.extend(row[col] for row in body)
        i = j
    return spans


def _is_ranking(ranks: list[int]) -> bool:
    if not ranks or ranks[0] != 1:
        return False
    for idx in range(1, len(ranks)):
        prev, cur = ranks[idx - 1], ranks[idx]
        if cur not in (prev, prev + 1, idx + 1):  # a tie, dense or competition ranking
            return False
    return True

_NUM_RE = re.compile(
    r"""
    (?P<open>\()?
    (?P<pre>(?:US\$|USD\s?|EUR\s?|GBP\s?|\$|€|£))?
    (?P<sign>[+\-−–])?
    (?P<pre2>\$|€|£)?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?P<close>\))?
    (?:[  ]?(?P<unit>%|pp\b|percent\b|percentage\s+points?\b|bps\b|bp\b|basis\s+points?\b|mm\b|million\b|mn\b|
        bn\b|billion\b|k\b|m\b|b\b|x\b))?
    """,
    re.X | re.I,
)

# Units kept in the token text; word units ("percent", "million", "basis points")
# are left out, so the token is the number as the UI highlights it ("0.92").
_SYMBOL_UNIT_RE = re.compile(r"%|pp|bps|bp|mm|bn|k|m|b|x", re.I)

_UNIT_FACTORS = {
    "mm": 1e6, "m": 1e6, "mn": 1e6, "million": 1e6,
    "bn": 1e9, "b": 1e9, "billion": 1e9,
    "k": 1e3,
}


@dataclass(frozen=True)
class NumberToken:
    text: str  # as written in the answer (bare token, no markdown)
    start: int
    value: float  # signed, unit not applied
    decimals: int
    unit: str | None  # normalized: "%", "bps", "mm", "bn", "k", "x" or None

    def interpretations(self) -> list[tuple[float, float]]:
        """(value, factor): the token's value in the scales it may be stored in."""
        v = self.value
        out = [(v, 1.0)]
        if self.unit == "bps":
            out.append((v / 100.0, 0.01))
        elif self.unit in ("mm", "bn", "k"):
            f = _UNIT_FACTORS[self.unit]
            out.append((v * f, f))
        return out


def _norm_unit(u: str | None) -> str | None:
    if not u:
        return None
    u = re.sub(r"\s+", " ", u.lower())
    if u in ("%", "percent") or u.startswith("pp") or u.startswith("percentage"):
        return "%"
    if u in ("bps", "bp") or u.startswith("basis"):
        return "bps"
    if u in ("mm", "m", "mn", "million"):
        return "mm"
    if u in ("bn", "b", "billion"):
        return "bn"
    return u  # k, x


def _scan(text: str, blocked: list[bool] | None, *, answer_rules: bool) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    n = len(text)
    for m in _NUM_RE.finditer(text):
        a = m.start()
        num_start = m.start("num")
        if blocked is not None and (blocked[num_start] or blocked[m.end("num") - 1]):
            continue
        prev = text[a - 1] if a > 0 else ""
        if prev and (prev.isalnum() or prev in "._"):
            continue
        end = m.end()
        nxt = text[end] if end < n else ""
        unit_raw = m.group("unit")
        if nxt and (nxt.isalnum() or nxt == "_") and not unit_raw:
            continue  # glued to a word: 1st, 3D, v2b
        if nxt == "." and end + 1 < n and text[end + 1].isdigit():
            continue  # version-like 1.2.3
        raw = m.group("num")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        sign = m.group("sign")
        unit = _norm_unit(unit_raw)
        pre = (m.group("pre") or "") + (m.group("pre2") or "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if answer_rules and not unit and not pre and decimals == 0 and "," not in raw and 1900 <= value <= 2100 \
                and len(raw) == 4:
            continue  # a year
        if sign and sign in "-−–":
            value = -value
        accounting = bool(m.group("open") and m.group("close") and not sign)
        if accounting:
            value = -abs(value)
        # Token text: bare, as written. Parentheses only for the accounting form "(0.36)%".
        if accounting:
            t_start = a
        else:
            t_start = next(m.start(g) for g in ("pre", "sign", "pre2", "num") if m.group(g))
        if unit_raw and _SYMBOL_UNIT_RE.fullmatch(unit_raw):
            t_end = m.end("unit")
        elif accounting:
            t_end = m.end("close")
        else:
            t_end = m.end("num")
        tok_text = text[t_start:t_end]
        tokens.append(NumberToken(tok_text, t_start, value, decimals, unit))
    return tokens


def extract_answer_numbers(answer: str) -> list[NumberToken]:
    blocked = code_mask(answer)
    for m in _MARKER_GROUP_RE.finditer(answer):
        for i in range(m.start(), m.end()):
            blocked[i] = True
    for pat in (*_DATE_PATTERNS, _LIST_MARKER_RE, _ORDINAL_RE):
        for m in pat.finditer(answer):
            for i in range(m.start(), m.end()):
                blocked[i] = True
    for a, b in _rank_cell_spans(answer):
        for i in range(a, b):
            blocked[i] = True
    return _scan(answer, blocked, answer_rules=True)


def numbers_in_text(text: str) -> list[float]:
    """Every number a source text states, in each scale it may be read in."""
    out: list[float] = []
    for t in _scan(text, None, answer_rules=False):
        out.extend(v for v, _ in t.interpretations())
    return out


def collect_values(sql_results: Iterable[Mapping], passages: Iterable[str]) -> list[float]:
    """All candidate values from SQL results ({rows, row_count}) and passage texts."""
    vals: list[float] = []

    def add_cell(c) -> None:
        if isinstance(c, bool) or c is None:
            return
        if isinstance(c, (int, float)):
            if math.isfinite(c):
                vals.append(float(c))
        elif isinstance(c, str):
            vals.extend(numbers_in_text(c))
        elif isinstance(c, (list, tuple)):
            for x in c:
                add_cell(x)
        elif isinstance(c, Mapping):
            for x in c.values():
                add_cell(x)

    for r in sql_results:
        vals.append(float(r.get("row_count", len(r.get("rows", [])))))
        for row in r.get("rows", []):
            for cell in row:
                add_cell(cell)
    for p in passages:
        vals.extend(numbers_in_text(p))
    return vals


def _matches(token: NumberToken, candidates: list[float]) -> bool:
    for x, factor in token.interpretations():
        tol = 0.5 * 10 ** (-token.decimals) * factor
        ax = abs(x)
        for c in candidates:
            for cc in (c, c * 100.0, c / 100.0):
                if abs(abs(cc) - ax) <= tol + 1e-9 * max(1.0, ax):
                    return True
    return False


def verify_numbers(answer: str, candidates: list[float]) -> list[str]:
    """Bare number tokens of `answer` that match no candidate value (unique, in order)."""
    cands = sorted(set(candidates))
    out: list[str] = []
    for tok in extract_answer_numbers(answer):
        if tok.text in out:
            continue
        if not _matches(tok, cands):
            out.append(tok.text)
    return out

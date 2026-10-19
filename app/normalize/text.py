"""Text normalization shared by snippet verification and alias matching."""

from __future__ import annotations

import re
import unicodedata

# Unicode minus, hyphens and dashes -> ASCII "-".
DASHES = "−‐‑‒–—―﹘﹣－"
_DASH_TABLE = {ord(c): "-" for c in DASHES}
_QUOTE_TABLE = {
    ord("‘"): "'", ord("’"): "'", ord("‚"): "'", ord("′"): "'",
    ord("“"): '"', ord("”"): '"', ord("„"): '"', ord("″"): '"',
}
_WS = re.compile(r"\s+")
_ALL_WS = re.compile(r"\s+")


def clean(s: str) -> str:
    """NFKC (folds no-break spaces, full-width digits), ASCII dashes and quotes, trimmed."""
    s = unicodedata.normalize("NFKC", s)
    return s.translate(_DASH_TABLE).translate(_QUOTE_TABLE).strip()


def normalize_for_match(s: str | None) -> str:
    """Case-folded, dash/quote-normalized text with whitespace collapsed to one space."""
    if not s:
        return ""
    return _WS.sub(" ", clean(s).casefold()).strip()


def snippet_in_text(snippet: str | None, normalized_text: str, *, compact_text: str | None = None) -> bool:
    """True when `snippet` occurs in the document text after normalization.

    `normalized_text` must come from `normalize_for_match`. Two passes: whitespace
    collapsed (the usual case), then with all whitespace removed, which tolerates
    line-wrapped words in PDF text layers ("cash-\\nfutures") and table cells that the
    text layer splits differently from how the snippet quotes them.
    """
    n = normalize_for_match(snippet)
    if not n:
        return False
    if n in normalized_text:
        return True
    compact = compact_text if compact_text is not None else _ALL_WS.sub("", normalized_text)
    return _ALL_WS.sub("", n) in compact


def compact(normalized_text: str) -> str:
    return _ALL_WS.sub("", normalized_text)

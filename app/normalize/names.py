"""Fund / account name matching (FR-6 alias resolution).

1. Exact: `alias_key()` equality (NFKC, case-folded, dashes normalized, whitespace
   collapsed). "Kestrel Quant Equity Fund LP" == "kestrel  quant equity fund lp".
2. Fuzzy: `match_score()` = rapidfuzz `fuzz.token_set_ratio` over the CORE names, i.e.
   the names with generic tokens removed (legal forms such as "Fund", "Ltd", "LP",
   "SPC"; share-class phrases such as "Class A"; currency codes). Threshold
   `FUZZY_THRESHOLD` = 80.
3. Guard (`distinct_identity`): if BOTH names keep a core token the other lacks, and
   those tokens don't explain each other (equal, prefix/abbreviation such as
   "opps"~"opportunities", acronym such as "rv"~"relative value", or a typo-level
   ratio >= 85), the names identify different entities and never fuzzy-match,
   whatever the score. One-sided extra tokens are descriptors and are allowed
   ("Northwind Macro" vs "Northwind Global Macro").

Sample data:
- "Northwind Macro Fund Ltd" vs "Northwind Global Macro Fund": core {northwind, macro}
  vs {northwind, global, macro}; score 100, one-sided extra token -> match.
- "Alder Asia Fund SPC – Class A" vs "Alder Asia Fund": same core -> score 100 -> match
  (fuzzy, not exact, so it is still confirmed in Review).
- "Alder Asia Fund" vs "Alder Dubai Fund": core score 76.2 < 80, and the guard rejects
  it anyway ("asia" vs "dubai" are distinct, unexplained tokens on both sides). The
  raw-string token_set_ratio would be 83.9, which is why generic tokens are removed.
- "Meridian Rates RV Fund" vs "Meridian Rates Relative Value Fund": 90.3, "rv" is the
  acronym of "relative value" -> match. "Harbor Credit Opps Fund" vs "Harbor Credit
  Opportunities Fund": 83.9, "opps" abbreviates "opportunities" -> match.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.normalize.text import clean

FUZZY_THRESHOLD = 80.0
TOKEN_TYPO_RATIO = 85.0

GENERIC_TOKENS = frozenset({
    "fund", "funds", "ltd", "limited", "lp", "llp", "llc", "inc", "incorporated", "corp", "corporation",
    "spc", "plc", "sa", "sarl", "sicav", "icav", "oeic", "ucits", "the", "of", "and", "co", "company",
    "usd", "eur", "gbp", "chf", "jpy", "share", "shares", "units", "master", "feeder", "offshore",
    "onshore", "portfolio", "segregated",
})
_SHARE_CLASS = re.compile(r"\b(?:share\s+)?class\s+[a-z0-9]{1,4}\b")
_LEGAL_DOTTED = (("l.p.", "lp"), ("l.l.c.", "llc"), ("s.a.", "sa"), ("s.p.c.", "spc"), ("inc.", "inc"),
                 ("ltd.", "ltd"), ("co.", "co"))


def alias_key(name: str) -> str:
    """Case/whitespace-normalized key for exact alias matching."""
    return re.sub(r"\s+", " ", clean(name).casefold()).strip()


def display_name(name: str) -> str:
    """Name as stored (aliases, canonical names): as written, NFKC, whitespace collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name)).strip()


def core_tokens(name: str) -> list[str]:
    s = alias_key(name)
    for dotted, plain in _LEGAL_DOTTED:
        s = s.replace(dotted, plain)
    s = _SHARE_CLASS.sub(" ", s)
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in GENERIC_TOKENS]


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(c in it for c in short)


def _singular(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokens_equivalent(a: str, b: str) -> bool:
    """Same word: equal (also singular/plural), prefix ("intl"/"international"),
    abbreviation with the same first letter ("opps"/"opportunities"), or a
    typo-level difference."""
    if a == b or _singular(a) == _singular(b):
        return True
    short, long = sorted((a, b), key=len)
    if len(short) >= 3 and short[0] == long[0] and (long.startswith(short) or _is_subsequence(short, long)):
        return True
    return min(len(a), len(b)) >= 4 and fuzz.ratio(a, b) >= TOKEN_TYPO_RATIO


def _acronym_spans(token: str, other: list[str]) -> list[range]:
    """Index ranges of `other` whose initials spell `token` ("rv" -> relative value)."""
    n = len(token)
    if n < 2:
        return []
    return [range(i, i + n) for i in range(len(other) - n + 1)
            if "".join(t[0] for t in other[i:i + n]) == token]


def _unexplained(mine: list[str], theirs: list[str]) -> set[str]:
    only_mine = [t for t in mine if t not in theirs]
    only_theirs = [t for t in theirs if t not in mine]
    left = set()
    for t in only_mine:
        if any(tokens_equivalent(t, u) for u in only_theirs):
            continue
        if _acronym_spans(t, theirs):
            continue
        # covered by one of my acronyms? (t is part of a phrase the other side abbreviates)
        if any(i_range for u in only_theirs for i_range in _acronym_spans(u, mine)
               if mine.index(t) in i_range):
            continue
        left.add(t)
    return left


def distinct_identity(a: str, b: str) -> bool:
    """True when both names carry distinct, unexplained core tokens (e.g. Asia vs Dubai)."""
    ta, tb = core_tokens(a), core_tokens(b)
    return bool(_unexplained(ta, tb)) and bool(_unexplained(tb, ta))


def match_score(a: str, b: str) -> float:
    """0-100 fuzzy score over core names (0 when either core is empty)."""
    ca, cb = core_tokens(a), core_tokens(b)
    if not ca or not cb:
        return 0.0
    return round(float(fuzz.token_set_ratio(" ".join(ca), " ".join(cb))), 1)


@dataclass(frozen=True)
class FuzzyMatch:
    entity_id: int
    matched_alias: str
    score: float


def best_fuzzy_match(name: str, candidates: Iterable[tuple[int, str]],
                     threshold: float = FUZZY_THRESHOLD) -> FuzzyMatch | None:
    """Best candidate (entity id, alias text) with score >= threshold that the guard
    allows. Ties go to the lower entity id, so the result is deterministic."""
    best: FuzzyMatch | None = None
    for entity_id, alias in candidates:
        score = match_score(name, alias)
        if score < threshold or distinct_identity(name, alias):
            continue
        if best is None or score > best.score or (score == best.score and entity_id < best.entity_id):
            best = FuzzyMatch(entity_id, alias, score)
    return best

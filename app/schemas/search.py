"""Keyword search (FR-10).

Highlight markup: `headline_html` is HTML whose only elements are `<mark>` and
`</mark>`, with no attributes. Everything else is HTML-escaped (`&amp; &lt; &gt;
&quot; &#x27;`). The UI may bind it with [innerHTML]. The implementation runs
ts_headline with StartSel=HEADLINE_START and StopSel=HEADLINE_STOP (private-use
characters that never appear in documents, because headline_to_html strips them
from the text first). It then calls headline_to_html, which escapes the text and
swaps the sentinels for <mark> tags.
"""

from __future__ import annotations

import html
import re

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, DocumentStatus, DocumentType

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 200
HEADLINE_START = ""
HEADLINE_STOP = ""
FRAGMENT_DELIMITER = " … "
# Options for ts_headline (FR-10).
TS_HEADLINE_OPTIONS = (
    f"StartSel={HEADLINE_START}, StopSel={HEADLINE_STOP}, MaxWords=35, MinWords=15, "
    f"MaxFragments=2, FragmentDelimiter=\"{FRAGMENT_DELIMITER}\""
)

_MARK_RE = re.compile(r"</?mark>")


def headline_to_html(raw: str) -> str:
    """Turn a ts_headline result (with sentinels) into the safe `headline_html` markup.
    Unbalanced sentinels are dropped rather than emitted as half-open tags."""
    out: list[str] = []
    open_ = False
    for part in re.split(f"([{HEADLINE_START}{HEADLINE_STOP}])", raw):
        if part == HEADLINE_START:
            if not open_:
                out.append("<mark>")
                open_ = True
        elif part == HEADLINE_STOP:
            if open_:
                out.append("</mark>")
                open_ = False
        else:
            out.append(html.escape(part, quote=True))
    if open_:
        out.append("</mark>")
    return "".join(out)


def is_safe_headline(s: str) -> bool:
    """True when the only markup is balanced, non-nested <mark>...</mark>."""
    depth = 0
    for tag in _MARK_RE.findall(s):
        depth += 1 if tag == "<mark>" else -1
        if depth not in (0, 1):
            return False
    rest = _MARK_RE.sub("", s)
    return depth == 0 and "<" not in rest and ">" not in rest and '"' not in rest


class SearchQuery(ApiModel):
    q: str = Field("", max_length=MAX_QUERY_LENGTH)
    limit: int = Field(5, ge=1, le=20)  # max hits per group


class HitAlias(ApiModel):
    alias: str
    confirmed: bool  # false: a fuzzy suggestion still waiting in Review


class FundHit(ApiModel):
    fund_id: int
    fund_name: str
    manager: str | None
    matched_alias: str | None  # set when the best match was an alias rather than the canonical name
    aliases: list[HitAlias]  # every alias of the fund except its canonical name, sorted (U2 note 2)
    score: float  # trigram similarity 0-1


class AccountHit(ApiModel):
    account_id: int
    account_name: str
    account_number_masked: str | None
    custodian: str | None
    matched_alias: str | None
    aliases: list[HitAlias]
    score: float


class DocumentHit(ApiModel):
    document_id: int
    document_name: str
    document_type: DocumentType | None
    status: DocumentStatus
    web_view_link: str | None
    score: float


class ChunkHit(ApiModel):
    chunk_id: int
    document_id: int
    document_name: str
    document_type: DocumentType | None
    page: int | None  # null for CSV row groups
    chunk_index: int
    headline_html: str
    rank: float  # ts_rank_cd
    web_view_link: str | None

    @field_validator("headline_html")
    @classmethod
    def _safe(cls, v: str) -> str:
        if not is_safe_headline(v):
            raise ValueError("headline_html may only contain <mark>...</mark> and escaped text")
        return v


class SearchGroups(ApiModel):
    funds: list[FundHit]  # funds and their aliases
    accounts: list[AccountHit]
    documents: list[DocumentHit]
    chunks: list[ChunkHit]


class SearchResponse(ApiModel):
    query: str  # q after trimming
    min_query_length: int
    groups: SearchGroups
    total: int  # hits across all groups in this response
    took_ms: int

"""Shared API conventions (docs/progress.md -> API Contract -> Conventions).

- Every model forbids unknown fields. On responses this means T6-T8 must build
  exactly the documented shape. On requests and query strings, a typo is a 422
  instead of being silently ignored.
- Response models have no defaults, so every field is always present in the JSON
  (null when empty). Request and query models do have defaults.
- Dates serialize as `YYYY-MM-DD`. Timestamps are timezone-aware and serialize as
  ISO 8601 UTC with a `Z` suffix.
- Numbers are JSON numbers. Returns and weights are decimal fractions in `value`,
  and the same number times 100 is in `value_pct`.
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# ---- base -------------------------------------------------------------------------


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Timestamp = AwareDatetime

# ---- enumerations (tests/test_api_contract.py pins these to app.models) ----------

# `removed` documents are never returned by the API, so it is not in this list.
DocumentStatus = Literal["pending", "processing", "done", "failed", "needs_review", "unsupported"]
DocumentType = Literal["factsheet", "account_statement", "performance_report", "email", "other"]
DocumentSource = Literal["drive", "local"]
ValueStatus = Literal["ok", "needs_review", "conflict"]
ReturnType = Literal["MTD", "QTD", "YTD", "ITD", "annualized", "unknown"]
NetGross = Literal["net", "gross", "unknown"]
ReviewReason = Literal[
    "low_confidence",
    "ytd_mismatch",
    "rollforward_mismatch",
    "alias_match",
    "conflict",
    "snippet_not_found",
    "future_date",
    "out_of_range",
    "net_gross_unknown",
    "return_type_unknown",
    "unparseable_value",
]
AliasMatchMethod = Literal["exact", "fuzzy", "new", "manual"]
ExtractionSource = Literal["claude", "fixture"]
Mode = Literal["demo", "drive"]

# How the UI formats a single value (side panel, review cards):
#   percent: show `value_pct` with 2 dp, a sign and "%". `value` is the fraction.
#   money:   show `value` with thousands separators and 2 dp, plus `currency`.
#   number:  show `value`, plus `unit` when it is not null.
#   text:    show `value` as a string.
#   date:    `value` is a `YYYY-MM-DD` string.
ValueKind = Literal["percent", "money", "number", "text", "date"]

# Human labels for review reasons, shared by /api/review, /api/sources and the
# status filters. The API returns them in `reasons[].label`.
REVIEW_REASON_LABELS: dict[str, str] = {
    "low_confidence": "Low confidence",
    "ytd_mismatch": "YTD mismatch",
    "rollforward_mismatch": "Roll-forward mismatch",
    "alias_match": "Alias match",
    "conflict": "Conflict",
    "snippet_not_found": "Snippet not found",
    "future_date": "Future date",
    "out_of_range": "Out of range",
    "net_gross_unknown": "Net/gross unknown",
    "return_type_unknown": "Return type unknown",
    "unparseable_value": "Unparseable value",
}

# ---- errors -----------------------------------------------------------------------

ErrorCode = Literal[
    "validation_error",  # 422: bad query param, body or path value
    "invalid_action",  # 422: review action not allowed for this item kind
    "not_found",  # 404: unknown route, or the id does not exist or is not visible
    "method_not_allowed",  # 405
    "not_connected",  # 409: Drive mode, no stored Google credentials
    "no_folder",  # 409: Drive mode, connected but no watched folder
    "demo_mode",  # 409: Drive/OAuth endpoint called in demo mode
    "document_busy",  # 409: reprocess while pending/processing
    "unsupported_document",  # 409: reprocess an unsupported file type
    "drive_error",  # 502: Google Drive API failed
    "upstream_error",  # 502: Anthropic API failed after retries
    "ask_disabled",  # 503: no ANTHROPIC_API_KEY
    "db_unavailable",  # 503: Postgres unreachable
    "internal_error",  # 500
]


class ErrorDetail(ApiModel):
    loc: str | None  # "query.sort", "body.question", "path.table"; null when not about one input
    message: str


class ErrorInfo(ApiModel):
    code: ErrorCode
    message: str  # safe to show to the user as-is
    details: list[ErrorDetail] | None


class ErrorResponse(ApiModel):
    error: ErrorInfo


# ---- lists ------------------------------------------------------------------------

T = TypeVar("T")


class Page(ApiModel, Generic[T]):
    """Envelope for paginated list endpoints."""

    items: list[T]
    total: int  # rows matching the filters, across all pages
    page: int  # 1-based
    page_size: int
    sort: str  # normalized sort actually applied, e.g. "-period_end,fund_name"


class ListResponse(ApiModel, Generic[T]):
    """Envelope for small unpaginated lists (/api/funds, /api/accounts)."""

    items: list[T]
    total: int


# ---- sorting ----------------------------------------------------------------------

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50


def parse_sort(sort: str | None, allowed: tuple[str, ...], default: str) -> list[tuple[str, bool]]:
    """Parse `sort` ("-period_end,fund_name") into [(column, descending), ...].

    Raises ValueError when a column is not in `allowed` or is repeated. The
    implementation appends the primary key ascending as a final tie-breaker, and
    NULLs always sort last in both directions.
    """
    raw = (sort or "").strip() or default
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty sort key")
        desc = part.startswith("-")
        col = part[1:] if desc else part
        if col not in allowed:
            raise ValueError(f"cannot sort by {col!r}; allowed: {', '.join(allowed)}")
        if col in seen:
            raise ValueError(f"sort key {col!r} repeated")
        seen.add(col)
        out.append((col, desc))
    return out


def format_sort(keys: list[tuple[str, bool]]) -> str:
    return ",".join(("-" if desc else "") + col for col, desc in keys)


class ListQuery(ApiModel):
    """Query params shared by every paginated list endpoint.

    `format=csv` returns every matching row (all pages) as CSV, with the same filters
    and sort. `page` and `page_size` are ignored for CSV.
    """

    SORTABLE: ClassVar[tuple[str, ...]] = ()
    DEFAULT_SORT: ClassVar[str] = ""

    page: int = Field(1, ge=1)
    page_size: int = Field(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort: str | None = None
    format: Literal["json", "csv"] = "json"

    @model_validator(mode="after")
    def _normalize_sort(self) -> ListQuery:
        self.sort = format_sort(parse_sort(self.sort, self.SORTABLE, self.DEFAULT_SORT))
        return self

    def sort_keys(self) -> list[tuple[str, bool]]:
        return parse_sort(self.sort, self.SORTABLE, self.DEFAULT_SORT)


class DateRangeMixin(ApiModel):
    date_from: date | None = None  # inclusive
    date_to: date | None = None  # inclusive

    @model_validator(mode="after")
    def _check_range(self) -> DateRangeMixin:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


JsonScalar = str | int | float | bool | None
JsonValue = Any

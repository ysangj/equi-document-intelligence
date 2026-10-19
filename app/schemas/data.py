"""Data browsing (FR-9): returns, fund facts, statements, holdings, funds, accounts,
and value provenance for the side panel. Item fields follow the DB views (T2), in
the same order. Fields the API computes are marked "API-computed"."""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Literal

from pydantic import Field

from app.schemas.common import (
    AliasMatchMethod,
    ApiModel,
    DateRangeMixin,
    DocumentSource,
    DocumentType,
    ExtractionSource,
    ListQuery,
    NetGross,
    ReturnType,
    ReviewReason,
    Timestamp,
    ValueKind,
    ValueStatus,
)

# ---- shared filters ---------------------------------------------------------------


class ValueFilters(ApiModel):
    """FR-9 filters. Every list param is a repeated key (`?status=ok&status=conflict`).
    Values within one param are ORed, and different params are ANDed."""

    document_id: list[int] = Field(default_factory=list)
    document_type: list[DocumentType] = Field(default_factory=list)
    status: list[ValueStatus] = Field(default_factory=list)
    review_reason: list[ReviewReason] = Field(default_factory=list)


# ---- returns ----------------------------------------------------------------------


class ReturnsQuery(ListQuery, ValueFilters, DateRangeMixin):
    """GET /api/returns. The date range applies to `period_end`."""

    SORTABLE: ClassVar[tuple[str, ...]] = (
        "fund_name", "manager", "strategy", "fund_currency", "period_end", "return_type", "net_gross",
        "value_pct", "status", "confidence", "document_name", "document_type",
    )
    DEFAULT_SORT: ClassVar[str] = "-period_end,fund_name"

    fund_id: list[int] = Field(default_factory=list)
    return_type: list[ReturnType] = Field(default_factory=list)
    net_gross: list[NetGross] = Field(default_factory=list)


class ReturnItem(ApiModel):
    return_id: int
    fund_id: int
    fund_name: str
    manager: str | None
    strategy: str | None
    fund_currency: str | None
    period_end: date
    period_year: int
    period_month: int
    period_label: str | None
    return_type: ReturnType
    net_gross: NetGross
    value: float | None  # fraction; null when unparseable
    value_pct: float | None  # display this
    status: ValueStatus
    review_reasons: list[ReviewReason]
    confidence: float | None
    page: int | None
    source_snippet: str | None
    document_id: int
    document_name: str
    document_type: DocumentType | None


# ---- fund facts -------------------------------------------------------------------


class FundFactsQuery(ListQuery, ValueFilters, DateRangeMixin):
    """GET /api/fund-facts. The date range applies to `as_of_date`."""

    SORTABLE: ClassVar[tuple[str, ...]] = (
        "fund_name", "manager", "strategy", "metric", "value", "unit", "as_of_date", "status",
        "confidence", "document_name", "document_type",
    )
    DEFAULT_SORT: ClassVar[str] = "-as_of_date,fund_name,metric"

    fund_id: list[int] = Field(default_factory=list)
    metric: list[str] = Field(default_factory=list)


class FundFactItem(ApiModel):
    fact_id: int
    fund_id: int
    fund_name: str
    manager: str | None
    strategy: str | None
    metric: str
    value: float | None
    value_pct: float | None  # API-computed: value * 100 when unit == "fraction", else null
    value_text: str | None
    unit: str | None  # "fraction" | ISO currency (e.g. "USD") | "ratio" | "years" | null
    as_of_date: date | None
    status: ValueStatus
    review_reasons: list[ReviewReason]
    confidence: float | None
    page: int | None
    source_snippet: str | None
    document_id: int
    document_name: str
    document_type: DocumentType | None


# ---- statements -------------------------------------------------------------------

# Every clickable Statements cell, addressed as /api/sources/statements/{id}?field=...
StatementField = Literal[
    "account_name", "account_number_masked", "custodian", "fund_name", "currency",
    "period_start", "period_end", "opening_balance", "contributions", "withdrawals",
    "pnl", "fees", "closing_balance",
]
# API field name -> key in statements.provenance (StatementExtraction field name, §9).
STATEMENT_PROVENANCE_KEYS: dict[str, str] = {
    "account_name": "account_name",
    "account_number_masked": "account_number_masked",
    "custodian": "custodian_or_manager",
    "fund_name": "fund_name",
    "currency": "currency",
    "period_start": "period_start",
    "period_end": "period_end",
    "opening_balance": "opening_balance",
    "contributions": "contributions",
    "withdrawals": "withdrawals",
    "pnl": "pnl",
    "fees": "fees",
    "closing_balance": "closing_balance",
}


class StatementsQuery(ListQuery, ValueFilters, DateRangeMixin):
    """GET /api/statements. The date range applies to `period_end`. `fund_id` matches
    statements linked to that fund (a capital-account statement)."""

    SORTABLE: ClassVar[tuple[str, ...]] = (
        "account_name", "account_number_masked", "custodian", "fund_name", "manager",
        "period_start", "period_end", "opening_balance", "contributions", "withdrawals",
        "pnl", "fees", "closing_balance", "currency", "status", "holdings_count",
        "document_name", "document_type",
    )
    DEFAULT_SORT: ClassVar[str] = "-period_end,account_name"

    account_id: list[int] = Field(default_factory=list)
    fund_id: list[int] = Field(default_factory=list)


class StatementItem(ApiModel):
    statement_id: int
    account_id: int
    account_name: str
    account_number_masked: str | None
    custodian: str | None
    fund_id: int | None
    fund_name: str | None
    manager: str | None
    period_start: date | None
    period_end: date
    opening_balance: float | None
    contributions: float | None
    withdrawals: float | None
    pnl: float | None
    fees: float | None
    closing_balance: float | None
    currency: str | None
    status: ValueStatus
    review_reasons: list[ReviewReason]
    # API-computed: the cells to highlight. Includes fields below the confidence
    # threshold, closing_balance on rollforward_mismatch, period_end on future_date,
    # and fields whose snippet was not found.
    flagged_fields: list[StatementField]
    holdings_count: int
    document_id: int
    document_name: str
    document_type: DocumentType | None


# ---- holdings ---------------------------------------------------------------------


class HoldingsQuery(ListQuery):
    """GET /api/statements/{id}/holdings."""

    SORTABLE: ClassVar[tuple[str, ...]] = (
        "line_no", "holding_name", "identifier", "quantity", "market_value", "weight_pct", "status", "confidence",
    )
    DEFAULT_SORT: ClassVar[str] = "line_no"

    status: list[ValueStatus] = Field(default_factory=list)


class HoldingItem(ApiModel):
    holding_id: int
    statement_id: int
    account_id: int
    account_name: str
    custodian: str | None
    fund_id: int | None
    fund_name: str | None
    period_end: date
    currency: str | None
    line_no: int
    holding_name: str
    identifier: str | None
    quantity: float | None
    market_value: float | None
    weight: float | None  # fraction
    weight_pct: float | None  # display this
    status: ValueStatus
    review_reasons: list[ReviewReason]
    confidence: float | None
    page: int | None
    source_snippet: str | None
    document_id: int
    document_name: str


# ---- funds / accounts -------------------------------------------------------------


class AliasItem(ApiModel):
    alias_id: int
    alias: str
    confirmed: bool
    match_method: AliasMatchMethod
    match_score: float | None  # rapidfuzz 0-100; null for exact/new/manual
    source_document_id: int | None
    source_document_name: str | None


class FundItem(ApiModel):
    fund_id: int
    fund_name: str
    manager: str | None
    strategy: str | None
    currency: str | None
    aliases: list[AliasItem]
    has_unconfirmed_alias: bool
    return_count: int  # visible returns rows
    document_count: int  # visible documents with at least one value for this fund
    latest_period_end: date | None


class AccountItem(ApiModel):
    account_id: int
    account_name: str
    account_number_masked: str | None
    custodian: str | None
    currency: str | None
    aliases: list[AliasItem]
    has_unconfirmed_alias: bool
    statement_count: int
    latest_period_end: date | None


# ---- sources (side panel) ---------------------------------------------------------

SourceTable = Literal["returns", "fund_facts", "statements", "statement_holdings"]
HoldingField = Literal["holding_name", "identifier", "quantity", "market_value", "weight"]

# Allowed `field` values per table; the first one is the default when `field` is omitted.
SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "returns": ("value",),
    "fund_facts": ("value",),
    "statements": (
        "closing_balance", "opening_balance", "contributions", "withdrawals", "pnl", "fees",
        "period_start", "period_end", "account_name", "account_number_masked", "custodian",
        "fund_name", "currency",
    ),
    "statement_holdings": ("market_value", "weight", "quantity", "holding_name", "identifier"),
}


class Provenance(ApiModel):
    page: int | None
    source_snippet: str | None  # verbatim; render as plain text with white-space: pre-wrap
    confidence: float | None
    # true: snippet found in the document text. false: not found (snippet_not_found).
    # null: check skipped (image-only page, e.g. the scanned PDF) or no snippet.
    snippet_verified: bool | None


class SnippetHighlight(ApiModel):
    """Where the value is written inside `provenance.source_snippet` (U1 request 4).

    `start`/`end` are UTF-16 code-unit offsets, i.e. JavaScript string indices, so
    `snippet.slice(start, end) === text`. Null when the value cannot be located (text
    values, no snippet, or no number in the snippet matches the value)."""

    start: int
    end: int
    text: str


class DocumentRef(ApiModel):
    document_id: int
    document_name: str
    document_type: DocumentType | None
    mime_type: str
    source: DocumentSource
    web_view_link: str | None  # Drive link; null in demo mode (local files)
    version: int
    processed_at: Timestamp | None


class ExtractionRef(ApiModel):
    extraction_id: int | None
    model: str | None  # e.g. "claude-opus-5-5"; for fixtures, the model that produced the fixture
    prompt_version: str | None
    source: ExtractionSource | None
    created_at: Timestamp | None


class ReasonInfo(ApiModel):
    code: ReviewReason
    label: str  # REVIEW_REASON_LABELS[code]
    message: str  # server-written sentence with the numbers, e.g. "Monthly returns compound to -0.15% but the stated YTD is +0.15%."


class ValueSnapshot(ApiModel):
    """One value from one document. Used for conflict candidates and review cards."""

    table: SourceTable
    id: int
    field: str
    value: float | str | None
    value_pct: float | None  # set when value_kind == "percent"
    value_kind: ValueKind
    unit: str | None
    currency: str | None
    status: ValueStatus
    review_reasons: list[ReviewReason]
    provenance: Provenance
    document_id: int
    document_name: str
    document_type: DocumentType | None
    web_view_link: str | None
    extraction_model: str | None


class ReviewHistoryEntry(ApiModel):
    review_action_id: int
    action: Literal["accept", "edit", "choose", "confirm_alias", "reject_alias"]
    old_value: Any
    new_value: Any
    note: str | None
    created_at: Timestamp


class SourceQuery(ApiModel):
    field: str | None = None


class SourceResponse(ApiModel):
    """GET /api/sources/{table}/{id}?field=..."""

    table: SourceTable
    id: int
    field: str
    label: str  # row title, e.g. "Kestrel Quant Equity Fund LP · Jan 2026 · MTD net"
    field_label: str  # e.g. "Return", "Closing balance"
    value: float | str | None
    value_pct: float | None
    value_kind: ValueKind
    unit: str | None
    currency: str | None
    status: ValueStatus
    review_reasons: list[ReviewReason]
    reasons: list[ReasonInfo]
    provenance: Provenance
    snippet_highlight: SnippetHighlight | None  # the value inside source_snippet, for <mark>
    document: DocumentRef
    extraction: ExtractionRef
    conflicts: list[ValueSnapshot]  # the other rows in a conflict group; [] when none
    review_history: list[ReviewHistoryEntry]  # newest first; [] when never reviewed

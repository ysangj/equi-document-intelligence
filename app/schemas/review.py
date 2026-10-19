"""Review queue (FR-13).

Item ids are "<target_table>:<target_id>", the same as `review_queue.item_key` (T2).
A conflict group, meaning two or more value rows sharing a natural key with
differing values, is ONE item. Its id is that of the lowest-id member, and POST
also accepts any member's id.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import (
    AliasMatchMethod,
    ApiModel,
    ListQuery,
    NetGross,
    ReturnType,
    ReviewReason,
    Timestamp,
    ValueStatus,
)
from app.schemas.data import ReasonInfo, ValueSnapshot

ReviewKind = Literal["value", "conflict", "alias"]
ReviewTargetTable = Literal[
    "returns", "fund_facts", "statements", "statement_holdings", "fund_aliases", "account_aliases",
]
ReviewActionName = Literal["accept", "edit", "choose", "confirm_alias", "reject_alias", "make_canonical"]
ALLOWED_ACTIONS: dict[str, tuple[str, ...]] = {
    "value": ("accept", "edit"),
    "conflict": ("choose", "edit"),
    "alias": ("confirm_alias", "reject_alias", "make_canonical"),
}

# ---- review_detail shapes (one key per reason) ------------------------------------


class _Detail(ApiModel):
    pass


class LowConfidenceDetail(_Detail):
    confidence: float
    threshold: float
    fields: list[str] | None = None  # statements: the fields below threshold


class YtdMonth(_Detail):
    period_end: date
    value: float  # fraction
    return_id: int | None


class YtdMismatchDetail(_Detail):
    year: int
    stated: float  # fraction
    computed: float  # fraction, compounded from `months`
    difference: float  # stated - computed
    tolerance: float  # fraction (0.0005 = 5 bps)
    months: list[YtdMonth]


class RollforwardMismatchDetail(_Detail):
    opening_balance: float
    contributions: float
    withdrawals: float
    pnl: float
    fees: float  # 0 when not stated
    expected_closing: float  # opening + contributions - withdrawals + pnl - fees
    stated_closing: float
    difference: float  # stated - expected
    tolerance: float  # currency units
    currency: str | None


class ConflictDetail(_Detail):
    conflicting_ids: list[int]  # other rows of the same table in the group
    tolerance: float


class OutOfRangeDetail(_Detail):
    value: float
    min: float
    max: float


class FutureDateDetail(_Detail):
    date: date
    today: date


class SnippetNotFoundDetail(_Detail):
    snippet: str | None
    fields: list[str] | None = None
    # true: the snippet IS in the document, but the value is not written in it, so the
    # value cannot be traced from its snippet (BUG-Q2-06). Absent/null: snippet not found.
    value_missing: bool | None = None


class AliasMatchDetail(_Detail):
    alias: str
    match_score: float | None
    fund_id: int | None = None
    account_id: int | None = None


class UnknownLabelDetail(_Detail):
    raw_label: str | None  # the column or label as written, if any


class UnparseableValueDetail(_Detail):
    raw: str | None
    field: str | None


REVIEW_DETAIL_MODELS: dict[str, type[_Detail]] = {
    "low_confidence": LowConfidenceDetail,
    "ytd_mismatch": YtdMismatchDetail,
    "rollforward_mismatch": RollforwardMismatchDetail,
    "alias_match": AliasMatchDetail,
    "conflict": ConflictDetail,
    "snippet_not_found": SnippetNotFoundDetail,
    "future_date": FutureDateDetail,
    "out_of_range": OutOfRangeDetail,
    "net_gross_unknown": UnknownLabelDetail,
    "return_type_unknown": UnknownLabelDetail,
    "unparseable_value": UnparseableValueDetail,
}


def validate_review_detail(detail: dict[str, Any]) -> dict[str, Any]:
    for key, value in detail.items():
        model = REVIEW_DETAIL_MODELS.get(key)
        if model is None:
            raise ValueError(f"unknown review_detail key {key!r}")
        model.model_validate(value)
    return detail


# ---- items ------------------------------------------------------------------------


class ReviewQuery(ListQuery):
    SORTABLE: ClassVar[tuple[str, ...]] = ("created_at", "kind", "document_name", "target_table")
    DEFAULT_SORT: ClassVar[str] = "-created_at"

    kind: list[ReviewKind] = Field(default_factory=list)
    reason: list[ReviewReason] = Field(default_factory=list)
    document_id: list[int] = Field(default_factory=list)


class ReviewSubject(ApiModel):
    """What the item is about. Fields that do not apply are null."""

    fund_id: int | None
    fund_name: str | None
    account_id: int | None
    account_name: str | None
    period_end: date | None
    period_label: str | None
    return_type: ReturnType | None
    net_gross: NetGross | None
    metric: str | None  # fund_facts
    holding_name: str | None  # statement_holdings


class AliasSuggestion(ApiModel):
    entity: Literal["fund", "account"]
    alias_id: int
    alias: str
    match_method: AliasMatchMethod
    match_score: float | None
    suggested_id: int  # the fund/account it was fuzzily linked to
    suggested_name: str
    source_document_id: int | None
    source_document_name: str | None
    affected_rows: int  # value rows linked through this alias (relinked on reject)


class ReviewItem(ApiModel):
    id: str
    kind: ReviewKind
    target_table: ReviewTargetTable
    target_id: int
    status: ValueStatus
    review_reasons: list[ReviewReason]
    reasons: list[ReasonInfo]
    title: str  # e.g. "Northwind Global Macro Fund · Jan 2026 · MTD net"
    subject: ReviewSubject
    detail: dict[str, Any]  # review_detail: one key per reason, shapes in REVIEW_DETAIL_MODELS
    current: ValueSnapshot | None  # kind=value: the flagged value (statements: closing_balance)
    # kind=value on statements: the six money fields, then period_start and period_end
    # (value_kind "date"), for the roll-forward view and for editing.
    fields: list[ValueSnapshot] | None
    candidates: list[ValueSnapshot] | None  # kind=conflict: every row in the group (>= 2)
    alias: AliasSuggestion | None  # kind=alias
    allowed_actions: list[ReviewActionName]
    document_id: int | None
    document_name: str | None
    created_at: Timestamp

    @field_validator("detail")
    @classmethod
    def _detail(cls, v: dict[str, Any]) -> dict[str, Any]:
        return validate_review_detail(v)

    @model_validator(mode="after")
    def _by_kind(self) -> ReviewItem:
        if self.id != f"{self.target_table}:{self.target_id}":
            raise ValueError("id must be '<target_table>:<target_id>'")
        if list(self.allowed_actions) != list(ALLOWED_ACTIONS[self.kind]):
            raise ValueError(f"allowed_actions for {self.kind} must be {ALLOWED_ACTIONS[self.kind]}")
        if self.kind == "alias":
            if self.alias is None or self.target_table not in ("fund_aliases", "account_aliases"):
                raise ValueError("alias items need `alias` and an *_aliases target")
        elif self.kind == "conflict":
            if not self.candidates or len(self.candidates) < 2 or self.status != "conflict":
                raise ValueError("conflict items need >= 2 candidates and status conflict")
        elif self.current is None:
            raise ValueError("value items need `current`")
        return self


class ReviewCounts(ApiModel):
    by_kind: dict[ReviewKind, int]
    by_reason: dict[ReviewReason, int]


class ReviewListResponse(ApiModel):
    items: list[ReviewItem]
    total: int
    page: int
    page_size: int
    sort: str
    counts: ReviewCounts  # over all open items, ignoring filters


# ---- actions ----------------------------------------------------------------------


class _Action(ApiModel):
    note: str | None = Field(None, max_length=2000)


class AcceptAction(_Action):
    action: Literal["accept"]


class EditAction(_Action):
    """Send `value_pct` for percent values (what the user typed, e.g. -0.15), and
    `value` for money, number, text and date values. Exactly one of the two."""

    action: Literal["edit"]
    field: str | None = None  # statements: required, one of the money/date fields; others: "value" (default)
    value: float | str | None = None
    value_pct: float | None = None
    chosen_id: int | None = None  # conflict items: required, the row that keeps the edited value

    @model_validator(mode="after")
    def _one_value(self) -> EditAction:
        if (self.value is None) == (self.value_pct is None):
            raise ValueError("send exactly one of value or value_pct")
        return self


class ChooseAction(_Action):
    action: Literal["choose"]
    chosen_id: int  # target_id of the winning candidate (same table)


class ConfirmAliasAction(_Action):
    action: Literal["confirm_alias"]


class RejectAliasAction(_Action):
    action: Literal["reject_alias"]
    # Existing fund/account to move the alias to. Null creates a new fund/account
    # named after the alias (e.g. the Alder Asia vs Alder Dubai sibling case).
    reassign_to_id: int | None = None


class MakeCanonicalAction(_Action):
    """The alias's text becomes the fund/account's canonical name (pinned: the
    automatic canonical-name rule no longer renames it); the old canonical name stays
    as a confirmed alias, and the alias is confirmed."""

    action: Literal["make_canonical"]


ReviewActionRequest = Annotated[
    AcceptAction | EditAction | ChooseAction | ConfirmAliasAction | RejectAliasAction | MakeCanonicalAction,
    Field(discriminator="action"),
]


class AffectedRow(ApiModel):
    table: ReviewTargetTable | Literal["funds", "accounts"]
    id: int
    change: Literal[
        "status_changed", "value_edited", "deleted", "alias_confirmed", "alias_reassigned", "relinked", "created",
        "renamed",
    ]
    status: ValueStatus | None  # new status of a value row; null for deleted rows and alias/entity rows


class ReviewActionResponse(ApiModel):
    id: str
    action: ReviewActionName
    resolved: bool  # the item has left the queue
    review_action_ids: list[int]  # rows written to review_actions
    affected: list[AffectedRow]
    remaining_open: int  # for the Review badge

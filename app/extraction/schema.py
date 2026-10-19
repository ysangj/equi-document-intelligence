"""Canonical extraction schema (PRD §9) plus the wire schema sent to Claude.

`Extraction` and its parts are exactly the §9 models. Every extractor returns an
`Extraction`; it is what `extractions.raw_json` stores and what T5's normalizer reads.

Claude's structured output (`output_config.format`, JSON schema) requires
`additionalProperties: false` on every object, so the two free-form dicts in §9
(`FundExtraction.other_metrics`, `Extraction.extra`) cannot be expressed directly. The
`Wire*` models are the same shape with those dicts as lists of name/value pairs, and
with "field not present" expressed as a SourcedValue whose `value` is null (fewer
union types in the schema). `wire_to_extraction()` converts back to the canonical form.

Values stay as written in the document ("(0.36)", "120bps", "Jan-26", "13.10mm");
normalization is T5's job (PRD §2: code for truth).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Bump when the prompt or wire schema changes; reprocessing compares against it (§9).
PROMPT_VERSION = "2026-10-16.3"

DocumentType = Literal["factsheet", "account_statement", "performance_report", "email", "other"]
ReturnType = Literal["MTD", "QTD", "YTD", "ITD", "annualized", "unknown"]
NetGross = Literal["net", "gross", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- canonical §9 models ------------------------------------------------------------


class SourcedValue(_Strict):
    value: str | float | None
    page: int | None
    source_snippet: str | None
    confidence: float


class ReturnEntry(_Strict):
    period: str
    value: SourcedValue
    return_type: ReturnType
    net_gross: NetGross


class FundExtraction(_Strict):
    fund_name: SourcedValue
    manager: SourcedValue | None
    strategy: SourcedValue | None
    currency: SourcedValue | None
    as_of_date: SourcedValue | None
    aum: SourcedValue | None
    returns: list[ReturnEntry]
    other_metrics: dict[str, SourcedValue]


class Holding(_Strict):
    name: SourcedValue
    identifier: SourcedValue | None
    quantity: SourcedValue | None
    market_value: SourcedValue | None
    weight: SourcedValue | None


class StatementExtraction(_Strict):
    account_name: SourcedValue
    account_number_masked: SourcedValue | None
    custodian_or_manager: SourcedValue | None
    fund_name: SourcedValue | None
    currency: SourcedValue | None
    period_start: SourcedValue | None
    period_end: SourcedValue
    opening_balance: SourcedValue | None
    contributions: SourcedValue | None
    withdrawals: SourcedValue | None
    pnl: SourcedValue | None
    fees: SourcedValue | None
    closing_balance: SourcedValue | None
    holdings: list[Holding]


class PageText(_Strict):
    page: int
    text: str


class Extraction(_Strict):
    document_type: DocumentType
    document_type_confidence: float
    funds: list[FundExtraction]  # factsheets, reports, emails
    statements: list[StatementExtraction]  # account statements
    pages: list[PageText]  # transcription for chunking
    extra: dict


# ---- wire models (Claude structured output) --------------------------------------------
# No field defaults: every property is required in the generated schema.


class WireValue(_Strict):
    """One extracted field. If the document does not state it: value, page and
    source_snippet are null and confidence is 0."""

    value: str | None = Field(
        description="The value exactly as written in the document, including signs, "
        "parentheses, % / bps / mm suffixes and date formats. Do not convert or compute."
    )
    page: int | None = Field(description="1-based page number where the value appears.")
    source_snippet: str | None = Field(
        description="Short verbatim excerpt (label plus value) copied character-for-character "
        "from the document so the value can be found in its text."
    )
    confidence: float = Field(description="0 to 1: how sure you are this value is correct and correctly labeled.")


class WireReturn(_Strict):
    period: str = Field(description='Period exactly as written, e.g. "Jan-26", "January 2026", "2026-01", "Q1 2026", "YTD 2026".')
    value: WireValue
    return_type: ReturnType
    net_gross: NetGross


class WireMetric(_Strict):
    name: str = Field(description="snake_case metric name, e.g. sharpe, volatility, max_drawdown, nav_per_share, inception_date, management_fee.")
    value: WireValue


class WireFund(_Strict):
    fund_name: WireValue
    manager: WireValue
    strategy: WireValue
    currency: WireValue
    as_of_date: WireValue
    aum: WireValue
    returns: list[WireReturn]
    other_metrics: list[WireMetric]


class WireHolding(_Strict):
    name: WireValue
    identifier: WireValue
    quantity: WireValue
    market_value: WireValue
    weight: WireValue


class WireStatement(_Strict):
    account_name: WireValue
    account_number_masked: WireValue
    custodian_or_manager: WireValue
    fund_name: WireValue
    currency: WireValue
    period_start: WireValue
    period_end: WireValue
    opening_balance: WireValue
    contributions: WireValue
    withdrawals: WireValue
    pnl: WireValue
    fees: WireValue
    closing_balance: WireValue
    holdings: list[WireHolding]


class WirePage(_Strict):
    page: int
    text: str = Field(description="Full transcription of the page's visible text, in reading order.")


class WireNote(_Strict):
    key: str
    value: str


class WireExtraction(_Strict):
    document_type: DocumentType
    document_type_confidence: float
    funds: list[WireFund]
    statements: list[WireStatement]
    pages: list[WirePage]
    extra: list[WireNote] = Field(
        description="Other noteworthy facts as key/value notes, e.g. estimate flags, footnotes, document date."
    )


def _is_empty(v: WireValue) -> bool:
    return (v.value is None or str(v.value).strip() == "") and not v.source_snippet


def _sv(v: WireValue) -> SourcedValue:
    return SourcedValue(
        value=v.value, page=v.page, source_snippet=v.source_snippet, confidence=_clamp(v.confidence)
    )


def _opt(v: WireValue) -> SourcedValue | None:
    return None if _is_empty(v) else _sv(v)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def wire_to_extraction(w: WireExtraction) -> Extraction:
    """Wire form -> canonical §9 form. Absent optional fields become None; duplicate
    metric names keep the first occurrence; confidences are clamped to [0, 1]."""
    funds = []
    for f in w.funds:
        metrics: dict[str, SourcedValue] = {}
        for m in f.other_metrics:
            if not _is_empty(m.value):
                metrics.setdefault(m.name, _sv(m.value))
        funds.append(
            FundExtraction(
                fund_name=_sv(f.fund_name),
                manager=_opt(f.manager),
                strategy=_opt(f.strategy),
                currency=_opt(f.currency),
                as_of_date=_opt(f.as_of_date),
                aum=_opt(f.aum),
                returns=[
                    ReturnEntry(period=r.period, value=_sv(r.value), return_type=r.return_type, net_gross=r.net_gross)
                    for r in f.returns
                ],
                other_metrics=metrics,
            )
        )
    statements = [
        StatementExtraction(
            account_name=_sv(s.account_name),
            account_number_masked=_opt(s.account_number_masked),
            custodian_or_manager=_opt(s.custodian_or_manager),
            fund_name=_opt(s.fund_name),
            currency=_opt(s.currency),
            period_start=_opt(s.period_start),
            period_end=_sv(s.period_end),
            opening_balance=_opt(s.opening_balance),
            contributions=_opt(s.contributions),
            withdrawals=_opt(s.withdrawals),
            pnl=_opt(s.pnl),
            fees=_opt(s.fees),
            closing_balance=_opt(s.closing_balance),
            holdings=[
                Holding(
                    name=_sv(h.name),
                    identifier=_opt(h.identifier),
                    quantity=_opt(h.quantity),
                    market_value=_opt(h.market_value),
                    weight=_opt(h.weight),
                )
                for h in s.holdings
            ],
        )
        for s in w.statements
    ]
    extra: dict[str, Any] = {}
    for n in w.extra:
        extra.setdefault(n.key, n.value)
    return Extraction(
        document_type=w.document_type,
        document_type_confidence=_clamp(w.document_type_confidence),
        funds=funds,
        statements=statements,
        pages=[PageText(page=p.page, text=p.text) for p in w.pages],
        extra=extra,
    )


def wire_json_schema() -> dict[str, Any]:
    """JSON schema for `output_config.format` (strict: additionalProperties false)."""
    from anthropic import transform_schema

    return transform_schema(WireExtraction)

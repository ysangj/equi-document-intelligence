"""T5: normalization, validation, alias resolution, conflicts, decisions, chunking.

`process_extraction` runs inside the job's transaction (T4), right after the
`extractions` row is inserted and the document's text/type are set, and before the
commit. It never commits. Raising aborts the attempt (retried with backoff).

Steps:
1. Take the postprocess advisory lock (serializes the cross-document part of
   concurrent jobs: alias creation, conflict detection; extraction stays parallel).
2. Remember the natural keys this file's rows had (any version), then delete this
   document's value rows (reprocessing replaces them; holdings cascade).
3. For each fund block: resolve the fund (exact / fuzzy / new), fill manager,
   strategy and currency, write `returns` and `fund_facts`. For each statement:
   resolve account (+ fund), write `statements` with per-field provenance and
   `statement_holdings`. Every value is normalized (app/normalize) and validated
   (app/ingest/validation): unparseable values are kept with value NULL and
   `unparseable_value`, never dropped.
4. YTD compounding check for every YTD row.
5. Re-apply review decisions by natural key + drive_file_id (app/ingest/decisions).
6. Recompute conflicts for the old and new keys (app/ingest/conflicts), then the
   canonical names of the funds/accounts those keys use (app/ingest/canonical), and
   refresh the status of other documents whose rows or aliases changed
   (`done` <-> `needs_review`).
7. Chunks (text + tsv; embeddings are filled after commit, app/search/indexing), in a
   savepoint: a failure leaves `search_indexed = false` and never fails the job.
8. Return "needs_review" if any of the document's rows, or an alias they use, is
   flagged; otherwise "done".

Document type comes from the extraction (set by the pipeline before this hook).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.extraction.schema import Extraction, FundExtraction, SourcedValue, StatementExtraction
from app.ingest import canonical
from app.ingest import conflicts as conflicts_mod
from app.ingest.decisions import reapply_decisions
from app.ingest.resolve import fill_fund_details, resolve_account, resolve_fund
from app.ingest.review_state import add_flag, set_flags
from app.ingest.validation import (
    MonthValue,
    check_confidence,
    check_future,
    check_monthly_range,
    check_rollforward,
    check_ytd,
)
from app.models import Document, Fund, FundFact, Return, Statement, StatementHolding
from app.normalize.dates import DAY, MONTH, Period, month_end, parse_period, quarter_end
from app.normalize.dates import return_period_end, return_period_start
from app.normalize.locate import snippet_numbers_sum_to, value_in_snippet
from app.normalize.names import display_name
from app.normalize.numbers import (
    UnitContext,
    csv_column_header,
    has_money_marker,
    has_percent_unit,
    parse_amount,
    parse_currency,
    parse_number,
    parse_percent,
)
from app.normalize.text import compact, normalize_for_match, snippet_in_text

log = logging.getLogger("app.ingest.postprocess")

POSTPROCESS_LOCK_KEY = 0x646F6332  # pg_advisory_xact_lock key ("doc2"); T6 review writes take it too
STATEMENT_MONEY_FIELDS = ("opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance")
STATEMENT_FIELDS = ("account_name", "account_number_masked", "custodian_or_manager", "fund_name", "currency",
                    "period_start", "period_end", *STATEMENT_MONEY_FIELDS)

# Metric names: lower snake_case, with a few synonyms folded together.
_METRIC_SYNONYMS = {
    "volatility": "vol", "annualized_volatility": "vol", "annualised_volatility": "vol", "annualized_vol": "vol",
    "annualised_vol": "vol", "volatility_annualized": "vol", "sharpe_ratio": "sharpe",
    "maximum_drawdown": "max_drawdown", "max_dd": "max_drawdown", "fund_aum": "aum", "fund_assets": "aum",
    "assets_under_management": "aum", "aum_usd": "aum", "total_aum": "aum",
}
MONEY_METRICS = frozenset({"aum", "nav", "net_asset_value", "nav_per_share", "capital", "commitment", "dry_powder_amount"})
RATIO_PREFIXES = ("sharpe", "sortino", "beta", "correlation", "calmar", "information_ratio", "treynor")
PERCENT_PREFIXES = ("vol", "max_drawdown", "drawdown", "management_fee", "performance_fee", "incentive_fee",
                    "hurdle", "target_vol", "tracking_error", "alpha", "cagr", "return", "ytd", "itd")
# Metrics that must be numeric: failing to parse them is `unparseable_value`. Other
# metrics (terms, liquidity, fee descriptions) are kept as text.
NUMERIC_METRICS = frozenset({"aum", "nav", "net_asset_value", "nav_per_share", "sharpe", "sortino", "vol",
                             "max_drawdown", "beta", "correlation"})


# Period labels that mean "the period ending at the document's as-of date".
_AS_OF_PERIOD = re.compile(
    r"(?:mtd|qtd|ytd|itd|month|quarter|year|full[\s-]year|fy|period|latest|current\s+month|this\s+month|month[\s-]to[\s-]date|"
    r"quarter[\s-]to[\s-]date|year[\s-]to[\s-]date|since\s+inception|inception[\s-]to[\s-]date|"
    r"annuali[sz]ed(?:\s+since\s+inception)?|)"
)


def metric_name(name: str) -> str:
    snake = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "metric"
    return _METRIC_SYNONYMS.get(snake, snake)


def lock_postprocess(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": POSTPROCESS_LOCK_KEY})


def _today() -> date:
    return datetime.now(UTC).date()


def _raw(sv: SourcedValue | None) -> Any:
    return None if sv is None else sv.value


def _raw_str(sv: SourcedValue | None) -> str | None:
    v = _raw(sv)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _fact_kind(unit: str | None) -> str:
    """How a fact's value is written (as `app.api.values.field_value` shows it)."""
    if unit == "fraction":
        return "percent"
    if unit and len(unit) == 3 and unit.isupper():
        return "money"
    return "number"


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


@dataclass
class _Text:
    """The document text for snippet checks and unit context."""

    content: str
    source: str  # file | transcription
    is_csv: bool
    pages: list[str] = field(init=False)
    norm: str = field(init=False)
    norm_compact: str = field(init=False)

    def __post_init__(self):
        self.pages = self.content.split("\f") if self.content else []
        self.norm = normalize_for_match(self.content)
        self.norm_compact = compact(self.norm)

    def page_text(self, page: int | None) -> str:
        if page is not None and 1 <= page <= len(self.pages):
            return self.pages[page - 1]
        return self.content

    def verify(self, snippet: str | None, page: int | None) -> bool | None:
        """True/False = found/not found; None = skipped (no text layer for that page)."""
        if self.source != "file" or not self.norm:
            return None
        if page is not None and 1 <= page <= len(self.pages) and not self.pages[page - 1].strip():
            return None  # image-only page in a PDF that has text elsewhere
        if not snippet or not snippet.strip():
            return False
        return snippet_in_text(snippet, self.norm, compact_text=self.norm_compact)

    def unit_context(self, sv: SourcedValue) -> UnitContext:
        header = csv_column_header(self.content, sv.value, sv.source_snippet) if self.is_csv else None
        return UnitContext(snippet=sv.source_snippet, column_header=header, is_csv=self.is_csv,
                           page_text=self.page_text(sv.page))


def _text_source(doc: Document, extraction: Extraction) -> str:
    if doc.text_source in ("file", "transcription"):
        return doc.text_source
    # Rows processed before migration 0003 / direct callers: infer it. A PDF whose
    # stored text is exactly the extractor's transcription had no text layer.
    if doc.mime_type == "application/pdf":
        transcription = "\f".join(p.text.strip() for p in sorted(extraction.pages, key=lambda p: p.page))
        if (doc.text_content or "") == transcription:
            return "transcription"
    return "file"


class _Processor:
    def __init__(self, session: Session, doc: Document, extraction: Extraction, extraction_id: int,
                 today: date, threshold: float):
        self.s = session
        self.doc = doc
        self.ex = extraction
        self.extraction_id = extraction_id
        self.today = today
        self.threshold = threshold
        self.text = _Text(doc.text_content or "", _text_source(doc, extraction), doc.mime_type == "text/csv")
        self.rows: dict[str, list[Any]] = {"returns": [], "fund_facts": [], "statements": [], "statement_holdings": []}
        self._return_keys: dict[tuple, Return] = {}
        self._fact_keys: dict[tuple, FundFact] = {}
        self._statement_keys: dict[tuple, Statement] = {}
        # every fund/account this document resolved, with or without value rows (canonical names)
        self.fund_ids: set[int] = set()
        self.account_ids: set[int] = set()

    # ---- shared value checks -------------------------------------------------------------
    def _common_flags(self, flags: dict, sv: SourcedValue | None, kind: str | None = None,
                      value: Any = None) -> bool | None:
        """Snippet + confidence checks for one sourced value. Returns snippet_verified:
        False also when the snippet is in the document but the value is not written
        in it (§2 traceability; `snippet_not_found` with `value_missing`)."""
        if sv is None:
            return None
        verified = self.text.verify(sv.source_snippet, sv.page)
        if verified is False:
            flags["snippet_not_found"] = {"snippet": sv.source_snippet}
        elif verified and kind is not None and value_in_snippet(sv.source_snippet, kind, value, _raw_str(sv)) is False:
            verified = False
            flags["snippet_not_found"] = {"snippet": sv.source_snippet, "value_missing": True}
        low = check_confidence(sv.confidence, self.threshold)
        if low:
            flags["low_confidence"] = low
        return verified

    def _as_of(self, f: FundExtraction) -> Period | None:
        return parse_period(_raw_str(f.as_of_date), prefer="end") if f.as_of_date else None

    # ---- funds ---------------------------------------------------------------------------
    def write_fund(self, f: FundExtraction) -> None:
        name = _raw_str(f.fund_name)
        name_flags: dict[str, dict] = {}
        if not name:
            name = f"Unnamed fund ({self.doc.name})"
            name_flags["unparseable_value"] = {"raw": None, "field": "fund_name"}
        res = resolve_fund(self.s, name, document_id=self.doc.id)
        self.fund_ids.add(res.entity_id)
        fund = self.s.get(Fund, res.entity_id)
        currency = parse_currency(_raw_str(f.currency)) or (parse_currency(f.currency.source_snippet)
                                                            if f.currency else None)
        fill_fund_details(fund, manager=_raw_str(f.manager), strategy=_raw_str(f.strategy), currency=currency)

        as_of = self._as_of(f)
        as_of_end = as_of.end if as_of else None
        parsed_periods = []
        for r in f.returns:
            p = parse_period(r.period, ref=as_of_end)
            if p is not None and r.return_type == "MTD" and p.precision in (DAY, MONTH):
                parsed_periods.append(month_end(p.end))
        latest = max(parsed_periods) if parsed_periods else None
        for r in f.returns:
            self._write_return(fund.id, res.alias_id, r, as_of_end, latest, name_flags)
        facts_date = as_of_end if as_of_end else latest
        if as_of is not None and as_of.precision == MONTH:
            facts_date = month_end(as_of.end)
        if f.aum is not None:
            self._write_fact(fund, res.alias_id, "aum", f.aum, facts_date, as_of, name_flags)
        for raw_name, sv in f.other_metrics.items():
            self._write_fact(fund, res.alias_id, metric_name(raw_name), sv, facts_date, as_of, name_flags)

    def _write_return(self, fund_id: int, alias_id: int | None, r, as_of_end: date | None, latest: date | None,
                      base_flags: dict) -> None:
        sv = r.value
        flags: dict[str, dict] = dict(base_flags)
        parsed = parse_percent(sv.value, self.text.unit_context(sv))
        if parsed.value is None:
            flags["unparseable_value"] = {"raw": None if sv.value is None else str(sv.value), "field": "value"}
        period = parse_period(r.period, ref=as_of_end)
        pe = return_period_end(period, r.return_type, as_of_end) if period else None
        if pe is None and period is None and as_of_end and _AS_OF_PERIOD.fullmatch((r.period or "").strip().lower()):
            # "YTD", "Month", "Since inception": the period is the document's as-of date
            pe = quarter_end(as_of_end) if r.return_type == "QTD" else month_end(as_of_end)
        if pe is None:
            # Never dropped: stored at a stand-in date (the as-of month end, else the
            # latest period in the block, else today), flagged with the period as written.
            # A stand-in that collides with another row's key moves back a day.
            flags.setdefault("unparseable_value", {"raw": r.period, "field": "period"})
            pe = (month_end(as_of_end) if as_of_end else None) or latest or self.today
            while (fund_id, pe, r.return_type, r.net_gross) in self._return_keys:
                pe = pe.fromordinal(pe.toordinal() - 1)
        if r.return_type == "unknown":
            flags["return_type_unknown"] = {"raw_label": sv.source_snippet or r.period}
        if r.net_gross == "unknown":
            flags["net_gross_unknown"] = {"raw_label": sv.source_snippet or r.period}
        if r.return_type == "MTD":
            rng = check_monthly_range(parsed.value)
            if rng:
                flags["out_of_range"] = rng
        fut = check_future(return_period_start(pe, r.return_type), pe, self.today)
        if fut:
            flags["future_date"] = fut
        verified = self._common_flags(flags, sv, "percent", parsed.value)

        key = (fund_id, pe, r.return_type, r.net_gross)
        prev = self._return_keys.get(key)
        if prev is not None and not self._replace_unparsed(prev, parsed.value, "returns"):
            self._merge_duplicate(prev, parsed.value, sv, conflicts_mod.returns_differ)
            return
        row = Return(
            fund_id=fund_id, document_id=self.doc.id, extraction_id=self.extraction_id, fund_alias_id=alias_id,
            period_end=pe, period_label=r.period, return_type=r.return_type, net_gross=r.net_gross,
            value=parsed.value, page=sv.page, source_snippet=sv.source_snippet, confidence=sv.confidence,
            snippet_verified=verified,
        )
        set_flags(row, flags)
        self.s.add(row)
        self._return_keys[key] = row
        self.rows["returns"].append(row)

    def _replace_unparsed(self, prev, value, table: str) -> bool:
        """Same key stated twice and only the new copy parses: drop the earlier,
        unparseable copy so the parseable one is stored. True if replaced."""
        if prev.value is not None or value is None:
            return False
        if sa_inspect(prev).pending:
            self.s.expunge(prev)
        else:
            self.s.delete(prev)
            self.s.flush()
        self.rows[table].remove(prev)
        return True

    def _merge_duplicate(self, prev, value: Decimal | None, sv: SourcedValue, differ) -> None:
        """The document states the same key twice (e.g. a KPI tile and the monthly
        table). Equal values: keep one row. Different values: keep the first row and
        flag it, since the document contradicts itself."""
        if value is None or prev.value is None:
            return
        if differ(prev.value, value):
            add_flag(prev, "unparseable_value", {
                "raw": f"document states two values: {prev.source_snippet or prev.value} / {sv.source_snippet or sv.value}",
                "field": "value"})

    def _write_fact(self, fund: Fund, alias_id: int | None, name: str, sv: SourcedValue, as_of: date | None,
                    as_of_period: Period | None, base_flags: dict) -> None:
        flags: dict[str, dict] = dict(base_flags)
        value, value_text, unit = self._normalize_fact(name, sv, fund.currency)
        if value is None and value_text is None:
            flags["unparseable_value"] = {"raw": None if sv.value is None else str(sv.value), "field": "value"}
        elif value is None and name in NUMERIC_METRICS:
            flags["unparseable_value"] = {"raw": value_text, "field": "value"}
        if as_of is not None and as_of_period is not None:
            fut = check_future(as_of_period.start if as_of_period.precision != DAY else as_of, as_of, self.today)
            if fut:
                flags["future_date"] = fut
        verified = self._common_flags(flags, sv, _fact_kind(unit), value)
        key = (fund.id, name, as_of)
        prev = self._fact_keys.get(key)
        if prev is not None and not (prev.value_text is None and self._replace_unparsed(prev, value, "fund_facts")):
            self._merge_duplicate(prev, value, sv, lambda a, b: a != b)
            return
        row = FundFact(
            fund_id=fund.id, document_id=self.doc.id, extraction_id=self.extraction_id, fund_alias_id=alias_id,
            name=name, value=value, value_text=value_text, unit=unit, as_of_date=as_of, page=sv.page,
            source_snippet=sv.source_snippet, confidence=sv.confidence, snippet_verified=verified,
        )
        set_flags(row, flags)
        self.s.add(row)
        self._fact_keys[key] = row
        self.rows["fund_facts"].append(row)

    def _normalize_fact(self, name: str, sv: SourcedValue, fund_currency: str | None
                        ) -> tuple[Decimal | None, str | None, str | None]:
        """(value, value_text, unit). unit: "fraction" for percentages (value is a
        fraction), a currency code for money, "ratio"/"x"/"years", or None."""
        raw = sv.value
        if raw is None:
            return None, None, None
        raw_s = str(raw).strip()
        is_percent_name = name.startswith(PERCENT_PREFIXES)
        if isinstance(raw, str) and has_percent_unit(raw_s):
            p = parse_percent(raw_s)
            return (p.value, None, "fraction") if p.ok else (None, raw_s, None)
        if name in MONEY_METRICS or (isinstance(raw, str) and has_money_marker(raw_s)):
            a = parse_amount(raw)
            if a.ok:
                cur = a.currency or parse_currency(sv.source_snippet) or fund_currency
                return a.value, None, cur
            return None, raw_s, None
        n = parse_number(raw)
        if n.ok:
            if is_percent_name and n.unit is None:
                p = parse_percent(raw, self.text.unit_context(sv))
                return (p.value, None, "fraction") if p.ok else (None, raw_s, None)
            if n.unit is None and name.startswith(RATIO_PREFIXES):
                return n.value, None, "ratio"
            return n.value, None, n.unit
        return None, raw_s, None

    # ---- statements -----------------------------------------------------------------------
    def write_statement(self, st: StatementExtraction) -> None:
        flags: dict[str, dict] = {}
        unparseable: list[tuple[str, str | None]] = []
        name = _raw_str(st.account_name)
        if not name:
            name = f"Unnamed account ({self.doc.name})"
            unparseable.append(("account_name", None))
        amounts: dict[str, Decimal | None] = {}
        amount_currency = None
        for f in STATEMENT_MONEY_FIELDS:
            sv = getattr(st, f)
            if sv is None or sv.value is None or str(sv.value).strip() == "":
                amounts[f] = None
                continue
            a = parse_amount(sv.value)
            if not a.ok:
                unparseable.append((f, str(sv.value)))
                amounts[f] = None
                continue
            amount_currency = amount_currency or a.currency
            amounts[f] = abs(a.value) if f in ("withdrawals", "fees") else a.value
        currency = (parse_currency(_raw_str(st.currency)) or (parse_currency(st.currency.source_snippet)
                                                               if st.currency else None) or amount_currency)
        res = resolve_account(self.s, name, masked=_raw_str(st.account_number_masked),
                              custodian=_raw_str(st.custodian_or_manager), currency=currency,
                              document_id=self.doc.id)
        self.account_ids.add(res.entity_id)
        fund_id = fund_alias_id = None
        fund_name = _raw_str(st.fund_name)
        if fund_name:
            fres = resolve_fund(self.s, fund_name, document_id=self.doc.id)
            fund_id, fund_alias_id = fres.entity_id, fres.alias_id
            self.fund_ids.add(fund_id)
            fill_fund_details(self.s.get(Fund, fund_id), manager=None, strategy=None, currency=currency)

        end_p = parse_period(_raw_str(st.period_end), prefer="end")
        period_end = end_p.end if end_p is not None else None  # day as stated; month/quarter/year -> its last day
        start_p = parse_period(_raw_str(st.period_start), prefer="start") if st.period_start else None
        if start_p is None and end_p is not None and st.period_end is not None:
            # "01/01/2026 to 01/31/2026" given as the period end: its first part is the start
            rng = parse_period(_raw_str(st.period_end), prefer="start")
            start_p = rng if rng is not None and rng.start < end_p.end and rng.precision == DAY else None
        period_start = start_p.start if start_p else None
        if st.period_start is not None and _raw_str(st.period_start) and start_p is None:
            unparseable.append(("period_start", _raw_str(st.period_start)))
        if period_end is None:
            unparseable.append(("period_end", _raw_str(st.period_end)))
            period_end = self.today
        if period_start is not None and period_start > period_end:
            unparseable.append(("period_start", _raw_str(st.period_start)))
            period_start = None
        fut = check_future(end_p.start if end_p is not None and end_p.precision != DAY else period_end,
                           period_end, self.today)
        if fut:
            flags["future_date"] = fut

        provenance: dict[str, dict] = {}
        not_found: list[str] = []
        value_missing: list[str] = []
        low: list[tuple[str, float]] = []
        for f in STATEMENT_FIELDS:
            sv = getattr(st, f)
            if sv is None:
                continue
            verified = self.text.verify(sv.source_snippet, sv.page)
            if (verified and f in STATEMENT_MONEY_FIELDS
                    and value_in_snippet(sv.source_snippet, "money", amounts[f], _raw_str(sv)) is False
                    and not (f == "fees" and snippet_numbers_sum_to(sv.source_snippet, amounts[f]))):
                verified = False
                value_missing.append(f)
            provenance[f] = {"page": sv.page, "source_snippet": sv.source_snippet, "confidence": sv.confidence,
                             "snippet_verified": verified, "raw": None if sv.value is None else str(sv.value)}
            if verified is False:
                not_found.append(f)
            if check_confidence(sv.confidence, self.threshold):
                low.append((f, sv.confidence))
        if not_found:
            first = getattr(st, not_found[0])
            flags["snippet_not_found"] = {"snippet": first.source_snippet, "fields": not_found}
            if value_missing:
                flags["snippet_not_found"]["value_missing"] = True
        if low:
            flags["low_confidence"] = {"confidence": min(c for _, c in low), "threshold": self.threshold,
                                       "fields": [f for f, _ in low]}
        if unparseable:
            flags["unparseable_value"] = {"raw": " | ".join(r or "" for _, r in unparseable),
                                          "field": ", ".join(f for f, _ in unparseable)}
        rf = check_rollforward(
            opening=amounts["opening_balance"], contributions=amounts["contributions"],
            withdrawals=amounts["withdrawals"], pnl=amounts["pnl"], fees=amounts["fees"],
            closing=amounts["closing_balance"], currency=currency,
            unparseable_flow=any(f in STATEMENT_MONEY_FIELDS for f, _ in unparseable),
        )
        if rf:
            flags["rollforward_mismatch"] = rf

        key = (res.entity_id, fund_id, period_end)
        if key in self._statement_keys:
            prev = self._statement_keys[key]
            add_flag(prev, "unparseable_value", {"raw": "document states this statement twice", "field": "statement"})
            return
        row = Statement(
            account_id=res.entity_id, fund_id=fund_id, document_id=self.doc.id, extraction_id=self.extraction_id,
            fund_alias_id=fund_alias_id, account_alias_id=res.alias_id, period_start=period_start,
            period_end=period_end, currency=currency, provenance=provenance, **amounts,
        )
        set_flags(row, flags)
        self.s.add(row)
        self.s.flush()
        self._statement_keys[key] = row
        self.rows["statements"].append(row)
        for line_no, h in enumerate(st.holdings):
            self._write_holding(row, line_no, h)

    def _write_holding(self, statement: Statement, line_no: int, h) -> None:
        flags: dict[str, dict] = {}
        bad: list[tuple[str, str | None]] = []
        name = _raw_str(h.name)
        if not name:
            name = f"Holding {line_no + 1}"
            bad.append(("name", None))
        market_value = quantity = weight = None
        if h.market_value is not None and _raw_str(h.market_value):
            a = parse_amount(h.market_value.value)
            market_value = a.value if a.ok else None
            if not a.ok:
                bad.append(("market_value", _raw_str(h.market_value)))
        if h.quantity is not None and _raw_str(h.quantity):
            n = parse_number(h.quantity.value)
            quantity = n.value if n.ok else None
            if not n.ok:
                bad.append(("quantity", _raw_str(h.quantity)))
        if h.weight is not None and _raw_str(h.weight):
            p = parse_percent(h.weight.value, self.text.unit_context(h.weight))
            weight = p.value if p.ok else None
            if not p.ok:
                bad.append(("weight", _raw_str(h.weight)))
        if bad:
            flags["unparseable_value"] = {"raw": " | ".join(r or "" for _, r in bad),
                                          "field": ", ".join(f for f, _ in bad)}
        # One provenance per holding row. Pick the field snippet that is found in the
        # document and quotes the most of the row's numbers (BUG-Q2-06: the market
        # value's snippet often stops before the weight); ties keep the order market
        # value, name, weight, quantity, identifier.
        main = self._holding_snippet(h, (("money", market_value), ("percent", weight), ("number", quantity)))
        present = [sv for sv in (h.name, h.identifier, h.quantity, h.market_value, h.weight) if sv is not None]
        confidence = min((sv.confidence for sv in present), default=None)
        verified = self.text.verify(main.source_snippet, main.page)
        if verified is False:
            flags["snippet_not_found"] = {"snippet": main.source_snippet}
        elif verified:
            missing = [f for f, k, v in (("market_value", "money", market_value), ("weight", "percent", weight),
                                         ("quantity", "number", quantity))
                       if value_in_snippet(main.source_snippet, k, v) is False]
            if missing:
                verified = False
                flags["snippet_not_found"] = {"snippet": main.source_snippet, "fields": missing, "value_missing": True}
        low = check_confidence(confidence, self.threshold)
        if low:
            flags["low_confidence"] = low
        row = StatementHolding(
            statement_id=statement.id, line_no=line_no, name=display_name(name),
            identifier=_raw_str(h.identifier), quantity=quantity, market_value=market_value, weight=weight,
            page=main.page, source_snippet=main.source_snippet, confidence=confidence, snippet_verified=verified,
        )
        set_flags(row, flags)
        self.s.add(row)
        self.rows["statement_holdings"].append(row)

    def _holding_snippet(self, h, values) -> SourcedValue:
        cands = [sv for sv in (h.market_value, h.name, h.weight, h.quantity, h.identifier)
                 if sv is not None and sv.source_snippet]
        if not cands:
            return h.market_value or h.name
        present = [(k, v) for k, v in values if v is not None]

        def rank(sv: SourcedValue) -> tuple[bool, int]:
            found = sum(bool(value_in_snippet(sv.source_snippet, k, v)) for k, v in present)
            return (self.text.verify(sv.source_snippet, sv.page) is not False, found)

        return max(cands, key=rank)  # max keeps the first of equal ranks

    # ---- cross-row checks --------------------------------------------------------------------
    def check_ytd(self) -> None:
        """YTD rows vs the compounded monthly returns of January..that month (same fund,
        same net/gross). Months come from this document first, then from other visible
        documents (ok rows first, then higher confidence). An incomplete run of months is
        not checked."""
        self.s.flush()
        mine = {(r.fund_id, r.net_gross, r.period_end): r for r in self.rows["returns"]
                if r.return_type == "MTD" and r.value is not None}
        for y in self.rows["returns"]:
            if y.return_type != "YTD" or y.value is None:
                continue
            ends = [month_end(date(y.period_end.year, m, 1)) for m in range(1, y.period_end.month + 1)]
            months: list[MonthValue] = []
            missing = []
            for e in ends:
                r = mine.get((y.fund_id, y.net_gross, e))
                if r is not None:
                    months.append(MonthValue(e, r.value, r.id))
                else:
                    missing.append(e)
            if missing:
                found = self._other_months(y.fund_id, y.net_gross, missing)
                if len(found) < len(missing):
                    continue
                months = sorted(months + found, key=lambda m: m.period_end)
            detail = check_ytd(y.value, months, y.period_end.year)
            if detail:
                add_flag(y, "ytd_mismatch", detail)

    def _other_months(self, fund_id: int, net_gross: str, ends: list[date]) -> list[MonthValue]:
        rows = self.s.execute(
            select(Return).join(Document, Document.id == Return.document_id)
            .where(Document.is_latest, Document.status != "removed", Document.id != self.doc.id,
                   Return.fund_id == fund_id, Return.net_gross == net_gross, Return.return_type == "MTD",
                   Return.period_end.in_(ends), Return.value.is_not(None))
            .order_by(Return.id)
        ).scalars()
        best: dict[date, Return] = {}
        for r in rows:
            cur = best.get(r.period_end)
            rank = (r.status == "ok", r.confidence or 0.0)
            if cur is None or rank > (cur.status == "ok", cur.confidence or 0.0):
                best[r.period_end] = r
        return [MonthValue(e, best[e].value, best[e].id) for e in ends if e in best]


# ---- keys, cleanup, status ----------------------------------------------------------------------


def _file_keys(session: Session, drive_file_id: str) -> dict[str, set[tuple]]:
    """Natural keys of every value row of this file (all versions)."""
    doc_ids = select(Document.id).where(Document.drive_file_id == drive_file_id)
    return {
        "returns": {tuple(r) for r in session.execute(
            select(Return.fund_id, Return.period_end, Return.return_type, Return.net_gross)
            .where(Return.document_id.in_(doc_ids))).all()},
        "fund_facts": {tuple(r) for r in session.execute(
            select(FundFact.fund_id, FundFact.name, FundFact.as_of_date)
            .where(FundFact.document_id.in_(doc_ids))).all()},
        "statements": {tuple(r) for r in session.execute(
            select(Statement.account_id, Statement.fund_id, Statement.period_end)
            .where(Statement.document_id.in_(doc_ids))).all()},
    }


def delete_document_rows(session: Session, document_id: int) -> None:
    for model in (Return, FundFact, Statement):  # holdings cascade with statements
        session.execute(delete(model).where(model.document_id == document_id))
    session.expire_all()


_NEEDS_REVIEW_SQL = text("""
SELECT EXISTS (SELECT 1 FROM returns WHERE document_id = :d AND status <> 'ok')
    OR EXISTS (SELECT 1 FROM fund_facts WHERE document_id = :d AND status <> 'ok')
    OR EXISTS (SELECT 1 FROM statements WHERE document_id = :d AND status <> 'ok')
    OR EXISTS (SELECT 1 FROM statement_holdings h JOIN statements s ON s.id = h.statement_id
               WHERE s.document_id = :d AND h.status <> 'ok')
    OR EXISTS (SELECT 1 FROM fund_aliases a
               WHERE a.match_method = 'fuzzy' AND NOT a.confirmed
                 AND (a.source_document_id = :d
                      OR a.id IN (SELECT fund_alias_id FROM returns WHERE document_id = :d)
                      OR a.id IN (SELECT fund_alias_id FROM fund_facts WHERE document_id = :d)
                      OR a.id IN (SELECT fund_alias_id FROM statements WHERE document_id = :d)))
    OR EXISTS (SELECT 1 FROM account_aliases a
               WHERE a.match_method = 'fuzzy' AND NOT a.confirmed
                 AND (a.source_document_id = :d
                      OR a.id IN (SELECT account_alias_id FROM statements WHERE document_id = :d)))
""")


def document_status(session: Session, document_id: int) -> str:
    """'needs_review' if any of the document's rows, or an alias they use (or that it
    introduced) awaits review; otherwise 'done'. T6 calls this after a review action."""
    session.flush()
    return "needs_review" if session.execute(_NEEDS_REVIEW_SQL, {"d": document_id}).scalar() else "done"


def refresh_document_statuses(session: Session, document_ids: set[int]) -> None:
    """Re-derive done/needs_review for other processed documents.

    Deadlock-free under the postprocess lock: only `done`/`needs_review` rows are
    locked, and a document whose own job holds its row lock is `processing` (that job
    computes its own status after this transaction commits). The remaining lock
    holders (heartbeat, sync) never wait for the postprocess lock."""
    if not document_ids:
        return
    docs = session.execute(
        select(Document).where(Document.id.in_(sorted(document_ids)), Document.status.in_(("done", "needs_review")))
        .order_by(Document.id).with_for_update()
    ).scalars()
    for d in docs:
        new = document_status(session, d.id)
        if d.status != new:
            d.status = new


# ---- entry point ------------------------------------------------------------------------------------


def process_extraction(session: Session, document_id: int, extraction: Extraction, *, extraction_id: int,
                       today: date | None = None) -> str:
    settings = get_settings()
    lock_postprocess(session)
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"document {document_id} not found")
    old_keys = _file_keys(session, doc.drive_file_id)
    delete_document_rows(session, doc.id)
    doc = session.get(Document, document_id)

    p = _Processor(session, doc, extraction, extraction_id, today or _today(), settings.review_confidence_threshold)
    for f in extraction.funds:
        p.write_fund(f)
    for st in extraction.statements:
        p.write_statement(st)
    session.flush()
    p.check_ytd()
    session.flush()
    reapply_decisions(session, doc.drive_file_id, p.rows)

    new_keys = {
        "returns": {conflicts_mod.return_key(r) for r in p.rows["returns"]},
        "fund_facts": {conflicts_mod.fact_key(f) for f in p.rows["fund_facts"]},
        "statements": {conflicts_mod.statement_key(s) for s in p.rows["statements"]},
    }
    keys = {t: old_keys[t] | new_keys[t] for t in new_keys}
    changed_docs = conflicts_mod.refresh_conflicts(session, keys)
    session.flush()
    # Canonical names of every fund/account this file's rows used, before and after.
    fund_ids = {k[0] for k in keys["returns"]} | {k[0] for k in keys["fund_facts"]} | \
        {k[1] for k in keys["statements"] if k[1] is not None}
    account_ids = {k[0] for k in keys["statements"]} | p.account_ids
    fund_ids |= p.fund_ids
    changed_docs |= canonical.recompute_entities(session, fund_ids, account_ids).document_ids
    refresh_document_statuses(session, changed_docs - {doc.id})

    _prepare_search(session, doc, extraction, settings.embedding_model)

    status = document_status(session, doc.id)
    log.info("values written", extra={
        "event": "postprocess_done", "document_id": doc.id, "status": status,
        "returns": len(p.rows["returns"]), "fund_facts": len(p.rows["fund_facts"]),
        "statements": len(p.rows["statements"]), "holdings": len(p.rows["statement_holdings"]),
        "flagged": sum(1 for rows in p.rows.values() for r in rows if r.status != "ok"),
        "other_documents_changed": len(changed_docs - {doc.id}),
    })
    return status


def _prepare_search(session: Session, doc: Document, extraction: Extraction, model_name: str) -> None:
    """Chunks in a savepoint; any failure only leaves search_indexed = false (FR-7)."""
    from app.search.embedding import get_embedder
    from app.search.indexing import prepare_chunks

    try:
        current_model = get_embedder(model_name).model_name
    except Exception:  # noqa: BLE001
        current_model = model_name
    pages = [pg.text for pg in sorted(extraction.pages, key=lambda pg: pg.page)]
    try:
        with session.begin_nested():
            indexed = prepare_chunks(session, doc, pages, current_model)
    except Exception as e:  # noqa: BLE001 - FR-7: never fails the structured extraction
        log.warning("chunking failed; will retry", extra={"event": "chunking_error", "document_id": doc.id,
                                                          "error_type": type(e).__name__})
        indexed = False
    doc.search_indexed = indexed


__all__ = ["process_extraction", "document_status", "refresh_document_statuses", "lock_postprocess",
           "POSTPROCESS_LOCK_KEY", "metric_name"]

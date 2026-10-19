"""Single values: loading value rows with their document/extraction, labels, value
kinds (C6), provenance, reason messages and `ValueSnapshot`s. Shared by
`/api/sources` and `/api/review`."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.api.listing import num, utc
from app.normalize.locate import locate_value
from app.schemas.common import REVIEW_REASON_LABELS
from app.schemas.data import STATEMENT_PROVENANCE_KEYS
from app.schemas.review import REVIEW_DETAIL_MODELS

log = logging.getLogger("app.api")

VALUE_TABLES = ("returns", "fund_facts", "statements", "statement_holdings")
STATEMENT_MONEY_FIELDS = ("opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance")
STATEMENT_DATE_FIELDS = ("period_start", "period_end")
STATEMENT_TEXT_FIELDS = ("account_name", "account_number_masked", "custodian", "fund_name", "currency")

_DOC_COLS = """
       d.name AS document_name, d.document_type, d.mime_type, d.source AS document_source,
       d.web_view_link, d.version, d.processed_at, d.drive_file_id, d.status AS document_status"""
_EXT_COLS = """
       e.id AS ext_id, e.model AS extraction_model, e.prompt_version, e.source AS extraction_source,
       e.created_at AS extraction_created_at"""
_VISIBLE = "d.is_latest AND d.status <> 'removed'"

_ROW_SQL = {
    "returns": f"""
SELECT r.id, r.fund_id, r.document_id, r.extraction_id, r.fund_alias_id, r.period_end, r.period_label,
       r.return_type, r.net_gross, r.value, r.page, r.source_snippet, r.confidence, r.snippet_verified,
       r.status, r.review_reasons, r.review_detail, r.created_at,
       f.canonical_name AS fund_name, f.manager, f.strategy, f.currency AS fund_currency,
       NULL::bigint AS account_id, NULL::text AS account_name, {_DOC_COLS}, {_EXT_COLS}
FROM returns r JOIN funds f ON f.id = r.fund_id JOIN documents d ON d.id = r.document_id
LEFT JOIN extractions e ON e.id = r.extraction_id
WHERE r.id IN :ids AND {_VISIBLE}""",
    "fund_facts": f"""
SELECT ff.id, ff.fund_id, ff.document_id, ff.extraction_id, ff.fund_alias_id, ff.name AS metric, ff.value,
       ff.value_text, ff.unit, ff.as_of_date, ff.page, ff.source_snippet, ff.confidence, ff.snippet_verified,
       ff.status, ff.review_reasons, ff.review_detail, ff.created_at,
       f.canonical_name AS fund_name, f.manager, f.strategy, f.currency AS fund_currency,
       NULL::bigint AS account_id, NULL::text AS account_name, {_DOC_COLS}, {_EXT_COLS}
FROM fund_facts ff JOIN funds f ON f.id = ff.fund_id JOIN documents d ON d.id = ff.document_id
LEFT JOIN extractions e ON e.id = ff.extraction_id
WHERE ff.id IN :ids AND {_VISIBLE}""",
    "statements": f"""
SELECT s.id, s.account_id, s.fund_id, s.document_id, s.extraction_id, s.fund_alias_id, s.account_alias_id,
       s.period_start, s.period_end, s.opening_balance, s.contributions, s.withdrawals, s.pnl, s.fees,
       s.closing_balance, s.currency, s.provenance, s.status, s.review_reasons, s.review_detail, s.created_at,
       ac.canonical_name AS account_name, ac.account_number_masked, ac.custodian,
       f.canonical_name AS fund_name, f.manager, {_DOC_COLS}, {_EXT_COLS}
FROM statements s JOIN accounts ac ON ac.id = s.account_id LEFT JOIN funds f ON f.id = s.fund_id
JOIN documents d ON d.id = s.document_id LEFT JOIN extractions e ON e.id = s.extraction_id
WHERE s.id IN :ids AND {_VISIBLE}""",
    "statement_holdings": f"""
SELECT h.id, h.statement_id, h.line_no, h.name AS holding_name, h.identifier, h.quantity, h.market_value,
       h.weight, h.page, h.source_snippet, h.confidence, h.snippet_verified, h.status, h.review_reasons,
       h.review_detail, h.created_at,
       s.account_id, s.fund_id, s.document_id, s.extraction_id, s.period_end, s.currency,
       ac.canonical_name AS account_name, ac.account_number_masked, ac.custodian,
       f.canonical_name AS fund_name, {_DOC_COLS}, {_EXT_COLS}
FROM statement_holdings h JOIN statements s ON s.id = h.statement_id JOIN accounts ac ON ac.id = s.account_id
LEFT JOIN funds f ON f.id = s.fund_id JOIN documents d ON d.id = s.document_id
LEFT JOIN extractions e ON e.id = s.extraction_id
WHERE h.id IN :ids AND {_VISIBLE}""",
}
_ROW_STMTS = {t: text(sql).bindparams(bindparam("ids", expanding=True)) for t, sql in _ROW_SQL.items()}


def load_rows(session: Session, table: str, ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Visible value rows by id (rows of removed/superseded documents are absent)."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    return {r["id"]: dict(r) for r in session.execute(_ROW_STMTS[table], {"ids": ids}).mappings()}


# ---- formatting -------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_label(d: date | None) -> str | None:
    return f"{_MONTHS[d.month - 1]} {d.year}" if d else None


def fmt_pct(fraction: float | Decimal | None) -> str:
    if fraction is None:
        return "n/a"
    v = round(float(fraction) * 100, 2)
    if v == 0:
        return "0.00%"
    return f"{v:+.2f}%"


def fmt_money(x: float | Decimal | None) -> str:
    return "n/a" if x is None else f"{float(x):,.2f}"


def pct_of(fraction: Any) -> float | None:
    """value_pct = value * 100, computed in Decimal so 0.0284 -> 2.84 exactly."""
    if fraction is None:
        return None
    return float(Decimal(str(fraction)) * 100)


def _join(*parts: str | None) -> str:
    return " · ".join(p for p in parts if p)


def metric_label(metric: str) -> str:
    special = {"aum": "AUM", "nav": "NAV", "vol": "Volatility", "max_drawdown": "Max drawdown", "sharpe": "Sharpe"}
    return special.get(metric, metric.replace("_", " ").capitalize())


def is_currency_unit(unit: str | None) -> bool:
    return bool(unit) and len(unit) == 3 and unit.isalpha() and unit.isupper()


def row_label(table: str, row: dict[str, Any]) -> str:
    if table == "returns":
        return _join(row["fund_name"], month_label(row["period_end"]), f"{row['return_type']} {row['net_gross']}")
    if table == "fund_facts":
        return _join(row["fund_name"], metric_label(row["metric"]), month_label(row["as_of_date"]))
    if table == "statements":
        return _join(row["account_name"], row["account_number_masked"], month_label(row["period_end"]))
    return _join(row["account_name"], row["holding_name"], month_label(row["period_end"]))


FIELD_LABELS = {
    "closing_balance": "Closing balance", "opening_balance": "Opening balance", "contributions": "Contributions",
    "withdrawals": "Withdrawals", "pnl": "P&L", "fees": "Fees", "period_start": "Period start",
    "period_end": "Period end", "account_name": "Account", "account_number_masked": "Account number",
    "custodian": "Custodian / manager", "fund_name": "Fund", "currency": "Currency",
    "market_value": "Market value", "weight": "Weight", "quantity": "Quantity", "holding_name": "Holding",
    "identifier": "Identifier",
}


def field_label(table: str, row: dict[str, Any], field: str) -> str:
    if table == "returns":
        return "Return"
    if table == "fund_facts":
        return metric_label(row["metric"])
    return FIELD_LABELS.get(field, field)


def default_field(table: str) -> str:
    return {"returns": "value", "fund_facts": "value", "statements": "closing_balance",
            "statement_holdings": "market_value"}[table]


def field_value(table: str, row: dict[str, Any], field: str) -> dict[str, Any]:
    """{value, value_pct, value_kind, unit, currency} for one field (C6)."""
    out = {"value": None, "value_pct": None, "value_kind": "number", "unit": None, "currency": None}
    if table == "returns":
        out.update(value=num(row["value"]), value_pct=pct_of(row["value"]), value_kind="percent")
    elif table == "fund_facts":
        unit = row["unit"]
        out["unit"] = unit
        if row["value"] is None and row["value_text"] is not None:
            out.update(value=row["value_text"], value_kind="text")
        elif unit == "fraction":
            out.update(value=num(row["value"]), value_pct=pct_of(row["value"]), value_kind="percent")
        elif is_currency_unit(unit):
            out.update(value=num(row["value"]), value_kind="money", currency=unit)
        else:
            out.update(value=num(row["value"]))
    elif table == "statements":
        if field in STATEMENT_MONEY_FIELDS:
            out.update(value=num(row[field]), value_kind="money", currency=row["currency"])
        elif field in STATEMENT_DATE_FIELDS:
            d = row[field]
            out.update(value=d.isoformat() if d else None, value_kind="date")
        else:
            out.update(value=row[field], value_kind="text")
    else:
        if field == "market_value":
            out.update(value=num(row["market_value"]), value_kind="money", currency=row["currency"])
        elif field == "weight":
            out.update(value=num(row["weight"]), value_pct=pct_of(row["weight"]), value_kind="percent")
        elif field == "quantity":
            out.update(value=num(row["quantity"]))
        else:
            out.update(value=row[field], value_kind="text")
    return out


def provenance(table: str, row: dict[str, Any], field: str) -> dict[str, Any]:
    if table == "statements":
        entry = (row.get("provenance") or {}).get(STATEMENT_PROVENANCE_KEYS.get(field, field)) or {}
        verified = entry.get("snippet_verified")
        return {
            "page": entry.get("page"),
            "source_snippet": entry.get("source_snippet"),
            "confidence": entry.get("confidence"),
            "snippet_verified": verified if isinstance(verified, bool) else None,
        }
    return {"page": row["page"], "source_snippet": row["source_snippet"], "confidence": row["confidence"],
            "snippet_verified": row.get("snippet_verified")}


def provenance_raw(table: str, row: dict[str, Any], field: str) -> str | None:
    """The value string as extracted (statements keep it per field), for the highlight."""
    if table != "statements":
        return None
    entry = (row.get("provenance") or {}).get(STATEMENT_PROVENANCE_KEYS.get(field, field)) or {}
    raw = entry.get("raw")
    return raw if isinstance(raw, str) else None


def document_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": row["document_id"], "document_name": row["document_name"],
        "document_type": row["document_type"], "mime_type": row["mime_type"], "source": row["document_source"],
        "web_view_link": row["web_view_link"], "version": row["version"], "processed_at": utc(row["processed_at"]),
    }


def extraction_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {"extraction_id": row["ext_id"], "model": row["extraction_model"], "prompt_version": row["prompt_version"],
            "source": row["extraction_source"], "created_at": utc(row["extraction_created_at"])}


def snapshot(table: str, row: dict[str, Any], field: str | None = None) -> dict[str, Any]:
    """`ValueSnapshot` for one field of a row."""
    field = field or default_field(table)
    return {
        "table": table, "id": row["id"], "field": field, **field_value(table, row, field),
        "status": row["status"], "review_reasons": list(row["review_reasons"] or []),
        "provenance": provenance(table, row, field),
        "document_id": row["document_id"], "document_name": row["document_name"],
        "document_type": row["document_type"], "web_view_link": row["web_view_link"],
        "extraction_model": row["extraction_model"],
    }


# ---- statements: flagged cells ------------------------------------------------------------

_PROV_TO_FIELD = {v: k for k, v in STATEMENT_PROVENANCE_KEYS.items()}
STATEMENT_FIELD_ORDER = tuple(STATEMENT_PROVENANCE_KEYS)


def _as_fields(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return [_PROV_TO_FIELD.get(str(f), str(f)) for f in raw]


def statement_flagged_fields(reasons: Sequence[str], detail: dict[str, Any] | None) -> list[str]:
    """Cells to highlight on a statement row (StatementItem.flagged_fields)."""
    detail = detail or {}
    out: set[str] = set()
    for reason in reasons or []:
        d = detail.get(reason) or {}
        if reason == "rollforward_mismatch":
            out.add("closing_balance")
        elif reason == "future_date":
            out.add("period_end")
        elif reason in ("low_confidence", "snippet_not_found"):
            out.update(_as_fields(d.get("fields")))
        elif reason == "unparseable_value":
            out.update(_as_fields(d.get("field")))
        elif reason == "conflict":
            out.update(STATEMENT_MONEY_FIELDS)
    return [f for f in STATEMENT_FIELD_ORDER if f in out]


# ---- reasons -----------------------------------------------------------------------------


def clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """`review_detail` entries that match the documented shapes (others are dropped and
    logged, so a stray key from the pipeline can never break a response)."""
    out: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        model = REVIEW_DETAIL_MODELS.get(key)
        if model is None:
            log.warning("unknown review_detail key dropped", extra={"event": "review_detail_dropped", "key": key})
            continue
        try:
            model.model_validate(value)
        except Exception:  # noqa: BLE001 - pydantic ValidationError and friends
            log.warning("malformed review_detail entry dropped",
                        extra={"event": "review_detail_dropped", "key": key})
            continue
        out[key] = value
    return out


def _fields_suffix(d: dict[str, Any]) -> str:
    fields = _as_fields(d.get("fields"))
    return f" (fields: {', '.join(FIELD_LABELS.get(f, f).lower() for f in fields)})" if fields else ""


def reason_message(code: str, d: dict[str, Any], *, title: str = "", conflict_values: Sequence[str] = ()) -> str:
    try:
        if code == "low_confidence":
            return (f"Extraction confidence {float(d['confidence']):.2f} is below the review threshold "
                    f"{float(d['threshold']):.2f}{_fields_suffix(d)}.")
        if code == "ytd_mismatch":
            return (f"Monthly returns for {d['year']} compound to {fmt_pct(d['computed'])} but the stated YTD is "
                    f"{fmt_pct(d['stated'])}.")
        if code == "rollforward_mismatch":
            cur = f" {d['currency']}" if d.get("currency") else ""
            return (f"Opening {fmt_money(d['opening_balance'])} + contributions {fmt_money(d['contributions'])} - "
                    f"withdrawals {fmt_money(d['withdrawals'])} + P&L {fmt_money(d['pnl'])} - fees "
                    f"{fmt_money(d['fees'])} = {fmt_money(d['expected_closing'])}, but the stated closing balance is "
                    f"{fmt_money(d['stated_closing'])} (difference {fmt_money(abs(float(d['difference'])))}{cur}).")
        if code == "alias_match":
            score = f" (score {float(d['match_score']):.1f})" if d.get("match_score") is not None else ""
            return f"“{d['alias']}” was matched to {title}{score}."
        if code == "conflict":
            n = len(conflict_values) or len(d.get("conflicting_ids", [])) + 1
            vals = ""
            if len(conflict_values) > 1:
                vals = ": " + ", ".join(conflict_values[:-1]) + " and " + conflict_values[-1]
            return f"{n} documents disagree on {title}{vals}."
        if code == "snippet_not_found":
            if d.get("value_missing"):
                return f"The value is not written in its source snippet{_fields_suffix(d)}, so it cannot be traced."
            return f"The source snippet was not found in the document text{_fields_suffix(d)}."
        if code == "future_date":
            return f"The date {d['date']} is after today ({d['today']})."
        if code == "out_of_range":
            return (f"The monthly return {fmt_pct(d['value'])} is outside the allowed range "
                    f"{fmt_pct(d['min'])} to {fmt_pct(d['max'])}.")
        if code == "net_gross_unknown":
            label = f" (label: “{d['raw_label']}”)" if d.get("raw_label") else ""
            return f"The document does not say whether this return is net or gross{label}."
        if code == "return_type_unknown":
            label = f" (label: “{d['raw_label']}”)" if d.get("raw_label") else ""
            return f"The return type (MTD, QTD, YTD, ...) could not be determined{label}."
        if code == "unparseable_value":
            what = d.get("field") or "value"
            raw = f" “{d['raw']}”" if d.get("raw") else ""
            return f"The {what}{raw} could not be parsed."
    except (KeyError, TypeError, ValueError):
        pass
    return f"{REVIEW_REASON_LABELS.get(code, code)}."


def reason_infos(reasons: Sequence[str], detail: dict[str, Any] | None, *, title: str = "",
                 conflict_values: Sequence[str] = ()) -> list[dict[str, Any]]:
    detail = detail or {}
    return [{"code": r, "label": REVIEW_REASON_LABELS[r],
             "message": reason_message(r, detail.get(r) or {}, title=title, conflict_values=conflict_values)}
            for r in reasons or [] if r in REVIEW_REASON_LABELS]


def display_value(snap: dict[str, Any]) -> str:
    if snap["value_kind"] == "percent":
        return fmt_pct(snap["value"])
    if snap["value_kind"] == "money":
        return fmt_money(snap["value"]) + (f" {snap['currency']}" if snap.get("currency") else "")
    return "n/a" if snap["value"] is None else str(snap["value"])


# ---- snippet highlight (U1 request 4) -------------------------------------------------------


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def snippet_highlight(snippet: str | None, value_kind: str, value: Any, raw: str | None = None
                      ) -> dict[str, Any] | None:
    """Where the value is written in the snippet: UTF-16 offsets (JavaScript string
    indices) and the matched text, or None when no number in the snippet matches.
    The rules (raw string first, percent/fraction/bps forms, money magnitude, CSV
    delimiters) live in `app.normalize.locate.locate_value`."""
    hit = locate_value(snippet, value_kind, value, raw)
    if hit is None:
        return None
    start, end = hit
    return {"start": _utf16_len(snippet[:start]), "end": _utf16_len(snippet[:end]), "text": snippet[start:end]}

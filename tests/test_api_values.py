"""Unit tests for the pure T6 helpers (no DB): value formatting, snippet highlight,
statement flagged cells, reason messages, CSV cells, conflict grouping, review sorting."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.api import review_queue
from app.api.listing import csv_cell, csv_lines, utc
from app.api.review import _sort_items
from app.api.review_queue import OpenItem, counts, review_count_by_document
from app.api.values import (
    clean_detail,
    fmt_money,
    fmt_pct,
    month_label,
    pct_of,
    reason_infos,
    reason_message,
    snippet_highlight,
    statement_flagged_fields,
)


@pytest.mark.parametrize("snippet,kind,value,text", [
    ("MTD % (net) +2.84%", "percent", 0.0284, "+2.84%"),
    ("Perf. (net) Jan-26 1.92%", "percent", 0.0192, "1.92%"),
    ("Jan (0.36)% Feb 0.21%", "percent", -0.0036, "(0.36)"),
    ("Feb 0.21% Jan (0.36)%", "percent", 0.0021, "0.21%"),
    ("Month (net of fees) +110 bps", "percent", 0.011, "+110 bps"),
    ("Month -125 bps", "percent", -0.0125, "-125 bps"),
    ("NET_RET 0.0071", "percent", 0.0071, "0.0071"),
    ("2026 YTD 0.15%", "percent", 0.0015, "0.15%"),
    ("Closing balance 13,100,000.00", "money", 13100000, "13,100,000.00"),
    ("Withdrawals (1,000,000.00)", "money", 1000000, "(1,000,000.00)"),
    ("Fund AUM $412.6mm", "money", 412600000, "$412.6mm"),
    ("USD 1.18bn", "money", 1180000000, "1.18bn"),
    ("Sharpe (since incept.) 1.84", "number", 1.84, "1.84"),
    ("Kestrel Quant Equity Fund LP Hedge fund 4,250,000 32.4%", "percent", 0.324, "32.4%"),
])
def test_snippet_highlight_finds_the_value(snippet, kind, value, text):
    hl = snippet_highlight(snippet, kind, value)
    assert hl is not None and hl["text"] == text and snippet[hl["start"]:hl["end"]] == text


@pytest.mark.parametrize("snippet,kind,value", [
    ("nothing numeric", "percent", 0.01), (None, "percent", 0.01), ("1.5%", "text", "x"), ("2.84%", "percent", None),
    ("Year 2026 only", "number", 2026.5), ("+2.84%", "percent", -0.0284),
])
def test_snippet_highlight_none(snippet, kind, value):
    assert snippet_highlight(snippet, kind, value) is None


def test_snippet_highlight_offsets_are_utf16():
    s = "\U0001F4C8 Return +2.84%"  # an astral character is 2 UTF-16 code units
    hl = snippet_highlight(s, "percent", 0.0284)
    assert hl == {"start": 10, "end": 16, "text": "+2.84%"}


def test_formatting():
    assert fmt_pct(0.0284) == "+2.84%" and fmt_pct(-0.0036) == "-0.36%" and fmt_pct(0) == "0.00%"
    assert fmt_pct(-0.00001) == "0.00%" and fmt_pct(None) == "n/a"
    assert fmt_money(13080000) == "13,080,000.00" and fmt_money(Decimal("-1.5")) == "-1.50"
    assert month_label(date(2026, 1, 31)) == "Jan 2026" and month_label(None) is None
    assert pct_of(Decimal("0.0284")) == 2.84 and pct_of(0.0015) == 0.15 and pct_of(None) is None


def test_statement_flagged_fields():
    detail = {
        "low_confidence": {"confidence": 0.6, "threshold": 0.7, "fields": ["account_number_masked",
                                                                           "custodian_or_manager"]},
        "snippet_not_found": {"snippet": "x", "fields": ["pnl"]},
        "unparseable_value": {"raw": "a | b", "field": "fees, period_start"},
        "rollforward_mismatch": {},
        "future_date": {"date": "2027-01-31", "today": "2026-10-16"},
    }
    got = statement_flagged_fields(list(detail), detail)
    assert got == ["account_number_masked", "custodian", "period_start", "period_end", "pnl", "fees",
                   "closing_balance"]
    assert statement_flagged_fields([], None) == []
    assert statement_flagged_fields(["rollforward_mismatch"], None) == ["closing_balance"]


def test_reason_messages():
    ytd = {"year": 2026, "stated": 0.0015, "computed": -0.0015076, "difference": 0.003, "tolerance": 0.0005,
           "months": []}
    assert reason_message("ytd_mismatch", ytd) == ("Monthly returns for 2026 compound to -0.15% but the stated YTD "
                                                    "is +0.15%.")
    rf = {"opening_balance": 12400000.0, "contributions": 500000.0, "withdrawals": 0.0, "pnl": 180000.0, "fees": 0.0,
          "expected_closing": 13080000.0, "stated_closing": 13100000.0, "difference": 20000.0, "tolerance": 13.1,
          "currency": "USD"}
    assert reason_message("rollforward_mismatch", rf) == (
        "Opening 12,400,000.00 + contributions 500,000.00 - withdrawals 0.00 + P&L 180,000.00 - fees 0.00 = "
        "13,080,000.00, but the stated closing balance is 13,100,000.00 (difference 20,000.00 USD).")
    assert reason_message("alias_match", {"alias": "A SPC", "match_score": 91.4}, title="A") == \
        "“A SPC” was matched to A (score 91.4)."
    assert reason_message("conflict", {"conflicting_ids": [2]}, title="F · Jan 2026 · MTD net",
                          conflict_values=["+1.92%", "+1.89%"]) == \
        "2 documents disagree on F · Jan 2026 · MTD net: +1.92% and +1.89%."
    assert "below the review threshold 0.70 (fields: account number)" in reason_message(
        "low_confidence", {"confidence": 0.6, "threshold": 0.7, "fields": ["account_number_masked"]})
    assert reason_message("out_of_range", {"value": 0.62, "min": -0.5, "max": 0.5}) == \
        "The monthly return +62.00% is outside the allowed range -50.00% to +50.00%."
    assert reason_message("unparseable_value", {"raw": "n/a", "field": "period"}) == "The period “n/a” could not be parsed."
    assert reason_message("future_date", {"date": "2027-01-31", "today": "2026-10-16"}).startswith("The date 2027")
    # missing detail never breaks: the label is used
    assert reason_message("ytd_mismatch", {}) == "YTD mismatch."
    infos = reason_infos(["net_gross_unknown"], {"net_gross_unknown": {"raw_label": "Perf."}})
    assert infos == [{"code": "net_gross_unknown", "label": "Net/gross unknown",
                      "message": "The document does not say whether this return is net or gross (label: “Perf.”)."}]


def test_clean_detail_drops_unknown_and_malformed():
    d = {"conflict": {"conflicting_ids": [1], "tolerance": 0.0001}, "bogus": {}, "ytd_mismatch": {"year": "x"}}
    assert clean_detail(d) == {"conflict": {"conflicting_ids": [1], "tolerance": 0.0001}}
    assert clean_detail(None) == {}


def test_csv_cells():
    assert csv_cell(None) == "" and csv_cell(True) == "true" and csv_cell(False) == "false"
    assert csv_cell(["a", "b"]) == "a;b" and csv_cell([]) == ""
    assert csv_cell(0.0284) == "0.0284" and csv_cell(2) == "2" and csv_cell(date(2026, 1, 31)) == "2026-01-31"
    assert csv_cell(datetime(2026, 10, 16, 20, 44, 31, 5, tzinfo=timezone(timedelta(hours=2)))) == "2026-10-16T18:44:31Z"
    out = "".join(csv_lines(["a", "b"], [{"a": 'x,"y"', "b": "line\nbreak"}]))
    assert out == 'a,b\r\n"x,""y""","line\nbreak"\r\n'


def test_utc_normalizes():
    assert utc(datetime(2026, 1, 1, 12, 0, 0, 999, tzinfo=timezone(timedelta(hours=-5)))) == \
        datetime(2026, 1, 1, 17, 0, tzinfo=UTC)
    assert utc(None) is None


# ---- conflict grouping ---------------------------------------------------------------------


def _q(table, i, reasons, detail=None, doc=1, created=0):
    return {"item_key": f"{table}:{i}", "target_table": table, "target_id": i, "status": "conflict" if "conflict" in
            reasons else "needs_review", "review_reasons": reasons, "review_detail": detail, "document_id": doc,
            "document_name": f"d{doc}", "fund_id": 1, "account_id": None,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=created)}


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _stmt):
        rows = self.rows

        class R:
            def mappings(self):
                return rows
        return R()


def test_open_items_groups_conflicts_transitively():
    c = lambda ids: {"conflict": {"conflicting_ids": ids, "tolerance": 0.00005}}  # noqa: E731
    rows = [
        _q("returns", 5, ["conflict"], c([9]), doc=1),
        _q("returns", 9, ["conflict", "low_confidence"], c([5, 12]), doc=2),
        _q("returns", 12, ["conflict"], c([9]), doc=3),
        _q("returns", 20, ["conflict"], c([99]), doc=4),  # partner not visible -> plain value item
        _q("fund_facts", 5, ["conflict"], c([6]), doc=5),  # same id, other table: separate group
        _q("fund_facts", 6, ["conflict"], c([5]), doc=6),
        _q("statements", 1, ["rollforward_mismatch"], {"rollforward_mismatch": {}}, doc=7),
        _q("fund_aliases", 3, ["alias_match"], {"alias": "X", "match_score": 90.0, "fund_id": 1}, doc=8),
    ]
    items = review_queue.open_items(_FakeSession(rows))
    by_id = {i.id: i for i in items}
    g = by_id["returns:5"]
    assert g.kind == "conflict" and g.members == [5, 9, 12] and g.reasons == ["low_confidence", "conflict"]
    assert g.document_ids == {1, 2, 3} and g.covers("returns", 12) and not g.covers("fund_facts", 12)
    assert by_id["returns:20"].kind == "value"
    assert by_id["fund_facts:5"].kind == "conflict" and by_id["fund_facts:5"].members == [5, 6]
    assert by_id["fund_aliases:3"].detail == {"alias_match": {"alias": "X", "match_score": 90.0, "fund_id": 1}}
    assert len(items) == 5
    cs = counts(items)
    assert cs["by_kind"] == {"value": 2, "conflict": 2, "alias": 1}
    assert cs["by_reason"]["conflict"] == 3 and list(cs["by_reason"])[0] == "low_confidence"
    assert review_count_by_document(items)[2] == 1


def test_review_sort_nulls_last_and_tiebreak():
    def it(table, i, created, doc_name):
        return OpenItem(id=f"{table}:{i}", kind="value", target_table=table, target_id=i, status="needs_review",
                        reasons=[], detail={}, document_id=None, document_name=doc_name,
                        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=created))
    items = [it("returns", 3, 1, None), it("returns", 1, 1, "b"), it("fund_facts", 2, 2, "a")]
    assert [i.id for i in _sort_items(items, [("document_name", False)])] == ["fund_facts:2", "returns:1", "returns:3"]
    assert [i.id for i in _sort_items(items, [("document_name", True)])] == ["returns:1", "fund_facts:2", "returns:3"]
    assert [i.id for i in _sort_items(items, [("created_at", True)])] == ["fund_facts:2", "returns:1", "returns:3"]

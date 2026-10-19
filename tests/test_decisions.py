"""Unit tests: re-applying review decisions by natural key (FR-13, T3 decision 8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.ingest.decisions import apply_decisions, natural_key
from app.ingest.review_state import set_flags

D = Decimal


def action(name, old=None, new=None, key=None):
    return SimpleNamespace(action=name, old_value=old, new_value=new, natural_key=key or {})


def flagged_return(value="0.0015"):
    r = SimpleNamespace(fund_id=3, period_end=date(2026, 2, 28), return_type="YTD", net_gross="net", value=D(value),
                        value_text=None, status="ok", review_reasons=[], review_detail=None)
    set_flags(r, {"ytd_mismatch": {"x": 1}})
    return r


def test_natural_keys():
    r = flagged_return()
    assert natural_key("returns", r) == {"fund_id": 3, "period_end": "2026-02-28", "return_type": "YTD",
                                         "net_gross": "net"}
    f = SimpleNamespace(fund_id=1, name="aum", as_of_date=None)
    assert natural_key("fund_facts", f) == {"fund_id": 1, "name": "aum", "as_of_date": None}
    st = SimpleNamespace(account_id=2, fund_id=None, period_end=date(2026, 1, 31))
    assert natural_key("statements", st) == {"account_id": 2, "fund_id": None, "period_end": "2026-01-31"}
    h = SimpleNamespace(line_no=4)
    assert natural_key("statement_holdings", h, statement=st) == {**natural_key("statements", st), "line_no": 4}


def test_edit_always_reapplies_even_if_the_document_changed():
    r = flagged_return("0.0099")  # re-extracted value differs from the edit's old value
    assert apply_decisions("returns", r, [action("edit", {"value": 0.0015}, {"value": -0.0015})]) == "resolved"
    assert (r.value, r.status, r.review_reasons, r.review_detail) == (D("-0.0015"), "ok", [], None)


def test_accept_only_when_values_unchanged():
    r = flagged_return()
    assert apply_decisions("returns", r, [action("accept", {"value": 0.0015}, {"value": 0.0015})]) == "resolved"
    assert r.status == "ok"
    r2 = flagged_return("0.0016")
    assert apply_decisions("returns", r2, [action("accept", {"value": 0.0015}, {"value": 0.0015})]) == "unchanged"
    assert r2.status == "needs_review" and r2.review_reasons == ["ytd_mismatch"]


def test_newest_decision_wins_and_edits_still_apply_first():
    r = flagged_return()
    acts = [action("edit", {"value": 0.0015}, {"value": 0.002}), action("accept", {"value": 0.002}, {"value": 0.002})]
    assert apply_decisions("returns", r, acts) == "resolved" and r.value == D("0.002")
    r2 = flagged_return()
    acts = [action("accept", {"value": 0.0015}), action("edit", {"value": 0.0015}, {"value": 0.003})]
    assert apply_decisions("returns", r2, acts) == "resolved" and r2.value == D("0.003")


def test_choose_loser_deleted_again_only_if_unchanged():
    r = flagged_return("0.0189")
    assert apply_decisions("returns", r, [action("choose", {"value": 0.0189}, {"deleted": True})]) == "deleted"
    r2 = flagged_return("0.0190")
    assert apply_decisions("returns", r2, [action("choose", {"value": 0.0189}, {"deleted": True})]) == "unchanged"
    # edit on a conflict deleted the other candidates: always re-applied
    r3 = flagged_return("0.05")
    assert apply_decisions("returns", r3, [action("edit", {"value": 0.0189}, {"deleted": True})]) == "deleted"


def test_statement_field_edits_and_accept():
    st = SimpleNamespace(account_id=1, fund_id=None, period_end=date(2026, 1, 31), period_start=None,
                         closing_balance=D("13100000"), opening_balance=D("12400000"), currency="USD",
                         status="ok", review_reasons=[], review_detail=None)
    set_flags(st, {"rollforward_mismatch": {}})
    acts = [action("edit", {"closing_balance": 13100000.0}, {"value": 13080000.0}, key={"field": "closing_balance"}),
            action("edit", {"period_start": None}, {"period_start": "2026-01-01"})]
    assert apply_decisions("statements", st, acts) == "resolved"
    assert (st.closing_balance, st.period_start, st.status) == (D("13080000"), date(2026, 1, 1), "ok")
    st.review_reasons, st.status = ["rollforward_mismatch"], "needs_review"
    ok = action("accept", {"closing_balance": 13080000.0, "opening_balance": 12400000.0})
    assert apply_decisions("statements", st, [ok]) == "resolved"
    bad = action("accept", {"no_such_field": 1})
    assert apply_decisions("statements", st, [bad]) == "unchanged"


def test_holding_fields_and_unknown_actions():
    h = SimpleNamespace(name="Cash", market_value=D("280000"), weight=D("0.021"), status="ok", review_reasons=[],
                        review_detail=None)
    set_flags(h, {"low_confidence": {"confidence": 0.5, "threshold": 0.7}})
    acts = [action("edit", {"weight": 0.021}, {"weight": 0.022, "holding_name": "Cash (USD)"})]
    assert apply_decisions("statement_holdings", h, acts) == "resolved"
    assert (h.weight, h.name) == (D("0.022"), "Cash (USD)")
    r = flagged_return()
    assert apply_decisions("returns", r, [action("confirm_alias")]) == "unchanged"
    assert apply_decisions("returns", r, []) == "unchanged"
    assert apply_decisions("returns", r, [action("accept", "not a dict")]) == "unchanged"

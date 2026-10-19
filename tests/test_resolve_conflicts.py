"""Fund/account resolution and conflict detection against the DB (FR-6, §14)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.ingest.conflicts import facts_differ, refresh_conflicts, returns_differ, statements_differ
from app.ingest.resolve import resolve_account, resolve_fund
from app.models import Account, AccountAlias, Document, Fund, FundAlias, FundFact, Return, Statement
from app.schemas.review import validate_review_detail

D = Decimal


def doc(s, name, **kw) -> Document:
    d = Document(drive_file_id=name, name=name, mime_type="application/pdf", md5_checksum=f"md5-{name}",
                 status=kw.pop("status", "done"), **kw)
    s.add(d)
    s.flush()
    return d


# ---- fund resolution -------------------------------------------------------------------------


def test_new_then_exact_then_fuzzy(db_session):
    s = db_session
    d1, d2, d3 = doc(s, "a.pdf"), doc(s, "b.pdf"), doc(s, "c.pdf")
    new = resolve_fund(s, "Northwind Global Macro Fund", document_id=d1.id)
    assert new.method == "new"
    fund = s.get(Fund, new.entity_id)
    assert fund.canonical_name == "Northwind Global Macro Fund"
    alias = s.get(FundAlias, new.alias_id)
    assert (alias.match_method, alias.confirmed, alias.source_document_id) == ("new", True, d1.id)

    exact = resolve_fund(s, "  northwind global  MACRO fund ", document_id=d2.id)
    assert (exact.method, exact.entity_id, exact.alias_id) == ("exact", new.entity_id, new.alias_id)

    fuzzy = resolve_fund(s, "Northwind Macro Fund Ltd", document_id=d3.id)
    assert (fuzzy.method, fuzzy.entity_id) == ("fuzzy", new.entity_id) and fuzzy.needs_confirmation
    fa = s.get(FundAlias, fuzzy.alias_id)
    assert (fa.alias, fa.match_method, fa.confirmed, fa.source_document_id) == (
        "Northwind Macro Fund Ltd", "fuzzy", False, d3.id)
    assert fa.match_score >= 80
    # seen again: the unconfirmed alias is matched exactly but still awaits confirmation
    again = resolve_fund(s, "Northwind Macro Fund Ltd", document_id=d3.id)
    assert (again.alias_id, again.method) == (fuzzy.alias_id, "fuzzy")
    # once confirmed (T6 confirm_alias) it is a plain exact match
    fa.confirmed = True
    s.flush()
    assert resolve_fund(s, "Northwind Macro Fund Ltd", document_id=d3.id).method == "exact"
    assert s.execute(select(FundAlias).where(FundAlias.fund_id == new.entity_id)).scalars().all().__len__() == 2


def test_alder_asia_share_class_suggested_but_dubai_separate(db_session):
    s = db_session
    d = doc(s, "x.pdf")
    asia = resolve_fund(s, "Alder Asia Fund", document_id=d.id)
    cls = resolve_fund(s, "Alder Asia Fund SPC – Class A", document_id=d.id)
    dubai = resolve_fund(s, "Alder Dubai Fund", document_id=d.id)
    assert cls.method == "fuzzy" and cls.entity_id == asia.entity_id
    assert dubai.method == "new" and dubai.entity_id != asia.entity_id
    assert s.get(FundAlias, cls.alias_id).alias == "Alder Asia Fund SPC – Class A"  # stored as written
    # and in the other order Dubai still does not attach to Asia's share-class alias
    assert resolve_fund(s, "Alder Dubai Fund", document_id=d.id).entity_id == dubai.entity_id


def test_rejected_alias_moved_to_other_fund_is_reused(db_session):
    """A reject_alias decision lives on the alias row (manual, confirmed): next time the
    name resolves exactly to the fund it was moved to."""
    s = db_session
    d = doc(s, "x.pdf")
    a = resolve_fund(s, "Kestrel Quant Equity Fund LP", document_id=d.id)
    f = resolve_fund(s, "Kestrel Quant Equity Fund", document_id=d.id)
    assert f.method == "fuzzy"
    other = Fund(canonical_name="Kestrel Onshore Feeder")
    s.add(other)
    s.flush()
    alias = s.get(FundAlias, f.alias_id)
    alias.fund_id, alias.match_method, alias.confirmed = other.id, "manual", True
    s.flush()
    r = resolve_fund(s, "Kestrel Quant Equity Fund", document_id=d.id)
    assert (r.entity_id, r.method) == (other.id, "exact") and r.entity_id != a.entity_id


def test_canonical_name_without_alias_row(db_session):
    s = db_session
    f = Fund(canonical_name="Orphan Fund")
    s.add(f)
    s.flush()
    r = resolve_fund(s, "orphan fund", document_id=None)
    assert (r.entity_id, r.method) == (f.id, "exact") and s.get(FundAlias, r.alias_id).confirmed


# ---- account resolution ------------------------------------------------------------------------


def test_accounts_by_name_and_masked_number(db_session):
    s = db_session
    d = doc(s, "st.pdf")
    acme = resolve_account(s, "Acme Family Office LLC", masked="XXXX-4471", custodian="Larkspur Trust Company",
                           currency="USD", document_id=d.id)
    assert acme.method == "new"
    acc = s.get(Account, acme.entity_id)
    assert (acc.account_number_masked, acc.custodian, acc.currency) == ("XXXX-4471", "Larkspur Trust Company", "USD")
    same = resolve_account(s, "ACME FAMILY OFFICE LLC", masked="xxxx-4471", custodian=None, currency=None,
                           document_id=d.id)
    assert (same.method, same.entity_id) == ("exact", acme.entity_id)
    fuzzy = resolve_account(s, "Acme Family Office", masked="XXXX-4471", custodian=None, currency=None,
                            document_id=d.id)
    assert (fuzzy.method, fuzzy.entity_id) == ("fuzzy", acme.entity_id)
    assert s.get(AccountAlias, fuzzy.alias_id).confirmed is False
    # same name, different account number: a different account, never merged
    other = resolve_account(s, "Acme Family Office LLC", masked="XXXX-9999", custodian=None, currency=None,
                            document_id=d.id)
    assert other.method == "new" and other.entity_id != acme.entity_id
    assert s.get(AccountAlias, other.alias_id).alias == "Acme Family Office LLC (XXXX-9999)"
    # distinct names never fuzzy-match
    bw = resolve_account(s, "Birchwood Foundation", masked="XXXX-9025", custodian=None, currency=None,
                         document_id=d.id)
    assert bw.method == "new"


def test_account_number_filled_in_when_missing(db_session):
    s = db_session
    a = resolve_account(s, "Cedar Ridge Partners LP", masked=None, custodian=None, currency=None, document_id=None)
    b = resolve_account(s, "Cedar Ridge Partners LP", masked="CRP-0042", custodian="Brightline", currency="USD",
                        document_id=None)
    assert a.entity_id == b.entity_id
    acc = s.get(Account, a.entity_id)
    assert (acc.account_number_masked, acc.custodian, acc.currency) == ("CRP-0042", "Brightline", "USD")


# ---- conflicts -----------------------------------------------------------------------------------


def _fund(s, name="Northwind Global Macro Fund") -> Fund:
    f = Fund(canonical_name=name)
    s.add(f)
    s.flush()
    return f


def _ret(s, fund, d, value, *, period=date(2026, 1, 31), rt="MTD", ng="net") -> Return:
    r = Return(fund_id=fund.id, document_id=d.id, period_end=period, return_type=rt, net_gross=ng,
               value=D(value) if value is not None else None)
    s.add(r)
    s.flush()
    return r


def test_return_tolerance():
    assert returns_differ(D("0.0192"), D("0.0189"))
    assert not returns_differ(D("0.0192"), D("0.0192"))
    assert not returns_differ(D("0.01234"), D("0.0123"))  # rounding, 0.4 bp
    assert returns_differ(D("0.0192"), D("0.0193"))  # 1 bp, visible at 2 dp
    assert not returns_differ(None, D("0.01"))


def test_northwind_conflict_flags_both_and_keeps_both(db_session):
    s = db_session
    f = _fund(s)
    factsheet, email = doc(s, "northwind_monthly_jan26.pdf"), doc(s, "northwind_flash_update_jan2026.eml")
    r1, r2 = _ret(s, f, factsheet, "0.0192"), _ret(s, f, email, "0.0189")
    changed = refresh_conflicts(s, {"returns": {(f.id, date(2026, 1, 31), "MTD", "net")}})
    assert changed == {factsheet.id, email.id}
    for r, other in ((r1, r2), (r2, r1)):
        assert r.status == "conflict" and r.review_reasons == ["conflict"]
        assert r.review_detail["conflict"] == {"conflicting_ids": [other.id], "tolerance": 0.00005}
        validate_review_detail(r.review_detail)
    assert r1.value == D("0.0192") and r2.value == D("0.0189")  # no overwrite


def test_equal_values_from_two_documents_are_not_a_conflict(db_session):
    s = db_session
    f = _fund(s, "Kestrel Quant Equity Fund LP")
    rows = [_ret(s, f, doc(s, f"k{i}.pdf"), "0.0284") for i in range(3)]
    assert refresh_conflicts(s, {"returns": {(f.id, date(2026, 1, 31), "MTD", "net")}}) == set()
    assert all(r.status == "ok" for r in rows)


def test_conflict_cleared_when_values_agree_again_and_ignores_hidden_docs(db_session):
    s = db_session
    f = _fund(s)
    a, b = doc(s, "a.pdf"), doc(s, "b.pdf")
    ra, rb = _ret(s, f, a, "0.0192"), _ret(s, f, b, "0.0189")
    key = {"returns": {(f.id, date(2026, 1, 31), "MTD", "net")}}
    refresh_conflicts(s, key)
    assert ra.status == rb.status == "conflict"
    rb.value = D("0.0192")
    refresh_conflicts(s, key)
    assert ra.status == rb.status == "ok" and ra.review_detail is None
    # a removed document's row no longer takes part
    rb.value = D("0.0100")
    b.status = "removed"
    s.flush()
    refresh_conflicts(s, key)
    assert ra.status == "ok"


def test_conflict_keeps_other_reasons(db_session):
    s = db_session
    f = _fund(s)
    a, b = doc(s, "a.pdf"), doc(s, "b.pdf")
    ra, _ = _ret(s, f, a, "0.0192"), _ret(s, f, b, "0.0189")
    ra.review_reasons, ra.status = ["low_confidence"], "needs_review"
    ra.review_detail = {"low_confidence": {"confidence": 0.5, "threshold": 0.7}}
    s.flush()
    refresh_conflicts(s, {"returns": {(f.id, date(2026, 1, 31), "MTD", "net")}})
    assert ra.status == "conflict" and ra.review_reasons == ["low_confidence", "conflict"]


def test_fact_and_statement_conflicts(db_session):
    s = db_session
    f = _fund(s, "Kestrel Quant Equity Fund LP")
    a, b = doc(s, "a.pdf"), doc(s, "b.pdf")
    facts = []
    for d, v in ((a, "412600000"), (b, "412600000.4")):
        ff = FundFact(fund_id=f.id, document_id=d.id, name="aum", value=D(v), unit="USD", as_of_date=date(2026, 1, 31))
        s.add(ff)
        facts.append(ff)
    for d, v in ((a, "-0.068"), (b, "-0.070")):
        s.add(FundFact(fund_id=f.id, document_id=d.id, name="max_drawdown", value=D(v), unit="fraction",
                       as_of_date=None))
    acc = Account(canonical_name="Acme")
    s.add(acc)
    s.flush()
    sts = [Statement(account_id=acc.id, document_id=d.id, period_end=date(2026, 1, 31), closing_balance=D(v))
           for d, v in ((a, "13100000"), (b, "13080000"))]
    s.add_all(sts)
    s.flush()
    changed = refresh_conflicts(s, {
        "fund_facts": {(f.id, "aum", date(2026, 1, 31)), (f.id, "max_drawdown", None)},
        "statements": {(acc.id, None, date(2026, 1, 31))},
    })
    assert changed == {a.id, b.id}
    assert all(ff.status == "ok" for ff in facts)  # 0.4 on 412.6mm is rounding
    dd = s.execute(select(FundFact).where(FundFact.name == "max_drawdown")).scalars().all()
    assert {x.status for x in dd} == {"conflict"}  # NULL as_of_date still groups
    assert {x.status for x in sts} == {"conflict"}
    assert not statements_differ(sts[0], sts[0])
    assert facts_differ(FundFact(value=D(1), unit="ratio"), FundFact(value=D(1), unit="x"))  # unit mismatch

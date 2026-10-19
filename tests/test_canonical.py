"""Canonical fund/account names (BUG-Q3-01): the deterministic rule
(`app/ingest/canonical.py`) and the `make_canonical` review action.

- The rule: most visible documents, then factsheets (accounts: account statements),
  then the longest name, then alphabetical. Same documents -> same name, in any
  processing order (the committed real fixtures, several orders).
- Recompute: after each document, after a sync removed documents (worker), at boot.
- `make_canonical`: renames, keeps the old name as a confirmed alias, pins the name
  across reprocessing and recomputes; only for alias items; the account variant.
- Alder Asia and Alder Dubai stay two funds.
"""

from __future__ import annotations

import shutil
import threading
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction.fixture import FIXTURES_DIR, FixtureExtractor
from app.extraction.schema import Extraction, FundExtraction, PageText, StatementExtraction
from app.ingest import canonical, postprocess
from app.ingest.canonical import NameUsage, choose_canonical, name_of, preferred_spelling
from app.ingest.postprocess import process_extraction
from app.models import Account, AccountAlias, Document, Fund, FundAlias, Return
from app.models import Extraction as ExtractionRow
from app.schemas import ReviewActionResponse, ReviewListResponse
from app.sources.local import DEFAULT_LOCAL_DIR, LocalFolderSource
from tests.api_helpers import api_client, assert_error, demo_settings, q1, rows, truncate, validate
from tests.pipeline_helpers import FIXED_TODAY, drain, sync_folder
from tests.synthetic_samples import ret, sv

FLASH = "northwind_flash_update_jan2026.eml"
DEC, JAN, FEB = "northwind_dec25_scanned.pdf", "northwind_monthly_jan26.pdf", "northwind_monthly_feb26.pdf"
NORTHWIND = [FLASH, DEC, JAN, FEB]
GLOBAL, LTD = "Northwind Global Macro Fund", "Northwind Macro Fund Ltd"

# ---- the rule, pure ---------------------------------------------------------------------------------


def u(name, docs=(), pref=()):
    return NameUsage(name, frozenset(docs), frozenset(pref))


def test_most_documents_wins_over_factsheets_and_length():
    a, b = u("Short Fund", {1, 2, 3}), u("A Much Longer Fund Name", {4}, {4})
    assert choose_canonical([b, a]) == a == choose_canonical([a, b])


def test_tie_goes_to_factsheets_then_longest_then_alphabetical():
    assert choose_canonical([u("Northwind Macro Fund Ltd", {1}), u("Northwind Global Macro Fund", {2}, {2})]).name \
        == "Northwind Global Macro Fund"
    # no factsheet on either side: the longest name
    assert choose_canonical([u("Harbor Credit Opps Fund", {1}), u("Harbor Credit Opportunities Fund", {2})]).name \
        == "Harbor Credit Opportunities Fund"
    # same length: alphabetical (case-insensitive), then code points
    assert choose_canonical([u("Beta Fund", {1}), u("Alfa Fund", {2})]).name == "Alfa Fund"
    assert choose_canonical([u("alfa fund", {1}), u("Alfa Fund", {2})]).name == "Alfa Fund"
    assert choose_canonical([u("Alfa Fund", {2}), u("alfa fund", {1})]).name == "Alfa Fund"


def test_nothing_visible_means_no_decision():
    assert choose_canonical([u("A"), u("B")]) is None
    assert choose_canonical([]) is None


def test_preferred_spelling_is_order_independent():
    caps, mixed, lower = "MERIDIAN RATES RELATIVE VALUE FUND", "Meridian Rates Relative Value Fund", \
        "meridian rates relative value fund"
    for a, b in ((caps, mixed), (mixed, caps), (lower, mixed), (mixed, lower)):
        assert preferred_spelling(a, b) == mixed
    assert preferred_spelling(caps, lower) == preferred_spelling(lower, caps) == lower


def test_account_alias_with_number_suffix_stands_for_the_plain_name():
    acc = Account(canonical_name="Acme", account_number_masked="XXXX-1")
    assert name_of("account", "Acme (XXXX-1)", acc) == "Acme"
    assert name_of("account", "Acme (XXXX-2)", acc) == "Acme (XXXX-2)"
    assert name_of("fund", "Acme (XXXX-1)", acc) == "Acme (XXXX-1)"


# ---- the rule over real documents (committed fixtures, real pipeline) -----------------------------------


@pytest.fixture
def fixtures_ready():
    if not FIXTURES_DIR.is_dir() or len(list(FIXTURES_DIR.glob("*.json"))) < 21:
        pytest.skip("fixtures/ not generated yet")


@pytest.fixture
def db(test_engine, fixtures_ready, monkeypatch):
    truncate(test_engine)
    monkeypatch.setattr(postprocess, "_today", lambda: FIXED_TODAY)
    yield test_engine
    truncate(test_engine)


def ingest_in_order(engine, folder: Path, names, *, remove=()) -> None:
    """Add the files to the watched folder one at a time, processing each before the next."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(DEFAULT_LOCAL_DIR / name, folder / name)
        sync_folder(engine, folder)
        drain(engine, folder, FixtureExtractor(FIXTURES_DIR))


def fund_state(engine, like: str) -> dict:
    fs = rows(engine, "SELECT id, canonical_name FROM funds WHERE canonical_name ILIKE :l", l=like)
    assert len(fs) == 1, fs
    aliases = {r["alias"]: r for r in rows(engine, """SELECT id, alias, match_method, confirmed, match_score
                                                    FROM fund_aliases WHERE fund_id = :f""", f=fs[0]["id"])}
    return {"id": fs[0]["id"], "name": fs[0]["canonical_name"], "aliases": aliases}


ORDERS = {
    "drive (flash first)": [FLASH, DEC, JAN, FEB],
    "factsheets first": [DEC, JAN, FEB, FLASH],
    "flash second": [JAN, FLASH, FEB, DEC],
    "flash third": [FEB, DEC, FLASH, JAN],
    "reverse alphabetical": sorted(NORTHWIND, reverse=True),
}


@pytest.mark.parametrize("order", list(ORDERS.values()), ids=list(ORDERS))
def test_northwind_canonical_name_does_not_depend_on_order(db, tmp_path, order):
    ingest_in_order(db, tmp_path / "docs", order)
    st = fund_state(db, "Northwind%")
    assert st["name"] == GLOBAL
    assert set(st["aliases"]) == {GLOBAL, LTD}
    assert st["aliases"][GLOBAL]["confirmed"] is True
    ltd = st["aliases"][LTD]
    assert (ltd["match_method"], ltd["confirmed"]) == ("fuzzy", False)  # the open review question
    assert q1(db, "SELECT count(*) FROM funds") == 1
    names = {r["fund_name"] for r in rows(db, "SELECT DISTINCT fund_name FROM v_returns")}
    assert names == {GLOBAL}
    assert q1(db, "SELECT fund_name FROM v_funds") == GLOBAL
    with api_client() as c:
        body = c.get("/api/review?kind=alias").json()
    validate(ReviewListResponse, body)
    (item,) = body["items"]
    assert (item["alias"]["alias"], item["alias"]["suggested_name"]) == (LTD, GLOBAL)
    assert item["allowed_actions"] == ["confirm_alias", "reject_alias", "make_canonical"]
    st_docs = {r["name"]: r["status"] for r in rows(db, "SELECT name, status FROM documents")}
    assert st_docs[FLASH] == "needs_review"  # it uses the unconfirmed alias (and holds the Jan conflict)
    assert st_docs[DEC] == "done"  # the scanned Dec factsheet has nothing to review any more


def _all_names(engine) -> dict[str, list[str]]:
    out = {}
    for f in rows(engine, "SELECT id, canonical_name FROM funds ORDER BY canonical_name"):
        out[f["canonical_name"]] = sorted(r["alias"] for r in rows(
            engine, "SELECT alias FROM fund_aliases WHERE fund_id = :f", f=f["id"]))
    return out


def test_all_fixtures_give_the_same_names_in_opposite_orders(db, tmp_path):
    names = sorted(p.name for p in DEFAULT_LOCAL_DIR.iterdir() if p.name != "MANIFEST.md")
    ingest_in_order(db, tmp_path / "a", names)
    first = _all_names(db)
    accounts_first = sorted(r["canonical_name"] for r in rows(db, "SELECT canonical_name FROM accounts"))
    truncate(db)
    ingest_in_order(db, tmp_path / "b", list(reversed(names)))
    assert _all_names(db) == first
    assert sorted(r["canonical_name"] for r in rows(db, "SELECT canonical_name FROM accounts")) == accounts_first
    assert set(first) == {
        "Alder Asia Fund", "Alder Dubai Fund", "Harbor Credit Opportunities Fund", "Kestrel Quant Equity Fund LP",
        "Meridian Rates Relative Value Fund", GLOBAL,
    }
    # siblings are never merged; the share-class name stays an alias of Alder Asia
    assert first["Alder Asia Fund"] == ["Alder Asia Fund", "Alder Asia Fund SPC – Class A"]
    assert first["Alder Dubai Fund"] == ["Alder Dubai Fund"]
    assert first["Meridian Rates Relative Value Fund"] == ["Meridian Rates RV Fund",
                                                           "Meridian Rates Relative Value Fund"]
    # a recompute over everything changes nothing (idempotent)
    assert canonical.run_recompute(db).renamed == []


def test_removed_documents_trigger_a_recompute_and_re_adding_restores(db, tmp_path):
    from app.worker import Worker

    folder = tmp_path / "docs"
    ingest_in_order(db, folder, NORTHWIND)
    assert fund_state(db, "Northwind%")["name"] == GLOBAL
    w = Worker(demo_settings(), db, threading.Event(), source_resolver=lambda s, sess: LocalFolderSource(folder),
               indexing=False)
    for name in (DEC, JAN, FEB):
        (folder / name).unlink()
    assert w.sync_tick() == "reconcile"
    st = fund_state(db, "Northwind%")
    assert st["name"] == LTD  # only the flash email is visible now
    assert (st["aliases"][LTD]["confirmed"], st["aliases"][GLOBAL]["match_method"]) == (True, "fuzzy")
    ingest_in_order(db, folder, [DEC, JAN, FEB])
    assert fund_state(db, "Northwind%")["name"] == GLOBAL


def test_worker_boot_recompute_corrects_names_from_older_code(db, tmp_path, monkeypatch):
    from app import worker as wk

    ingest_in_order(db, tmp_path / "docs", NORTHWIND)
    st = fund_state(db, "Northwind%")
    # the state the pre-rule code left behind on Drive: first-seen name canonical
    with Session(db) as s, s.begin():
        s.get(Fund, st["id"]).canonical_name = LTD
        g, ltd = s.get(FundAlias, st["aliases"][GLOBAL]["id"]), s.get(FundAlias, st["aliases"][LTD]["id"])
        g.match_method, g.match_score, g.confirmed = "fuzzy", 100.0, True  # the user had confirmed it
        ltd.match_method, ltd.match_score, ltd.confirmed = "new", None, True
    w = wk.Worker(demo_settings(), db, threading.Event(), source_resolver=lambda s, sess: None, indexing=False)
    assert w.recompute_canonical_names() is True
    st = fund_state(db, "Northwind%")
    assert st["name"] == GLOBAL and st["id"] == fund_state(db, "Northwind%")["id"]
    assert all(a["confirmed"] for a in st["aliases"].values())  # the user's confirmation is kept
    assert canonical.run_recompute(db).renamed == []  # idempotent
    # the boot run is wired into Worker.run, and a failure never stops the worker
    calls = []
    monkeypatch.setattr(wk.Worker, "recompute_canonical_names", lambda self, ids=None: calls.append(ids) or True)
    stop = threading.Event()
    wk.run(demo_settings(), stop, max_iterations=1, engine=db, source_resolver=lambda s, sess: None, indexing=False)
    assert calls == [None]
    monkeypatch.undo()
    monkeypatch.setattr(canonical, "run_recompute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert w.recompute_canonical_names() is False


# ---- tie-breaks and accounts through process_extraction (rolled back) -------------------------------------

TODAY = date(2026, 10, 16)


def run_doc(s, name, doc_type, funds=(), statements=()):
    doc = Document(drive_file_id=name, name=name, mime_type="application/pdf", md5_checksum=f"md5-{name}",
                   status="processing", text_content=name, text_source="file")
    s.add(doc)
    s.flush()
    ex = Extraction(document_type=doc_type, document_type_confidence=0.9, funds=list(funds),
                    statements=list(statements), pages=[PageText(page=1, text=name)], extra={})
    row = ExtractionRow(document_id=doc.id, status="ok", raw_json=ex.model_dump(mode="json"))
    s.add(row)
    s.flush()
    doc.document_type = doc_type
    doc.status = process_extraction(s, doc.id, ex, extraction_id=row.id, today=TODAY)
    s.flush()
    return doc


def fund(name, month="Jan 2026"):
    return FundExtraction(fund_name=sv(name, name), manager=None, strategy=None, currency=None, as_of_date=None,
                          aum=None, returns=[ret(month, "+1.00%", f"{month} +1.00%")], other_metrics={})


def statement(name, masked="****4821", period="31 Jan 2026"):
    return StatementExtraction(
        account_name=sv(name, name), account_number_masked=sv(masked, masked), custodian_or_manager=None,
        fund_name=None, currency=sv("USD", "USD"), period_start=None, period_end=sv(period, period),
        opening_balance=None, contributions=None, withdrawals=None, pnl=None, fees=None,
        closing_balance=sv("1,000.00", "1,000.00"), holdings=[])


def names(s, model):
    return sorted(x.canonical_name for x in s.execute(select(model)).scalars())


@pytest.mark.parametrize("factsheet_first", [True, False])
def test_tie_prefers_the_factsheet_name(db_session, factsheet_first):
    s = db_session
    docs = [("fs.pdf", "factsheet", "Northwind Global Macro Fund"), ("mail.eml", "email", "Northwind Macro Fund Ltd")]
    for n, t, f in docs if factsheet_first else reversed(docs):
        run_doc(s, n, t, [fund(f)])
    assert names(s, Fund) == ["Northwind Global Macro Fund"]


@pytest.mark.parametrize("letter_first", [True, False])
def test_tie_without_factsheets_prefers_the_longest(db_session, letter_first):
    s = db_session
    docs = [("letter.pdf", "performance_report", "Harbor Credit Opportunities Fund"),
            ("nav.csv", "performance_report", "Harbor Credit Opps Fund")]
    for n, t, f in docs if letter_first else reversed(docs):
        run_doc(s, n, t, [fund(f)])
    assert names(s, Fund) == ["Harbor Credit Opportunities Fund"]


def test_more_documents_beat_the_factsheet_preference(db_session):
    s = db_session
    run_doc(s, "fs.pdf", "factsheet", [fund("Harbor Credit Opportunities Fund")])
    run_doc(s, "nav1.csv", "performance_report", [fund("Harbor Credit Opps Fund", "Feb 2026")])
    assert names(s, Fund) == ["Harbor Credit Opportunities Fund"]
    run_doc(s, "nav2.csv", "performance_report", [fund("Harbor Credit Opps Fund", "Mar 2026")])
    assert names(s, Fund) == ["Harbor Credit Opps Fund"]  # 2 documents vs 1


@pytest.mark.parametrize("statement_first", [True, False])
def test_account_tie_prefers_the_account_statement_name(db_session, statement_first):
    s = db_session
    docs = [("st.pdf", "account_statement", "Acme Family Office"),
            ("mail.eml", "email", "Acme Family Office LLC")]
    for n, t, a in docs if statement_first else reversed(docs):
        run_doc(s, n, t, statements=[statement(a, period="31 Jan 2026" if n == "st.pdf" else "28 Feb 2026")])
    assert names(s, Account) == ["Acme Family Office"]
    (acc,) = s.execute(select(Account)).scalars()
    aliases = {a.alias: a for a in s.execute(select(AccountAlias)).scalars()}
    assert set(aliases) == {"Acme Family Office", "Acme Family Office LLC"}
    assert aliases["Acme Family Office"].confirmed and not aliases["Acme Family Office LLC"].confirmed
    assert all(a.account_id == acc.id for a in aliases.values())


def test_siblings_are_never_merged_by_the_rule(db_session):
    s = db_session
    run_doc(s, "asia.pdf", "factsheet", [fund("Alder Asia Fund")])
    run_doc(s, "dubai.pdf", "factsheet", [fund("Alder Dubai Fund")])
    run_doc(s, "q1.pdf", "performance_report", [fund("Alder Asia Fund", "Feb 2026"), fund("Alder Dubai Fund",
                                                                                           "Feb 2026")])
    run_doc(s, "asia-mar.pdf", "factsheet", [fund("Alder Asia Fund SPC – Class A", "Mar 2026")])
    assert names(s, Fund) == ["Alder Asia Fund", "Alder Dubai Fund"]
    assert canonical.recompute_all(s).renamed == []


# ---- make_canonical (API) ------------------------------------------------------------------------------


def review(client, query=""):
    r = client.get(f"/api/review?page_size=100{query}")
    assert r.status_code == 200, r.text
    validate(ReviewListResponse, r.json())
    return r.json()


def act(client, item_id, body):
    r = client.post(f"/api/review/{item_id}", json=body)
    assert r.status_code == 200, r.text
    validate(ReviewActionResponse, r.json())
    return r.json()


def alias_item(client, alias):
    return next(i for i in review(client, "&kind=alias")["items"] if i["alias"]["alias"] == alias)


def reprocess_all(client, engine, folder, names_):
    for n in names_:
        doc = q1(engine, "SELECT id FROM documents WHERE name = :n AND is_latest", n=n)
        assert client.post(f"/api/documents/{doc}/reprocess").status_code == 202
    drain(engine, folder, FixtureExtractor(FIXTURES_DIR))


def test_make_canonical_flips_the_name_and_survives_reprocessing(db, tmp_path):
    folder = tmp_path / "docs"
    ingest_in_order(db, folder, ORDERS["drive (flash first)"])
    before = fund_state(db, "Northwind%")
    with api_client() as c:
        item = alias_item(c, LTD)
        out = act(c, item["id"], {"action": "make_canonical"})
        assert out["resolved"] is True and out["action"] == "make_canonical" and len(out["review_action_ids"]) == 1
        assert {"table": "funds", "id": before["id"], "change": "renamed", "status": None} in out["affected"]
        assert {"table": "fund_aliases", "id": item["target_id"], "change": "alias_confirmed", "status": None} \
            in out["affected"]
        st = fund_state(db, "Northwind%")
        assert (st["id"], st["name"]) == (before["id"], LTD)  # renamed, same fund
        assert all(a["confirmed"] for a in st["aliases"].values())
        assert set(st["aliases"]) == {GLOBAL, LTD}
        assert {r["fund_name"] for r in rows(db, "SELECT DISTINCT fund_name FROM v_returns")} == {LTD}
        funds = c.get("/api/funds").json()["items"]
        assert [f["fund_name"] for f in funds] == [LTD] and funds[0]["has_unconfirmed_alias"] is False
        ra = rows(db, "SELECT target_table, target_id, action, natural_key, old_value, new_value FROM review_actions")
        assert ra == [{"target_table": "fund_aliases", "target_id": item["target_id"], "action": "make_canonical",
                       "natural_key": {"entity": "fund", "alias": LTD.lower()},
                       "old_value": {"fund_id": before["id"], "canonical_name": GLOBAL, "confirmed": False},
                       "new_value": {"fund_id": before["id"], "canonical_name": LTD, "confirmed": True}}]
        assert review(c, "&kind=alias")["items"] == []
        assert q1(db, "SELECT status FROM documents WHERE name = :n", n=DEC) == "done"

        # reprocessing every Northwind document keeps the user's name (the rule would pick GLOBAL)
        reprocess_all(c, db, folder, NORTHWIND)
        assert fund_state(db, "Northwind%")["name"] == LTD
        assert review(c, "&kind=alias")["items"] == []
        assert canonical.run_recompute(db).renamed == []
        assert fund_state(db, "Northwind%")["name"] == LTD

        # the document carrying the pinned name is removed: the name stays, nothing breaks
        (folder / FLASH).unlink()
        sync_folder(db, folder)
        canonical.run_recompute(db, [q1(db, "SELECT id FROM documents WHERE name = :n", n=FLASH)])
        assert fund_state(db, "Northwind%")["name"] == LTD
        assert c.get("/api/funds").json()["items"][0]["fund_name"] == LTD


def test_make_canonical_is_only_for_alias_items(db, tmp_path):
    ingest_in_order(db, tmp_path / "docs", ORDERS["drive (flash first)"])
    with api_client() as c:
        items = review(c)["items"]
        conflict = next(i for i in items if i["kind"] == "conflict")
        body = assert_error(c.post(f"/api/review/{conflict['id']}", json={"action": "make_canonical"}), 422,
                            "invalid_action", "body.action")
        assert "make_canonical" in body["error"]["message"] and "choose, edit" in body["error"]["message"]
        item = alias_item(c, LTD)
        assert_error(c.post(f"/api/review/{item['id']}", json={"action": "make_canonical", "reassign_to_id": 1}),
                     422, "validation_error", "body.reassign_to_id")
        assert q1(db, "SELECT count(*) FROM review_actions") == 0
        assert fund_state(db, "Northwind%")["name"] == GLOBAL
    # a value item
    rid = q1(db, "SELECT min(id) FROM returns WHERE status = 'ok'")
    with Session(db) as s, s.begin():
        r = s.get(Return, rid)
        r.status, r.review_reasons, r.review_detail = "needs_review", ["low_confidence"], {
            "low_confidence": {"confidence": 0.5, "threshold": 0.7}}
    with api_client() as c:
        assert_error(c.post(f"/api/review/returns:{rid}", json={"action": "make_canonical"}), 422, "invalid_action",
                     "body.action")


def _commit_doc(engine, name, doc_type, statements):
    with Session(engine) as s, s.begin():
        run_doc(s, name, doc_type, statements=statements)


def test_make_canonical_on_an_account_alias(test_engine):
    truncate(test_engine)
    try:
        _commit_doc(test_engine, "st.pdf", "account_statement", [statement("Acme Family Office")])
        _commit_doc(test_engine, "mail.eml", "email", [statement("Acme Family Office LLC", period="28 Feb 2026")])
        acc = q1(test_engine, "SELECT id FROM accounts")
        assert q1(test_engine, "SELECT canonical_name FROM accounts") == "Acme Family Office"
        with api_client() as c:
            item = alias_item(c, "Acme Family Office LLC")
            assert item["target_table"] == "account_aliases"
            assert item["allowed_actions"] == ["confirm_alias", "reject_alias", "make_canonical"]
            out = act(c, item["id"], {"action": "make_canonical"})
            assert {"table": "accounts", "id": acc, "change": "renamed", "status": None} in out["affected"]
            assert out["resolved"] is True
            assert q1(test_engine, "SELECT account_name FROM v_accounts") == "Acme Family Office LLC"
            assert q1(test_engine, "SELECT DISTINCT account_name FROM v_statements") == "Acme Family Office LLC"
            aliases = rows(test_engine, "SELECT alias, confirmed FROM account_aliases ORDER BY alias")
            assert aliases == [{"alias": "Acme Family Office", "confirmed": True},
                               {"alias": "Acme Family Office LLC", "confirmed": True}]
            assert c.get("/api/accounts").json()["items"][0]["account_name"] == "Acme Family Office LLC"
        # another statement under the old name (it would win the rule 2 to 1): the pin holds
        _commit_doc(test_engine, "st2.pdf", "account_statement", [statement("Acme Family Office",
                                                                            period="31 Mar 2026")])
        assert q1(test_engine, "SELECT canonical_name FROM accounts") == "Acme Family Office LLC"
        assert canonical.run_recompute(test_engine).renamed == []
        assert q1(test_engine, "SELECT count(*) FROM accounts") == 1
    finally:
        truncate(test_engine)


def test_reject_alias_recomputes_both_funds(db, tmp_path):
    """Moving the alias away re-runs the rule for both funds (no stale names)."""
    ingest_in_order(db, tmp_path / "docs", ORDERS["drive (flash first)"])
    with api_client() as c:
        item = alias_item(c, LTD)
        out = act(c, item["id"], {"action": "reject_alias", "reassign_to_id": None})
    created = [a for a in out["affected"] if a["change"] == "created"]
    assert len(created) == 1
    assert sorted(r["canonical_name"] for r in rows(db, "SELECT canonical_name FROM funds")) == [GLOBAL, LTD]
    assert canonical.run_recompute(db).renamed == []

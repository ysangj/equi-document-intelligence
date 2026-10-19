"""FR-13 review queue: GET /api/review and POST /api/review/{id} over the synthetic
sample set (the planted conflict, aliases, YTD and roll-forward cases)."""

from __future__ import annotations

import pytest

from app.extraction.fixture import FixtureExtractor
from app.ingest.decisions import natural_key
from app.models import Return
from app.schemas import ReviewActionResponse, ReviewItem, ReviewListResponse
from tests.api_helpers import api_client, assert_error, exec_sql, ingest_into, q1, return_id, rows, truncate, validate
from tests.pipeline_helpers import drain


@pytest.fixture
def db(test_engine, tmp_path):
    ingest_into(test_engine, tmp_path)
    yield test_engine
    truncate(test_engine)


@pytest.fixture
def client(db):
    with api_client() as c:
        yield c


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


def item_by(items, **kw):
    for it in items:
        if all((it.get(k) == v) if not callable(v) else v(it.get(k)) for k, v in kw.items()):
            return it
    raise AssertionError(f"no item with {kw}")


def reprocess(client, db, tmp_path, name):
    doc = q1(db, "SELECT id FROM documents WHERE name = :n AND is_latest", n=name)
    assert client.post(f"/api/documents/{doc}/reprocess").status_code == 202
    return drain(db, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))


def doc_status(db, name):
    return q1(db, "SELECT status FROM documents WHERE name = :n AND is_latest", n=name)


def actions(db):
    return rows(db, "SELECT * FROM review_actions ORDER BY id")


# ---- listing ---------------------------------------------------------------------------------


def test_queue_contains_the_planted_cases(client):
    body = review(client)
    items = body["items"]
    conflict = item_by(items, kind="conflict", title="Northwind Global Macro Fund · Jan 2026 · MTD net")
    docs = {c["document_name"] for c in conflict["candidates"]}
    assert {"northwind_monthly_jan26.pdf", "northwind_flash_update_jan2026.eml"} <= docs
    assert {c["value_pct"] for c in conflict["candidates"]} == {1.92, 1.89}
    assert conflict["id"] == f"returns:{min(c['id'] for c in conflict['candidates'])}"
    assert conflict["allowed_actions"] == ["choose", "edit"] and conflict["status"] == "conflict"
    assert conflict["reasons"][0]["code"] == "conflict" and "+1.89%" in conflict["reasons"][0]["message"]
    assert conflict["subject"]["fund_name"] == "Northwind Global Macro Fund"
    for alias in ("Northwind Macro Fund Ltd", "Alder Asia Fund SPC – Class A"):
        a = item_by(items, kind="alias", alias=lambda x, alias=alias: x and x["alias"] == alias)
        assert a["allowed_actions"] == ["confirm_alias", "reject_alias", "make_canonical"] and a["target_table"] == "fund_aliases"
        assert a["detail"]["alias_match"]["alias"] == alias and a["alias"]["affected_rows"] >= 1
        assert "was matched to" in a["reasons"][0]["message"]
    ytd = item_by(items, kind="value", review_reasons=["ytd_mismatch"])
    assert ytd["current"]["value_pct"] == 0.15 and ytd["subject"]["return_type"] == "YTD"
    assert ytd["detail"]["ytd_mismatch"]["year"] == 2026
    assert "compound to -0.15%" in ytd["reasons"][0]["message"]
    acme = item_by(items, kind="value", review_reasons=["rollforward_mismatch"])
    assert acme["target_table"] == "statements" and acme["current"]["field"] == "closing_balance"
    assert [f["field"] for f in acme["fields"]] == ["opening_balance", "contributions", "withdrawals", "pnl", "fees",
                                                    "closing_balance", "period_start", "period_end"]
    dates = {f["field"]: f for f in acme["fields"][-2:]}
    assert dates["period_end"]["value"] == "2026-01-31" and dates["period_end"]["value_kind"] == "date"
    assert acme["allowed_actions"] == ["accept", "edit"]
    assert body["counts"]["by_kind"]["conflict"] >= 1 and body["counts"]["by_kind"]["alias"] >= 2
    assert body["counts"]["by_reason"]["rollforward_mismatch"] == 1
    for it in items:
        ReviewItem.model_validate(it)
        assert it["id"] == f"{it['target_table']}:{it['target_id']}"


def test_conflict_group_is_one_item(client, db):
    members = rows(db, "SELECT id FROM returns WHERE 'conflict' = ANY(review_reasons)")
    items = [i for i in review(client)["items"] if i["kind"] == "conflict"]
    covered = [c["id"] for i in items for c in i["candidates"]]
    assert sorted(covered) == sorted(m["id"] for m in members)  # each conflict row in exactly one item
    assert len(items) < len(members)


def test_filters_sort_and_paging(client):
    all_ = review(client)
    assert all_["sort"] == "-created_at"
    aliases = review(client, "&kind=alias")
    assert aliases["total"] == all_["counts"]["by_kind"]["alias"] and {i["kind"] for i in aliases["items"]} == {"alias"}
    assert review(client, "&kind=alias&kind=conflict")["total"] == aliases["total"] + all_["counts"]["by_kind"]["conflict"]
    rf = review(client, "&reason=rollforward_mismatch")
    assert [i["target_table"] for i in rf["items"]] == ["statements"]
    doc = rf["items"][0]["document_id"]
    assert [i["id"] for i in review(client, f"&document_id={doc}")["items"]] == [rf["items"][0]["id"]]
    assert review(client, "&kind=alias&reason=ytd_mismatch")["total"] == 0
    assert review(client, "&reason=conflict")["counts"] == all_["counts"]  # counts ignore filters
    by_table = [i["target_table"] for i in review(client, "&sort=target_table")["items"]]
    assert by_table == sorted(by_table)
    created = [i["created_at"] for i in all_["items"]]
    assert created == sorted(created, reverse=True)
    p1 = client.get("/api/review?page_size=2").json()
    p2 = client.get("/api/review?page_size=2&page=2").json()
    assert len(p1["items"]) == 2 and p1["total"] == all_["total"]
    assert {i["id"] for i in p1["items"]}.isdisjoint({i["id"] for i in p2["items"]})
    assert client.get("/api/review?page=99").json()["items"] == []
    assert_error(client.get("/api/review?sort=title"), 422, "validation_error", "query.sort")
    assert_error(client.get("/api/review?kind=other"), 422, "validation_error", "query.kind")


# ---- accept / edit -------------------------------------------------------------------------------


def test_accept_value_item(client, db):
    item = item_by(review(client)["items"], review_reasons=["ytd_mismatch"])
    rid = item["target_id"]
    before = review(client)["total"]
    out = act(client, item["id"], {"action": "accept", "note": "stated YTD kept as reported"})
    assert out["resolved"] is True and out["remaining_open"] == before - 1
    assert out["affected"] == [{"table": "returns", "id": rid, "change": "status_changed", "status": "ok"}]
    r = rows(db, "SELECT status, review_reasons, review_detail, value FROM returns WHERE id = :i", i=rid)[0]
    assert (r["status"], r["review_reasons"], r["review_detail"]) == ("ok", [], None)
    assert float(r["value"]) == 0.0015
    (ra,) = actions(db)
    assert ra["id"] == out["review_action_ids"][0] and ra["action"] == "accept" and ra["target_id"] == rid
    assert ra["old_value"] == ra["new_value"] == {"value": 0.0015} and ra["note"] == "stated YTD kept as reported"
    assert ra["reason"] == "ytd_mismatch" and ra["document_id"] is not None
    from sqlalchemy.orm import Session

    with Session(db) as s:
        assert ra["natural_key"] == natural_key("returns", s.get(Return, rid))
    assert doc_status(db, "meridian_rv_tearsheet_2026-02.pdf") == "done"  # no flagged rows left
    assert item["id"] not in [i["id"] for i in review(client)["items"]]
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "accept"}), 404, "not_found")


def test_edit_percent_value(client, db):
    item = item_by(review(client)["items"], review_reasons=["ytd_mismatch"])
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "value": -0.0015}), 422,
                 "invalid_action", "body.value_pct")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "field": "pnl", "value_pct": 1}),
                 422, "invalid_action", "body.field")
    out = act(client, item["id"], {"action": "edit", "value_pct": -0.15, "note": "sign fixed"})
    assert out["resolved"] is True and out["affected"][0]["change"] == "value_edited"
    r = rows(db, "SELECT status, value FROM returns WHERE id = :i", i=item["target_id"])[0]
    assert r["status"] == "ok" and float(r["value"]) == -0.0015
    (ra,) = actions(db)
    assert (ra["action"], ra["old_value"], ra["new_value"]) == ("edit", {"value": 0.0015}, {"value": -0.0015})
    src = client.get(f"/api/sources/returns/{item['target_id']}").json()
    assert src["value_pct"] == -0.15 and src["review_history"][0]["action"] == "edit"


def test_edit_statement_field(client, db):
    item = item_by(review(client)["items"], review_reasons=["rollforward_mismatch"])
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "value": 13080000.0}), 422,
                 "invalid_action", "body.field")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "field": "closing_balance",
                                                                 "value": "lots"}), 422, "invalid_action", "body.value")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "field": "period_end",
                                                                 "value": "31/01/2026"}), 422, "invalid_action")
    out = act(client, item["id"], {"action": "edit", "field": "closing_balance", "value": 13080000.0})
    assert out["resolved"] is True
    r = rows(db, "SELECT status, closing_balance FROM statements WHERE id = :i", i=item["target_id"])[0]
    assert r["status"] == "ok" and float(r["closing_balance"]) == 13080000.0
    (ra,) = actions(db)
    assert ra["natural_key"]["field"] == "closing_balance"
    assert ra["old_value"] == {"closing_balance": 13100000.0} and ra["new_value"] == {"closing_balance": 13080000.0}
    assert doc_status(db, "acme_family_office_statement_2026-01.pdf") == "done"
    act_date = client.get(f"/api/sources/statements/{item['target_id']}?field=closing_balance").json()
    assert act_date["value"] == 13080000.0 and act_date["status"] == "ok"


def test_disallowed_actions(client):
    items = review(client)["items"]
    value = item_by(items, review_reasons=["ytd_mismatch"])
    conflict = item_by(items, kind="conflict")
    alias = item_by(items, kind="alias")
    for item, body, kind in [
        (value, {"action": "choose", "chosen_id": value["target_id"]}, "value"),
        (value, {"action": "confirm_alias"}, "value"),
        (conflict, {"action": "accept"}, "conflict"),
        (conflict, {"action": "reject_alias"}, "conflict"),
        (alias, {"action": "accept"}, "alias"),
        (alias, {"action": "edit", "value": 1}, "alias"),
    ]:
        err = assert_error(client.post(f"/api/review/{item['id']}", json=body), 422, "invalid_action", "body.action")
        assert f"not allowed for a {kind} item" in err["error"]["message"]
    assert_error(client.post("/api/review/returns:99999999", json={"action": "accept"}), 404, "not_found")
    assert_error(client.post("/api/review/nonsense", json={"action": "accept"}), 404, "not_found")
    assert review(client)["total"] == len(items)  # nothing changed


def test_accept_on_an_ok_row_is_404(client, db):
    rid = return_id(db, "Kestrel%", "2026-01-31", "kestrel_factsheet_2026-01.pdf")
    assert_error(client.post(f"/api/review/returns:{rid}", json={"action": "accept"}), 404, "not_found")


# ---- conflicts -----------------------------------------------------------------------------------


def conflict_item(client):
    return item_by(review(client)["items"], kind="conflict", title="Northwind Global Macro Fund · Jan 2026 · MTD net")


def _split_candidates(item, winner_doc="northwind_monthly_jan26.pdf"):
    winner = next(c for c in item["candidates"] if c["document_name"] == winner_doc)
    others = [c for c in item["candidates"] if c["id"] != winner["id"]]
    agreeing = [c["id"] for c in others if abs(c["value"] - winner["value"]) <= 0.00005]
    losers = [c["id"] for c in others if c["id"] not in agreeing]
    return winner, agreeing, losers


def test_choose_deletes_the_losers_and_keeps_agreeing_rows(client, db):
    """Lead decision (Q2): `choose` deletes only candidates whose value differs from the
    chosen one beyond the conflict tolerance; the Feb factsheet's matching +1.92% is a
    corroborating source and is kept (status ok)."""
    item = conflict_item(client)
    winner, agreeing, losers = _split_candidates(item)
    agree_docs = {c["document_name"] for c in item["candidates"] if c["id"] in agreeing}
    assert agree_docs == {"northwind_monthly_feb26.pdf"} and len(losers) == 1  # the 1.89 flash email
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "choose", "chosen_id": 1}), 422,
                 "invalid_action", "body.chosen_id")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "choose"}), 422, "invalid_action",
                 "body.chosen_id")
    # any member's id addresses the group
    out = act(client, f"returns:{losers[-1]}", {"action": "choose", "chosen_id": winner["id"], "note": "final"})
    assert out["id"] == item["id"] and out["resolved"] is True
    changes = {(a["id"], a["change"], a["status"]) for a in out["affected"]}
    assert (winner["id"], "status_changed", "ok") in changes
    assert all((i, "status_changed", "ok") in changes for i in agreeing)
    assert all((i, "deleted", None) in changes for i in losers)
    assert q1(db, "SELECT count(*) FROM returns WHERE id = ANY(:ids)", ids=losers) == 0
    kept = rows(db, "SELECT id, status, review_reasons FROM returns WHERE id = ANY(:ids) ORDER BY id",
                ids=[winner["id"], *agreeing])
    assert [(r["status"], r["review_reasons"]) for r in kept] == [("ok", [])] * (1 + len(agreeing))
    ras = actions(db)
    assert len(ras) == 1 + len(agreeing) + len(losers) and {r["action"] for r in ras} == {"choose"}
    assert [r["new_value"] for r in ras if r["target_id"] in losers] == [{"deleted": True}] * len(losers)
    for r in ras:
        if r["target_id"] in (winner["id"], *agreeing):
            assert r["old_value"] == r["new_value"] == {"value": 0.0192}
    assert item["id"] not in [i["id"] for i in review(client)["items"]]
    assert q1(db, "SELECT count(*) FROM v_returns WHERE return_id = ANY(:ids)", ids=losers) == 0
    assert doc_status(db, "northwind_monthly_feb26.pdf") in ("done", "needs_review")


def test_choose_the_odd_one_out_deletes_every_disagreeing_row(client, db):
    item = conflict_item(client)
    flash = next(c for c in item["candidates"] if c["document_name"] == "northwind_flash_update_jan2026.eml")
    others = [c["id"] for c in item["candidates"] if c["id"] != flash["id"]]
    out = act(client, item["id"], {"action": "choose", "chosen_id": flash["id"]})
    assert out["resolved"] is True
    assert q1(db, "SELECT count(*) FROM returns WHERE id = ANY(:ids)", ids=others) == 0
    assert q1(db, "SELECT status FROM returns WHERE id = :i", i=flash["id"]) == "ok"


def test_choose_keeps_agreeing_row_across_reprocessing(client, db, tmp_path):
    item = conflict_item(client)
    winner, agreeing, _ = _split_candidates(item)
    act(client, item["id"], {"action": "choose", "chosen_id": winner["id"]})
    for name in ("northwind_monthly_feb26.pdf", "northwind_flash_update_jan2026.eml", "northwind_monthly_jan26.pdf"):
        reprocess(client, db, tmp_path, name)
    r = rows(db, """SELECT document_name, value, status FROM v_returns WHERE fund_name = 'Northwind Global Macro Fund'
                    AND period_end = '2026-01-31' AND return_type = 'MTD' AND net_gross = 'net'
                    ORDER BY document_name""")
    assert [(x["document_name"], float(x["value"]), x["status"]) for x in r] == [
        ("northwind_monthly_feb26.pdf", 0.0192, "ok"), ("northwind_monthly_jan26.pdf", 0.0192, "ok")]
    titles = [i["title"] for i in review(client, "&kind=conflict")["items"]]
    assert "Northwind Global Macro Fund · Jan 2026 · MTD net" not in titles


def test_edit_conflict_needs_chosen_id(client, db):
    item = conflict_item(client)
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "edit", "value_pct": 1.9}), 422,
                 "invalid_action", "body.chosen_id")
    chosen = item["candidates"][0]["id"]
    out = act(client, item["id"], {"action": "edit", "value_pct": 1.9, "chosen_id": chosen})
    assert out["resolved"] is True
    assert float(q1(db, "SELECT value FROM returns WHERE id = :i", i=chosen)) == 0.019
    others = [c["id"] for c in item["candidates"] if c["id"] != chosen]
    assert q1(db, "SELECT count(*) FROM returns WHERE id = ANY(:ids)", ids=others) == 0
    ras = actions(db)
    assert {r["action"] for r in ras} == {"edit"}
    assert sum(1 for r in ras if r["new_value"] == {"deleted": True}) == len(others)


def test_choose_persists_across_reprocessing(client, db, tmp_path):
    item = conflict_item(client)
    winner = next(c for c in item["candidates"] if c["document_name"] == "northwind_monthly_jan26.pdf")
    act(client, item["id"], {"action": "choose", "chosen_id": winner["id"]})
    out = reprocess(client, db, tmp_path, "northwind_flash_update_jan2026.eml")
    assert list(out) == ["northwind_flash_update_jan2026.eml"]
    # the flash email's January MTD was the losing value: deleted again, conflict not back
    flash_jan = q1(db, """SELECT count(*) FROM v_returns WHERE document_name = 'northwind_flash_update_jan2026.eml'
                          AND period_end = '2026-01-31' AND return_type = 'MTD'""")
    assert flash_jan == 0
    titles = [i["title"] for i in review(client, "&kind=conflict")["items"]]
    assert "Northwind Global Macro Fund · Jan 2026 · MTD net" not in titles


def test_edit_persists_across_reprocessing(client, db, tmp_path):
    item = item_by(review(client)["items"], review_reasons=["ytd_mismatch"])
    act(client, item["id"], {"action": "edit", "value_pct": -0.15})
    reprocess(client, db, tmp_path, "meridian_rv_tearsheet_2026-02.pdf")
    r = rows(db, """SELECT value, status FROM v_returns WHERE document_name = 'meridian_rv_tearsheet_2026-02.pdf'
                    AND return_type = 'YTD'""")
    assert [(float(x["value"]), x["status"]) for x in r] == [(-0.0015, "ok")]
    assert doc_status(db, "meridian_rv_tearsheet_2026-02.pdf") == "done"


# ---- aliases ---------------------------------------------------------------------------------------


def alias_item(client, alias):
    return item_by(review(client, "&kind=alias")["items"], alias=lambda a: a["alias"] == alias)


def test_confirm_alias(client, db):
    item = alias_item(client, "Alder Asia Fund SPC – Class A")
    linked = q1(db, "SELECT count(*) FROM returns WHERE fund_alias_id = :a", a=item["target_id"])
    out = act(client, item["id"], {"action": "confirm_alias"})
    assert out["resolved"] is True and out["affected"][0]["change"] == "alias_confirmed"
    a = rows(db, "SELECT confirmed, match_method, fund_id FROM fund_aliases WHERE id = :a", a=item["target_id"])[0]
    assert a["confirmed"] is True and a["match_method"] == "fuzzy" and a["fund_id"] == item["alias"]["suggested_id"]
    assert q1(db, "SELECT count(*) FROM returns WHERE fund_alias_id = :a AND fund_id = :f", a=item["target_id"],
              f=item["alias"]["suggested_id"]) == linked
    (ra,) = actions(db)
    assert ra["target_table"] == "fund_aliases" and ra["action"] == "confirm_alias"
    assert ra["natural_key"] == {"entity": "fund", "alias": "alder asia fund spc – class a"}
    assert doc_status(db, "alder_asia_fund_march2026.pdf") == "done"
    funds = client.get("/api/funds").json()["items"]
    assert next(f for f in funds if f["fund_name"] == "Alder Asia Fund")["has_unconfirmed_alias"] is False


def test_reject_alias_splits_the_fund(client, db, tmp_path):
    item = alias_item(client, "Alder Asia Fund SPC – Class A")
    old_fund = item["alias"]["suggested_id"]
    moved = rows(db, "SELECT id FROM returns WHERE fund_alias_id = :a ORDER BY id", a=item["target_id"])
    assert moved
    out = act(client, item["id"], {"action": "reject_alias", "reassign_to_id": None})
    created = [a for a in out["affected"] if a["change"] == "created"]
    assert len(created) == 1 and created[0]["table"] == "funds"
    new_fund = created[0]["id"]
    assert q1(db, "SELECT canonical_name FROM funds WHERE id = :f", f=new_fund) == "Alder Asia Fund SPC – Class A"
    a = rows(db, "SELECT fund_id, match_method, confirmed, match_score FROM fund_aliases WHERE id = :a",
             a=item["target_id"])[0]
    assert (a["fund_id"], a["match_method"], a["confirmed"], a["match_score"]) == (new_fund, "manual", True, None)
    assert q1(db, "SELECT count(*) FROM returns WHERE id = ANY(:ids) AND fund_id = :f",
              ids=[m["id"] for m in moved], f=new_fund) == len(moved)
    relinked = {x["id"] for x in out["affected"] if x["change"] == "relinked" and x["table"] == "returns"}
    assert relinked == {m["id"] for m in moved}
    funds = {f["fund_name"]: f for f in client.get("/api/funds").json()["items"]}
    assert "Alder Asia Fund SPC – Class A" in funds and funds["Alder Asia Fund"]["fund_id"] == old_fund
    (ra,) = actions(db)
    assert ra["old_value"]["fund_id"] == old_fund and ra["new_value"] == {"fund_id": new_fund, "confirmed": True,
                                                                          "match_method": "manual"}
    # persistence: reprocessing the March factsheet keeps its values on the new fund, no new suggestion
    reprocess(client, db, tmp_path, "alder_asia_fund_march2026.pdf")
    assert q1(db, """SELECT count(DISTINCT fund_id) FROM v_returns
                     WHERE document_name = 'alder_asia_fund_march2026.pdf'""") == 1
    assert q1(db, "SELECT DISTINCT fund_id FROM v_returns WHERE document_name = 'alder_asia_fund_march2026.pdf'") \
        == new_fund
    assert "Alder Asia Fund SPC – Class A" not in [i["alias"]["alias"] for i in review(client, "&kind=alias")["items"]]


def test_reject_alias_to_existing_fund(client, db):
    item = alias_item(client, "Northwind Macro Fund Ltd")
    kestrel = q1(db, "SELECT id FROM funds WHERE canonical_name = 'Kestrel Quant Equity Fund LP'")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "reject_alias", "reassign_to_id": 999999}),
                 422, "invalid_action", "body.reassign_to_id")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "reject_alias",
                                                                 "reassign_to_id": item["alias"]["suggested_id"]}),
                 422, "invalid_action", "body.reassign_to_id")
    out = act(client, item["id"], {"action": "reject_alias", "reassign_to_id": kestrel})
    assert not [a for a in out["affected"] if a["change"] == "created"]
    assert q1(db, "SELECT fund_id FROM fund_aliases WHERE id = :a", a=item["target_id"]) == kestrel
    assert q1(db, "SELECT count(*) FROM returns WHERE fund_alias_id = :a AND fund_id <> :k", a=item["target_id"],
              k=kestrel) == 0
    # moving the flash email's rows away from Northwind clears the Northwind conflict they were part of
    assert out["affected"] and any(a["change"] == "relinked" for a in out["affected"])


def test_note_too_long_and_extra_fields(client):
    item = item_by(review(client)["items"], review_reasons=["ytd_mismatch"])
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "accept", "note": "x" * 2001}), 422,
                 "validation_error", "body.note")
    assert_error(client.post(f"/api/review/{item['id']}", json={"action": "accept", "chosen_id": 1}), 422,
                 "validation_error", "body.chosen_id")


def test_flagged_holding_and_fact_items(client, db):
    """accept/edit also work on fund_facts and statement_holdings rows."""
    hid = q1(db, "SELECT min(id) FROM statement_holdings")
    fid = q1(db, "SELECT min(id) FROM fund_facts WHERE unit = 'USD'")
    detail = '{"low_confidence": {"confidence": 0.5, "threshold": 0.7}}'
    for table, i in (("statement_holdings", hid), ("fund_facts", fid)):
        exec_sql(db, f"UPDATE {table} SET status = 'needs_review', review_reasons = ARRAY['low_confidence'], "
                     f"review_detail = CAST(:d AS jsonb) WHERE id = :i", d=detail, i=i)
    items = review(client)["items"]
    h = item_by(items, id=f"statement_holdings:{hid}")
    assert h["current"]["field"] == "market_value" and h["subject"]["holding_name"]
    out = act(client, h["id"], {"action": "edit", "field": "weight", "value_pct": 30})
    assert float(q1(db, "SELECT weight FROM statement_holdings WHERE id = :i", i=hid)) == 0.3 and out["resolved"]
    f = item_by(items, id=f"fund_facts:{fid}")
    assert f["current"]["value_kind"] == "money"
    assert_error(client.post(f"/api/review/{f['id']}", json={"action": "edit", "value_pct": 1}), 422,
                 "invalid_action", "body.value")
    act(client, f["id"], {"action": "edit", "value": 1000000})
    assert float(q1(db, "SELECT value FROM fund_facts WHERE id = :i", i=fid)) == 1000000
    ras = actions(db)
    assert ras[0]["natural_key"]["line_no"] == 0 and ras[0]["new_value"] == {"weight": 0.3}


def test_edit_reruns_the_ytd_check(client, db):
    """Dependent checks: editing January (-0.36% -> -0.06%) makes Meridian's stated Feb YTD
    (+0.15%) tie, so the YTD row leaves the queue and is listed in `affected`."""
    jan = return_id(db, "Meridian%", "2026-01-31", "meridian_rv_tearsheet_2026-02.pdf")
    ytd = item_by(review(client)["items"], review_reasons=["ytd_mismatch"])
    exec_sql(db, "UPDATE returns SET status = 'needs_review', review_reasons = ARRAY['low_confidence'], "
                 "review_detail = CAST(:d AS jsonb) WHERE id = :i",
             d='{"low_confidence": {"confidence": 0.5, "threshold": 0.7}}', i=jan)
    out = act(client, f"returns:{jan}", {"action": "edit", "value_pct": -0.06})
    assert {"table": "returns", "id": ytd["target_id"], "change": "status_changed", "status": "ok"} in out["affected"]
    assert q1(db, "SELECT status FROM returns WHERE id = :i", i=ytd["target_id"]) == "ok"
    assert ytd["id"] not in [i["id"] for i in review(client)["items"]]
    # ...and the conflict check re-ran too: -0.06% now disagrees with the January tear sheet
    # and the CSV (-0.36%), so the edited row is flagged again and the item is not resolved.
    assert out["resolved"] is False
    assert q1(db, "SELECT status FROM returns WHERE id = :i", i=jan) == "conflict"
    assert doc_status(db, "meridian_rv_tearsheet_2026-02.pdf") == "needs_review"
    assert doc_status(db, "meridian_rv_tearsheet_2026-01.pdf") == "needs_review"  # other document refreshed

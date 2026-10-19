"""FR-9 data endpoints over the synthetic sample set (read-only, one ingest per module):
happy paths validated against app/schemas, C4 filters, C3 sort/paging edge cases, C5 CSV."""

from __future__ import annotations

import csv
import io

import pytest

from app.schemas import (
    AccountsList,
    FundFactItem,
    FundFactsPage,
    FundsList,
    HoldingItem,
    HoldingsPage,
    ReturnItem,
    ReturnsPage,
    ReturnsQuery,
    StatementItem,
    StatementsPage,
)
from tests.api_helpers import api_client, assert_error, exec_sql, ingest_into, q1, truncate, validate


@pytest.fixture(scope="module")
def engine(test_engine, tmp_path_factory):
    ingest_into(test_engine, tmp_path_factory.mktemp("samples"))
    # One return with no value (unparseable) so NULL ordering is observable.
    exec_sql(test_engine, """UPDATE returns SET value = NULL, confidence = NULL WHERE id = (
        SELECT r.id FROM returns r JOIN documents d ON d.id = r.document_id
        WHERE d.name = 'harbor_admin_nav_export_2026-03.csv' ORDER BY r.id LIMIT 1)""")
    yield test_engine
    truncate(test_engine)


@pytest.fixture(scope="module")
def client(engine):
    with api_client() as c:
        yield c


def get(client, url, model=None):
    r = client.get(url)
    assert r.status_code == 200, r.text
    body = r.json()
    if model is not None:
        validate(model, body)
    return body


def all_returns(client, params=""):
    return get(client, f"/api/returns?page_size=500{params}", ReturnsPage)


def fund_id(client, name):
    return next(f["fund_id"] for f in get(client, "/api/funds")["items"] if f["fund_name"] == name)


# ---- returns ---------------------------------------------------------------------------


def test_returns_kestrel_january(client):
    body = get(client, "/api/returns?date_from=2026-01-01&date_to=2026-01-31&return_type=MTD&net_gross=net"
                       "&sort=-value_pct&page_size=3", ReturnsPage)
    top = body["items"][0]
    assert (top["fund_name"], top["value_pct"], top["value"]) == ("Kestrel Quant Equity Fund LP", 2.84, 0.0284)
    assert top["document_name"] == "kestrel_factsheet_2026-01.pdf" and top["status"] == "ok"
    assert body["sort"] == "-value_pct" and body["page_size"] == 3 and body["total"] > 3
    assert list(top) == list(ReturnItem.model_fields)  # every field, in contract order


def test_returns_default_sort_and_shape(client):
    body = get(client, "/api/returns", ReturnsPage)
    assert body["sort"] == "-period_end,fund_name" and body["page"] == 1 and body["page_size"] == 50
    ends = [i["period_end"] for i in body["items"]]
    assert ends == sorted(ends, reverse=True)


def test_repeated_keys_or_within_and_across(client):
    kestrel, northwind = fund_id(client, "Kestrel Quant Equity Fund LP"), fund_id(client, "Northwind Global Macro Fund")
    both = all_returns(client, f"&fund_id={kestrel}&fund_id={northwind}")
    assert {i["fund_id"] for i in both["items"]} == {kestrel, northwind}
    only_k = all_returns(client, f"&fund_id={kestrel}")["total"]
    only_n = all_returns(client, f"&fund_id={northwind}")["total"]
    assert both["total"] == only_k + only_n
    anded = all_returns(client, f"&fund_id={kestrel}&fund_id={northwind}&return_type=YTD")
    assert anded["total"] < both["total"]
    assert {i["return_type"] for i in anded["items"]} == {"YTD"}


def test_status_and_reason_filters(client):
    conflicts = all_returns(client, "&status=conflict")
    assert conflicts["total"] >= 2
    assert {i["fund_name"] for i in conflicts["items"]} == {"Northwind Global Macro Fund"}
    assert all("conflict" in i["review_reasons"] for i in conflicts["items"])
    ytd = all_returns(client, "&review_reason=ytd_mismatch")
    assert [i["fund_name"] for i in ytd["items"]] == ["Meridian Rates Relative Value Fund"]
    either = all_returns(client, "&review_reason=ytd_mismatch&review_reason=conflict")
    assert either["total"] == ytd["total"] + conflicts["total"]
    flagged = all_returns(client, "&status=conflict&status=needs_review")["total"]
    ok = all_returns(client, "&status=ok")["total"]
    assert flagged + ok == all_returns(client)["total"]


def test_document_filters_and_date_range_inclusive(client):
    emails = all_returns(client, "&document_type=email")
    assert emails["total"] >= 1 and {i["document_type"] for i in emails["items"]} == {"email"}
    doc = emails["items"][0]["document_id"]
    assert {i["document_id"] for i in all_returns(client, f"&document_id={doc}")["items"]} == {doc}
    jan = all_returns(client, "&date_from=2026-01-31&date_to=2026-01-31")
    assert jan["total"] > 0 and {i["period_end"] for i in jan["items"]} == {"2026-01-31"}
    after = all_returns(client, "&date_from=2026-03-01")
    assert all(i["period_end"] >= "2026-03-01" for i in after["items"])


@pytest.mark.parametrize("col", ReturnsQuery.SORTABLE)
def test_every_sort_column_orders_with_nulls_last_and_id_tiebreak(client, col):
    for desc in (False, True):
        items = all_returns(client, f"&sort={'-' if desc else ''}{col}")["items"]
        values = [i[col] for i in items]
        present = [v for v in values if v is not None]
        assert values[: len(present)] == present, "NULLs must sort last"
        assert present == sorted(present, reverse=desc)
        # ties are broken by return_id ascending
        for a, b in zip(items, items[1:]):
            if a[col] == b[col]:
                assert a["return_id"] < b["return_id"]


def test_nulls_last_on_value(client):
    asc = all_returns(client, "&sort=value_pct")["items"]
    desc = all_returns(client, "&sort=-value_pct")["items"]
    assert asc[-1]["value_pct"] is None and desc[-1]["value_pct"] is None
    assert asc[0]["value_pct"] is not None and desc[0]["value_pct"] is not None


def test_multi_key_sort(client):
    items = all_returns(client, "&sort=fund_name,-period_end")["items"]
    keys = [(i["fund_name"], i["period_end"]) for i in items]
    for (fa, pa), (fb, pb) in zip(keys, keys[1:]):
        assert fa < fb or (fa == fb and pa >= pb)


def test_pagination_is_stable_and_complete(client):
    total = all_returns(client)["total"]
    seen = []
    page = 1
    while True:
        body = get(client, f"/api/returns?page_size=7&page={page}&sort=fund_name", ReturnsPage)
        assert body["total"] == total
        if not body["items"]:
            break
        seen += [i["return_id"] for i in body["items"]]
        page += 1
    assert len(seen) == total and len(set(seen)) == total
    assert seen == [i["return_id"] for i in all_returns(client, "&sort=fund_name")["items"]]
    past = get(client, "/api/returns?page=999", ReturnsPage)
    assert past["items"] == [] and past["total"] == total and past["page"] == 999


def test_returns_csv(client):
    r = client.get("/api/returns?format=csv&sort=-value_pct&page_size=2&page=3&status=ok")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="returns_')
    assert not r.content.startswith(b"\xef\xbb\xbf")  # no BOM
    assert b"\r\n" in r.content
    parsed = list(csv.reader(io.StringIO(r.text)))
    assert parsed[0] == list(ReturnItem.model_fields)
    rows = parsed[1:]
    json_items = all_returns(client, "&sort=-value_pct&status=ok")["items"]
    assert len(rows) == len(json_items)  # all pages; page/page_size ignored
    assert [int(x[0]) for x in rows] == [i["return_id"] for i in json_items]
    first = dict(zip(parsed[0], rows[0]))
    assert first["review_reasons"] == "" and float(first["value_pct"]) == json_items[0]["value_pct"]
    assert float(first["value"]) == json_items[0]["value"]


def test_csv_list_cells_and_nulls(client):
    r = client.get("/api/returns?format=csv&status=conflict")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows and all(row["review_reasons"] == "conflict" for row in rows)
    r = client.get("/api/statements?format=csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3
    acme = next(x for x in rows if x["account_name"] == "Acme Family Office LLC")
    assert acme["flagged_fields"] == "closing_balance" and acme["fund_id"] == ""


def test_csv_errors_are_json(client):
    assert_error(client.get("/api/returns?format=csv&sort=nope"), 422, "validation_error", "query.sort")


# ---- fund facts --------------------------------------------------------------------------


def test_fund_facts(client):
    body = get(client, "/api/fund-facts?page_size=500", FundFactsPage)
    assert body["sort"] == "-as_of_date,fund_name,metric" and body["total"] > 0
    for i in body["items"]:
        if i["unit"] == "fraction" and i["value"] is not None:
            assert i["value_pct"] == pytest.approx(i["value"] * 100)
        else:
            assert i["value_pct"] is None
    assert list(body["items"][0]) == list(FundFactItem.model_fields)
    aum = get(client, "/api/fund-facts?metric=aum&page_size=500", FundFactsPage)
    assert aum["total"] > 0 and {i["metric"] for i in aum["items"]} == {"aum"}


def test_fund_facts_sort_and_csv(client):
    items = get(client, "/api/fund-facts?sort=-value&page_size=500")["items"]
    vals = [i["value"] for i in items]
    present = [v for v in vals if v is not None]
    assert vals[: len(present)] == present == sorted(present, reverse=True)
    r = client.get("/api/fund-facts?format=csv")
    assert next(csv.reader(io.StringIO(r.text))) == list(FundFactItem.model_fields)


# ---- statements / holdings ---------------------------------------------------------------


def test_statements(client):
    body = get(client, "/api/statements", StatementsPage)
    assert body["total"] == 3 and body["sort"] == "-period_end,account_name"
    by_name = {i["account_name"]: i for i in body["items"]}
    assert set(by_name) == {"Acme Family Office LLC", "Birchwood Foundation", "Cedar Ridge Partners LP"}
    acme = by_name["Acme Family Office LLC"]
    assert acme["status"] == "needs_review" and acme["review_reasons"] == ["rollforward_mismatch"]
    assert acme["flagged_fields"] == ["closing_balance"] and acme["closing_balance"] == 13100000.0
    assert acme["holdings_count"] == 5
    cedar = by_name["Cedar Ridge Partners LP"]
    assert cedar["fund_name"] == "Kestrel Quant Equity Fund LP"
    assert list(acme) == list(StatementItem.model_fields)


def test_statement_filters(client):
    body = get(client, "/api/statements")
    acme = next(i for i in body["items"] if i["account_name"].startswith("Acme"))
    one = get(client, f"/api/statements?account_id={acme['account_id']}", StatementsPage)
    assert [i["statement_id"] for i in one["items"]] == [acme["statement_id"]]
    kestrel = fund_id(client, "Kestrel Quant Equity Fund LP")
    linked = get(client, f"/api/statements?fund_id={kestrel}", StatementsPage)
    assert [i["account_name"] for i in linked["items"]] == ["Cedar Ridge Partners LP"]
    flagged = get(client, "/api/statements?review_reason=rollforward_mismatch", StatementsPage)
    assert [i["account_name"] for i in flagged["items"]] == ["Acme Family Office LLC"]
    assert get(client, "/api/statements?date_from=2026-02-01&date_to=2026-02-28")["total"] == 1


def test_statement_money_sort(client):
    items = get(client, "/api/statements?sort=-closing_balance")["items"]
    assert [i["closing_balance"] for i in items] == sorted((i["closing_balance"] for i in items), reverse=True)


def test_holdings(client):
    acme = next(i for i in get(client, "/api/statements")["items"] if i["account_name"].startswith("Acme"))
    body = get(client, f"/api/statements/{acme['statement_id']}/holdings", HoldingsPage)
    assert body["total"] == 5 and body["sort"] == "line_no"
    assert [i["line_no"] for i in body["items"]] == list(range(5))
    for i in body["items"]:
        assert i["statement_id"] == acme["statement_id"]
        if i["weight"] is not None:
            assert i["weight_pct"] == pytest.approx(i["weight"] * 100)
    assert list(body["items"][0]) == list(HoldingItem.model_fields)
    paged = get(client, f"/api/statements/{acme['statement_id']}/holdings?page_size=2&page=3&sort=-market_value")
    assert len(paged["items"]) == 1 and paged["total"] == 5
    r = client.get(f"/api/statements/{acme['statement_id']}/holdings?format=csv")
    assert len(list(csv.reader(io.StringIO(r.text)))) == 6


def test_holdings_empty_and_missing(client):
    cedar = next(i for i in get(client, "/api/statements")["items"] if i["account_name"].startswith("Cedar"))
    assert get(client, f"/api/statements/{cedar['statement_id']}/holdings", HoldingsPage)["items"] == []
    assert_error(client.get("/api/statements/999999/holdings"), 404, "not_found")
    assert_error(client.get("/api/statements/1/holdings?sort=nope"), 422, "validation_error", "query.sort")


# ---- funds / accounts ----------------------------------------------------------------------


def test_funds(client, engine):
    body = get(client, "/api/funds", FundsList)
    names = [f["fund_name"] for f in body["items"]]
    assert names == sorted(names, key=str.lower) and body["total"] == len(names) == 6
    asia = next(f for f in body["items"] if f["fund_name"] == "Alder Asia Fund")
    assert asia["has_unconfirmed_alias"] is True
    fuzzy = [a for a in asia["aliases"] if a["match_method"] == "fuzzy"]
    assert [a["alias"] for a in fuzzy] == ["Alder Asia Fund SPC – Class A"]
    assert fuzzy[0]["confirmed"] is False and fuzzy[0]["match_score"] is not None
    assert fuzzy[0]["source_document_name"] == "alder_asia_fund_march2026.pdf"
    assert all(a["confirmed"] for a in asia["aliases"] if a["match_method"] in ("new", "exact"))
    dubai = next(f for f in body["items"] if f["fund_name"] == "Alder Dubai Fund")
    assert dubai["has_unconfirmed_alias"] is False  # siblings stay separate
    for f in body["items"]:
        assert f["return_count"] == q1(engine, "SELECT count(*) FROM v_returns WHERE fund_id = :f", f=f["fund_id"])
        assert f["latest_period_end"] is not None and f["document_count"] >= 1


def test_funds_without_visible_values_are_hidden(client, engine):
    exec_sql(engine, "INSERT INTO funds (canonical_name) VALUES ('Orphan Fund No Values')")
    try:
        assert "Orphan Fund No Values" not in [f["fund_name"] for f in get(client, "/api/funds")["items"]]
    finally:
        exec_sql(engine, "DELETE FROM funds WHERE canonical_name = 'Orphan Fund No Values'")


def test_accounts(client):
    body = get(client, "/api/accounts", AccountsList)
    assert [a["account_name"] for a in body["items"]] == [
        "Acme Family Office LLC", "Birchwood Foundation", "Cedar Ridge Partners LP"]
    acme = body["items"][0]
    assert acme["statement_count"] == 1 and acme["latest_period_end"] == "2026-01-31"
    assert acme["aliases"][0]["match_method"] == "new" and acme["has_unconfirmed_alias"] is False

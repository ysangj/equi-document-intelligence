"""GET /api/sources/{table}/{id} (FR-9 side panel; §15 every value links to a document,
page and snippet)."""

from __future__ import annotations

import json

import pytest

from app.schemas import SourceResponse
from app.schemas.data import SOURCE_FIELDS
from tests.api_helpers import api_client, assert_error, exec_sql, ingest_into, q1, return_id, truncate, validate


@pytest.fixture(scope="module")
def engine(test_engine, tmp_path_factory):
    ingest_into(test_engine, tmp_path_factory.mktemp("samples"))
    yield test_engine
    truncate(test_engine)


@pytest.fixture(scope="module")
def client(engine):
    with api_client() as c:
        yield c


def src(client, path):
    r = client.get(f"/api/sources/{path}")
    assert r.status_code == 200, r.text
    validate(SourceResponse, r.json())
    return r.json()


def test_return_source(client, engine):
    rid = return_id(engine, "Kestrel%", "2026-01-31", "kestrel_factsheet_2026-01.pdf")
    body = src(client, f"returns/{rid}")
    assert body["label"] == "Kestrel Quant Equity Fund LP · Jan 2026 · MTD net"
    assert (body["field"], body["field_label"], body["value_kind"]) == ("value", "Return", "percent")
    assert (body["value"], body["value_pct"], body["status"]) == (0.0284, 2.84, "ok")
    p = body["provenance"]
    assert p["page"] >= 1 and p["source_snippet"] and 0 < p["confidence"] <= 1 and p["snippet_verified"] is True
    d = body["document"]
    assert d["document_name"] == "kestrel_factsheet_2026-01.pdf" and d["source"] == "local"
    assert d["web_view_link"] is None and d["mime_type"] == "application/pdf" and d["version"] == 1
    assert body["extraction"]["source"] == "fixture" and body["extraction"]["model"] == "claude-opus-5-5"
    assert body["extraction"]["extraction_id"] is not None and body["extraction"]["prompt_version"]
    hl = body["snippet_highlight"]
    assert hl is not None and p["source_snippet"][hl["start"]:hl["end"]] == hl["text"] and "2.84" in hl["text"]
    assert body["conflicts"] == [] and body["review_history"] == []


def test_fund_fact_source(client, engine):
    fid = q1(engine, "SELECT fact_id FROM v_fund_facts WHERE metric = 'aum' AND unit = 'USD' ORDER BY fact_id LIMIT 1")
    body = src(client, f"fund_facts/{fid}")
    assert body["value_kind"] == "money" and body["currency"] == "USD" and body["field_label"] == "AUM"
    assert body["provenance"]["source_snippet"]
    pct = q1(engine, "SELECT fact_id FROM v_fund_facts WHERE unit = 'fraction' ORDER BY fact_id LIMIT 1")
    if pct:
        b = src(client, f"fund_facts/{pct}")
        assert b["value_kind"] == "percent" and b["value_pct"] == pytest.approx(b["value"] * 100)


def test_statement_fields(client, engine):
    sid = q1(engine, "SELECT statement_id FROM v_statements WHERE account_name LIKE 'Acme%'")
    default = src(client, f"statements/{sid}")
    assert default["field"] == "closing_balance" and default["value"] == 13100000.0
    assert default["value_kind"] == "money" and default["currency"] == "USD"
    assert default["label"] == "Acme Family Office LLC · XXXX-4471 · Jan 2026"
    assert default["reasons"][0]["code"] == "rollforward_mismatch"
    assert "13,080,000.00" in default["reasons"][0]["message"] and "13,100,000.00" in default["reasons"][0]["message"]
    kinds = {}
    for f in SOURCE_FIELDS["statements"]:
        b = src(client, f"statements/{sid}?field={f}")
        kinds[f] = b["value_kind"]
        assert b["field"] == f
    assert kinds["opening_balance"] == "money" and kinds["period_end"] == "date" and kinds["account_name"] == "text"
    assert src(client, f"statements/{sid}?field=period_end")["value"] == "2026-01-31"
    cust = src(client, f"statements/{sid}?field=custodian")
    assert cust["value"] == "Larkspur Trust Company" and cust["provenance"]["source_snippet"]  # custodian_or_manager


def test_statement_field_never_extracted_is_null(client, engine):
    sid = q1(engine, "SELECT statement_id FROM v_statements WHERE account_name LIKE 'Birchwood%'")
    exec_sql(engine, "UPDATE statements SET provenance = provenance - 'fund_name' WHERE id = :s", s=sid)
    body = src(client, f"statements/{sid}?field=fund_name")
    assert body["value"] is None and body["snippet_highlight"] is None
    assert body["provenance"] == {"page": None, "source_snippet": None, "confidence": None, "snippet_verified": None}


def test_holding_fields(client, engine):
    hid = q1(engine, "SELECT holding_id FROM v_holdings WHERE weight IS NOT NULL ORDER BY holding_id LIMIT 1")
    w = src(client, f"statement_holdings/{hid}?field=weight")
    assert w["value_kind"] == "percent" and w["value_pct"] == pytest.approx(w["value"] * 100)
    mv = src(client, f"statement_holdings/{hid}")
    assert mv["field"] == "market_value" and mv["value_kind"] == "money"
    name = src(client, f"statement_holdings/{hid}?field=holding_name")
    assert name["value_kind"] == "text" and name["field_label"] == "Holding"


def test_conflict_source_lists_the_other_rows(client, engine):
    rid = return_id(engine, "Northwind%", "2026-01-31", "northwind_monthly_jan26.pdf")
    body = src(client, f"returns/{rid}")
    assert body["status"] == "conflict"
    others = {c["document_name"] for c in body["conflicts"]}
    assert "northwind_flash_update_jan2026.eml" in others and rid not in [c["id"] for c in body["conflicts"]]
    assert all(c["provenance"]["source_snippet"] for c in body["conflicts"])
    assert "+1.89%" in body["reasons"][0]["message"] and "+1.92%" in body["reasons"][0]["message"]


def test_scanned_pdf_snippet_check_is_skipped(client, engine):
    rid = q1(engine, """SELECT r.id FROM returns r JOIN documents d ON d.id = r.document_id
                        WHERE d.name = 'northwind_dec25_scanned.pdf' ORDER BY r.id LIMIT 1""")
    assert src(client, f"returns/{rid}")["provenance"]["snippet_verified"] is None


def test_every_value_has_document_page_and_snippet(client, engine):
    """§15: sweep every visible value through /api/sources."""
    paths = [f"returns/{i}" for (i,) in _ids(engine, "SELECT return_id FROM v_returns")]
    paths += [f"fund_facts/{i}" for (i,) in _ids(engine, "SELECT fact_id FROM v_fund_facts")]
    for (i,) in _ids(engine, "SELECT statement_id FROM v_statements"):
        paths += [f"statements/{i}?field={f}" for f in SOURCE_FIELDS["statements"][:6]]
    for (i,) in _ids(engine, "SELECT holding_id FROM v_holdings"):
        paths += [f"statement_holdings/{i}?field={f}" for f in SOURCE_FIELDS["statement_holdings"]]
    missing = []
    for p in paths:
        b = src(client, p)
        if b["value"] is not None and not (b["provenance"]["page"] and b["provenance"]["source_snippet"]
                                           and b["document"]["document_name"]):
            missing.append(p)
    assert len(paths) > 150 and missing == []


def _ids(engine, sql):
    with engine.connect() as c:
        from sqlalchemy import text

        return list(c.execute(text(sql)))


def test_errors(client, engine):
    rid = q1(engine, "SELECT min(return_id) FROM v_returns")
    assert_error(client.get("/api/sources/bogus/1"), 422, "validation_error", "path.table")
    assert_error(client.get(f"/api/sources/returns/{rid}?field=closing_balance"), 422, "validation_error",
                 "query.field")
    assert_error(client.get("/api/sources/statements/1?field=nope"), 422, "validation_error", "query.field")
    assert_error(client.get(f"/api/sources/returns/{rid}?fields=x"), 422, "validation_error", "query.fields")
    assert_error(client.get("/api/sources/returns/99999999"), 404, "not_found")
    assert_error(client.get("/api/sources/returns/0"), 422, "validation_error", "path.row_id")


# ---- visibility and review history (mutating: own dataset) -------------------------------------


@pytest.fixture
def fresh(test_engine, tmp_path):
    ingest_into(test_engine, tmp_path)
    yield test_engine
    truncate(test_engine)


def test_rows_of_removed_or_superseded_documents_are_404(fresh):
    rid = return_id(fresh, "Kestrel%", "2026-01-31", "kestrel_factsheet_2026-01.pdf")
    with api_client() as c:
        assert c.get(f"/api/sources/returns/{rid}").status_code == 200
        exec_sql(fresh, "UPDATE documents SET is_latest = false WHERE name = 'kestrel_factsheet_2026-01.pdf'")
        assert_error(c.get(f"/api/sources/returns/{rid}"), 404, "not_found")
        assert rid not in [i["return_id"] for i in c.get("/api/returns?page_size=500").json()["items"]]
        exec_sql(fresh, "UPDATE documents SET is_latest = true, status = 'removed' "
                        "WHERE name = 'kestrel_factsheet_2026-01.pdf'")
        assert_error(c.get(f"/api/sources/returns/{rid}"), 404, "not_found")


def test_review_history_survives_reprocessing(fresh, tmp_path):
    from app.extraction.fixture import FixtureExtractor
    from tests.pipeline_helpers import drain

    sid = q1(fresh, "SELECT statement_id FROM v_statements WHERE account_name LIKE 'Acme%'")
    with api_client() as c:
        r = c.post(f"/api/review/statements:{sid}", json={"action": "accept", "note": "checked"})
        assert r.status_code == 200, r.text
        hist = src(c, f"statements/{sid}")["review_history"]
        assert [h["action"] for h in hist] == ["accept"] and hist[0]["note"] == "checked"
        assert hist[0]["old_value"]["closing_balance"] == 13100000.0
        doc = q1(fresh, "SELECT document_id FROM v_statements WHERE statement_id = :s", s=sid)
        assert c.post(f"/api/documents/{doc}/reprocess").status_code == 202
        drain(fresh, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))
        new_sid = q1(fresh, "SELECT statement_id FROM v_statements WHERE account_name LIKE 'Acme%'")
        assert new_sid != sid
        body = src(c, f"statements/{new_sid}")
        assert body["status"] == "ok"  # accept re-applied: values unchanged
        assert [h["review_action_id"] for h in body["review_history"]] == [h["review_action_id"] for h in hist]


def test_natural_key_matches_t5_shape(fresh):
    from app.api.sources import row_natural_key
    from app.api.values import load_rows

    hid = q1(fresh, "SELECT min(holding_id) FROM v_holdings")
    with api_client():
        from sqlalchemy.orm import Session

        with Session(fresh) as s:
            row = load_rows(s, "statement_holdings", [hid])[hid]
    key = row_natural_key("statement_holdings", row)
    assert set(key) == {"account_id", "fund_id", "period_end", "line_no"}
    json.dumps(key)

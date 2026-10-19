"""§2 / FR-9 over the committed real fixtures (Q2 regressions):
- BUG-Q2-05: `snippet_highlight` is set for CSV values (Harbor NAV per share 1225.49).
- BUG-Q2-06: Birchwood holding weights are traceable (the row snippet quotes the
  weight), and no fixture value is flagged `value_missing`.
- Every numeric value with a verified snippet gets a highlight."""

from __future__ import annotations

import pytest

from app.extraction.fixture import FIXTURES_DIR, FixtureExtractor
from app.ingest import postprocess
from app.schemas import SourceResponse
from app.schemas.data import SOURCE_FIELDS
from tests.api_helpers import api_client, q1, rows, truncate, validate
from tests.pipeline_helpers import FIXED_TODAY, copy_samples, drain, sync_folder

NUMERIC = ("percent", "money", "number")


@pytest.fixture(scope="module")
def engine(test_engine, tmp_path_factory):
    if not FIXTURES_DIR.is_dir() or len(list(FIXTURES_DIR.glob("*.json"))) < 21:
        pytest.skip("fixtures/ not generated yet")
    truncate(test_engine)
    docs = copy_samples(tmp_path_factory.mktemp("real") / "docs")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(postprocess, "_today", lambda: FIXED_TODAY)
        sync_folder(test_engine, docs)
        drain(test_engine, docs, FixtureExtractor(FIXTURES_DIR))
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


def test_csv_value_is_highlighted(client, engine):
    fid = q1(engine, "SELECT fact_id FROM v_fund_facts WHERE metric = 'nav_per_share' AND document_name LIKE 'harbor%'")
    body = src(client, f"fund_facts/{fid}")
    assert body["value"] == pytest.approx(1225.49)
    assert body["snippet_highlight"]["text"] == "1225.49"
    snip = body["provenance"]["source_snippet"]
    hl = body["snippet_highlight"]
    assert snip[hl["start"]:hl["end"]] == "1225.49"  # ASCII: UTF-16 offsets equal str offsets


def test_birchwood_weights_are_traceable(client, engine):
    hs = rows(engine, """SELECT holding_id, weight_pct, source_snippet, review_reasons FROM v_holdings
                         WHERE document_name LIKE 'birchwood%' ORDER BY line_no""")
    assert len(hs) == 5
    for h in hs:
        pct = f"{float(h['weight_pct']):.1f}%"
        assert pct in h["source_snippet"], h
        assert h["review_reasons"] == []
        body = src(client, f"statement_holdings/{h['holding_id']}?field=weight")
        assert body["provenance"]["snippet_verified"] is True
        assert body["snippet_highlight"]["text"] == pct
        mv = src(client, f"statement_holdings/{h['holding_id']}?field=market_value")
        assert mv["snippet_highlight"] is not None


def test_no_fixture_value_is_flagged_value_missing(engine):
    flagged = rows(engine, """SELECT item_key FROM review_queue
                              WHERE review_detail -> 'snippet_not_found' ->> 'value_missing' = 'true'""")
    assert flagged == []


def test_every_verified_numeric_value_has_a_highlight(client, engine):
    ids = {
        "returns": [r["return_id"] for r in rows(engine, "SELECT return_id FROM v_returns WHERE value IS NOT NULL")],
        "fund_facts": [r["fact_id"] for r in rows(engine, "SELECT fact_id FROM v_fund_facts WHERE value IS NOT NULL")],
        "statements": [r["statement_id"] for r in rows(engine, "SELECT statement_id FROM v_statements")],
        "statement_holdings": [r["holding_id"] for r in rows(engine, "SELECT holding_id FROM v_holdings")],
    }
    checked, missing = 0, []
    for table, row_ids in ids.items():
        for rid in row_ids:
            for field in SOURCE_FIELDS[table]:
                b = src(client, f"{table}/{rid}?field={field}")
                if b["value_kind"] not in NUMERIC or b["value"] is None or b["provenance"]["snippet_verified"] is not True:
                    continue
                checked += 1
                if b["snippet_highlight"] is None:
                    missing.append((table, rid, field, b["value"], b["provenance"]["source_snippet"]))
    # Cedar's fees (30,010) is the sum of two quoted lines (prompt rule 3): traceable, not a single token.
    assert [(m[0], m[2], m[3]) for m in missing] == [("statements", "fees", 30010.0)], missing
    assert checked > 150

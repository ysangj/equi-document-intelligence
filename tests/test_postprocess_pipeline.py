"""Integration (§14): the sample folder through the real T4 pipeline (reconcile -> claim ->
run_job with a mocked extractor -> T5 process_extraction -> post-commit indexing),
using the synthetic extractions in tests/synthetic_samples.py. Also idempotency,
versions, review-decision re-apply and embedding failure/retry."""

from __future__ import annotations

import json
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.extraction.fixture import FixtureExtractor
from app.extraction.schema import ReturnEntry
from app.ingest import postprocess
from app.ingest.decisions import natural_key
from app.models import Document, FundAlias, Return, ReviewAction, Statement
from app.schemas.review import validate_review_detail
from app.search import embedding, indexing
from tests.embedding_fakes import FakeEmbedder
from tests.ingest_helpers import write_fixture
from tests.pipeline_helpers import FIXED_TODAY, copy_samples, drain, ingest_samples, sync_folder
from tests.synthetic_samples import SAMPLE_BUILDERS, northwind_flash, write_sample_fixtures

D = Decimal

# MANIFEST ground truth (monthly net returns, %), for the months the sample documents state.
GROUND_TRUTH = {
    "Kestrel Quant Equity Fund LP": {"2026-01": "2.84", "2026-02": "-0.92", "2026-03": "1.35"},
    "Northwind Global Macro Fund": {"2025-10": "-0.30", "2025-11": "0.85", "2025-12": "1.40", "2026-02": "0.55"},
    "Meridian Rates Relative Value Fund": {"2025-10": "0.22", "2025-11": "0.31", "2025-12": "0.18",
                                           "2026-01": "-0.36", "2026-02": "0.21", "2026-03": "0.27"},
    "Alder Asia Fund": {"2026-01": "1.10", "2026-02": "0.40", "2026-03": "-1.25"},
    "Alder Dubai Fund": {"2026-01": "0.48", "2026-02": "0.52", "2026-03": "0.37"},
    "Harbor Credit Opportunities Fund": {"2025-10": "0.58", "2025-11": "0.62", "2025-12": "0.49",
                                         "2026-01": "0.71", "2026-02": "0.64", "2026-03": "0.55"},
}

EXPECTED_STATUS = {
    "acme_family_office_statement_2026-01.pdf": "needs_review",  # roll-forward mismatch
    "alder_asia_fund_january2026.pdf": "done",
    "alder_asia_fund_march2026.pdf": "needs_review",  # share-class alias suggestion
    "alder_capital_q1_2026_performance_report.pdf": "done",
    "alder_dubai_fund_january2026.pdf": "done",
    "alder_dubai_fund_march2026.pdf": "done",
    "birchwood_foundation_statement_2026-02.pdf": "done",
    "cedar_ridge_capital_account_kestrel_q4_2025.pdf": "done",
    # Canonical-name rule: 1 document each, no factsheet, so the longer "Harbor Credit
    # Opportunities Fund" (letter) is canonical and the CSV's "Opps" is the alias suggestion.
    "harbor_admin_nav_export_2026-03.csv": "needs_review",
    "harbor_credit_q4_2025_investor_letter.pdf": "done",
    "kestrel_factsheet_2026-01.pdf": "done",
    "kestrel_factsheet_2026-02.pdf": "done",
    "kestrel_factsheet_2026-03.pdf": "done",
    "meridian_pm_note_feb2026.eml": "done",
    "meridian_returns_export.csv": "needs_review",  # alias suggestion (RV vs Relative Value)
    "meridian_rv_tearsheet_2026-01.pdf": "done",
    "meridian_rv_tearsheet_2026-02.pdf": "needs_review",  # YTD mismatch
    "northwind_dec25_scanned.pdf": "done",
    "northwind_flash_update_jan2026.eml": "needs_review",  # alias + conflict
    "northwind_monthly_feb26.pdf": "needs_review",  # conflict (Jan 1.92 vs 1.89)
    "northwind_monthly_jan26.pdf": "needs_review",  # conflict
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(postprocess, "_today", lambda: FIXED_TODAY)


@pytest.fixture
def ingested(clean_db, tmp_path):
    statuses = ingest_samples(clean_db, tmp_path)
    return clean_db, statuses


def q(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).mappings().all()


def fund_id(engine, alias: str) -> int:
    with Session(engine) as s:
        return next(a.fund_id for a in s.execute(select(FundAlias)).scalars() if a.alias.lower() == alias.lower())


def doc_status(engine) -> dict[str, str]:
    return {r["name"]: r["status"] for r in q(engine, "SELECT name, status FROM documents WHERE is_latest")}


# ---- the sample folder end to end ---------------------------------------------------------------------


def test_every_document_processed_with_expected_status(ingested):
    engine, statuses = ingested
    assert set(statuses) == set(SAMPLE_BUILDERS)
    assert doc_status(engine) == EXPECTED_STATUS
    types = {r["name"]: r["document_type"] for r in q(engine, "SELECT name, document_type FROM documents")}
    for name in ("acme_family_office_statement_2026-01.pdf", "birchwood_foundation_statement_2026-02.pdf",
                 "cedar_ridge_capital_account_kestrel_q4_2025.pdf"):
        assert types[name] == "account_statement"
    assert types["northwind_flash_update_jan2026.eml"] == "email"


def test_ground_truth_returns(ingested):
    engine, _ = ingested
    for fund, cells in GROUND_TRUTH.items():
        fid = fund_id(engine, fund)
        for month, pct in cells.items():
            rows = q(engine, """SELECT value, value_pct, status, document_name FROM v_returns
                                WHERE fund_id = :f AND return_type = 'MTD' AND net_gross = 'net'
                                  AND to_char(period_end, 'YYYY-MM') = :m""", f=fid, m=month)
            assert rows, (fund, month)
            assert {r["value_pct"] for r in rows} == {D(pct)}, (fund, month, rows)
    kestrel = q(engine, """SELECT value, status, document_name FROM v_returns WHERE fund_name = 'Kestrel Quant Equity Fund LP'
                           AND period_end = '2026-01-31' AND return_type = 'MTD' AND net_gross = 'net'""")
    assert len(kestrel) == 3 and {r["value"] for r in kestrel} == {D("0.0284")}
    assert {r["status"] for r in kestrel} == {"ok"}  # equal values in 3 documents: no conflict
    # "Which fund had the best January return?" -> Kestrel +2.84%
    best = q(engine, """SELECT fund_name, max(value_pct) AS v FROM v_returns WHERE period_year = 2026 AND period_month = 1
                        AND return_type = 'MTD' AND net_gross = 'net' GROUP BY fund_name ORDER BY v DESC LIMIT 1""")
    assert best[0]["fund_name"] == "Kestrel Quant Equity Fund LP" and best[0]["v"] == D("2.84")


def test_unit_variety_normalized_to_fractions(ingested):
    engine, _ = ingested
    one = lambda sql: q(engine, sql)[0]["value"]  # noqa: E731
    # bps (Alder), parentheses (Meridian), decimal fractions (Harbor CSV), % column (Meridian CSV), gross column
    assert one("SELECT value FROM v_returns WHERE document_name = 'alder_asia_fund_march2026.pdf' "
               "AND return_type = 'MTD'") == D("-0.0125")
    assert one("SELECT value FROM v_returns WHERE document_name = 'meridian_rv_tearsheet_2026-01.pdf' "
               "AND return_type = 'MTD'") == D("-0.0036")
    assert one("SELECT value FROM v_returns WHERE document_name = 'harbor_admin_nav_export_2026-03.csv' "
               "AND period_end = '2026-01-31'") == D("0.0071")
    assert one("SELECT value FROM v_returns WHERE document_name = 'meridian_returns_export.csv' "
               "AND period_end = '2026-01-31' AND net_gross = 'gross'") == D("-0.0015")
    # bare numbers under the Northwind "Perf. (net) — %" header
    assert one("SELECT value FROM v_returns WHERE document_name = 'northwind_monthly_feb26.pdf' "
               "AND period_end = '2026-02-28' AND return_type = 'MTD'") == D("0.0055")


def test_northwind_conflict_on_both_rows(ingested):
    engine, _ = ingested
    rows = q(engine, """SELECT r.id, r.value, r.status, r.review_reasons, r.review_detail, d.name
                        FROM returns r JOIN documents d ON d.id = r.document_id
                        WHERE r.fund_id = :f AND r.period_end = '2026-01-31' AND r.return_type = 'MTD'""",
             f=fund_id(engine, "Northwind Global Macro Fund"))
    by_doc = {r["name"]: r for r in rows}
    assert set(by_doc) == {"northwind_monthly_jan26.pdf", "northwind_monthly_feb26.pdf",
                           "northwind_flash_update_jan2026.eml"}
    assert by_doc["northwind_monthly_jan26.pdf"]["value"] == D("0.0192")
    assert by_doc["northwind_flash_update_jan2026.eml"]["value"] == D("0.0189")
    for r in rows:
        assert r["status"] == "conflict" and r["review_reasons"] == ["conflict"]
        validate_review_detail(r["review_detail"])
    flash = by_doc["northwind_flash_update_jan2026.eml"]
    assert sorted(flash["review_detail"]["conflict"]["conflicting_ids"]) == sorted(
        by_doc[n]["id"] for n in ("northwind_monthly_jan26.pdf", "northwind_monthly_feb26.pdf"))
    assert by_doc["northwind_monthly_jan26.pdf"]["review_detail"]["conflict"]["conflicting_ids"] == [flash["id"]]
    # the flash email is the alias "Northwind Macro Fund Ltd" yet resolves to the same fund
    assert fund_id(engine, "Northwind Macro Fund Ltd") == fund_id(engine, "Northwind Global Macro Fund")


def test_alias_suggestions_and_siblings_kept_apart(ingested):
    engine, _ = ingested
    fuzzy = {r["alias"]: r for r in q(engine, """SELECT a.alias, a.confirmed, a.match_score, f.canonical_name
        FROM fund_aliases a JOIN funds f ON f.id = a.fund_id WHERE a.match_method = 'fuzzy'""")}
    assert fuzzy["Northwind Macro Fund Ltd"]["canonical_name"] == "Northwind Global Macro Fund"
    assert fuzzy["Alder Asia Fund SPC – Class A"]["canonical_name"] == "Alder Asia Fund"
    assert all(not r["confirmed"] and r["match_score"] >= 80 for r in fuzzy.values())
    queue = {r["item_key"]: r for r in q(engine, "SELECT * FROM review_queue WHERE target_table = 'fund_aliases'")}
    assert {r["review_detail"]["alias"] for r in queue.values()} >= {"Northwind Macro Fund Ltd",
                                                                     "Alder Asia Fund SPC – Class A"}
    # siblings: two funds, never merged, each with its own confirmed alias
    asia, dubai = fund_id(engine, "Alder Asia Fund"), fund_id(engine, "Alder Dubai Fund")
    assert asia != dubai
    names = {r["canonical_name"] for r in q(engine, "SELECT canonical_name FROM funds")}
    assert {"Alder Asia Fund", "Alder Dubai Fund"} <= names and len(names) == 6
    dubai_aliases = q(engine, "SELECT alias, match_method, confirmed FROM fund_aliases WHERE fund_id = :f", f=dubai)
    assert dubai_aliases == [{"alias": "Alder Dubai Fund", "match_method": "new", "confirmed": True}]
    assert q(engine, "SELECT count(*) AS n FROM v_returns WHERE fund_id = :f", f=dubai)[0]["n"] == 9


def test_fund_details_filled_from_extraction(ingested):
    engine, _ = ingested
    f = {r["fund_name"]: r for r in q(engine, "SELECT * FROM v_funds")}
    assert f["Kestrel Quant Equity Fund LP"]["manager"] == "Kestrel Partners LLC"
    assert f["Kestrel Quant Equity Fund LP"]["strategy"] == "Systematic equity market neutral"
    assert f["Northwind Global Macro Fund"]["currency"] == "USD"
    assert f["Alder Dubai Fund"]["strategy"] == "MENA credit"
    assert f["Meridian Rates Relative Value Fund"]["manager"] == "Meridian Capital Advisors"


def test_meridian_ytd_mismatch_and_other_ytds_pass(ingested):
    engine, _ = ingested
    ytd = {(r["document_name"], r["period_end"]): r for r in q(engine, """SELECT r.*, d.name AS document_name
        FROM returns r JOIN documents d ON d.id = r.document_id WHERE r.return_type = 'YTD'""")}
    m = ytd[("meridian_rv_tearsheet_2026-02.pdf", date(2026, 2, 28))]
    assert m["status"] == "needs_review" and m["review_reasons"] == ["ytd_mismatch"]
    detail = m["review_detail"]["ytd_mismatch"]
    validate_review_detail(m["review_detail"])
    assert detail["stated"] == 0.0015 and round(detail["computed"], 6) == -0.001508
    assert [x["period_end"] for x in detail["months"]] == ["2026-01-31", "2026-02-28"]
    flagged = {k for k, r in ytd.items() if "ytd_mismatch" in r["review_reasons"]}
    assert flagged == {("meridian_rv_tearsheet_2026-02.pdf", date(2026, 2, 28))}
    # Alder Asia Q1 YTD in the report compounds the report's own months (incl. Feb 0.40%)
    assert ytd[("alder_capital_q1_2026_performance_report.pdf", date(2026, 3, 31))]["status"] == "ok"


def test_statements_and_rollforward(ingested):
    engine, _ = ingested
    st = {r["account_name"]: r for r in q(engine, "SELECT * FROM v_statements")}
    assert set(st) == {"Acme Family Office LLC", "Birchwood Foundation", "Cedar Ridge Partners LP"}
    acme = st["Acme Family Office LLC"]
    assert (acme["status"], acme["review_reasons"], acme["holdings_count"]) == ("needs_review", ["rollforward_mismatch"], 5)
    assert (acme["opening_balance"], acme["closing_balance"], acme["withdrawals"]) == (D("12400000"), D("13100000"), 0)
    assert (acme["period_start"], acme["period_end"], acme["currency"]) == (date(2026, 1, 1), date(2026, 1, 31), "USD")
    bw, cedar = st["Birchwood Foundation"], st["Cedar Ridge Partners LP"]
    assert (bw["status"], bw["withdrawals"], bw["fees"], bw["holdings_count"]) == ("ok", D("1000000"), D("12050"), 5)
    assert (cedar["status"], cedar["fees"], cedar["withdrawals"]) == ("ok", D("30010"), 0)
    assert cedar["fund_name"] == "Kestrel Quant Equity Fund LP" and cedar["custodian"].startswith("Brightline")
    assert (cedar["period_start"], cedar["period_end"]) == (date(2025, 10, 1), date(2025, 12, 31))
    detail = q(engine, "SELECT review_detail FROM statements WHERE id = :i", i=acme["statement_id"])[0]["review_detail"]
    validate_review_detail(detail)
    rf = detail["rollforward_mismatch"]
    assert (rf["expected_closing"], rf["stated_closing"], rf["difference"]) == (13080000.0, 13100000.0, 20000.0)
    prov = q(engine, "SELECT provenance FROM statements WHERE id = :i", i=acme["statement_id"])[0]["provenance"]
    assert prov["closing_balance"] == {"page": 1, "source_snippet": "Ending market value 13,100,000.00",
                                       "confidence": 0.95, "snippet_verified": True, "raw": "13,100,000.00"}
    assert set(prov) >= {"opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance",
                         "period_start", "period_end", "account_name", "account_number_masked",
                         "custodian_or_manager", "currency"}
    holdings = q(engine, "SELECT * FROM v_holdings WHERE statement_id = :i ORDER BY line_no", i=acme["statement_id"])
    assert [h["holding_name"] for h in holdings][:2] == ["Kestrel Quant Equity Fund LP", "Northwind Global Macro Fund"]
    assert (holdings[0]["market_value"], holdings[0]["weight"], holdings[0]["weight_pct"]) == (
        D("4250000"), D("0.324"), D("32.4"))
    assert all(h["status"] == "ok" for h in holdings)


def test_alder_asia_feb_only_from_the_report(ingested):
    engine, _ = ingested
    rows = q(engine, """SELECT value, document_name, document_type FROM v_returns WHERE fund_id = :f
                        AND period_end = '2026-02-28' AND return_type = 'MTD'""", f=fund_id(engine, "Alder Asia Fund"))
    assert rows == [{"value": D("0.004"), "document_name": "alder_capital_q1_2026_performance_report.pdf",
                     "document_type": "performance_report"}]
    # the multi-fund report wrote rows for both funds
    per_fund = q(engine, """SELECT fund_name, count(*) AS n FROM v_returns
                            WHERE document_name = 'alder_capital_q1_2026_performance_report.pdf' GROUP BY fund_name""")
    assert {r["fund_name"]: r["n"] for r in per_fund} == {"Alder Asia Fund": 5, "Alder Dubai Fund": 5}


def test_fund_facts_units(ingested):
    engine, _ = ingested
    facts = {(r["metric"], r["document_name"]): r for r in q(engine, "SELECT * FROM v_fund_facts")}
    k = "kestrel_factsheet_2026-01.pdf"
    assert (facts[("aum", k)]["value"], facts[("aum", k)]["unit"]) == (D("412600000"), "USD")
    assert (facts[("sharpe", k)]["value"], facts[("sharpe", k)]["unit"]) == (D("1.84"), "ratio")
    assert (facts[("max_drawdown", k)]["value"], facts[("max_drawdown", k)]["unit"]) == (D("-0.068"), "fraction")
    assert (facts[("vol", k)]["value"], facts[("vol", k)]["unit"]) == (D("0.076"), "fraction")
    assert facts[("vol", k)]["as_of_date"] == date(2026, 1, 31)
    assert facts[("management_fee", k)]["value_text"] == "1.5% management / 20% performance"
    assert facts[("management_fee", k)]["status"] == "ok"
    assert (facts[("leverage_gross", "meridian_rv_tearsheet_2026-01.pdf")]["unit"]) == "x"
    assert facts[("aum", "northwind_monthly_jan26.pdf")]["value"] == D("1180000000")
    assert facts[("duration", "harbor_credit_q4_2025_investor_letter.pdf")]["unit"] == "years"


def test_every_review_detail_matches_the_contract_shapes(ingested):
    engine, _ = ingested
    for table in ("returns", "fund_facts", "statements", "statement_holdings"):
        for r in q(engine, f"SELECT status, review_reasons, review_detail FROM {table}"):
            if r["status"] == "ok":
                assert r["review_reasons"] == [] and r["review_detail"] is None
            else:
                assert set(r["review_detail"]) == set(r["review_reasons"])
                validate_review_detail(r["review_detail"])
    counts = {r["target_table"]: r["n"] for r in q(engine, "SELECT target_table, count(*) AS n FROM review_queue GROUP BY 1")}
    assert counts == {"returns": 6, "statements": 1, "fund_aliases": 4}


def test_snippets_verified_and_skipped_for_the_scanned_pdf(ingested):
    engine, _ = ingested
    scanned = q(engine, "SELECT id, text_source, text_content FROM documents WHERE name = 'northwind_dec25_scanned.pdf'")[0]
    assert scanned["text_source"] == "transcription" and "Perf. (net)" in scanned["text_content"]
    rows = q(engine, "SELECT snippet_verified, review_reasons FROM returns WHERE document_id = :d", d=scanned["id"])
    assert rows and all(r["snippet_verified"] is None and r["review_reasons"] == [] for r in rows)
    others = q(engine, """SELECT r.snippet_verified FROM returns r JOIN documents d ON d.id = r.document_id
                          WHERE d.text_source = 'file'""")
    assert others and {r["snippet_verified"] for r in others} == {True}
    assert q(engine, "SELECT count(*) AS n FROM documents WHERE text_source = 'file'")[0]["n"] == 20


def test_chunks_with_embeddings_and_tsv(ingested, fake_embedder):
    engine, _ = ingested
    per_doc = q(engine, """SELECT d.name, d.search_indexed, count(c.id) AS chunks, count(c.embedding) AS embedded,
                                  count(c.tsv) AS tsv, min(c.embedding_model) AS model
                           FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id GROUP BY d.id""")
    assert len(per_doc) == 21
    for r in per_doc:
        assert r["search_indexed"] and r["chunks"] >= 1 and r["embedded"] == r["chunks"] == r["tsv"], r
        assert r["model"] == fake_embedder.model_name
    kestrel = q(engine, """SELECT c.page FROM document_chunks c JOIN documents d ON d.id = c.document_id
                           WHERE d.name = 'kestrel_factsheet_2026-01.pdf' ORDER BY c.chunk_index""")
    assert [r["page"] for r in kestrel] == [1, 2]
    csv = q(engine, """SELECT c.page, c.text FROM document_chunks c JOIN documents d ON d.id = c.document_id
                       WHERE d.name = 'meridian_returns_export.csv'""")
    assert all(r["page"] is None and r["text"].startswith("Fund,Month End,") for r in csv)
    hits = q(engine, """SELECT DISTINCT d.name FROM document_chunks c JOIN documents d ON d.id = c.document_id
                        WHERE c.tsv @@ websearch_to_tsquery('english', 'duration')""")
    assert {"meridian_rv_tearsheet_2026-01.pdf", "harbor_credit_q4_2025_investor_letter.pdf",
            "meridian_pm_note_feb2026.eml"} <= {r["name"] for r in hits}
    dim = q(engine, "SELECT vector_dims(embedding) AS n FROM document_chunks LIMIT 1")[0]["n"]
    assert dim == 384


# ---- idempotency and versions ------------------------------------------------------------------------------


def _counts(engine) -> dict[str, int]:
    return {t: q(engine, f"SELECT count(*) AS n FROM {t}")[0]["n"]
            for t in ("returns", "fund_facts", "statements", "statement_holdings", "document_chunks", "funds",
                      "fund_aliases", "accounts", "account_aliases")}


def _requeue(engine, name):
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET status = 'pending', attempts = 0, next_attempt_at = now() "
                       "WHERE name = :n AND is_latest"), {"n": name})


def test_reprocessing_the_same_documents_creates_no_duplicates(ingested, tmp_path, fake_embedder):
    engine, _ = ingested
    before = _counts(engine)
    returns_before = q(engine, "SELECT fund_id, period_end, return_type, net_gross, value, status, document_name "
                               "FROM v_returns ORDER BY 1, 2, 3, 4, 7")
    chunk_ids = [r["id"] for r in q(engine, "SELECT id FROM document_chunks ORDER BY id")]
    embedded = fake_embedder.texts_embedded
    for name in SAMPLE_BUILDERS:
        _requeue(engine, name)
    out = drain(engine, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))
    assert len(out) == 21
    assert _counts(engine) == before
    assert q(engine, "SELECT fund_id, period_end, return_type, net_gross, value, status, document_name "
                     "FROM v_returns ORDER BY 1, 2, 3, 4, 7") == returns_before
    assert doc_status(engine) == EXPECTED_STATUS
    # same version, same text: chunks kept with their embeddings (no re-embedding)
    assert [r["id"] for r in q(engine, "SELECT id FROM document_chunks ORDER BY id")] == chunk_ids
    assert fake_embedder.texts_embedded == embedded
    assert q(engine, "SELECT count(*) AS n FROM extractions")[0]["n"] == 42


def test_changed_checksum_creates_a_version_that_replaces_the_old_rows(ingested, tmp_path):
    engine, _ = ingested
    docs, fx = tmp_path / "docs", tmp_path / "fixtures"
    name = "northwind_flash_update_jan2026.eml"
    # the IR team re-sends the flash with the final figure: 1.92%
    path = docs / name
    path.write_bytes(path.read_bytes().replace(b"+1.89%", b"+1.92%"))
    ex = northwind_flash()
    for r in ex.funds[0].returns:
        r.value.value = "+1.92%"
        r.value.source_snippet = r.value.source_snippet.replace("+1.89%", "+1.92%")
    write_fixture(fx, name, path.read_bytes(), ex, mime="message/rfc822")
    sync_folder(engine, docs)
    drain(engine, docs, FixtureExtractor(fx))
    versions = q(engine, "SELECT id, version, is_latest, status FROM documents WHERE name = :n ORDER BY version", n=name)
    assert [(v["version"], v["is_latest"]) for v in versions] == [(1, False), (2, True)]
    rows = q(engine, """SELECT value, status, document_id FROM v_returns WHERE fund_id = :f AND period_end = '2026-01-31'
                        AND return_type = 'MTD'""", f=fund_id(engine, "Northwind Global Macro Fund"))
    assert len(rows) == 3 and {r["value"] for r in rows} == {D("0.0192")}
    assert {r["status"] for r in rows} == {"ok"}  # the conflict is gone on the factsheets too
    assert versions[0]["id"] not in {r["document_id"] for r in rows}
    st = doc_status(engine)
    assert st["northwind_monthly_jan26.pdf"] == "done" and st["northwind_monthly_feb26.pdf"] == "done"
    assert st[name] == "needs_review"  # still the unconfirmed alias "Northwind Macro Fund Ltd"
    assert q(engine, "SELECT count(*) AS n FROM review_queue WHERE target_table = 'returns' "
                     "AND 'conflict' = ANY(review_reasons)")[0]["n"] == 0


# ---- review decisions persist across reprocessing (FR-13) --------------------------------------------------------


def _add_action(engine, table, row_id, action, old_value, new_value):
    model = {"returns": Return, "statements": Statement}[table]
    with Session(engine) as s, s.begin():
        row = s.get(model, row_id)
        s.add(ReviewAction(target_table=table, target_id=row.id, document_id=row.document_id,
                           natural_key=natural_key(table, row), action=action, old_value=old_value,
                           new_value=new_value))


def _meridian_feb_ytd(engine):
    return q(engine, """SELECT r.* FROM returns r JOIN documents d ON d.id = r.document_id
                        WHERE d.name = 'meridian_rv_tearsheet_2026-02.pdf' AND d.is_latest AND r.return_type = 'YTD'""")[0]


def test_edit_is_reapplied_after_reprocessing(ingested, tmp_path):
    engine, _ = ingested
    row = _meridian_feb_ytd(engine)
    _add_action(engine, "returns", row["id"], "edit", {"value": 0.0015}, {"value": -0.0015})
    _requeue(engine, "meridian_rv_tearsheet_2026-02.pdf")
    drain(engine, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))
    new = _meridian_feb_ytd(engine)
    assert new["id"] != row["id"]
    assert (new["value"], new["status"], new["review_reasons"]) == (D("-0.0015"), "ok", [])
    assert doc_status(engine)["meridian_rv_tearsheet_2026-02.pdf"] == "done"


def test_accept_reapplied_only_while_values_are_unchanged(ingested, tmp_path):
    engine, _ = ingested
    docs, fx = tmp_path / "docs", tmp_path / "fixtures"
    acme = q(engine, "SELECT id FROM statements")[0]["id"]
    _add_action(engine, "statements", acme, "accept", {"closing_balance": 13100000.0, "opening_balance": 12400000.0},
                {"closing_balance": 13100000.0})
    _requeue(engine, "acme_family_office_statement_2026-01.pdf")
    drain(engine, docs, FixtureExtractor(fx))
    st = q(engine, "SELECT status, review_reasons FROM v_statements WHERE account_name = 'Acme Family Office LLC'")[0]
    assert (st["status"], st["review_reasons"]) == ("ok", [])
    assert doc_status(engine)["acme_family_office_statement_2026-01.pdf"] == "done"
    # the document now says something different: the accept no longer applies
    from tests.synthetic_samples import acme as acme_extraction

    ex = acme_extraction()
    ex.statements[0].closing_balance.value = "13,120,000.00"
    write_fixture(fx, "acme_family_office_statement_2026-01.pdf",
                  (docs / "acme_family_office_statement_2026-01.pdf").read_bytes(), ex)
    _requeue(engine, "acme_family_office_statement_2026-01.pdf")
    drain(engine, docs, FixtureExtractor(fx))
    st = q(engine, "SELECT status, review_reasons FROM v_statements WHERE account_name = 'Acme Family Office LLC'")[0]
    assert st["status"] == "needs_review" and "rollforward_mismatch" in st["review_reasons"]


def test_choose_deletes_the_losing_row_again_after_reprocessing(ingested, tmp_path):
    engine, _ = ingested
    rows = {r["name"]: r for r in q(engine, """SELECT r.id, r.value, d.name FROM returns r JOIN documents d
        ON d.id = r.document_id WHERE r.fund_id = :f AND r.period_end = '2026-01-31' AND r.return_type = 'MTD'""",
                                     f=fund_id(engine, "Northwind Global Macro Fund"))}
    winner, loser = rows["northwind_monthly_jan26.pdf"], rows["northwind_flash_update_jan2026.eml"]
    # what T6 does for `choose`: decision rows for every candidate, losers deleted
    _add_action(engine, "returns", winner["id"], "choose", {"value": 0.0192}, {"value": 0.0192})
    _add_action(engine, "returns", rows["northwind_monthly_feb26.pdf"]["id"], "choose", {"value": 0.0192},
                {"value": 0.0192})
    _add_action(engine, "returns", loser["id"], "choose", {"value": 0.0189}, {"deleted": True})
    with Session(engine) as s, s.begin():
        s.delete(s.get(Return, loser["id"]))
    for name in ("northwind_flash_update_jan2026.eml", "northwind_monthly_jan26.pdf"):
        _requeue(engine, name)
    drain(engine, tmp_path / "docs", FixtureExtractor(tmp_path / "fixtures"))
    after = q(engine, """SELECT r.value, r.status, d.name FROM returns r JOIN documents d ON d.id = r.document_id
                         WHERE r.fund_id = :f AND r.period_end = '2026-01-31' AND r.return_type = 'MTD'""",
              f=fund_id(engine, "Northwind Global Macro Fund"))
    assert sorted(r["name"] for r in after) == ["northwind_monthly_feb26.pdf", "northwind_monthly_jan26.pdf"]
    assert {r["status"] for r in after} == {"ok"}


# ---- embedding failures (FR-7) ---------------------------------------------------------------------------------------


def test_embedding_failure_keeps_document_done_and_retry_indexes_it(clean_db, tmp_path, fake_embedder):
    fake_embedder.fail = True
    names = ["kestrel_factsheet_2026-01.pdf", "meridian_returns_export.csv"]
    statuses = ingest_samples(clean_db, tmp_path, names)
    assert statuses == {n: "done" for n in names}
    docs = q(clean_db, "SELECT id, status, search_indexed FROM documents")
    assert all(d["status"] == "done" and not d["search_indexed"] for d in docs)
    # chunks exist (keyword search works already) but have no embeddings yet
    c = q(clean_db, "SELECT count(*) AS n, count(embedding) AS e, count(tsv) AS t FROM document_chunks")[0]
    assert c["n"] > 0 and c["e"] == 0 and c["t"] == c["n"]
    with pytest.raises(embedding.EmbeddingError):
        indexing.index_pending(clean_db)
    fake_embedder.fail = False
    assert indexing.index_pending(clean_db) == 2
    assert all(d["search_indexed"] for d in q(clean_db, "SELECT search_indexed FROM documents"))
    assert q(clean_db, "SELECT count(*) AS n FROM document_chunks WHERE embedding IS NULL")[0]["n"] == 0
    assert indexing.index_pending(clean_db) == 0  # nothing left


def test_chunking_failure_never_fails_the_job_and_is_redone_later(clean_db, tmp_path, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("chunker bug")

    monkeypatch.setattr(indexing, "prepare_chunks", broken)
    statuses = ingest_samples(clean_db, tmp_path, ["kestrel_factsheet_2026-02.pdf"])
    assert statuses == {"kestrel_factsheet_2026-02.pdf": "done"}
    assert q(clean_db, "SELECT count(*) AS n FROM returns")[0]["n"] > 0  # structured data committed
    assert q(clean_db, "SELECT count(*) AS n FROM document_chunks")[0]["n"] == 0
    assert q(clean_db, "SELECT search_indexed FROM documents")[0]["search_indexed"] is False
    monkeypatch.undo()
    monkeypatch.setattr(postprocess, "_today", lambda: FIXED_TODAY)
    assert indexing.index_pending(clean_db) == 1
    assert q(clean_db, "SELECT count(*) AS n FROM document_chunks WHERE embedding IS NOT NULL")[0]["n"] == 2


def test_model_change_triggers_reembedding(clean_db, tmp_path):
    ingest_samples(clean_db, tmp_path, ["alder_dubai_fund_january2026.pdf"])
    new = FakeEmbedder(model_name="another-model")
    with Session(clean_db) as s:
        assert indexing.pending_document_ids(s, "another-model") != []
    assert indexing.index_pending(clean_db, embedder=new) == 1
    assert {r["m"] for r in q(clean_db, "SELECT embedding_model AS m FROM document_chunks")} == {"another-model"}
    with Session(clean_db) as s:
        assert indexing.pending_document_ids(s, "another-model") == []


def test_index_document_skips_documents_that_are_not_processed(clean_db, tmp_path):
    ingest_samples(clean_db, tmp_path, ["alder_dubai_fund_march2026.pdf"])
    with clean_db.begin() as c:
        doc_id = c.execute(text("UPDATE documents SET status = 'processing', search_indexed = false RETURNING id")).scalar()
    assert indexing.index_document(clean_db, doc_id) is False
    assert indexing.index_document(clean_db, 999999) is False


def test_search_indexed_rows_have_no_null_embedding_after_worker_retry(clean_db, tmp_path, clean_env, fake_embedder):
    """The worker's index thread step (`Worker.index_tick`) is the retry path."""
    from app import worker as wk
    from app.config import Settings

    fake_embedder.fail = True
    ingest_samples(clean_db, tmp_path, ["birchwood_foundation_statement_2026-02.pdf"])
    fake_embedder.fail = False
    w = wk.Worker(Settings(_env_file=None), clean_db, threading.Event(), extractor=None,
                  source_resolver=lambda st, sess: None, index_min_age_seconds=0)
    assert w.index_tick() == 1
    assert q(clean_db, "SELECT search_indexed FROM documents")[0]["search_indexed"] is True


def test_sample_fixture_files_round_trip(tmp_path):
    fx = write_sample_fixtures(tmp_path / "fx")
    files = sorted(p.name for p in fx.glob("*.json"))
    assert len(files) == 21
    data = json.loads((fx / "kestrel_factsheet_2026-01.pdf.json").read_text())
    assert data["extraction"]["funds"][0]["fund_name"]["value"] == "Kestrel Quant Equity Fund LP"
    assert copy_samples(tmp_path / "d", ["acme_family_office_statement_2026-01.pdf"]).is_dir()
    assert isinstance(SAMPLE_BUILDERS["kestrel_factsheet_2026-01.pdf"]().funds[0].returns[0], ReturnEntry)


def test_concurrent_worker_gives_the_same_flags(clean_db, tmp_path, clean_env):
    """WORKER_CONCURRENCY=4: the cross-document steps are serialized by the postprocess
    lock, so concurrent jobs neither race on aliases nor miss conflicts."""
    import time

    from app import worker as wk
    from app.config import Settings
    from app.sources.local import LocalFolderSource

    docs = copy_samples(tmp_path / "docs")
    fx = write_sample_fixtures(tmp_path / "fixtures")
    clean_env.setenv("SYNC_INTERVAL_SECONDS", "3600")
    clean_env.setenv("WORKER_CONCURRENCY", "4")
    stop = threading.Event()
    w = wk.Worker(Settings(_env_file=None), clean_db, stop, extractor=FixtureExtractor(fx),
                  source_resolver=lambda st, sess: LocalFolderSource(docs), job_idle_seconds=0.05)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            st = doc_status(clean_db)
            if len(st) == 21 and set(st.values()) <= {"done", "needs_review"} and all(
                    r["search_indexed"] for r in q(clean_db, "SELECT search_indexed FROM documents")):
                break
            time.sleep(0.1)
    finally:
        stop.set()
        t.join(timeout=15)
    # Which name of an alias pair becomes canonical depends on which job takes the lock
    # first, so check order-independent facts: the pairs, the row flags, and that every
    # stored document status equals a fresh recomputation (none left stale).
    assert q(clean_db, "SELECT count(*) AS n FROM funds")[0]["n"] == 6
    pairs = {frozenset((r["alias"], r["canonical_name"])) for r in q(clean_db, """SELECT a.alias, f.canonical_name
        FROM fund_aliases a JOIN funds f ON f.id = a.fund_id WHERE a.match_method = 'fuzzy'""")}
    assert len(pairs) == 4
    assert frozenset({"Alder Asia Fund SPC – Class A", "Alder Asia Fund"}) in pairs
    assert any("Northwind Macro Fund Ltd" in pr for pr in pairs)
    with Session(clean_db) as s:
        for d in s.execute(select(Document).where(Document.is_latest)).scalars():
            assert d.status == postprocess.document_status(s, d.id), d.name
    flags = {(r["document_name"], r["review_reasons"][0]) for r in q(clean_db, """SELECT d.name AS document_name,
        r.review_reasons FROM returns r JOIN documents d ON d.id = r.document_id WHERE r.status <> 'ok'""")}
    assert ("meridian_rv_tearsheet_2026-02.pdf", "ytd_mismatch") in flags
    assert {n for n, reason in flags if reason == "conflict"} == {
        "northwind_monthly_jan26.pdf", "northwind_monthly_feb26.pdf", "northwind_flash_update_jan2026.eml"}
    assert q(clean_db, "SELECT review_reasons FROM v_statements WHERE account_name = 'Acme Family Office LLC'"
             )[0]["review_reasons"] == ["rollforward_mismatch"]
    nw = q(clean_db, """SELECT status FROM v_returns WHERE period_end = '2026-01-31' AND return_type = 'MTD'
                        AND fund_id = :f""", f=fund_id(clean_db, "Northwind Global Macro Fund"))
    assert [r["status"] for r in nw] == ["conflict"] * 3
    assert fund_id(clean_db, "Alder Asia Fund") != fund_id(clean_db, "Alder Dubai Fund")
    assert q(clean_db, "SELECT count(*) AS n FROM document_chunks WHERE embedding IS NULL")[0]["n"] == 0


# ---- the real extractor output (fixtures/, T4b) ---------------------------------------------------------------


def test_real_fixtures_through_t5(clean_db, tmp_path):
    """The committed fixtures (real claude-opus-5-5 output) give the planted results with
    no false flags: guards the T4b findings ("2025 YTD", bare "Q1", "full-year",
    parenthesized withdrawals/fees, "-" as nil)."""
    from app.extraction.fixture import FIXTURES_DIR

    if not FIXTURES_DIR.is_dir() or len(list(FIXTURES_DIR.glob("*.json"))) < 21:
        pytest.skip("fixtures/ not generated yet")
    docs = copy_samples(tmp_path / "docs")
    sync_folder(clean_db, docs)
    statuses = drain(clean_db, docs, FixtureExtractor(FIXTURES_DIR))
    assert len(statuses) == 21 and set(statuses.values()) <= {"done", "needs_review"}
    reasons = {(r["document_name"], reason) for r in q(clean_db, "SELECT document_name, review_reasons FROM review_queue")
               for reason in r["review_reasons"]}
    assert not any(reason == "unparseable_value" for _, reason in reasons)
    assert ("meridian_rv_tearsheet_2026-02.pdf", "ytd_mismatch") in reasons
    assert {n for n, reason in reasons if reason == "ytd_mismatch"} == {"meridian_rv_tearsheet_2026-02.pdf"}
    assert ("acme_family_office_statement_2026-01.pdf", "rollforward_mismatch") in reasons
    assert {n for n, reason in reasons if reason == "rollforward_mismatch"} == {"acme_family_office_statement_2026-01.pdf"}
    assert {n for n, reason in reasons if reason == "conflict"} == {
        "northwind_monthly_jan26.pdf", "northwind_monthly_feb26.pdf", "northwind_flash_update_jan2026.eml"}
    aliases = {r["alias"] for r in q(clean_db, "SELECT alias FROM fund_aliases WHERE match_method = 'fuzzy'")}
    assert {"Northwind Macro Fund Ltd", "Alder Asia Fund SPC – Class A"} <= aliases
    assert fund_id(clean_db, "Alder Asia Fund") != fund_id(clean_db, "Alder Dubai Fund")
    kestrel = q(clean_db, """SELECT DISTINCT value FROM v_returns WHERE fund_name = 'Kestrel Quant Equity Fund LP'
                             AND period_end = '2026-01-31' AND return_type = 'MTD' AND net_gross = 'net'""")
    assert kestrel == [{"value": D("0.0284")}]
    feb = q(clean_db, """SELECT value, document_name FROM v_returns WHERE fund_id = :f AND period_end = '2026-02-28'
                         AND return_type = 'MTD'""", f=fund_id(clean_db, "Alder Asia Fund"))
    assert feb == [{"value": D("0.004"), "document_name": "alder_capital_q1_2026_performance_report.pdf"}]
    nw_2025 = q(clean_db, """SELECT DISTINCT period_end FROM v_returns WHERE return_type = 'YTD' AND value = 0.0196""")
    assert nw_2025 == [{"period_end": date(2025, 12, 31)}]  # "2025 YTD" is the year 2025
    st = {r["account_name"]: r for r in q(clean_db, "SELECT * FROM v_statements")}
    for name in ("Birchwood Foundation", "Cedar Ridge Partners LP"):
        assert "rollforward_mismatch" not in st[name]["review_reasons"]
    assert all(r["search_indexed"] for r in q(clean_db, "SELECT search_indexed FROM documents"))

"""process_extraction edge cases, called directly in a rolled-back transaction:
unparseable values kept and flagged, unknown labels (§17), range/future/confidence
checks, snippet verification incl. image-only pages, duplicates inside a document,
YTD with months from other documents, statements, cross-document status refresh."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.extraction.schema import Extraction, FundExtraction, PageText, StatementExtraction
from app.ingest.postprocess import document_status, metric_name, process_extraction
from app.models import Document, FundAlias, FundFact, Return, Statement
from app.models import Extraction as ExtractionRow
from app.schemas.review import validate_review_detail
from tests.synthetic_samples import ret, sv

TODAY = date(2026, 10, 16)
D = Decimal


def fund(name="Test Fund", returns=(), metrics=None, as_of=("January 2026", "January 2026"), **kw) -> FundExtraction:
    return FundExtraction(fund_name=sv(name, name), manager=kw.get("manager"), strategy=None, currency=None,
                          as_of_date=sv(*as_of) if as_of else None, aum=kw.get("aum"), returns=list(returns),
                          other_metrics=metrics or {})


def run(s, name, text, funds=(), statements=(), *, mime="application/pdf", text_source="file", pages=None,
        drive_file_id=None, status="processing"):
    doc = Document(drive_file_id=drive_file_id or name, name=name, mime_type=mime, md5_checksum=f"md5-{name}",
                   status=status, text_content=text, text_source=text_source)
    s.add(doc)
    s.flush()
    ex = Extraction(document_type="factsheet", document_type_confidence=0.9, funds=list(funds),
                    statements=list(statements), pages=pages or [PageText(page=1, text=text)], extra={})
    row = ExtractionRow(document_id=doc.id, status="ok", raw_json=ex.model_dump(mode="json"))
    s.add(row)
    s.flush()
    status = process_extraction(s, doc.id, ex, extraction_id=row.id, today=TODAY)
    doc.status = status
    s.flush()
    return doc, status


def returns_of(s, doc) -> list[Return]:
    return list(s.execute(select(Return).where(Return.document_id == doc.id).order_by(Return.id)).scalars())


def test_clean_document_is_done_and_rows_are_linked(db_session):
    s = db_session
    doc, status = run(s, "a.pdf", "Test Fund\nJan 2026 +1.00%", [fund(returns=[ret("Jan 2026", "+1.00%", "Jan 2026 +1.00%")])])
    assert status == "done"
    (r,) = returns_of(s, doc)
    assert (r.value, r.period_end, r.period_label, r.status, r.snippet_verified) == (
        D("0.01"), date(2026, 1, 31), "Jan 2026", "ok", True)
    assert r.extraction_id is not None and r.fund_alias_id is not None
    assert s.get(FundAlias, r.fund_alias_id).alias == "Test Fund"


def test_unparseable_values_are_kept_and_flagged(db_session):
    s = db_session
    f = fund(returns=[ret("Jan 2026", "n/a", "Jan 2026 n/a"),
                      ret("sometime", "+1.0%", "sometime +1.0%"),
                      ret("Feb 2026", "0.36", "Feb 2026 0.36")])  # bare number, no unit anywhere
    doc, status = run(s, "b.pdf", "Test Fund\nJan 2026 n/a\nsometime +1.0%\nFeb 2026 0.36", [f])
    assert status == "needs_review"
    rows = returns_of(s, doc)
    assert len(rows) == 3  # never dropped
    na, period, bare = rows
    assert na.value is None and na.review_detail["unparseable_value"] == {"raw": "n/a", "field": "value"}
    assert period.value == D("0.01") and period.review_detail["unparseable_value"] == {"raw": "sometime", "field": "period"}
    # stand-in date: the as-of month end, moved back a day because Jan 31 is taken
    assert (na.period_end, period.period_end, period.period_label) == (date(2026, 1, 31), date(2026, 1, 30), "sometime")
    assert bare.value is None and bare.review_reasons == ["unparseable_value"]
    for r in rows:
        validate_review_detail(r.review_detail)


def test_unknown_labels_range_future_and_confidence(db_session):
    s = db_session
    f = fund(returns=[
        ReturnEntryLike("Jan 2026", "+1.0%", "Jan 2026 +1.0%", rt="unknown", ng="unknown"),
        ret("Feb 2026", "+62%", "Feb 2026 +62%"),
        ret("Dec 2026", "+1.0%", "Dec 2026 +1.0%"),
        ret("Mar 2026", "+1.0%", "Mar 2026 +1.0%", conf=0.3),
        ret("Oct 2026", "+0.5%", "Oct 2026 +0.5%"),  # current month: not future
    ])
    text = "Test Fund Jan 2026 +1.0% Feb 2026 +62% Dec 2026 +1.0% Mar 2026 +1.0% Oct 2026 +0.5%"
    doc, status = run(s, "c.pdf", text, [f])
    rows = {r.period_label: r for r in returns_of(s, doc)}
    assert rows["Jan 2026"].review_reasons == ["net_gross_unknown", "return_type_unknown"]
    assert rows["Jan 2026"].review_detail["net_gross_unknown"] == {"raw_label": "Jan 2026 +1.0%"}
    assert rows["Feb 2026"].review_detail["out_of_range"] == {"value": 0.62, "min": -0.5, "max": 0.5}
    assert rows["Dec 2026"].review_detail["future_date"] == {"date": "2026-12-31", "today": "2026-10-16"}
    assert rows["Mar 2026"].review_detail["low_confidence"] == {"confidence": 0.3, "threshold": 0.7}
    assert rows["Oct 2026"].status == "ok"
    for r in rows.values():
        validate_review_detail(r.review_detail or {})


def ReturnEntryLike(period, value, snippet, rt, ng):  # noqa: N802 - reads like the model it builds
    return ret(period, value, snippet, rt=rt, ng=ng)


def test_snippet_verification(db_session):
    s = db_session
    f = fund(returns=[ret("Jan 2026", "+1.0%", "Jan 2026 +1.0%"), ret("Feb 2026", "+2.0%", "Feb 2026 +2.0% (made up)"),
                      ret("Mar 2026", "+3.0%", "Mar 2026 +3.0%", page=2)])
    # page 2 has no text layer (image-only page in a PDF with text elsewhere)
    doc, _ = run(s, "d.pdf", "Test Fund January 2026\nJan 2026\n+1.0%\nFeb 2026 +2.0%\f", [f],
                 pages=[PageText(page=1, text="x"), PageText(page=2, text="Mar 2026 +3.0%")])
    rows = {r.period_label: r for r in returns_of(s, doc)}
    assert rows["Jan 2026"].snippet_verified is True and rows["Jan 2026"].status == "ok"
    assert rows["Feb 2026"].snippet_verified is False
    assert rows["Feb 2026"].review_detail["snippet_not_found"] == {"snippet": "Feb 2026 +2.0% (made up)"}
    assert rows["Mar 2026"].snippet_verified is None and rows["Mar 2026"].status == "ok"


def test_snippets_never_checked_against_a_transcription(db_session):
    s = db_session
    f = fund(returns=[ret("Jan 2026", "+1.0%", "not in the transcription")])
    doc, status = run(s, "scan.pdf", "Test Fund Jan 2026 +1.0%", [f], text_source="transcription")
    (r,) = returns_of(s, doc)
    assert r.snippet_verified is None and status == "done"
    # rows from before migration 0003: the source is inferred from the transcription
    doc2, _ = run(s, "scan2.pdf", "Test Fund Jan 2026 +1.0%", [fund("Other Fund", returns=[
        ret("Jan 2026", "+1.0%", "nowhere")])], text_source=None,
        pages=[PageText(page=1, text="Test Fund Jan 2026 +1.0%")])
    assert s.get(Document, doc2.id).text_source is None
    assert returns_of(s, doc2)[0].snippet_verified is None


def test_duplicate_where_only_the_second_copy_parses(db_session):
    s = db_session
    f = fund(returns=[ret("Jan 2026", "n/a", "MTD n/a"), ret("Jan 2026", "+1.0%", "Jan 2026 +1.0%")])
    doc, status = run(s, "dup.pdf", "Test Fund MTD n/a Jan 2026 +1.0%", [f])
    (r,) = returns_of(s, doc)
    assert (r.value, r.status, status) == (D("0.01"), "ok", "done")


def test_duplicates_within_a_document(db_session):
    s = db_session
    same = fund(returns=[ret("Jan 2026", "+1.0%", "MTD +1.0%"), ret("Jan 2026", "+1.00%", "Jan 2026 +1.00%")])
    doc, status = run(s, "e.pdf", "Test Fund MTD +1.0% Jan 2026 +1.00%", [same])
    assert len(returns_of(s, doc)) == 1 and status == "done"
    differ = fund("Other Fund", returns=[ret("Jan 2026", "+1.0%", "MTD +1.0%"), ret("Jan 2026", "+1.3%", "Jan 2026 +1.3%")])
    doc2, status2 = run(s, "f.pdf", "Other Fund MTD +1.0% Jan 2026 +1.3%", [differ])
    (r,) = returns_of(s, doc2)
    assert r.value == D("0.01") and status2 == "needs_review"
    assert r.review_detail["unparseable_value"]["raw"].startswith("document states two values")


def test_multi_fund_document_writes_every_fund(db_session):
    s = db_session
    text = "Q1 report\nAlpha One 1.00% 2.00%\nBeta Two 3.00% 4.00%"
    funds = [fund(n, as_of=("Q1 2026", "Q1 report"),
                  returns=[ret("Jan", a, f"{n} {a} {b}"), ret("Feb", b, f"{n} {a} {b}")])
             for n, a, b in (("Alpha One", "1.00%", "2.00%"), ("Beta Two", "3.00%", "4.00%"))]
    doc, status = run(s, "g.pdf", text, funds)
    rows = returns_of(s, doc)
    assert status == "done" and len(rows) == 4
    assert len({r.fund_id for r in rows}) == 2
    assert {r.period_end for r in rows} == {date(2026, 1, 31), date(2026, 2, 28)}  # "Jan" resolved via the as-of


def test_ytd_uses_months_from_other_documents_and_skips_incomplete_runs(db_session):
    s = db_session
    jan = fund("Gamma Fund", returns=[ret("Jan 2026", "+1.00%", "Jan 2026 +1.00%")])
    run(s, "jan.pdf", "Gamma Fund Jan 2026 +1.00%", [jan])
    feb = fund("Gamma Fund", as_of=("February 2026", "February 2026"),
               returns=[ret("Feb 2026", "+1.00%", "Feb 2026 +1.00%"),
                        ret("YTD 2026", "+3.00%", "YTD +3.00%", rt="YTD")])
    doc, status = run(s, "feb.pdf", "Gamma Fund February 2026 Feb 2026 +1.00% YTD +3.00%", [feb])
    ytd = next(r for r in returns_of(s, doc) if r.return_type == "YTD")
    assert ytd.review_reasons == ["ytd_mismatch"]  # Jan (other doc) + Feb compound to 2.01%, not 3%
    months = ytd.review_detail["ytd_mismatch"]["months"]
    assert [m["period_end"] for m in months] == ["2026-01-31", "2026-02-28"]
    # March YTD with February missing everywhere: not checked
    mar = fund("Delta Fund", as_of=("March 2026", "March 2026"),
               returns=[ret("Mar 2026", "+1.00%", "Mar 2026 +1.00%"), ret("YTD 2026", "+9.00%", "YTD +9.00%", rt="YTD")])
    doc2, status2 = run(s, "mar.pdf", "Delta Fund March 2026 Mar 2026 +1.00% YTD +9.00%", [mar])
    assert status2 == "done"


def test_statement_with_unparseable_flow_skips_rollforward(db_session):
    s = db_session
    st = StatementExtraction(
        account_name=sv("Zeta Trust", "Account: Zeta Trust"), account_number_masked=None, custodian_or_manager=None,
        fund_name=None, currency=sv("EUR", "EUR"), period_start=None, period_end=sv("March 2026", "March 2026"),
        opening_balance=sv("1,000.00", "Opening 1,000.00"), contributions=sv("see note", "Contributions see note"),
        withdrawals=None, pnl=sv("10.00", "P&L 10.00"), fees=None, closing_balance=sv("2,000.00", "Closing 2,000.00"),
        holdings=[])
    text = "Account: Zeta Trust EUR March 2026 Opening 1,000.00 Contributions see note P&L 10.00 Closing 2,000.00"
    doc, status = run(s, "st.pdf", text, statements=[st])
    row = s.execute(select(Statement).where(Statement.document_id == doc.id)).scalar_one()
    assert row.review_reasons == ["unparseable_value"]  # no roll-forward check on an unparseable flow
    assert row.review_detail["unparseable_value"] == {"raw": "see note", "field": "contributions"}
    assert (row.period_end, row.currency, row.contributions) == (date(2026, 3, 31), "EUR", None)
    assert row.provenance["contributions"]["raw"] == "see note"
    assert row.provenance["pnl"]["snippet_verified"] is True


def test_statement_low_confidence_and_missing_snippets_list_fields(db_session):
    s = db_session
    st = StatementExtraction(
        account_name=sv("Eta LP", "Eta LP"), account_number_masked=None, custodian_or_manager=None, fund_name=None,
        currency=None, period_start=sv("01/01/2026", "01/01/2026"), period_end=sv("01/31/2026", "01/31/2026"),
        opening_balance=sv("100", "Opening 100", conf=0.4), contributions=None, withdrawals=None,
        pnl=sv("1", "elsewhere 1"), fees=None, closing_balance=sv("101", "Closing 101"), holdings=[])
    doc, status = run(s, "st2.pdf", "Eta LP 01/01/2026 01/31/2026 Opening 100 Closing 101", statements=[st])
    row = s.execute(select(Statement).where(Statement.document_id == doc.id)).scalar_one()
    assert row.review_detail["low_confidence"] == {"confidence": 0.4, "threshold": 0.7, "fields": ["opening_balance"]}
    assert row.review_detail["snippet_not_found"] == {"snippet": "elsewhere 1", "fields": ["pnl"]}
    assert "rollforward_mismatch" not in row.review_reasons  # 100 + 1 = 101 ties
    validate_review_detail(row.review_detail)


def test_reprocessing_replaces_rows_and_conflict_refreshes_other_document_status(db_session):
    s = db_session
    a, status_a = run(s, "fs.pdf", "Omega Fund Jan 2026 +1.92%",
                      [fund("Omega Fund", returns=[ret("Jan 2026", "+1.92%", "Jan 2026 +1.92%")])])
    a.status = status_a
    assert status_a == "done"
    b, status_b = run(s, "flash.eml", "Omega Fund Jan 2026 +1.89%",
                      [fund("Omega Fund", returns=[ret("Jan 2026", "+1.89%", "Jan 2026 +1.89%")])], mime="message/rfc822")
    assert status_b == "needs_review"
    assert s.get(Document, a.id).status == "needs_review"  # the other document was refreshed
    assert {r.status for r in returns_of(s, a) + returns_of(s, b)} == {"conflict"}
    # reprocess b with the final figure: the conflict clears on both sides
    ex = Extraction(document_type="email", document_type_confidence=0.9,
                    funds=[fund("Omega Fund", returns=[ret("Jan 2026", "+1.92%", "Jan 2026 +1.92%")])],
                    statements=[], pages=[PageText(page=1, text="x")], extra={})
    b.text_content = "Omega Fund Jan 2026 +1.92%"
    s.flush()
    xrow = ExtractionRow(document_id=b.id, status="ok", raw_json=ex.model_dump(mode="json"))
    s.add(xrow)
    s.flush()
    assert process_extraction(s, b.id, ex, extraction_id=xrow.id, today=TODAY) == "done"
    assert len(returns_of(s, b)) == 1
    assert {r.status for r in returns_of(s, a) + returns_of(s, b)} == {"ok"}
    assert s.get(Document, a.id).status == "done"


def test_alias_only_flag_makes_document_need_review(db_session):
    s = db_session
    run(s, "one.pdf", "Northwind Global Macro Fund", [fund("Northwind Global Macro Fund", as_of=None)])
    doc, status = run(s, "two.eml", "Northwind Macro Fund Ltd", [fund("Northwind Macro Fund Ltd", as_of=None)],
                      mime="message/rfc822")
    assert status == "needs_review" and document_status(s, doc.id) == "needs_review"


def test_fact_normalization(db_session):
    s = db_session
    metrics = {"Sharpe Ratio": sv("1.2", "Sharpe 1.2"), "Annualized Volatility": sv("9.4", "Vol (%) 9.4"),
               "Max DD": sv("(6.8%)", "Max DD (6.8%)"), "Liquidity": sv("Monthly", "Liquidity Monthly"),
               "NAV per share": sv("1,225.49", "NAV per share 1,225.49"), "Sortino": sv("high", "Sortino high")}
    text = "Theta Fund January 2026 AUM €2.5bn Sharpe 1.2 Vol (%) 9.4 Max DD (6.8%) Liquidity Monthly NAV per share 1,225.49 Sortino high"
    doc, status = run(s, "h.pdf", text, [fund("Theta Fund", metrics=metrics, aum=sv("€2.5bn", "AUM €2.5bn"))])
    facts = {f.name: f for f in s.execute(select(FundFact).where(FundFact.document_id == doc.id)).scalars()}
    assert (facts["aum"].value, facts["aum"].unit) == (D("2500000000"), "EUR")
    assert (facts["sharpe"].value, facts["sharpe"].unit) == (D("1.2"), "ratio")
    assert (facts["vol"].value, facts["vol"].unit) == (D("0.094"), "fraction")  # bare number, "(%)" in snippet
    assert (facts["max_drawdown"].value, facts["max_drawdown"].unit) == (D("-0.068"), "fraction")
    assert (facts["liquidity"].value, facts["liquidity"].value_text, facts["liquidity"].status) == (None, "Monthly", "ok")
    assert facts["nav_per_share"].value == D("1225.49")
    assert facts["sortino"].review_detail["unparseable_value"] == {"raw": "high", "field": "value"}
    assert all(f.as_of_date == date(2026, 1, 31) for f in facts.values())
    assert status == "needs_review"


def test_as_of_period_words_and_full_year(db_session):
    s = db_session
    f = fund("Harbor X", as_of=("Q4 2025", "Q4 2025"), returns=[
        ret("full-year", "7.9%", "full-year 7.9%", rt="YTD"), ret("Q4", "1.70%", "Q4 1.70%", rt="QTD"),
        ret("YTD", "7.9%", "YTD 7.9%", rt="YTD")])
    doc, status = run(s, "letter.pdf", "Harbor X Q4 2025 full-year 7.9% Q4 1.70% YTD 7.9%", [f])
    rows = {(r.period_label, r.return_type): r for r in returns_of(s, doc)}
    assert rows[("full-year", "YTD")].period_end == date(2025, 12, 31)
    assert rows[("Q4", "QTD")].period_end == date(2025, 12, 31)
    assert ("YTD", "YTD") not in rows  # same key and value as "full-year": one row
    assert status == "done"


def test_metric_names():
    assert metric_name("Sharpe Ratio") == "sharpe" and metric_name("Annualised vol") == "vol"
    assert metric_name("Fund AUM") == "aum" and metric_name("Max drawdown") == "max_drawdown"
    assert metric_name("NAV per share") == "nav_per_share" and metric_name("???") == "metric"


def test_pipeline_indexer_failure_never_fails_the_job(clean_db, tmp_path, monkeypatch):
    from app.ingest import postprocess as pp
    from tests.pipeline_helpers import FIXED_TODAY, ingest_samples

    monkeypatch.setattr(pp, "_today", lambda: FIXED_TODAY)
    calls = []

    def broken_indexer(engine, document_id):
        calls.append(document_id)
        raise RuntimeError("embedder exploded")

    statuses = ingest_samples(clean_db, tmp_path, ["alder_dubai_fund_january2026.pdf"], indexer=broken_indexer)
    assert statuses == {"alder_dubai_fund_january2026.pdf": "done"} and len(calls) == 1
    with clean_db.connect() as c:
        from sqlalchemy import text as sql

        row = c.execute(sql("SELECT status, search_indexed, last_error FROM documents")).one()
    assert (row.status, row.search_indexed, row.last_error) == ("done", False, None)


# ---- value must be written in its snippet (BUG-Q2-06) -------------------------------------------------------------


def test_value_not_in_its_snippet_is_unverified_and_flagged(db_session):
    s = db_session
    text = "Test Fund January 2026 Jan 2026 +1.0% Feb 2026 performance table"
    f = fund(returns=[ret("Jan 2026", "+1.0%", "Jan 2026 +1.0%"),
                      ret("Feb 2026", "+2.0%", "Feb 2026 performance table")])  # snippet found, value absent
    doc, status = run(s, "v.pdf", text, [f])
    rows = {r.period_label: r for r in returns_of(s, doc)}
    assert rows["Jan 2026"].snippet_verified is True and rows["Jan 2026"].status == "ok"
    feb = rows["Feb 2026"]
    assert feb.snippet_verified is False and feb.review_reasons == ["snippet_not_found"]
    assert feb.review_detail["snippet_not_found"] == {"snippet": "Feb 2026 performance table", "value_missing": True}
    validate_review_detail(feb.review_detail)
    assert status == "needs_review"


def test_value_check_on_facts_and_statement_fields(db_session):
    s = db_session
    f = fund(metrics={"sharpe": sv("1.4", "Sharpe ratio 1.4"), "volatility": sv("7.9%", "Volatility (ann.)")})
    text = "Test Fund January 2026 Sharpe ratio 1.4 Volatility (ann.) 7.9%"
    doc, _ = run(s, "facts.pdf", text, [f])
    facts = {x.name: x for x in s.execute(select(FundFact).where(FundFact.document_id == doc.id)).scalars()}
    assert facts["sharpe"].snippet_verified is True
    vol = next(x for x in facts.values() if x.unit == "fraction")
    assert vol.snippet_verified is False and vol.review_detail["snippet_not_found"]["value_missing"] is True

    st = StatementExtraction(
        account_name=sv("Iota LP", "Iota LP"), account_number_masked=None, custodian_or_manager=None, fund_name=None,
        currency=None, period_start=sv("01/01/2026", "01/01/2026"), period_end=sv("01/31/2026", "01/31/2026"),
        opening_balance=sv("100", "Opening 100"), contributions=sv("-", "Contributions -"), withdrawals=None,
        pnl=sv("1", "Opening 100"),  # snippet found, but it quotes the wrong line
        fees=sv("(3.00)", "Mgmt (1.00)\nPerf (2.00)"),  # a summed total over the quoted lines is fine
        closing_balance=sv("98", "Closing 98"), holdings=[])
    text = "Iota LP 01/01/2026 01/31/2026 Opening 100 Contributions - Mgmt (1.00)\nPerf (2.00) Closing 98"
    doc, _ = run(s, "st3.pdf", text, statements=[st])
    row = s.execute(select(Statement).where(Statement.document_id == doc.id)).scalar_one()
    assert row.provenance["pnl"]["snippet_verified"] is False
    assert row.provenance["fees"]["snippet_verified"] is True
    assert row.provenance["contributions"]["snippet_verified"] is True  # "-" = nil
    assert row.review_detail["snippet_not_found"] == {"snippet": "Opening 100", "fields": ["pnl"], "value_missing": True}
    validate_review_detail(row.review_detail)


def test_holding_row_snippet_is_the_one_that_quotes_its_numbers(db_session):
    from app.extraction.schema import Holding
    from app.models import StatementHolding

    s = db_session
    line = "Alpha Fund Hedge fund 9,800,000 20.7%"
    holdings = [
        # the market value's snippet stops before the weight (Birchwood fixtures): the name's is used
        Holding(name=sv("Alpha Fund", line), identifier=None, quantity=None,
                market_value=sv("9,800,000", "Alpha Fund Hedge fund 9,800,000"), weight=sv("20.7%", line)),
        # no snippet quotes the weight: the row is unverified and flagged
        Holding(name=sv("Beta Fund", "Beta Fund Cash 100"), identifier=None, quantity=None,
                market_value=sv("100", "Beta Fund Cash 100"), weight=sv("5.0%", "Beta Fund Cash 100")),
    ]
    st = StatementExtraction(
        account_name=sv("Kappa LP", "Kappa LP"), account_number_masked=None, custodian_or_manager=None, fund_name=None,
        currency=None, period_start=None, period_end=sv("01/31/2026", "01/31/2026"), opening_balance=None,
        contributions=None, withdrawals=None, pnl=None, fees=None, closing_balance=None, holdings=holdings)
    doc, _ = run(s, "h.pdf", f"Kappa LP 01/31/2026 {line} Beta Fund Cash 100 5.0%", statements=[st])
    rows = list(s.execute(select(StatementHolding).join(Statement).where(Statement.document_id == doc.id)
                          .order_by(StatementHolding.line_no)).scalars())
    assert rows[0].source_snippet == line and rows[0].snippet_verified is True and rows[0].status == "ok"
    assert rows[1].snippet_verified is False
    assert rows[1].review_detail["snippet_not_found"] == {
        "snippet": "Beta Fund Cash 100", "fields": ["weight"], "value_missing": True}

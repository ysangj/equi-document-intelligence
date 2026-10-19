"""Content checks on the committed fixtures (FR-14, §13), against sample_docs/MANIFEST.md.

The fixtures are real ClaudeExtractor output (scripts/gen_fixtures.py). These tests pin
what demo mode and T5 depend on: the fixtures are current for the sample files and
PROMPT_VERSION, every value has provenance whose snippet is verbatim in the stored
document text, and the MANIFEST ground truth and planted edge cases are captured as
written. Values are compared loosely in the document's own unit ("(0.36)%" -> -0.36,
"+110 bps" -> 110); normalization itself is tested in T5's modules.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator

import pytest

from app.extraction.content import mime_for_filename, pdf_text_layer, text_content_for
from app.extraction.fixture import FIXTURES_DIR, fixture_path
from app.extraction.schema import PROMPT_VERSION, Extraction, SourcedValue
from app.sources.local import DEFAULT_LOCAL_DIR

FIXTURES = sorted(FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.is_dir() else []


def _load(name: str) -> dict:
    return json.loads(fixture_path(FIXTURES_DIR, name).read_text(encoding="utf-8"))


def _ex(name: str) -> Extraction:
    return Extraction.model_validate(_load(name)["extraction"])


def _sourced(ex: Extraction) -> Iterator[tuple[str, SourcedValue]]:
    for i, f in enumerate(ex.funds):
        for k in ("fund_name", "manager", "strategy", "currency", "as_of_date", "aum"):
            if getattr(f, k) is not None:
                yield f"funds[{i}].{k}", getattr(f, k)
        for r in f.returns:
            yield f"funds[{i}].returns[{r.period}]", r.value
        for k, v in f.other_metrics.items():
            yield f"funds[{i}].other_metrics.{k}", v
    for i, s in enumerate(ex.statements):
        for k in type(s).model_fields:
            if k == "holdings":
                for j, h in enumerate(s.holdings):
                    for hk in type(h).model_fields:
                        if getattr(h, hk) is not None:
                            yield f"statements[{i}].holdings[{j}].{hk}", getattr(h, hk)
            elif getattr(s, k) is not None:
                yield f"statements[{i}].{k}", getattr(s, k)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _num(raw) -> float | None:
    """Loose number in the document's own unit; accounting parentheses are negative,
    a lone dash is zero."""
    if raw is None:
        return None
    t = str(raw).replace(",", "").replace("−", "-").strip()
    if t in ("-", "—"):
        return 0.0
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group())
    return -abs(v) if t.startswith("(") else v


def _return(doc: str, fund: str, period_re: str, return_type: str):
    for f in _ex(doc).funds:
        if fund.lower() in str(f.fund_name.value).lower():
            for r in f.returns:
                if r.return_type == return_type and re.search(period_re, r.period, re.I):
                    return r
    return None


# ---- currency of the fixtures ---------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fixture_is_current_for_sample_file_and_prompt(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    src = DEFAULT_LOCAL_DIR / data["source_file"]
    assert hashlib.md5(src.read_bytes()).hexdigest() == data["source_md5"], "sample changed: regenerate"
    assert data["prompt_version"] == PROMPT_VERSION, "prompt changed: run scripts/gen_fixtures.py"
    assert data["mime_type"] == mime_for_filename(data["source_file"])


# ---- provenance -----------------------------------------------------------------------------


def test_every_value_has_page_and_verbatim_snippet():
    """FR-5/FR-6: page + source_snippet on every value; the snippet appears in the
    stored document text (whitespace-normalized). Image-only PDFs are skipped."""
    checked, misses = 0, []
    for path in FIXTURES:
        name = json.loads(path.read_text(encoding="utf-8"))["source_file"]
        content = (DEFAULT_LOCAL_DIR / name).read_bytes()
        mime = mime_for_filename(name)
        has_text = mime != "application/pdf" or bool(pdf_text_layer(content))
        text = _norm(text_content_for(content, mime)) if has_text else ""
        for label, sv in _sourced(_ex(name)):
            if sv.value is None:
                continue
            assert sv.page is not None and sv.page >= 1, f"{name} {label}: no page"
            assert sv.source_snippet, f"{name} {label}: no snippet"
            assert 0 <= sv.confidence <= 1
            if has_text:
                checked += 1
                if _norm(sv.source_snippet) not in text:
                    misses.append(f"{name} {label}")
    assert checked > 250
    assert not misses, misses


def test_scanned_pdf_is_transcribed():
    content = (DEFAULT_LOCAL_DIR / "northwind_dec25_scanned.pdf").read_bytes()
    assert pdf_text_layer(content) == ""
    ex = _ex("northwind_dec25_scanned.pdf")
    assert ex.pages and all(p.text.strip() for p in ex.pages)
    r = _return("northwind_dec25_scanned.pdf", "northwind", r"dec", "MTD")
    assert r is not None and _num(r.value.value) == 1.40


# ---- MANIFEST ground truth --------------------------------------------------------------------

STATEMENTS = {
    "acme_family_office_statement_2026-01.pdf",
    "birchwood_foundation_statement_2026-02.pdf",
    "cedar_ridge_capital_account_kestrel_q4_2025.pdf",
}
REPORTS = {
    "alder_capital_q1_2026_performance_report.pdf",
    "harbor_credit_q4_2025_investor_letter.pdf",
    # admin CSV exports: prompt rule 1 classifies them as performance_report
    "harbor_admin_nav_export_2026-03.csv",
    "meridian_returns_export.csv",
}
EMAILS = {"northwind_flash_update_jan2026.eml", "meridian_pm_note_feb2026.eml"}


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_document_type(path):
    name = path.name[: -len(".json")]
    expected = ("account_statement" if name in STATEMENTS else "performance_report" if name in REPORTS
                else "email" if name in EMAILS else "factsheet")
    assert _ex(name).document_type == expected


@pytest.mark.parametrize("doc,fund,period,rtype,expected", [
    ("kestrel_factsheet_2026-01.pdf", "kestrel", r"jan", "MTD", 2.84),
    ("northwind_monthly_jan26.pdf", "northwind", r"2026.*jan|jan.*2026", "MTD", 1.92),
    ("northwind_flash_update_jan2026.eml", "northwind", r"jan", "MTD", 1.89),  # planted conflict
    ("meridian_rv_tearsheet_2026-01.pdf", "meridian", r"jan", "MTD", -0.36),  # parentheses
    ("meridian_rv_tearsheet_2026-02.pdf", "meridian", r"year|ytd", "YTD", 0.15),  # planted YTD sign error
    ("alder_asia_fund_january2026.pdf", "alder asia", r"jan", "MTD", 110),  # bps
    ("alder_asia_fund_march2026.pdf", "alder asia", r"mar", "MTD", -125),
    ("alder_dubai_fund_january2026.pdf", "alder dubai", r"jan", "MTD", 48),
    ("alder_dubai_fund_march2026.pdf", "alder dubai", r"mar", "MTD", 37),
    ("alder_capital_q1_2026_performance_report.pdf", "alder asia", r"feb", "MTD", 0.40),  # missing month
    ("alder_capital_q1_2026_performance_report.pdf", "alder asia", r"ytd", "YTD", 0.24),  # "Q1 / YTD"
    ("harbor_admin_nav_export_2026-03.csv", "harbor", r"2026-01", "MTD", 0.0071),  # decimal fraction
    ("meridian_returns_export.csv", "meridian", r"^1/31/2026$", "MTD", -0.36),
])
def test_ground_truth_returns(doc, fund, period, rtype, expected):
    r = _return(doc, fund, period, rtype)
    assert r is not None, f"{doc}: no {rtype} return for {period}"
    assert _num(r.value.value) == pytest.approx(expected)
    assert isinstance(r.value.value, str), "values are kept as written"


def test_mid_month_running_figure_is_not_a_return():
    """'Month-to-date we are up roughly 15bps' (18 Feb) must not become a Feb return
    that would conflict with the tear sheet's 0.21%."""
    assert all(not f.returns for f in _ex("meridian_pm_note_feb2026.eml").funds)


@pytest.mark.parametrize("doc,expected", [
    ("northwind_flash_update_jan2026.eml", ["Northwind Macro Fund Ltd"]),
    ("northwind_monthly_jan26.pdf", ["Northwind Global Macro Fund"]),
    ("alder_asia_fund_march2026.pdf", ["Alder Asia Fund SPC – Class A"]),
    ("alder_asia_fund_january2026.pdf", ["Alder Asia Fund"]),
    ("alder_dubai_fund_january2026.pdf", ["Alder Dubai Fund"]),
    ("alder_capital_q1_2026_performance_report.pdf", ["Alder Asia Fund", "Alder Dubai Fund"]),
    ("meridian_pm_note_feb2026.eml", ["Meridian Rates Relative Value Fund"]),
])
def test_fund_names_as_written(doc, expected):
    assert [f.fund_name.value for f in _ex(doc).funds] == expected


def test_year_less_columns_have_an_as_of_date():
    for f in _ex("alder_capital_q1_2026_performance_report.pdf").funds:
        assert f.as_of_date is not None and "2026" in str(f.as_of_date.value)


@pytest.mark.parametrize("doc,expected,holdings", [
    ("acme_family_office_statement_2026-01.pdf",
     dict(opening_balance=12_400_000, contributions=500_000, pnl=180_000, closing_balance=13_100_000), 5),
    ("birchwood_foundation_statement_2026-02.pdf",
     dict(opening_balance=48_200_000, contributions=0, withdrawals=-1_000_000, pnl=236_000, fees=-12_050,
          closing_balance=47_423_950), 5),
    ("cedar_ridge_capital_account_kestrel_q4_2025.pdf",
     dict(opening_balance=5_000_000, contributions=250_000, withdrawals=0, pnl=71_830, fees=-30_010,
          closing_balance=5_291_820), 0),
])
def test_statement_roll_forward_fields(doc, expected, holdings):
    (s,) = _ex(doc).statements
    for field, value in expected.items():
        sv = getattr(s, field)
        assert sv is not None and _num(sv.value) == value, field
    assert len(s.holdings) == holdings


@pytest.mark.parametrize("doc,ties", [
    ("birchwood_foundation_statement_2026-02.pdf", True),
    ("cedar_ridge_capital_account_kestrel_q4_2025.pdf", True),
    ("acme_family_office_statement_2026-01.pdf", False),  # planted: 13.08mm vs stated 13.10mm
])
def test_roll_forward_ties_only_where_manifest_says(doc, ties):
    """opening + contributions - withdrawals + pnl - fees, with withdrawals/fees taken
    as magnitudes (statements print them in parentheses)."""
    (s,) = _ex(doc).statements
    v = {k: abs(_num(getattr(s, k).value) or 0) if getattr(s, k) else 0.0
         for k in ("opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance")}
    expected = v["opening_balance"] + v["contributions"] - v["withdrawals"] + v["pnl"] - v["fees"]
    assert (abs(expected - v["closing_balance"]) < 0.01) is ties


@pytest.mark.parametrize("doc,phrases", [
    ("meridian_rv_tearsheet_2026-01.pdf", ["duration"]),
    ("meridian_rv_tearsheet_2026-02.pdf", ["rate sensitivity", "interest-rate exposure"]),
    ("harbor_credit_q4_2025_investor_letter.pdf", ["duration"]),
    ("alder_dubai_fund_january2026.pdf", ["spread duration"]),
    ("meridian_pm_note_feb2026.eml", ["interest-rate exposure", "rate sensitivity", "duration"]),
])
def test_commentary_synonyms_are_in_page_text(doc, phrases):
    """Hybrid retrieval (§8) needs the commentary passages in pages[].text."""
    text = _norm(" ".join(p.text for p in _ex(doc).pages))
    for phrase in phrases:
        assert phrase in text, phrase

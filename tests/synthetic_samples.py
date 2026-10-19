"""Synthetic §9 extractions for every file in sample_docs/ (test data, not fixtures).

`fixtures/` is reserved for real extractor output, which is blocked on Anthropic credits.
These mirror what the extractor is asked to return for each sample document (values
as written, verbatim snippets copied from the files' text layers, pages, confidence)
and include the planted edge cases of sample_docs/MANIFEST.md:
- Northwind Jan-26: factsheet 1.92 vs flash email +1.89 (conflict)
- "Northwind Macro Fund Ltd" vs "Northwind Global Macro Fund" (alias)
- "Alder Asia Fund SPC – Class A" vs "Alder Asia Fund" (alias); Alder Dubai is a sibling
- Meridian Feb YTD 0.15% vs compounded -0.15% (YTD mismatch)
- Acme Jan roll-forward 13.08mm vs stated 13.10mm (mismatch)
- Alder Asia Feb 0.40% only in the Q1 multi-fund report (missing month)
- bps, parentheses, decimal fractions in the Harbor CSV, % columns in the Meridian CSV
- the scanned Northwind Dec-25 PDF (no text layer: transcription only)

`SAMPLE_EXTRACTIONS[name]` is an `Extraction`. `write_sample_fixtures(dir, docs_dir)`
writes them in the FixtureExtractor file format, for pipeline/worker runs.
"""

from __future__ import annotations

from pathlib import Path

from app.extraction.content import mime_for_filename, text_content_for
from app.extraction.schema import (
    Extraction,
    FundExtraction,
    Holding,
    PageText,
    ReturnEntry,
    SourcedValue,
    StatementExtraction,
)
from app.sources.local import DEFAULT_LOCAL_DIR
from tests.ingest_helpers import write_fixture


def sv(value, snippet, page=1, conf=0.95) -> SourcedValue:
    return SourcedValue(value=value, page=page, source_snippet=snippet, confidence=conf)


def ret(period, value, snippet, rt="MTD", ng="net", page=1, conf=0.95) -> ReturnEntry:
    return ReturnEntry(period=period, value=sv(value, snippet, page, conf), return_type=rt, net_gross=ng)


def _file_pages(name: str) -> list[PageText]:
    p = DEFAULT_LOCAL_DIR / name
    text = text_content_for(p.read_bytes(), mime_for_filename(name), [])
    return [PageText(page=i + 1, text=t) for i, t in enumerate(text.split("\f"))]


def _ex(doc_type, name, funds=(), statements=(), pages=None, extra=None, conf=0.97) -> Extraction:
    return Extraction(document_type=doc_type, document_type_confidence=conf, funds=list(funds),
                      statements=list(statements), pages=pages if pages is not None else _file_pages(name),
                      extra=extra or {})


# ---- Kestrel (template A) ----------------------------------------------------------------------

_K_MONTHS = [("Jan 2026", "+2.84%"), ("Feb 2026", "-0.92%"), ("Mar 2026", "+1.35%")]
_K_YTD = {1: "+2.84%", 2: "+1.89%", 3: "+3.27%"}
_K_LABEL = {1: "January 2026", 2: "February 2026", 3: "March 2026"}


def kestrel(month: int) -> Extraction:
    name = f"kestrel_factsheet_2026-0{month}.pdf"
    mtd_period, mtd = _K_MONTHS[month - 1]
    returns = [
        ret(mtd_period, mtd, f"MTD % (NET) {mtd}"),
        ret(f"YTD {2026}", _K_YTD[month], f"YTD % (NET) {_K_YTD[month]}", rt="YTD"),
    ]
    # the monthly table repeats every month so far (the current one duplicates the tile)
    returns += [ret(p, v, f"{p} {v}", conf=0.93) for p, v in _K_MONTHS[:month]]
    fund = FundExtraction(
        fund_name=sv("Kestrel Quant Equity Fund LP", "Kestrel Quant Equity Fund LP"),
        manager=sv("Kestrel Partners LLC", f"Monthly Factsheet · {_K_LABEL[month]} · Kestrel Partners LLC"),
        strategy=sv("Systematic equity market neutral", "Systematic equity market neutral"),
        currency=sv("USD", "FUND AUM $412.6mm", conf=0.8),
        as_of_date=sv(_K_LABEL[month], f"Monthly Factsheet · {_K_LABEL[month]}"),
        aum=sv("$412.6mm", "FUND AUM $412.6mm"),
        returns=returns,
        other_metrics={
            "sharpe": sv("1.84", "SHARPE (SINCE INCEPT.) 1.84"),
            "max_drawdown": sv("-6.8%", "Max drawdown (since incept.) -6.8%"),
            "volatility": sv("7.6%", "annualized volatility since inception 7.6%", page=2),
            "inception_date": sv("March 2021", "Inception March 2021"),
            "management_fee": sv("1.5% management / 20% performance", "1.5% management / 20% performance"),
        },
    )
    return _ex("factsheet", name, [fund], extra={"estimate": "Figures for the latest month are estimates"})


# ---- Northwind (template B) -------------------------------------------------------------------

def _northwind_fund(label: str, returns, conf=0.95) -> FundExtraction:
    return FundExtraction(
        fund_name=sv("Northwind Global Macro Fund", "Northwind Global Macro Fund", conf=conf),
        manager=sv("Northwind Capital Management", "Northwind Capital Management", conf=conf),
        strategy=sv("Discretionary global macro", "Strategy: Discretionary global macro", conf=conf),
        currency=sv("USD", "Base currency: USD", conf=conf),
        as_of_date=sv(label, f"Monthly Update · {label}", conf=conf),
        aum=sv("USD 1.18bn", "Fund assets: USD 1.18bn", conf=conf),
        returns=returns,
        other_metrics={
            "annualised_vol": sv("9.4%", "Annualised vol: 9.4%", conf=conf),
            "sharpe_3y": sv("1.12", "Sharpe (3y): 1.12", conf=conf),
        },
    )


def _nw_2025(conf=0.95):
    s = "2025 -0.30 0.85 1.40"
    return [ret("Oct 2025", "-0.30", s, conf=conf), ret("Nov 2025", "0.85", s, conf=conf),
            ret("Dec 2025", "1.40", s, conf=conf), ret("2025", "1.96", "1.40 1.96", rt="YTD", conf=conf)]


def northwind_jan() -> Extraction:
    returns = _nw_2025() + [ret("Jan 2026", "1.92", "2026 1.92"),
                            ret("2026", "1.92", "2026 1.92 1.92", rt="YTD")]
    return _ex("factsheet", "northwind_monthly_jan26.pdf", [_northwind_fund("Jan-26", returns)])


def northwind_feb() -> Extraction:
    returns = _nw_2025() + [ret("Jan 2026", "1.92", "2026 1.92 0.55 2.48"),
                            ret("Feb 2026", "0.55", "2026 1.92 0.55 2.48"),
                            ret("2026", "2.48", "2026 1.92 0.55 2.48", rt="YTD")]
    return _ex("factsheet", "northwind_monthly_feb26.pdf", [_northwind_fund("Feb-26", returns)])


NORTHWIND_DEC_TRANSCRIPTION = (
    "Northwind Global Macro Fund\nNorthwind Capital Management · Monthly Update · Dec-25\n"
    "Perf. (net) — %\nYear Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec YTD\n2025 -0.30 0.85 1.40 1.96\n\n"
    "Review\nDecember was driven by our curve steepener in EUR rates and long USD vs. a basket of Asian "
    "currencies.\n\nFund Facts\nStrategy: Discretionary global macro\nBase currency: USD\nFund assets: USD 1.18bn\n"
    "Dealing: Monthly; 45 days notice\nMgmt / perf fee: 2% / 20%\nAnnualised vol: 9.4%\nSharpe (3y): 1.12\n"
    "Share class A USD. Performance is net of all fees. Fictional fund for demonstration purposes."
)


def northwind_dec_scanned() -> Extraction:
    fund = _northwind_fund("Dec-25", _nw_2025(conf=0.82), conf=0.85)
    return _ex("factsheet", "northwind_dec25_scanned.pdf", [fund],
               pages=[PageText(page=1, text=NORTHWIND_DEC_TRANSCRIPTION)], conf=0.9)


def northwind_flash() -> Extraction:
    fund = FundExtraction(
        fund_name=sv("Northwind Macro Fund Ltd", "flash estimate for Northwind Macro Fund Ltd"),
        manager=None, strategy=None,
        currency=sv("USD", "Class A USD"),
        as_of_date=sv("January 2026", "for January 2026"),
        aum=None,
        returns=[ret("Jan 2026", "+1.89%", "Jan 2026 +1.89%", conf=0.9),
                 ret("YTD 2026", "+1.89%", "YTD 2026 +1.89%", rt="YTD", conf=0.9)],
        other_metrics={},
    )
    return _ex("email", "northwind_flash_update_jan2026.eml", [fund], extra={"estimate": "flash estimate"})


# ---- Meridian (template C, CSV, email) ---------------------------------------------------------------

def _meridian_fund(as_of: str, returns, aum=True) -> FundExtraction:
    return FundExtraction(
        fund_name=sv("Meridian Rates Relative Value Fund", "MERIDIAN RATES RELATIVE VALUE FUND"),
        manager=sv("Meridian Capital Advisors", "Manager Meridian Capital Advisors"),
        strategy=sv("Fixed income relative value", "Strategy Fixed income relative value"),
        currency=None,
        as_of_date=sv(as_of, f"Tear Sheet | As of {as_of}"),
        aum=sv("$640mm", "AUM $640mm") if aum else None,
        returns=returns,
        other_metrics={"leverage_gross": sv("6.2x", "Leverage (gross) 6.2x")},
    )


def meridian_jan() -> Extraction:
    returns = [ret("January", "(0.36)%", "NET RETURN January (0.36)%"),
               ret("Year to date", "(0.36)%", "Year to date (0.36)%", rt="YTD"),
               ret("January", "(0.36)%", "MONTHLY NET RETURN 2026 January (0.36)%", conf=0.93)]
    return _ex("factsheet", "meridian_rv_tearsheet_2026-01.pdf", [_meridian_fund("01/31/2026", returns)])


def meridian_feb() -> Extraction:
    returns = [ret("February", "0.21%", "NET RETURN February 0.21%"),
               ret("Year to date", "0.15%", "Year to date 0.15%", rt="YTD", conf=0.92),
               ret("January", "(0.36)%", "MONTHLY NET RETURN 2026 January (0.36)%", conf=0.93),
               ret("February", "0.21%", "February 0.21%", conf=0.93)]
    return _ex("factsheet", "meridian_rv_tearsheet_2026-02.pdf", [_meridian_fund("02/28/2026", returns)])


_MERIDIAN_CSV_ROWS = [
    ("10/31/2025", "0.22", "0.43", ""), ("11/30/2025", "0.31", "0.52", ""), ("12/31/2025", "0.18", "0.39", ""),
    ("1/31/2026", "-0.36", "-0.15", ""), ("2/28/2026", "0.21", "0.42", ""), ("3/31/2026", "0.27", "0.48", "estimate"),
]


def meridian_csv() -> Extraction:
    returns = []
    for d, net, gross, note in _MERIDIAN_CSV_ROWS:
        row = f"Meridian Rates RV Fund,{d},{net},{gross},{note}"
        conf = 0.8 if note else 0.95
        returns += [ret(d, net, row, conf=conf), ret(d, gross, row, ng="gross", conf=conf)]
    fund = FundExtraction(fund_name=sv("Meridian Rates RV Fund", "Meridian Rates RV Fund"), manager=None,
                          strategy=None, currency=None, as_of_date=sv("3/31/2026", "3/31/2026"), aum=None,
                          returns=returns, other_metrics={})
    return _ex("performance_report", "meridian_returns_export.csv", [fund],
               extra={"estimate": "Mar 2026 row noted estimate"}, conf=0.85)


def meridian_pm_note() -> Extraction:
    fund = FundExtraction(
        fund_name=sv("Meridian Rates Relative Value Fund", "Portfolio Manager, Meridian Rates Relative Value Fund"),
        manager=None, strategy=None, currency=None, as_of_date=sv("18 Feb 2026", "18 Feb 2026"), aum=None,
        returns=[], other_metrics={},
    )
    return _ex("email", "meridian_pm_note_feb2026.eml", [fund], conf=0.9)


# ---- Alder (template D: bps; multi-fund report) --------------------------------------------------------

def _alder(name_value: str, label: str, mtd: str, ytd: str, nav: str, strategy: str, file: str) -> Extraction:
    fund = FundExtraction(
        fund_name=sv(name_value, name_value),
        manager=sv("Alder Capital Pte. Ltd.", "Investment manager Alder Capital Pte. Ltd."),
        strategy=sv(strategy, f"Strategy {strategy}"),
        currency=sv("USD", f"USD {nav}"),
        as_of_date=sv(label, f"Investor Report — {label}"),
        aum=sv(f"USD {nav}", f"Net asset value USD {nav}"),
        returns=[ret(label, mtd, f"Month (net of fees) {mtd}"),
                 ret(label, ytd, f"Year to date (net) {ytd}", rt="YTD")],
        other_metrics={
            "management_fee": sv("1.25% p.a.", "Management fee 1.25% p.a."),
            "incentive_fee": sv("15% over a 5% hurdle", "Incentive fee 15% over a 5% hurdle"),
        },
    )
    return _ex("factsheet", file, [fund])


def alder_asia_jan() -> Extraction:
    return _alder("Alder Asia Fund", "January 2026", "+110 bps", "+110 bps", "284.3mm",
                  "Asia-Pacific equity long/short", "alder_asia_fund_january2026.pdf")


def alder_asia_mar() -> Extraction:
    return _alder("Alder Asia Fund SPC – Class A", "March 2026", "-125 bps", "+24 bps", "279.9mm",
                  "Asia-Pacific equity long/short", "alder_asia_fund_march2026.pdf")


def alder_dubai_jan() -> Extraction:
    return _alder("Alder Dubai Fund", "January 2026", "+48 bps", "+48 bps", "156.0mm", "MENA credit",
                  "alder_dubai_fund_january2026.pdf")


def alder_dubai_mar() -> Extraction:
    return _alder("Alder Dubai Fund", "March 2026", "+37 bps", "+138 bps", "158.7mm", "MENA credit",
                  "alder_dubai_fund_march2026.pdf")


def alder_report() -> Extraction:
    def fund(name, jan, feb, mar, q1):
        s = f"{name} {jan} {feb} {mar} {q1}"
        return FundExtraction(
            fund_name=sv(name, name), manager=None, strategy=None, currency=None,
            as_of_date=sv("Q1 2026", "Quarterly Performance Report, Q1 2026"), aum=None,
            returns=[ret("Jan", jan, s), ret("Feb", feb, s), ret("Mar", mar, s),
                     ret("Q1 2026", q1, s, rt="QTD"), ret("Q1 2026", q1, s, rt="YTD")],
            other_metrics={},
        )
    return _ex("performance_report", "alder_capital_q1_2026_performance_report.pdf",
               [fund("Alder Asia Fund", "1.10%", "0.40%", "-1.25%", "0.24%"),
                fund("Alder Dubai Fund", "0.48%", "0.52%", "0.37%", "1.38%")])


# ---- Harbor (letter + admin CSV) -----------------------------------------------------------------------------

def harbor_letter() -> Extraction:
    fund = FundExtraction(
        fund_name=sv("Harbor Credit Opportunities Fund", "Harbor Credit Opportunities Fund returned 1.70% net"),
        manager=sv("Harbor Asset Management", "HARBOR ASSET MANAGEMENT"),
        strategy=None, currency=None,
        as_of_date=sv("Q4 2025", "in the fourth quarter of 2025"),
        aum=sv("$905 million", "Fund AUM at quarter end was $905 million"),
        returns=[ret("October 2025", "0.58%", "October 2025 0.58%"),
                 ret("November 2025", "0.62%", "November 2025 0.62%"),
                 ret("December 2025", "0.49%", "December 2025 0.49%"),
                 ret("Q4 2025", "1.70%", "Q4 2025 1.70%", rt="QTD"),
                 ret("2025", "7.9%", "full-year performance to 7.9% net", rt="YTD", conf=0.9)],
        other_metrics={"duration": sv("1.3 years", "Portfolio duration remains short at roughly 1.3 years")},
    )
    return _ex("performance_report", "harbor_credit_q4_2025_investor_letter.pdf", [fund])


_HARBOR_ROWS = [("2025-10", "1189.26", "0.0058", "910249000"), ("2025-11", "1196.63", "0.0062", "915892544"),
                ("2025-12", "1202.49", "0.0049", "920380417"), ("2026-01", "1211.03", "0.0071", "926915118"),
                ("2026-02", "1218.78", "0.0064", "932847375"), ("2026-03", "1225.49", "0.0055", "937978036")]


def harbor_csv() -> Extraction:
    rows = {p: f"HCO-A,Harbor Credit Opps Fund,A,{p},{nav},{r},{aum}" for p, nav, r, aum in _HARBOR_ROWS}
    last = _HARBOR_ROWS[-1]
    fund = FundExtraction(
        fund_name=sv("Harbor Credit Opps Fund", "Harbor Credit Opps Fund"), manager=None, strategy=None,
        currency=sv("USD", "AUM_USD", conf=0.85), as_of_date=sv("2026-03", rows["2026-03"]),
        aum=sv(last[3], rows["2026-03"]),
        returns=[ret(p, r, rows[p]) for p, _, r, _ in _HARBOR_ROWS],
        other_metrics={"nav_per_share": sv(last[1], rows["2026-03"])},
    )
    return _ex("performance_report", "harbor_admin_nav_export_2026-03.csv", [fund], conf=0.85)


# ---- account statements ---------------------------------------------------------------------------------------

def _larkspur(account, number, label, start, end, opening, contrib, withdr, pnl, fees, closing, holdings, file):
    summary = f"Account Summary — {start} to {end} (USD)"
    return _ex("account_statement", file, statements=[StatementExtraction(
        account_name=sv(account, f"Account: {account}"),
        account_number_masked=sv(number, f"Acct No. {number}"),
        custodian_or_manager=sv("Larkspur Trust Company", "Larkspur Trust Company"),
        fund_name=None,
        currency=sv("USD", summary),
        period_start=sv(start, summary),
        period_end=sv(end, summary),
        opening_balance=sv(opening, f"Beginning market value {opening}"),
        contributions=sv(contrib, f"Contributions {contrib}"),
        withdrawals=sv(withdr, f"Withdrawals {withdr}"),
        pnl=sv(pnl, f"Investment gain / (loss) {pnl}"),
        fees=sv(fees, f"Fees {fees}"),
        closing_balance=sv(closing, f"Ending market value {closing}"),
        holdings=[Holding(name=sv(n, n), identifier=None, quantity=None,
                          market_value=sv(mv, f"{n} {kind} {mv} {w}"), weight=sv(w, f"{n} {kind} {mv} {w}"))
                  for n, kind, mv, w in holdings],
    )])


def acme() -> Extraction:
    return _larkspur(
        "Acme Family Office LLC", "XXXX-4471", "January 2026", "01/01/2026", "01/31/2026",
        "12,400,000.00", "500,000.00", "(0.00)", "180,000.00", "(0.00)", "13,100,000.00",
        [("Kestrel Quant Equity Fund LP", "Hedge fund", "4,250,000", "32.4%"),
         ("Northwind Global Macro Fund", "Hedge fund", "3,600,000", "27.5%"),
         ("Harbor Credit Opportunities Fund", "Private credit", "3,150,000", "24.0%"),
         ("US Treasury Bills", "Cash equivalent", "1,820,000", "13.9%"),
         ("Cash", "Cash", "280,000", "2.1%")],
        "acme_family_office_statement_2026-01.pdf")


def birchwood() -> Extraction:
    return _larkspur(
        "Birchwood Foundation", "XXXX-9025", "February 2026", "02/01/2026", "02/28/2026",
        "48,200,000.00", "0.00", "(1,000,000.00)", "236,000.00", "(12,050.00)", "47,423,950.00",
        [("Meridian Rates Relative Value Fund", "Hedge fund", "14,100,000", "29.7%"),
         ("Alder Asia Fund", "Hedge fund", "9,800,000", "20.7%"),
         ("Alder Dubai Fund", "Hedge fund", "7,400,000", "15.6%"),
         ("Kestrel Quant Equity Fund LP", "Hedge fund", "11,900,000", "25.1%"),
         ("Cash", "Cash", "4,223,950", "8.9%")],
        "birchwood_foundation_statement_2026-02.pdf")


def cedar_ridge() -> Extraction:
    return _ex("account_statement", "cedar_ridge_capital_account_kestrel_q4_2025.pdf", statements=[StatementExtraction(
        account_name=sv("Cedar Ridge Partners LP", "Limited Partner: Cedar Ridge Partners LP"),
        account_number_masked=sv("CRP-0042", "Investor ID: CRP-0042"),
        custodian_or_manager=sv("Brightline Fund Services (Cayman) Ltd.", "BRIGHTLINE FUND SERVICES (CAYMAN) LTD."),
        fund_name=sv("Kestrel Quant Equity Fund LP", "Kestrel Quant Equity Fund LP"),
        currency=sv("USD", "USD 5,000,000.00"),
        period_start=sv("1 October 2025", "Capital account, 1 October 2025"),
        period_end=sv("31 December 2025", "For the quarter ended 31 December 2025"),
        opening_balance=sv("USD 5,000,000.00", "Capital account, 1 October 2025 USD 5,000,000.00"),
        contributions=sv("250,000.00", "Subscriptions 250,000.00"),
        withdrawals=sv("-", "Redemptions -"),
        pnl=sv("71,830.00", "Net profit / (loss) allocated 71,830.00"),
        fees=sv("(30,010.00)", "Management fee (19,560.00)\nIncentive allocation (10,450.00)", conf=0.85),
        closing_balance=sv("USD 5,291,820.00", "Capital account, 31 December 2025 USD 5,291,820.00"),
        holdings=[],
    )])


SAMPLE_BUILDERS = {
    "acme_family_office_statement_2026-01.pdf": acme,
    "alder_asia_fund_january2026.pdf": alder_asia_jan,
    "alder_asia_fund_march2026.pdf": alder_asia_mar,
    "alder_capital_q1_2026_performance_report.pdf": alder_report,
    "alder_dubai_fund_january2026.pdf": alder_dubai_jan,
    "alder_dubai_fund_march2026.pdf": alder_dubai_mar,
    "birchwood_foundation_statement_2026-02.pdf": birchwood,
    "cedar_ridge_capital_account_kestrel_q4_2025.pdf": cedar_ridge,
    "harbor_admin_nav_export_2026-03.csv": harbor_csv,
    "harbor_credit_q4_2025_investor_letter.pdf": harbor_letter,
    "kestrel_factsheet_2026-01.pdf": lambda: kestrel(1),
    "kestrel_factsheet_2026-02.pdf": lambda: kestrel(2),
    "kestrel_factsheet_2026-03.pdf": lambda: kestrel(3),
    "meridian_pm_note_feb2026.eml": meridian_pm_note,
    "meridian_returns_export.csv": meridian_csv,
    "meridian_rv_tearsheet_2026-01.pdf": meridian_jan,
    "meridian_rv_tearsheet_2026-02.pdf": meridian_feb,
    "northwind_dec25_scanned.pdf": northwind_dec_scanned,
    "northwind_flash_update_jan2026.eml": northwind_flash,
    "northwind_monthly_feb26.pdf": northwind_feb,
    "northwind_monthly_jan26.pdf": northwind_jan,
}


def sample_extraction(name: str) -> Extraction:
    return SAMPLE_BUILDERS[name]()


def write_sample_fixtures(fixtures_dir: Path, docs_dir: Path = DEFAULT_LOCAL_DIR) -> Path:
    """FixtureExtractor files for every sample document, into `fixtures_dir` (a temp dir)."""
    for name, build in SAMPLE_BUILDERS.items():
        content = (docs_dir / name).read_bytes()
        write_fixture(fixtures_dir, name, content, build(), mime=mime_for_filename(name))
    return fixtures_dir

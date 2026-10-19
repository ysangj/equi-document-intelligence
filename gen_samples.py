"""Generate synthetic fund documents for the Document Intelligence take-home.

All funds, managers, accounts and numbers are fictional.
"""
import csv
import io
import random
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from weasyprint import HTML
from PIL import Image, ImageFilter, ImageOps

OUT = Path(__file__).parent / "sample_docs"
OUT.mkdir(exist_ok=True)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FULL = ["January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December"]

# Monthly net returns in percent, keyed (year, month)
R = {
    "kestrel": {(2025, 10): 0.65, (2025, 11): 1.12, (2025, 12): -0.40,
                (2026, 1): 2.84, (2026, 2): -0.92, (2026, 3): 1.35},
    "northwind": {(2025, 10): -0.30, (2025, 11): 0.85, (2025, 12): 1.40,
                  (2026, 1): 1.92, (2026, 2): 0.55},
    "meridian": {(2025, 10): 0.22, (2025, 11): 0.31, (2025, 12): 0.18,
                 (2026, 1): -0.36, (2026, 2): 0.21, (2026, 3): 0.27},
    "alder_asia": {(2026, 1): 1.10, (2026, 2): 0.40, (2026, 3): -1.25},
    "alder_dubai": {(2026, 1): 0.48, (2026, 2): 0.52, (2026, 3): 0.37},
    "harbor": {(2025, 10): 0.58, (2025, 11): 0.62, (2025, 12): 0.49,
               (2026, 1): 0.71, (2026, 2): 0.64, (2026, 3): 0.55},
}


def ytd(fund, year, month):
    acc = 1.0
    for m in range(1, month + 1):
        if (year, m) in R[fund]:
            acc *= 1 + R[fund][(year, m)] / 100
    return (acc - 1) * 100


def pct(v, style="sign"):
    if v is None:
        return "—"
    if style == "paren":
        return f"({abs(v):.2f})%" if v < 0 else f"{v:.2f}%"
    if style == "bps":
        return f"{v * 100:+.0f} bps"
    return f"{v:+.2f}%"


def pdf(name, html):
    HTML(string=html).write_pdf(OUT / name)


# ---------------------------------------------------------------- Template A: Kestrel
def kestrel(month):
    y = 2026
    rows = "".join(
        f"<tr><td>{MONTHS[m-1]} {y}</td><td>{pct(R['kestrel'].get((y, m)))}</td></tr>"
        for m in range(1, month + 1))
    mtd = R["kestrel"][(y, month)]
    comm = {
        1: "January gains came primarily from short-term reversal signals in US large caps. "
           "As dispersion compressed in the final week, we reduced gross exposure from 3.1x to 2.6x. "
           "Factor exposures remained tightly controlled; net beta to the S&P 500 averaged 0.02.",
        2: "February was a difficult month for the fund. A sharp momentum unwind mid-month hurt our "
           "medium-horizon signals, and we did not fully offset the move with our short-term book. "
           "We kept gross exposure near 2.6x and see no structural change in signal efficacy.",
        3: "March recovered most of February's drawdown. Earnings-revision signals contributed the "
           "bulk of returns, while the reversal sleeve was flat. Gross exposure ended the month at 2.9x.",
    }[month]
    html = f"""
<html><head><style>
@page {{ size: A4; margin: 0; }}
body {{ font-family: 'DejaVu Sans', sans-serif; margin: 0; color: #1d2733; font-size: 10.5pt; }}
.hdr {{ background: #12355b; color: white; padding: 26px 36px; }}
.hdr h1 {{ margin: 0; font-size: 20pt; }} .hdr p {{ margin: 4px 0 0; opacity: .8; }}
.wrap {{ padding: 24px 36px; }}
.tiles {{ display: flex; gap: 12px; }}
.tile {{ flex: 1; border: 1px solid #d6dde6; border-radius: 6px; padding: 10px 12px; }}
.tile b {{ display: block; font-size: 16pt; color: #12355b; }}
.tile span {{ font-size: 8.5pt; color: #6b7785; text-transform: uppercase; letter-spacing: .05em; }}
h2 {{ font-size: 11pt; color: #12355b; border-bottom: 2px solid #12355b; padding-bottom: 4px; margin-top: 22px; }}
table {{ border-collapse: collapse; width: 60%; }} td, th {{ padding: 5px 8px; border-bottom: 1px solid #e3e8ee; text-align: left; }}
th {{ background: #eef2f7; font-size: 9pt; }}
.terms td:first-child {{ color: #6b7785; width: 45%; }}
.pb {{ page-break-before: always; }}
.foot {{ font-size: 7.5pt; color: #8a95a3; margin-top: 30px; }}
</style></head><body>
<div class="hdr"><h1>Kestrel Quant Equity Fund LP</h1>
<p>Monthly Factsheet · {FULL[month-1]} {y} · Kestrel Partners LLC</p></div>
<div class="wrap">
<div class="tiles">
 <div class="tile"><span>MTD % (net)</span><b>{pct(mtd)}</b></div>
 <div class="tile"><span>YTD % (net)</span><b>{pct(ytd('kestrel', y, month))}</b></div>
 <div class="tile"><span>Fund AUM</span><b>$412.6mm</b></div>
 <div class="tile"><span>Sharpe (since incept.)</span><b>1.84</b></div>
</div>
<h2>Strategy</h2>
<p>Systematic equity market neutral. Long/short US and European large caps using short-term
reversal, earnings-revision and quality signals. Target annualized volatility 8%.</p>
<h2>Monthly Performance (MTD %, net of fees)</h2>
<table><tr><th>Month</th><th>MTD %</th></tr>{rows}</table>
<h2>Key Terms</h2>
<table class="terms">
<tr><td>Inception</td><td>March 2021</td></tr>
<tr><td>Liquidity</td><td>Monthly, 30 days' notice</td></tr>
<tr><td>Fees</td><td>1.5% management / 20% performance</td></tr>
<tr><td>Administrator</td><td>Brightline Fund Services</td></tr>
<tr><td>Max drawdown (since incept.)</td><td>-6.8%</td></tr>
</table>
<div class="pb"></div>
<h2>Portfolio Manager Commentary</h2>
<p>{comm}</p>
<p>Risk metrics: annualized volatility since inception 7.6%; correlation to MSCI World 0.04.</p>
<div class="foot">For professional investors only. Past performance is not indicative of future results.
Figures for the latest month are estimates until finalized by the administrator. Fictional fund for demonstration.</div>
</div></body></html>"""
    pdf(f"kestrel_factsheet_2026-{month:02d}.pdf", html)


# ---------------------------------------------------------------- Template B: Northwind
def northwind_html(y, month, extra_note=""):
    years = sorted({k[0] for k in R["northwind"]})
    head = "".join(f"<th>{m}</th>" for m in MONTHS) + "<th>YTD</th>"
    body = ""
    for yy in years:
        if yy > y:
            continue
        cells = ""
        last = month if yy == y else 12
        for m in range(1, 13):
            v = R["northwind"].get((yy, m)) if m <= last else None
            cells += f"<td>{'' if v is None else f'{v:.2f}'}</td>"
        cells += f"<td><b>{ytd('northwind', yy, last):.2f}</b></td>"
        body += f"<tr><td><b>{yy}</b></td>{cells}</tr>"
    return f"""
<html><head><style>
@page {{ size: A4 landscape; margin: 18mm; }}
body {{ font-family: 'DejaVu Serif', serif; color: #222; font-size: 10pt; }}
.top {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px double #6d1f1f; padding-bottom: 6px; }}
.top h1 {{ font-size: 19pt; margin: 0; color: #6d1f1f; }}
.cols {{ display: flex; gap: 28px; margin-top: 14px; }}
.left {{ flex: 2; }} .right {{ flex: 1; background: #f6f1ea; padding: 10px 14px; }}
table.perf {{ border-collapse: collapse; width: 100%; font-size: 8.5pt; }}
table.perf td, table.perf th {{ border: 1px solid #c9bfb3; padding: 3px 4px; text-align: center; }}
table.perf th {{ background: #6d1f1f; color: white; }}
h3 {{ color: #6d1f1f; font-size: 11pt; margin: 14px 0 6px; }}
.right p {{ margin: 3px 0; }}
.small {{ font-size: 7.5pt; color: #777; margin-top: 16px; }}
</style></head><body>
<div class="top"><h1>Northwind Global Macro Fund</h1>
<div>Northwind Capital Management · Monthly Update · {MONTHS[month-1]}-{str(y)[2:]}</div></div>
<div class="cols"><div class="left">
<h3>Perf. (net) — %</h3>
<table class="perf"><tr><th>Year</th>{head}</tr>{body}</table>
<h3>Review</h3>
<p>{'The fund benefited from long positions in front-end US rates as markets priced additional cuts, and from a short JPY position into the BoJ meeting. Equity index hedges were a small drag.' if month == 1 else 'Returns were modest. Gains in commodity relative value offset small losses in EM FX. We added to long gold and trimmed USD exposure.' if month == 2 else 'December was driven by our curve steepener in EUR rates and long USD vs. a basket of Asian currencies.'}</p>
{extra_note}
</div><div class="right">
<h3>Fund Facts</h3>
<p><b>Strategy:</b> Discretionary global macro</p>
<p><b>Base currency:</b> USD</p>
<p><b>Fund assets:</b> USD 1.18bn</p>
<p><b>Dealing:</b> Monthly; 45 days notice</p>
<p><b>Mgmt / perf fee:</b> 2% / 20%</p>
<p><b>Annualised vol:</b> 9.4%</p>
<p><b>Sharpe (3y):</b> 1.12</p>
</div></div>
<div class="small">Share class A USD. Performance is net of all fees. Fictional fund for demonstration purposes.</div>
</body></html>"""


def northwind(month):
    pdf(f"northwind_monthly_{MONTHS[month-1].lower()}26.pdf", northwind_html(2026, month))


def northwind_scanned():
    tmp = OUT / "_tmp_nw_dec.pdf"
    HTML(string=northwind_html(2025, 12)).write_pdf(tmp)
    base = OUT / "_tmp_nw"
    subprocess.run(["pdftoppm", "-r", "110", "-gray", "-png", str(tmp), str(base)], check=True)
    pages = sorted(OUT.glob("_tmp_nw*.png"))
    rnd = random.Random(7)
    imgs = []
    for p in pages:
        im = Image.open(p).convert("L")
        im = im.rotate(0.8, expand=True, fillcolor=245)
        im = im.filter(ImageFilter.GaussianBlur(0.6))
        px = im.load()
        w, h = im.size
        for _ in range(w * h // 60):
            x, yy = rnd.randrange(w), rnd.randrange(h)
            px[x, yy] = max(0, px[x, yy] - rnd.randint(20, 90))
        im = ImageOps.autocontrast(im, cutoff=1)
        imgs.append(im.convert("RGB"))
        p.unlink()
    imgs[0].save(OUT / "northwind_dec25_scanned.pdf", save_all=True, append_images=imgs[1:], resolution=110)
    tmp.unlink()


# ---------------------------------------------------------------- Template C: Meridian
def meridian(month, stated_ytd=None):
    y = 2026
    mtd = R["meridian"][(y, month)]
    true_ytd = ytd("meridian", y, month)
    shown_ytd = stated_ytd if stated_ytd is not None else true_ytd
    comm = {
        1: "Rates were volatile into the January FOMC. Our main loss came from a 2s10s flattener that "
           "moved against us as the long end sold off. We reduced duration from 3.4 to 2.1 years ahead "
           "of the March meeting and remain cautious on term premium.",
        2: "February returns came from swap spread and basis trades. Portfolio rate sensitivity remains "
           "low; a 25bp parallel move in Treasuries would move NAV by roughly 0.5%. We expect to keep "
           "interest-rate exposure light until inflation data stabilizes.",
    }[month]
    rows = "".join(
        f"<tr><td>{FULL[m-1]}</td><td>{pct(R['meridian'][(y, m)], 'paren')}</td></tr>"
        for m in range(1, month + 1))
    html = f"""
<html><head><style>
@page {{ size: Letter; margin: 14mm 16mm; }}
body {{ font-family: 'DejaVu Sans Condensed', 'DejaVu Sans', sans-serif; font-size: 9pt; color: #111; }}
.band {{ background: #0e6b6b; color: #fff; padding: 8px 12px; display: flex; justify-content: space-between; }}
.band b {{ font-size: 14pt; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-top: 10px; }}
.box {{ border: 1px solid #9cc; padding: 8px; }}
.box h4 {{ margin: 0 0 6px; font-size: 8.5pt; color: #0e6b6b; text-transform: uppercase; }}
table {{ border-collapse: collapse; width: 100%; }} td {{ padding: 2px 4px; border-bottom: 1px dotted #9cc; }}
.big {{ font-size: 15pt; font-weight: bold; }}
.c {{ margin-top: 10px; column-count: 2; column-gap: 18px; text-align: justify; }}
.disc {{ font-size: 6.5pt; color: #555; margin-top: 12px; }}
</style></head><body>
<div class="band"><b>MERIDIAN RATES RELATIVE VALUE FUND</b><span>Tear Sheet | As of {month:02d}/{28 if month == 2 else 31}/{y}</span></div>
<div class="grid">
 <div class="box"><h4>Net Return</h4>
   <div>{FULL[month-1]}</div><div class="big">{pct(mtd, 'paren')}</div>
   <div style="margin-top:6px">Year to date</div><div class="big">{pct(shown_ytd, 'paren')}</div></div>
 <div class="box"><h4>Monthly Net Return {y}</h4><table>{rows}</table></div>
 <div class="box"><h4>Fund Details</h4><table>
   <tr><td>Manager</td><td>Meridian Capital Advisors</td></tr>
   <tr><td>Strategy</td><td>Fixed income relative value</td></tr>
   <tr><td>AUM</td><td>$640mm</td></tr>
   <tr><td>Liquidity</td><td>Quarterly, 60d notice, 1yr soft lock</td></tr>
   <tr><td>Fees</td><td>1.75 / 20</td></tr>
   <tr><td>Leverage (gross)</td><td>6.2x</td></tr></table></div>
</div>
<div class="c"><b>Manager Comments.</b> {comm}
Liquidity in our core cash-futures basis positions remains good, and margin usage is 38% of available.</div>
<div class="disc">Returns are net of fees for the founders share class. Negative returns shown in parentheses.
Fictional fund for demonstration purposes.</div>
</body></html>"""
    pdf(f"meridian_rv_tearsheet_2026-{month:02d}.pdf", html)


# ---------------------------------------------------------------- Template D: Alder (bps)
def alder(fund_key, display_name, strategy, month, aum):
    y = 2026
    mtd = R[fund_key][(y, month)]
    html = f"""
<html><head><style>
@page {{ size: A4; margin: 20mm; }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; color: #233; }}
.logo {{ color: #2f6f3e; font-weight: bold; font-size: 11pt; letter-spacing: .2em; }}
h1 {{ font-size: 17pt; margin: 6px 0 2px; }}
.sub {{ color: #5c6b62; margin-bottom: 16px; }}
.hero {{ background: #eef6f0; border-left: 5px solid #2f6f3e; padding: 12px 16px; display: flex; gap: 40px; }}
.hero div span {{ display: block; font-size: 8.5pt; color: #5c6b62; }}
.hero div b {{ font-size: 15pt; }}
h3 {{ color: #2f6f3e; margin-top: 20px; font-size: 11pt; }}
dl {{ display: grid; grid-template-columns: 160px 1fr; row-gap: 4px; }}
dt {{ color: #5c6b62; }} dd {{ margin: 0; }}
.note {{ font-size: 7.5pt; color: #7a8a80; margin-top: 30px; }}
</style></head><body>
<div class="logo">ALDER CAPITAL</div>
<h1>{display_name}</h1>
<div class="sub">Investor Report — {FULL[month-1]} {y}</div>
<div class="hero">
 <div><span>Month (net of fees)</span><b>{pct(mtd, 'bps')}</b></div>
 <div><span>Year to date (net)</span><b>{pct(ytd(fund_key, y, month), 'bps')}</b></div>
 <div><span>Net asset value</span><b>{aum}</b></div>
</div>
<h3>Fund information</h3>
<dl>
 <dt>Investment manager</dt><dd>Alder Capital Pte. Ltd.</dd>
 <dt>Strategy</dt><dd>{strategy}</dd>
 <dt>Redemptions</dt><dd>Quarterly with 90 days' notice; 25% investor-level gate</dd>
 <dt>Management fee</dt><dd>1.25% p.a.</dd>
 <dt>Incentive fee</dt><dd>15% over a 5% hurdle</dd>
</dl>
<h3>Commentary</h3>
<p>{'Our long book in Japanese industrials and Korean semiconductors drove performance, while shorts in Chinese consumer names were roughly flat.' if fund_key == 'alder_asia' and month == 1 else 'Asian equities sold off on renewed tariff concerns; our net exposure of 35% was too high going into the move. We have since reduced net to 18%.' if fund_key == 'alder_asia' else 'Gulf sovereign and quasi-sovereign bonds were stable. Carry contributed most of the return; spread duration was kept below 4 years given rate uncertainty.'}</p>
<div class="note">Performance in basis points, net of all fees, Class A shares. Fictional fund for demonstration purposes.</div>
</body></html>"""
    pdf(f"{fund_key}_fund_{FULL[month-1].lower()}{y}.pdf", html)


# ---------------------------------------------------------------- Template E: Harbor letter
def harbor_letter():
    q = [(2025, 10), (2025, 11), (2025, 12)]
    qret = 1.0
    for k in q:
        qret *= 1 + R["harbor"][k] / 100
    qret = (qret - 1) * 100
    html = f"""
<html><head><style>
@page {{ size: Letter; margin: 25mm; @bottom-center {{ content: "Page " counter(page); font-size: 8pt; color: #888; }} }}
body {{ font-family: 'DejaVu Serif', serif; font-size: 11pt; line-height: 1.5; color: #222; }}
.lh {{ font-family: 'DejaVu Sans', sans-serif; color: #29465b; font-weight: bold; letter-spacing: .08em; }}
table {{ border-collapse: collapse; margin: 10px 0; font-family: 'DejaVu Sans', sans-serif; font-size: 9.5pt; }}
td, th {{ border-bottom: 1px solid #ccc; padding: 4px 14px 4px 0; text-align: left; }}
</style></head><body>
<div class="lh">HARBOR ASSET MANAGEMENT</div>
<p>January 20, 2026</p>
<p>Dear Partners,</p>
<p>Harbor Credit Opportunities Fund returned <b>{qret:.2f}%</b> net in the fourth quarter of 2025, bringing
full-year performance to 7.9% net. Returns were driven by carry in our senior secured loan book and by
two restructurings that closed ahead of schedule.</p>
<table><tr><th>Period</th><th>Net return</th></tr>
<tr><td>October 2025</td><td>{R['harbor'][(2025,10)]:.2f}%</td></tr>
<tr><td>November 2025</td><td>{R['harbor'][(2025,11)]:.2f}%</td></tr>
<tr><td>December 2025</td><td>{R['harbor'][(2025,12)]:.2f}%</td></tr>
<tr><td><b>Q4 2025</b></td><td><b>{qret:.2f}%</b></td></tr></table>
<p><b>Positioning.</b> Portfolio duration remains short at roughly 1.3 years, as most of the book is floating
rate. We see limited exposure to a further move in long-end yields, though slower rate cuts would keep
coupon income elevated. Default rates in our universe ticked up to 3.1%, and we have been avoiding
cov-lite deals in software and healthcare services.</p>
<p><b>Outlook.</b> We expect 2026 to offer better entry points in stressed credit as maturity walls approach.
Dry powder stands at 14% of NAV.</p>
<p>Fund AUM at quarter end was $905 million. Liquidity: quarterly redemptions with 90 days' notice.</p>
<p>Sincerely,<br>Dana Whitlock<br>Chief Investment Officer</p>
<p style="font-size:8pt;color:#777">Fictional fund and people for demonstration purposes.</p>
</body></html>"""
    pdf("harbor_credit_q4_2025_investor_letter.pdf", html)


# ---------------------------------------------------------------- Alder multi-fund report
def alder_quarterly():
    def row(k, name):
        cells = "".join(f"<td>{R[k][(2026, m)]:.2f}%</td>" for m in (1, 2, 3))
        return f"<tr><td>{name}</td>{cells}<td><b>{ytd(k, 2026, 3):.2f}%</b></td></tr>"
    html = f"""
<html><head><style>
@page {{ size: A4 landscape; margin: 18mm; }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; color: #233; }}
h1 {{ color: #2f6f3e; font-size: 16pt; margin: 0; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 14px; }}
th {{ background: #2f6f3e; color: #fff; padding: 6px; text-align: left; }}
td {{ padding: 6px; border-bottom: 1px solid #d5e3d9; }}
</style></head><body>
<h1>Alder Capital — Quarterly Performance Report, Q1 2026</h1>
<p>Prepared for investors in Alder Capital funds. All figures net of fees, Class A.</p>
<table><tr><th>Fund</th><th>Jan</th><th>Feb</th><th>Mar</th><th>Q1 / YTD</th></tr>
{row('alder_asia', 'Alder Asia Fund')}
{row('alder_dubai', 'Alder Dubai Fund')}
</table>
<p style="margin-top:18px"><b>Asia:</b> a difficult March after tariff headlines; net exposure reduced to 18%.
<b>Dubai:</b> steady carry; spread duration kept under 4 years.</p>
<p style="font-size:8pt;color:#777">Fictional funds for demonstration purposes.</p>
</body></html>"""
    pdf("alder_capital_q1_2026_performance_report.pdf", html)


# ---------------------------------------------------------------- Statements
def custodian_statement(fname, account, acct_no, period_label, period_start, period_end,
                        opening, contrib, withdr, pnl, fees, closing, holdings):
    hrows = "".join(
        f"<tr><td>{h[0]}</td><td>{h[1]}</td><td style='text-align:right'>{h[2]:,.0f}</td>"
        f"<td style='text-align:right'>{h[3]:.1f}%</td></tr>" for h in holdings)
    html = f"""
<html><head><style>
@page {{ size: Letter; margin: 16mm; }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 9.5pt; color: #222; }}
.hd {{ display: flex; justify-content: space-between; border-bottom: 2px solid #444; padding-bottom: 8px; }}
.hd h1 {{ margin: 0; font-size: 15pt; color: #3a3a7a; }}
h2 {{ font-size: 10.5pt; background: #eceef6; padding: 4px 8px; margin-top: 18px; }}
table {{ border-collapse: collapse; width: 100%; }} td, th {{ padding: 4px 8px; border-bottom: 1px solid #ddd; text-align: left; }}
.amt {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style></head><body>
<div class="hd"><div><h1>Larkspur Trust Company</h1>Custody &amp; Reporting Services</div>
<div style="text-align:right"><b>Account Statement</b><br>{period_label}<br>Account: {account}<br>Acct No. {acct_no}</div></div>
<h2>Account Summary — {period_start} to {period_end} (USD)</h2>
<table>
<tr><td>Beginning market value</td><td class="amt">{opening:,.2f}</td></tr>
<tr><td>Contributions</td><td class="amt">{contrib:,.2f}</td></tr>
<tr><td>Withdrawals</td><td class="amt">({withdr:,.2f})</td></tr>
<tr><td>Investment gain / (loss)</td><td class="amt">{pnl:,.2f}</td></tr>
<tr><td>Fees</td><td class="amt">({fees:,.2f})</td></tr>
<tr><td><b>Ending market value</b></td><td class="amt"><b>{closing:,.2f}</b></td></tr>
</table>
<h2>Holdings</h2>
<table><tr><th>Investment</th><th>Type</th><th class="amt">Market value</th><th class="amt">% of account</th></tr>{hrows}</table>
<p style="font-size:7.5pt;color:#777;margin-top:20px">Values for private funds reflect the most recent NAV
reported by each fund administrator and may be estimates. Fictional institution and account.</p>
</body></html>"""
    pdf(fname, html)


def capital_account_statement():
    html = """
<html><head><style>
@page { size: A4; margin: 20mm; }
body { font-family: 'DejaVu Serif', serif; font-size: 10pt; }
h1 { font-size: 13pt; text-align: center; margin-bottom: 0; }
.c { text-align: center; margin-top: 2px; color: #555; }
table { border-collapse: collapse; width: 80%; margin: 18px auto; }
td { padding: 5px 10px; border-bottom: 1px solid #ccc; } td:last-child { text-align: right; }
</style></head><body>
<div style="font-family:'DejaVu Sans';font-size:9pt;color:#555">BRIGHTLINE FUND SERVICES (CAYMAN) LTD.</div>
<h1>Statement of Partner's Capital Account</h1>
<div class="c">Kestrel Quant Equity Fund LP</div>
<div class="c">For the quarter ended 31 December 2025</div>
<p><b>Limited Partner:</b> Cedar Ridge Partners LP<br><b>Investor ID:</b> CRP-0042</p>
<table>
<tr><td>Capital account, 1 October 2025</td><td>USD 5,000,000.00</td></tr>
<tr><td>Subscriptions</td><td>250,000.00</td></tr>
<tr><td>Redemptions</td><td>-</td></tr>
<tr><td>Net profit / (loss) allocated</td><td>71,830.00</td></tr>
<tr><td>Management fee</td><td>(19,560.00)</td></tr>
<tr><td>Incentive allocation</td><td>(10,450.00)</td></tr>
<tr><td><b>Capital account, 31 December 2025</b></td><td><b>USD 5,291,820.00</b></td></tr>
</table>
<p>Quarterly net return to the Limited Partner: 1.37%. Ownership percentage of the Fund: 1.28%.</p>
<p style="font-size:8pt;color:#777">This statement is unaudited. Fictional fund, administrator and investor.</p>
</body></html>"""
    pdf("cedar_ridge_capital_account_kestrel_q4_2025.pdf", html)


# ---------------------------------------------------------------- CSVs
def harbor_csv():
    with open(OUT / "harbor_admin_nav_export_2026-03.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["FUND_ID", "FUND_NAME", "SHARE_CLASS", "PERIOD", "NAV_PER_SHARE", "NET_RET", "AUM_USD"])
        nav = 1182.40
        aum = 905_000_000
        for (y, m), r in sorted(R["harbor"].items()):
            nav *= 1 + r / 100
            aum *= 1 + r / 100
            w.writerow(["HCO-A", "Harbor Credit Opps Fund", "A", f"{y}-{m:02d}", f"{nav:.2f}", f"{r/100:.4f}", f"{aum:.0f}"])


def meridian_csv():
    with open(OUT / "meridian_returns_export.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Fund", "Month End", "Return (Net) %", "Return (Gross) %", "Notes"])
        for (y, m), r in sorted(R["meridian"].items()):
            day = {2: 28, 4: 30, 6: 30, 9: 30, 11: 30}.get(m, 31)
            note = "estimate" if (y, m) == (2026, 3) else ""
            w.writerow(["Meridian Rates RV Fund", f"{m}/{day}/{y}", f"{r:.2f}", f"{r + 0.21:.2f}", note])


# ---------------------------------------------------------------- Emails
def eml(fname, sender, subject, sent, html_body):
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = "investor.relations@example-allocator.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(sent)
    msg.attach(MIMEText(html_body, "html"))
    (OUT / fname).write_text(msg.as_string())


def emails():
    eml("northwind_flash_update_jan2026.eml", "Northwind IR <ir@northwind-capital.example>",
        "Northwind Macro Fund Ltd – January 2026 flash estimate",
        datetime(2026, 2, 3, 14, 5, tzinfo=timezone.utc),
        """<html><body style="font-family:Arial,sans-serif;font-size:14px">
<p>Dear Investor,</p>
<p>Please find below the <b>flash estimate</b> for Northwind Macro Fund Ltd (Class A USD) for January 2026:</p>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>Period</th><th>Net return (est.)</th></tr>
<tr><td>Jan 2026</td><td>+1.89%</td></tr>
<tr><td>YTD 2026</td><td>+1.89%</td></tr></table>
<p>Gains came from front-end US rates and a short yen position. Final figures will follow in the monthly factsheet.</p>
<p>Best regards,<br>Northwind Investor Relations</p>
<p style="font-size:11px;color:#888">Fictional fund for demonstration purposes.</p></body></html>""")
    eml("meridian_pm_note_feb2026.eml", "Priya Raman <praman@meridian-ca.example>",
        "Meridian RV – quick note on positioning",
        datetime(2026, 2, 18, 16, 40, tzinfo=timezone.utc),
        """<html><body style="font-family:Georgia,serif;font-size:14px">
<p>Hi all,</p>
<p>A few of you asked about our interest-rate exposure after last week's CPI print. Short version: we are
running very little outright rate sensitivity. Most of the book is in swap spreads and cash-futures basis,
which are largely insulated from parallel moves in yields.</p>
<p>We cut duration earlier in the year and have not added it back. If the March meeting surprises hawkishly
we would expect a small positive impact from our curve positions.</p>
<p>Month-to-date we are up roughly 15bps. Happy to set up calls.</p>
<p>Priya<br>Portfolio Manager, Meridian Rates Relative Value Fund</p>
<p style="font-size:11px;color:#888">Fictional fund and people for demonstration purposes.</p></body></html>""")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    for m in (1, 2, 3):
        kestrel(m)
    northwind(1)
    northwind(2)
    northwind_scanned()
    meridian(1)
    meridian(2, stated_ytd=0.15)  # planted: true YTD is -0.15%
    alder("alder_asia", "Alder Asia Fund", "Asia-Pacific equity long/short", 1, "USD 284.3mm")
    alder("alder_asia", "Alder Asia Fund SPC – Class A", "Asia-Pacific equity long/short", 3, "USD 279.9mm")
    alder("alder_dubai", "Alder Dubai Fund", "MENA credit", 1, "USD 156.0mm")
    alder("alder_dubai", "Alder Dubai Fund", "MENA credit", 3, "USD 158.7mm")
    alder_quarterly()
    harbor_letter()
    custodian_statement(
        "acme_family_office_statement_2026-01.pdf", "Acme Family Office LLC", "XXXX-4471",
        "January 2026", "01/01/2026", "01/31/2026",
        12_400_000.00, 500_000.00, 0.00, 180_000.00, 0.00, 13_100_000.00,  # planted: should be 13,080,000
        [("Kestrel Quant Equity Fund LP", "Hedge fund", 4_250_000, 32.4),
         ("Northwind Global Macro Fund", "Hedge fund", 3_600_000, 27.5),
         ("Harbor Credit Opportunities Fund", "Private credit", 3_150_000, 24.0),
         ("US Treasury Bills", "Cash equivalent", 1_820_000, 13.9),
         ("Cash", "Cash", 280_000, 2.1)])
    custodian_statement(
        "birchwood_foundation_statement_2026-02.pdf", "Birchwood Foundation", "XXXX-9025",
        "February 2026", "02/01/2026", "02/28/2026",
        48_200_000.00, 0.00, 1_000_000.00, 236_000.00, 12_050.00, 47_423_950.00,
        [("Meridian Rates Relative Value Fund", "Hedge fund", 14_100_000, 29.7),
         ("Alder Asia Fund", "Hedge fund", 9_800_000, 20.7),
         ("Alder Dubai Fund", "Hedge fund", 7_400_000, 15.6),
         ("Kestrel Quant Equity Fund LP", "Hedge fund", 11_900_000, 25.1),
         ("Cash", "Cash", 4_223_950, 8.9)])
    capital_account_statement()
    harbor_csv()
    meridian_csv()
    emails()
    print("\n".join(sorted(p.name for p in OUT.iterdir())))

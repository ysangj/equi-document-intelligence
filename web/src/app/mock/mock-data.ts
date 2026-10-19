/**
 * Dev-only fixture data for the mock api. Funds, accounts and figures follow
 * sample_docs/MANIFEST.md (ground-truth monthly net returns, planted edge cases) and
 * the API Contract examples (same ids where the contract shows one).
 */
import {
  AccountItem,
  DocumentStatus,
  DocumentType,
  FundFactItem,
  FundItem,
  HoldingItem,
  NetGross,
  ReturnItem,
  ReturnType,
  ReviewReason,
  StatementItem,
  ValueStatus,
} from '../api/models';

export interface MockDocument {
  document_id: number;
  document_name: string;
  document_type: DocumentType;
  mime_type: string;
  /** final status once processed */
  final_status: Extract<DocumentStatus, 'done' | 'needs_review' | 'failed'>;
  /** true for the scanned PDF: snippet verification is skipped */
  image_only?: boolean;
}

/** Processing order = array order (the mock "worker" completes them one by one). */
export const MOCK_DOCUMENTS: MockDocument[] = [
  { document_id: 3, document_name: 'kestrel_factsheet_2026-01.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 7, document_name: 'northwind_monthly_jan26.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 15, document_name: 'acme_family_office_statement_2026-01.pdf', document_type: 'account_statement', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 9, document_name: 'meridian_rv_tearsheet_2026-01.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 10, document_name: 'alder_asia_fund_january2026.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 13, document_name: 'alder_dubai_fund_january2026.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 2, document_name: 'harbor_admin_nav_export_2026-03.csv', document_type: 'performance_report', mime_type: 'text/csv', final_status: 'done' },
  { document_id: 18, document_name: 'northwind_flash_update_jan2026.eml', document_type: 'email', mime_type: 'message/rfc822', final_status: 'needs_review' },
  { document_id: 17, document_name: 'cedar_ridge_capital_account_kestrel_q4_2025.pdf', document_type: 'account_statement', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 4, document_name: 'kestrel_factsheet_2026-02.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 11, document_name: 'alder_asia_fund_march2026.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 12, document_name: 'meridian_rv_tearsheet_2026-02.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 1, document_name: 'harbor_credit_q4_2025_investor_letter.pdf', document_type: 'performance_report', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 21, document_name: 'meridian_pm_note_feb2026.eml', document_type: 'email', mime_type: 'message/rfc822', final_status: 'done' },
  { document_id: 16, document_name: 'birchwood_foundation_statement_2026-02.pdf', document_type: 'account_statement', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 20, document_name: 'alder_capital_q1_2026_performance_report.pdf', document_type: 'performance_report', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 5, document_name: 'kestrel_factsheet_2026-03.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 6, document_name: 'northwind_monthly_feb26.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'needs_review' },
  { document_id: 14, document_name: 'alder_dubai_fund_march2026.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done' },
  { document_id: 19, document_name: 'meridian_returns_export.csv', document_type: 'performance_report', mime_type: 'text/csv', final_status: 'done' },
  { document_id: 8, document_name: 'northwind_dec25_scanned.pdf', document_type: 'factsheet', mime_type: 'application/pdf', final_status: 'done', image_only: true },
];

export const docById = (id: number): MockDocument => {
  const d = MOCK_DOCUMENTS.find((x) => x.document_id === id);
  if (!d) throw new Error(`mock document ${id} missing`);
  return d;
};

export interface FundSeed {
  fund_id: number;
  fund_name: string;
  manager: string;
  strategy: string;
  currency: string;
}

export const MOCK_FUND_SEEDS: FundSeed[] = [
  { fund_id: 1, fund_name: 'Kestrel Quant Equity Fund LP', manager: 'Kestrel Partners LLC', strategy: 'Systematic equity market neutral', currency: 'USD' },
  { fund_id: 2, fund_name: 'Northwind Global Macro Fund', manager: 'Northwind Capital Management', strategy: 'Discretionary global macro', currency: 'USD' },
  { fund_id: 3, fund_name: 'Meridian Rates Relative Value Fund', manager: 'Meridian Asset Management', strategy: 'Fixed income relative value', currency: 'USD' },
  { fund_id: 4, fund_name: 'Alder Asia Fund', manager: 'Alder Capital Pte. Ltd.', strategy: 'Asia-Pacific equity long/short', currency: 'USD' },
  { fund_id: 5, fund_name: 'Alder Dubai Fund', manager: 'Alder Capital Pte. Ltd.', strategy: 'MENA credit', currency: 'USD' },
  { fund_id: 6, fund_name: 'Harbor Credit Opportunities Fund', manager: 'Harbor Asset Management', strategy: 'Credit opportunities', currency: 'USD' },
];

const fundSeed = (id: number) => MOCK_FUND_SEEDS.find((f) => f.fund_id === id)!;

const MONTH_END: Record<string, string> = {
  '2025-10': '2025-10-31',
  '2025-11': '2025-11-30',
  '2025-12': '2025-12-31',
  '2026-01': '2026-01-31',
  '2026-02': '2026-02-28',
  '2026-03': '2026-03-31',
};

interface ReturnSeed {
  id: number;
  fund: number;
  ym: keyof typeof MONTH_END;
  pct: number;
  doc: number;
  label: string;
  snippet: string;
  type?: ReturnType;
  ng?: NetGross;
  status?: ValueStatus;
  reasons?: ReviewReason[];
  conf?: number;
  page?: number;
}

const R: ReturnSeed[] = [
  // Kestrel Quant Equity: 0.65 1.12 -0.40 2.84 -0.92 1.35
  { id: 101, fund: 1, ym: '2026-01', pct: 2.84, doc: 3, label: 'Jan 2026', snippet: 'MTD % (net) +2.84%', conf: 0.97 },
  { id: 102, fund: 1, ym: '2025-12', pct: -0.4, doc: 3, label: 'Dec 2025', snippet: 'Dec  -0.40%', conf: 0.95 },
  { id: 103, fund: 1, ym: '2025-11', pct: 1.12, doc: 3, label: 'Nov 2025', snippet: 'Nov  +1.12%', conf: 0.95 },
  { id: 104, fund: 1, ym: '2025-10', pct: 0.65, doc: 3, label: 'Oct 2025', snippet: 'Oct  +0.65%', conf: 0.95 },
  { id: 105, fund: 1, ym: '2026-02', pct: -0.92, doc: 4, label: 'Feb 2026', snippet: 'MTD % (net) -0.92%', conf: 0.97 },
  { id: 106, fund: 1, ym: '2026-03', pct: 1.35, doc: 5, label: 'Mar 2026', snippet: 'MTD % (net) +1.35%*', conf: 0.94 },
  { id: 107, fund: 1, ym: '2026-03', pct: 3.27, doc: 5, label: '2026 YTD', snippet: 'YTD % (net) +3.27%', type: 'YTD', conf: 0.96 },
  // Northwind Global Macro: -0.30 0.85 1.40 1.92 0.55 (conflict Jan: 1.92 (Jan + Feb factsheets) vs 1.89 (flash))
  { id: 214, fund: 2, ym: '2026-01', pct: 1.92, doc: 7, label: 'Jan-26', snippet: 'Perf. (net) Jan-26 1.92%', status: 'conflict', reasons: ['conflict'], conf: 0.93 },
  { id: 231, fund: 2, ym: '2026-01', pct: 1.89, doc: 18, label: 'Jan 2026', snippet: 'Jan 2026 +1.89%', status: 'conflict', reasons: ['conflict'], conf: 0.9 },
  // the Feb factsheet's grid repeats Jan at 1.92: it agrees with the Jan factsheet, so `choose` 1.92 keeps it
  { id: 219, fund: 2, ym: '2026-01', pct: 1.92, doc: 6, label: 'Jan-26', snippet: 'Perf. (net) Jan-26 1.92%', status: 'conflict', reasons: ['conflict'], conf: 0.94 },
  { id: 215, fund: 2, ym: '2026-02', pct: 0.55, doc: 6, label: 'Feb-26', snippet: 'Perf. (net) Feb-26 0.55%', conf: 0.94 },
  { id: 216, fund: 2, ym: '2025-12', pct: 1.4, doc: 8, label: 'Dec-25', snippet: 'Dec-25 1.40%', conf: 0.82 },
  { id: 217, fund: 2, ym: '2025-11', pct: 0.85, doc: 8, label: 'Nov-25', snippet: 'Nov-25 0.85%', conf: 0.8 },
  { id: 218, fund: 2, ym: '2025-10', pct: -0.3, doc: 8, label: 'Oct-25', snippet: 'Oct-25 (0.30)%', status: 'needs_review', reasons: ['low_confidence'], conf: 0.62 },
  // Meridian Rates RV: 0.22 0.31 0.18 -0.36 0.21 0.27(est); YTD stated +0.15 (sign error)
  { id: 301, fund: 3, ym: '2026-01', pct: -0.36, doc: 9, label: 'January 2026', snippet: 'Net Return  January  (0.36)%', conf: 0.95 },
  { id: 302, fund: 3, ym: '2025-12', pct: 0.18, doc: 9, label: 'December 2025', snippet: 'December 0.18%', conf: 0.93 },
  { id: 303, fund: 3, ym: '2025-11', pct: 0.31, doc: 9, label: 'November 2025', snippet: 'November 0.31%', conf: 0.93 },
  { id: 304, fund: 3, ym: '2025-10', pct: 0.22, doc: 9, label: 'October 2025', snippet: 'October 0.22%', conf: 0.93 },
  { id: 305, fund: 3, ym: '2026-02', pct: 0.21, doc: 12, label: 'February 2026', snippet: 'Net Return  February  0.21%', conf: 0.95 },
  { id: 306, fund: 3, ym: '2026-02', pct: 0.15, doc: 12, label: '2026 YTD', snippet: 'YTD  0.15%', type: 'YTD', status: 'needs_review', reasons: ['ytd_mismatch'], conf: 0.91 },
  { id: 307, fund: 3, ym: '2026-03', pct: 0.27, doc: 19, label: '03/31/2026', snippet: '03/31/2026,0.27,0.32,estimate', conf: 0.88 },
  { id: 308, fund: 3, ym: '2026-03', pct: 0.32, doc: 19, label: '03/31/2026', snippet: '03/31/2026,0.27,0.32,estimate', ng: 'gross', conf: 0.88 },
  { id: 309, fund: 3, ym: '2026-02', pct: 0.26, doc: 19, label: '02/28/2026', snippet: '02/28/2026,0.21,0.26,', ng: 'gross', conf: 0.9 },
  // Alder Asia: 1.10 0.40 -1.25 (Feb only in Q1 report; Mar under share-class alias)
  { id: 401, fund: 4, ym: '2026-01', pct: 1.1, doc: 10, label: 'January 2026', snippet: 'Monthly return +110 bps', conf: 0.92 },
  { id: 402, fund: 4, ym: '2026-02', pct: 0.4, doc: 20, label: 'Feb 2026', snippet: 'Alder Asia Fund  Feb  0.40%', conf: 0.9 },
  { id: 403, fund: 4, ym: '2026-03', pct: -1.25, doc: 11, label: 'March 2026', snippet: 'Monthly return -125 bps', status: 'needs_review', reasons: ['alias_match'], conf: 0.92 },
  // snippet is in the document but stops before the value (Q2 fix: snippet_not_found + value_missing)
  { id: 404, fund: 4, ym: '2026-03', pct: 0.24, doc: 20, label: 'Q1 2026', snippet: 'Alder Asia Fund  Q1', type: 'QTD', status: 'needs_review', reasons: ['snippet_not_found'], conf: 0.9 },
  // Alder Dubai: 0.48 0.52 0.37
  { id: 501, fund: 5, ym: '2026-01', pct: 0.48, doc: 13, label: 'January 2026', snippet: 'Monthly return +48 bps', conf: 0.93 },
  { id: 502, fund: 5, ym: '2026-02', pct: 0.52, doc: 20, label: 'Feb 2026', snippet: 'Alder Dubai Fund  Feb  0.52%', conf: 0.9 },
  { id: 503, fund: 5, ym: '2026-03', pct: 0.37, doc: 14, label: 'March 2026', snippet: 'Monthly return +37 bps', conf: 0.93 },
  // Harbor Credit Opps: 0.58 0.62 0.49 0.71 0.64 0.55
  { id: 601, fund: 6, ym: '2025-10', pct: 0.58, doc: 1, label: 'October', snippet: 'October  0.58%', conf: 0.9 },
  { id: 602, fund: 6, ym: '2025-11', pct: 0.62, doc: 1, label: 'November', snippet: 'November  0.62%', conf: 0.9 },
  { id: 603, fund: 6, ym: '2025-12', pct: 0.49, doc: 1, label: 'December', snippet: 'December  0.49%', conf: 0.9 },
  { id: 604, fund: 6, ym: '2026-01', pct: 0.71, doc: 2, label: '2026-01', snippet: 'HCO-A,2026-01,0.0071', conf: 0.99 },
  { id: 605, fund: 6, ym: '2026-02', pct: 0.64, doc: 2, label: '2026-02', snippet: 'HCO-A,2026-02,0.0064', conf: 0.99 },
  { id: 606, fund: 6, ym: '2026-03', pct: 0.55, doc: 2, label: '2026-03', snippet: 'HCO-A,2026-03,0.0055', conf: 0.99 },
];

function round(n: number, dp: number): number {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

export const MOCK_RETURNS: ReturnItem[] = R.map((s) => {
  const f = fundSeed(s.fund);
  const d = docById(s.doc);
  const end = MONTH_END[s.ym];
  return {
    return_id: s.id,
    fund_id: f.fund_id,
    fund_name: f.fund_name,
    manager: f.manager,
    strategy: f.strategy,
    fund_currency: f.currency,
    period_end: end,
    period_year: Number(end.slice(0, 4)),
    period_month: Number(end.slice(5, 7)),
    period_label: s.label,
    return_type: s.type ?? 'MTD',
    net_gross: s.ng ?? 'net',
    value: round(s.pct / 100, 6),
    value_pct: s.pct,
    status: s.status ?? 'ok',
    review_reasons: s.reasons ?? [],
    confidence: s.conf ?? 0.95,
    page: s.page ?? 1,
    source_snippet: s.snippet,
    document_id: d.document_id,
    document_name: d.document_name,
    document_type: d.document_type,
  };
});

interface FactSeed {
  id: number;
  fund: number;
  metric: string;
  value: number | null;
  unit: string | null;
  text?: string;
  asOf: string;
  doc: number;
  snippet: string;
  conf?: number;
}

const F: FactSeed[] = [
  { id: 40, fund: 1, metric: 'aum', value: 412_600_000, unit: 'USD', asOf: '2026-01-31', doc: 3, snippet: 'Fund AUM $412.6mm', conf: 0.96 },
  { id: 41, fund: 1, metric: 'sharpe', value: 1.84, unit: 'ratio', asOf: '2026-01-31', doc: 3, snippet: 'Sharpe (since incept.) 1.84', conf: 0.95 },
  { id: 42, fund: 1, metric: 'max_drawdown', value: -0.068, unit: 'fraction', asOf: '2026-01-31', doc: 3, snippet: 'Max drawdown (since incept.) -6.8%', conf: 0.94 },
  { id: 43, fund: 1, metric: 'aum', value: 431_200_000, unit: 'USD', asOf: '2026-03-31', doc: 5, snippet: 'Fund AUM $431.2mm', conf: 0.96 },
  { id: 44, fund: 2, metric: 'aum', value: 1_280_000_000, unit: 'USD', asOf: '2026-01-31', doc: 7, snippet: 'AUM USD 1.28bn', conf: 0.94 },
  { id: 45, fund: 2, metric: 'vol', value: 0.084, unit: 'fraction', asOf: '2026-01-31', doc: 7, snippet: 'Ann. volatility 8.4%', conf: 0.92 },
  { id: 46, fund: 3, metric: 'duration', value: 2.1, unit: 'years', asOf: '2026-01-31', doc: 9, snippet: 'Portfolio duration 2.1y', conf: 0.93 },
  { id: 47, fund: 3, metric: 'aum', value: 655_000_000, unit: 'USD', asOf: '2026-02-28', doc: 12, snippet: 'Strategy AUM $655mm', conf: 0.93 },
  { id: 48, fund: 4, metric: 'net_exposure', value: 0.38, unit: 'fraction', asOf: '2026-01-31', doc: 10, snippet: 'Net exposure 38%', conf: 0.9 },
  { id: 49, fund: 5, metric: 'spread_duration', value: 3.4, unit: 'years', asOf: '2026-03-31', doc: 14, snippet: 'Spread duration 3.4 yrs', conf: 0.9 },
  { id: 50, fund: 6, metric: 'liquidity_terms', value: null, unit: null, text: 'Quarterly, 65 days notice', asOf: '2025-12-31', doc: 1, snippet: 'Liquidity: quarterly with 65 days notice', conf: 0.86 },
];

export const MOCK_FUND_FACTS: FundFactItem[] = F.map((s) => {
  const f = fundSeed(s.fund);
  const d = docById(s.doc);
  return {
    fact_id: s.id,
    fund_id: f.fund_id,
    fund_name: f.fund_name,
    manager: f.manager,
    strategy: f.strategy,
    metric: s.metric,
    value: s.value,
    value_pct: s.unit === 'fraction' && s.value !== null ? round(s.value * 100, 6) : null,
    value_text: s.text ?? null,
    unit: s.unit,
    as_of_date: s.asOf,
    status: 'ok',
    review_reasons: [],
    confidence: s.conf ?? 0.93,
    page: 1,
    source_snippet: s.snippet,
    document_id: d.document_id,
    document_name: d.document_name,
    document_type: d.document_type,
  };
});

export const MOCK_ACCOUNT_SEEDS = [
  { account_id: 1, account_name: 'Acme Family Office LLC', account_number_masked: 'XXXX-4471', custodian: 'Larkspur Trust Company', currency: 'USD' },
  { account_id: 2, account_name: 'Birchwood Foundation', account_number_masked: 'BWF-2210', custodian: 'Granite Custody Services', currency: 'USD' },
  { account_id: 3, account_name: 'Cedar Ridge Partners LP', account_number_masked: 'CRP-0042', custodian: 'Brightline Fund Services (Cayman) Ltd.', currency: 'USD' },
];

export const MOCK_STATEMENTS: StatementItem[] = [
  {
    statement_id: 12,
    account_id: 1,
    account_name: 'Acme Family Office LLC',
    account_number_masked: 'XXXX-4471',
    custodian: 'Larkspur Trust Company',
    fund_id: null,
    fund_name: null,
    manager: null,
    period_start: '2026-01-01',
    period_end: '2026-01-31',
    opening_balance: 12_400_000,
    contributions: 500_000,
    withdrawals: 0,
    pnl: 180_000,
    fees: 0,
    closing_balance: 13_100_000,
    currency: 'USD',
    status: 'needs_review',
    review_reasons: ['rollforward_mismatch'],
    flagged_fields: ['closing_balance'],
    holdings_count: 5,
    document_id: 15,
    document_name: 'acme_family_office_statement_2026-01.pdf',
    document_type: 'account_statement',
  },
  {
    statement_id: 13,
    account_id: 2,
    account_name: 'Birchwood Foundation',
    account_number_masked: 'BWF-2210',
    custodian: 'Granite Custody Services',
    fund_id: null,
    fund_name: null,
    manager: null,
    period_start: '2026-02-01',
    period_end: '2026-02-28',
    opening_balance: 8_750_000,
    contributions: 0,
    withdrawals: 250_000,
    pnl: 96_500,
    fees: 4_200,
    closing_balance: 8_592_300,
    currency: 'USD',
    status: 'ok',
    review_reasons: [],
    flagged_fields: [],
    holdings_count: 12,
    document_id: 16,
    document_name: 'birchwood_foundation_statement_2026-02.pdf',
    document_type: 'account_statement',
  },
  {
    statement_id: 14,
    account_id: 3,
    account_name: 'Cedar Ridge Partners LP',
    account_number_masked: 'CRP-0042',
    custodian: 'Brightline Fund Services (Cayman) Ltd.',
    fund_id: 1,
    fund_name: 'Kestrel Quant Equity Fund LP',
    manager: 'Kestrel Partners LLC',
    period_start: '2025-10-01',
    period_end: '2025-12-31',
    opening_balance: 5_000_000,
    contributions: 250_000,
    withdrawals: 0,
    pnl: 71_830,
    fees: 30_010,
    closing_balance: 5_291_820,
    currency: 'USD',
    status: 'ok',
    review_reasons: [],
    flagged_fields: [],
    holdings_count: 0,
    document_id: 17,
    document_name: 'cedar_ridge_capital_account_kestrel_q4_2025.pdf',
    document_type: 'account_statement',
  },
];

function holdings(statementId: number, startId: number, rows: [string, string | null, number | null, number][]): HoldingItem[] {
  const st = MOCK_STATEMENTS.find((s) => s.statement_id === statementId)!;
  const total = rows.reduce((a, r) => a + r[3], 0);
  return rows.map(([name, ident, qty, mv], i) => {
    const w = round(mv / total, 4);
    return {
      holding_id: startId + i,
      statement_id: st.statement_id,
      account_id: st.account_id,
      account_name: st.account_name,
      custodian: st.custodian,
      fund_id: st.fund_id,
      fund_name: st.fund_name,
      period_end: st.period_end,
      currency: st.currency,
      line_no: i,
      holding_name: name,
      identifier: ident,
      quantity: qty,
      market_value: mv,
      weight: w,
      weight_pct: round(w * 100, 2),
      status: 'ok',
      review_reasons: [],
      confidence: 0.96,
      page: i < 8 ? 2 : 3,
      source_snippet: `${name} ${ident ?? ''} ${mv.toLocaleString('en-US')} ${(w * 100).toFixed(1)}%`.replace(/\s+/g, ' '),
      document_id: st.document_id,
      document_name: st.document_name,
    };
  });
}

export const MOCK_HOLDINGS: HoldingItem[] = [
  ...holdings(12, 41, [
    ['Kestrel Quant Equity Fund LP', null, null, 4_250_000],
    ['Northwind Global Macro Fund', null, null, 3_600_000],
    ['Meridian Rates Relative Value Fund', null, null, 2_400_000],
    ['US Treasury Bill 0% 05/2026', '912797KL0', 1_870_000, 1_850_000],
    ['Cash & equivalents', null, null, 1_000_000],
  ]),
  ...holdings(13, 60, [
    ['Vanguard Total Stock Market ETF', 'VTI', 7_200, 2_181_600],
    ['iShares Core MSCI EAFE ETF', 'IEFA', 14_500, 1_131_000],
    ['iShares Core US Aggregate Bond ETF', 'AGG', 11_000, 1_078_000],
    ['Harbor Credit Opportunities Fund', null, null, 1_050_000],
    ['Alder Asia Fund', null, null, 820_000],
    ['Alder Dubai Fund', null, null, 640_000],
    ['US Treasury Note 4.125% 2030', '91282CJW2', 500_000, 497_300],
    ['Microsoft Corp.', 'MSFT', 600, 249_000],
    ['Apple Inc.', 'AAPL', 1_000, 228_000],
    ['Johnson & Johnson', 'JNJ', 1_100, 172_400],
    ['Municipal Bond Fund I', 'MBFIX', 9_000, 95_000],
    ['Cash & equivalents', null, null, 450_000],
  ]),
];

export const MOCK_ALIASES: Record<number, FundItem['aliases']> = {
  2: [
    { alias_id: 3, alias: 'Northwind Global Macro Fund', confirmed: true, match_method: 'new', match_score: null, source_document_id: 7, source_document_name: 'northwind_monthly_jan26.pdf' },
    { alias_id: 4, alias: 'Northwind Macro Fund Ltd', confirmed: false, match_method: 'fuzzy', match_score: 88.2, source_document_id: 18, source_document_name: 'northwind_flash_update_jan2026.eml' },
  ],
  4: [
    { alias_id: 8, alias: 'Alder Asia Fund', confirmed: true, match_method: 'new', match_score: null, source_document_id: 10, source_document_name: 'alder_asia_fund_january2026.pdf' },
    { alias_id: 9, alias: 'Alder Asia Fund SPC – Class A', confirmed: false, match_method: 'fuzzy', match_score: 91.4, source_document_id: 11, source_document_name: 'alder_asia_fund_march2026.pdf' },
  ],
};

/**
 * `/api/funds` from the visible returns. `aliases` / `seeds` let the mock backend apply
 * Review decisions (confirmed aliases, funds created by "Different fund", renames by
 * "Use as main name").
 */
export function buildFunds(
  visibleReturns: ReturnItem[],
  aliases: Record<number, FundItem['aliases']> = MOCK_ALIASES,
  seeds: FundSeed[] = MOCK_FUND_SEEDS,
): FundItem[] {
  return seeds.map((f) => {
    const rows = visibleReturns.filter((r) => r.fund_id === f.fund_id);
    const own = aliases[f.fund_id] ?? [
      { alias_id: 100 + f.fund_id, alias: f.fund_name, confirmed: true, match_method: 'new' as const, match_score: null, source_document_id: rows[0]?.document_id ?? null, source_document_name: rows[0]?.document_name ?? null },
    ];
    return {
      ...f,
      aliases: own,
      has_unconfirmed_alias: own.some((a) => !a.confirmed),
      return_count: rows.length,
      document_count: new Set(rows.map((r) => r.document_id)).size,
      latest_period_end: rows.map((r) => r.period_end).sort().at(-1) ?? null,
    };
  })
    .filter((f) => f.return_count > 0)
    .sort((a, b) => a.fund_name.localeCompare(b.fund_name));
}

export function buildAccounts(visibleStatements: StatementItem[]): AccountItem[] {
  return MOCK_ACCOUNT_SEEDS.map((a) => {
    const rows = visibleStatements.filter((s) => s.account_id === a.account_id);
    return {
      ...a,
      aliases: [
        { alias_id: a.account_id, alias: a.account_name, confirmed: true, match_method: 'new' as const, match_score: null, source_document_id: rows[0]?.document_id ?? null, source_document_name: rows[0]?.document_name ?? null },
      ],
      has_unconfirmed_alias: false,
      statement_count: rows.length,
      latest_period_end: rows.map((r) => r.period_end).sort().at(-1) ?? null,
    };
  })
    .filter((a) => a.statement_count > 0)
    .sort((a, b) => a.account_name.localeCompare(b.account_name));
}

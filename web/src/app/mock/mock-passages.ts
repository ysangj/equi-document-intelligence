/**
 * Dev-only text chunks for the mock `/api/search`. The wording is taken from the
 * sample documents themselves (gen_samples.py renders sample_docs/), so a search for
 * "duration" or "rate sensitivity" hits the same passages it will against the real
 * FTS index. `page` is null for CSV row groups, as in the contract.
 */
export interface MockPassage {
  chunk_id: number;
  document_id: number;
  page: number | null;
  chunk_index: number;
  text: string;
}

const P: Omit<MockPassage, 'chunk_id'>[] = [
  // kestrel_factsheet_2026-01/02/03.pdf (3, 4, 5): commentary on page 2
  { document_id: 3, page: 1, chunk_index: 0, text: 'Kestrel Quant Equity Fund LP. Monthly Factsheet, January 2026. MTD % (net) +2.84%. Systematic equity market neutral; signals: short-term reversal, earnings-revision and quality signals. Target annualized volatility 8%.' },
  { document_id: 3, page: 2, chunk_index: 1, text: 'Portfolio Manager Commentary. January gains came primarily from short-term reversal signals in US large caps. As dispersion compressed in the final week, we reduced gross exposure from 3.1x to 2.6x. Factor exposures remained tightly controlled; net beta to the S&P 500 averaged 0.02.' },
  { document_id: 4, page: 2, chunk_index: 1, text: 'Portfolio Manager Commentary. February was a difficult month for the fund. A sharp momentum unwind mid-month hurt our medium-horizon signals, and we did not fully offset the move with our short-term book. We kept gross exposure near 2.6x and see no structural change in signal efficacy.' },
  { document_id: 5, page: 2, chunk_index: 1, text: "Portfolio Manager Commentary. March recovered most of February's drawdown. Earnings-revision signals contributed the bulk of returns, while the reversal sleeve was flat. Gross exposure ended the month at 2.9x. *Latest month is an estimate." },
  // northwind_monthly_jan26/feb26.pdf (7, 6), dec25 scanned (8)
  { document_id: 7, page: 1, chunk_index: 0, text: 'Northwind Capital Management · Monthly Update · Jan-26. Review. The fund benefited from long positions in front-end US rates as markets priced additional cuts, and from a short JPY position into the BoJ meeting. Equity index hedges were a small drag.' },
  { document_id: 6, page: 1, chunk_index: 0, text: 'Northwind Capital Management · Monthly Update · Feb-26. Review. Returns were modest. Gains in commodity relative value offset small losses in EM FX. We added to long gold and trimmed USD exposure.' },
  { document_id: 8, page: 1, chunk_index: 0, text: 'Northwind Capital Management · Monthly Update · Dec-25. Review. December was driven by our curve steepener in EUR rates and long USD vs. a basket of Asian currencies.' },
  // meridian_rv_tearsheet_2026-01/02.pdf (9, 12)
  { document_id: 9, page: 1, chunk_index: 1, text: 'Manager Comments. Rates were volatile into the January FOMC. Our main loss came from a 2s10s flattener that moved against us as the long end sold off. We reduced duration from 3.4 to 2.1 years ahead of the March meeting and remain cautious on term premium. Liquidity in our core cash-futures basis positions remains good, and margin usage is 38% of available.' },
  { document_id: 12, page: 1, chunk_index: 1, text: 'Manager Comments. February returns came from swap spread and basis trades. Portfolio rate sensitivity remains low; a 25bp parallel move in Treasuries would move NAV by roughly 0.5%. We expect to keep interest-rate exposure light until inflation data stabilizes.' },
  // alder_asia_fund_january2026 / march2026 (10, 11), alder_dubai (13, 14)
  { document_id: 10, page: 1, chunk_index: 1, text: 'Commentary. Our long book in Japanese industrials and Korean semiconductors drove performance, while shorts in Chinese consumer names were roughly flat. Performance in basis points, net of all fees, Class A shares.' },
  { document_id: 11, page: 1, chunk_index: 1, text: 'Alder Asia Fund SPC – Class A. Commentary. Asian equities sold off on renewed tariff concerns; our net exposure of 35% was too high going into the move. We have since reduced net to 18%.' },
  { document_id: 13, page: 1, chunk_index: 1, text: 'Commentary. Gulf sovereign and quasi-sovereign bonds were stable. Carry contributed most of the return; spread duration was kept below 4 years given rate uncertainty.' },
  { document_id: 14, page: 1, chunk_index: 1, text: 'Commentary. Gulf sovereign and quasi-sovereign bonds were stable. Carry contributed most of the return; spread duration was kept below 4 years given rate uncertainty.' },
  // alder_capital_q1_2026_performance_report.pdf (20)
  { document_id: 20, page: 1, chunk_index: 0, text: 'Alder Capital — Quarterly Performance Report, Q1 2026. Asia: a difficult March after tariff headlines; net exposure reduced to 18%. Dubai: steady carry; spread duration kept under 4 years.' },
  // harbor_credit_q4_2025_investor_letter.pdf (1)
  { document_id: 1, page: 1, chunk_index: 0, text: 'Dear Partners, Harbor Credit Opportunities Fund returned 1.70% net in the fourth quarter of 2025, bringing full-year performance to 7.9% net. Returns were driven by carry in our senior secured loan book and by two restructurings that closed ahead of schedule.' },
  { document_id: 1, page: 1, chunk_index: 1, text: 'Positioning. Portfolio duration remains short at roughly 1.3 years, as most of the book is floating rate. We see limited exposure to a further move in long-end yields, though slower rate cuts would keep coupon income elevated. Default rates in our universe ticked up to 3.1%, and we have been avoiding cov-lite deals in software & healthcare services.' },
  { document_id: 1, page: 1, chunk_index: 2, text: "Outlook. We expect 2026 to offer better entry points in stressed credit as maturity walls approach. Dry powder stands at 14% of NAV. Fund AUM at quarter end was $905 million. Liquidity: quarterly redemptions with 90 days' notice." },
  // emails (18, 21)
  { document_id: 18, page: 1, chunk_index: 0, text: 'Please find below the flash estimate for Northwind Macro Fund Ltd (Class A USD) for January 2026: Jan 2026 +1.89%. Gains came from front-end US rates and a short yen position. Final figures will follow in the monthly factsheet.' },
  { document_id: 21, page: 1, chunk_index: 0, text: "A few of you asked about our interest-rate exposure after last week's CPI print. Short version: we are running very little outright rate sensitivity. Most of the book is in swap spreads and cash-futures basis, which are largely insulated from parallel moves in yields." },
  { document_id: 21, page: 1, chunk_index: 1, text: 'We cut duration earlier in the year and have not added it back. If the March meeting surprises hawkishly we would expect a small positive impact from our curve positions. Month-to-date we are up roughly 15bps. Priya, Portfolio Manager, Meridian Rates Relative Value Fund' },
  // statements (15, 16, 17)
  { document_id: 15, page: 1, chunk_index: 0, text: 'Larkspur Trust Company. Statement of account, Acme Family Office LLC, account XXXX-4471, January 2026. Opening balance 12,400,000.00. Contributions 500,000.00. Withdrawals 0.00. Investment gain / (loss) 180,000.00. Closing balance 13,100,000.00.' },
  { document_id: 16, page: 1, chunk_index: 0, text: 'Granite Custody Services. Birchwood Foundation, account BWF-2210, February 2026 statement. Opening balance 8,750,000.00; withdrawals 250,000.00; fees 4,200.00; closing balance 8,592,300.00.' },
  { document_id: 17, page: 1, chunk_index: 0, text: 'Brightline Fund Services (Cayman) Ltd. Capital account statement for Cedar Ridge Partners LP in Kestrel Quant Equity Fund LP, quarter ended 31 December 2025. Management fee and incentive allocation are shown separately.' },
  // CSV row groups (2, 19): page is null
  { document_id: 2, page: null, chunk_index: 0, text: 'fund_id,fund_name,share_class,period,nav,net_ret,aum HCO-A,Harbor Credit Opps Fund,A,2026-01,0.0071 HCO-A,Harbor Credit Opps Fund,A,2026-02,0.0064 HCO-A,Harbor Credit Opps Fund,A,2026-03,0.0055' },
  { document_id: 19, page: null, chunk_index: 0, text: 'Fund,Month End,Return (Net) %,Return (Gross) %,Notes Meridian Rates RV Fund,1/31/2026,-0.36,-0.15 Meridian Rates RV Fund,2/28/2026,0.21,0.42 Meridian Rates RV Fund,3/31/2026,0.27,0.48,estimate' },
];

export const MOCK_PASSAGES: MockPassage[] = P.map((p, i) => ({ chunk_id: 80 + i, ...p }));

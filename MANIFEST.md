# sample_docs — Manifest

All funds, managers, people, accounts and figures are fictional.

## Files

| File | Type | Notes |
|---|---|---|
| kestrel_factsheet_2026-01/02/03.pdf | Factsheet | Template A (KPI tiles, "MTD %"), commentary on page 2 |
| northwind_monthly_jan26.pdf / feb26.pdf | Factsheet | Template B (landscape, year×month grid, "Perf. (net)") |
| northwind_dec25_scanned.pdf | Factsheet (scanned) | Image-only PDF, slight rotation + noise, no text layer |
| meridian_rv_tearsheet_2026-01/02.pdf | Factsheet | Template C (dense tear sheet, negatives in parentheses) |
| alder_asia_fund_january2026.pdf / march2026.pdf | Factsheet | Template D (returns in **bps**) |
| alder_dubai_fund_january2026.pdf / march2026.pdf | Factsheet | Template D (bps); sibling fund of Alder Asia |
| alder_capital_q1_2026_performance_report.pdf | Performance report | Multi-fund table (Asia + Dubai) |
| harbor_credit_q4_2025_investor_letter.pdf | Performance report / letter | Prose letter with a monthly table |
| acme_family_office_statement_2026-01.pdf | Account statement | Custodian statement with holdings |
| birchwood_foundation_statement_2026-02.pdf | Account statement | Custodian statement with holdings |
| cedar_ridge_capital_account_kestrel_q4_2025.pdf | Account statement | Fund-admin capital account statement |
| harbor_admin_nav_export_2026-03.csv | CSV | Admin export, returns as decimals (0.0071) |
| meridian_returns_export.csv | CSV | Returns as %, US dates, net + gross columns |
| northwind_flash_update_jan2026.eml | HTML email | Flash estimate |
| meridian_pm_note_feb2026.eml | HTML email | Qualitative PM note, no table |

## Planted edge cases

| Case | Where | Expected behaviour |
|---|---|---|
| Conflict | Northwind Jan 2026: factsheet **+1.92%** vs flash email **+1.89%** | Both kept, flagged `conflict` |
| Alias (same fund) | "Northwind Macro Fund Ltd" (email) vs "Northwind Global Macro Fund" | Alias suggestion in Review |
| Alias (share class) | "Alder Asia Fund SPC – Class A" (Mar) vs "Alder Asia Fund" (Jan) | Alias suggestion in Review |
| Siblings, must NOT merge | Alder Asia Fund vs Alder Dubai Fund | Two separate funds |
| YTD mismatch | Meridian Feb tear sheet states YTD **0.15%**; Jan (0.36)% and Feb 0.21% compound to **−0.15%** | `needs_review` (sign error) |
| Roll-forward mismatch | Acme Jan statement: 12.40mm + 0.50mm − 0 + 0.18mm − 0 = 13.08mm ≠ stated **13.10mm** | `needs_review` |
| Missing month | No Alder Asia Feb factsheet; Feb (0.40%) only appears in the Q1 multi-fund report | Feb row sourced from report |
| Unit variety | % with sign, % in parentheses, bps, decimal fractions | Normalized to % |
| Scanned PDF | northwind_dec25_scanned.pdf | Extracted via Claude vision; snippet check skipped |
| Estimate | Meridian CSV Mar 2026 row noted "estimate"; Kestrel footnote says latest month is estimate | Optional flag |
| Synonyms (hybrid retrieval) | "duration" (Meridian Jan, Harbor letter, Alder Dubai "spread duration") vs "rate sensitivity" / "interest-rate exposure" (Meridian Feb, PM email) | "What did managers say about rate risk?" finds all |

## Ground truth — monthly net returns (%)

| Fund | Oct-25 | Nov-25 | Dec-25 | Jan-26 | Feb-26 | Mar-26 |
|---|---|---|---|---|---|---|
| Kestrel Quant Equity | 0.65 | 1.12 | −0.40 | **2.84** | −0.92 | 1.35 |
| Northwind Global Macro | −0.30 | 0.85 | 1.40 | 1.92 | 0.55 | — |
| Meridian Rates RV | 0.22 | 0.31 | 0.18 | −0.36 | 0.21 | 0.27 (CSV, est.) |
| Alder Asia | — | — | — | 1.10 | 0.40 | −1.25 |
| Alder Dubai | — | — | — | 0.48 | 0.52 | 0.37 |
| Harbor Credit Opps | 0.58 | 0.62 | 0.49 | 0.71 | 0.64 | 0.55 |

"Which fund had the best January return?" → **Kestrel Quant Equity, +2.84%**.

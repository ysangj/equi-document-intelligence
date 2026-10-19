/**
 * Dev-only `POST /api/ask` for the mock api (FR-11, T8 contract). Canned answers for
 * the three example questions, built from sample_docs/MANIFEST.md ground truth and
 * the mock's own rows and passages, so citations, source links and numbers agree:
 *
 * - "best January": `query_sql` only; Kestrel +2.84%, the Northwind conflict
 *   (uses_flagged_data) and one unverified, derived number ("0.92" points ahead).
 * - "rate risk": `search_documents` only; "duration" passages plus the synonym hits
 *   "rate sensitivity" / "interest-rate exposure" (§13 hybrid retrieval).
 * - "worst March": both tools; the first `query_sql` is a non-SELECT that the SQL
 *   guard rejects (§15), then the corrected SELECT; Alder Asia -1.25% (needs review).
 *
 * Any other question gets a short `query_sql` answer over v_funds.
 */
import {
  ASK_MAX_HISTORY,
  ASK_MAX_HISTORY_CONTENT,
  ASK_MAX_QUESTION_LENGTH,
  AskResponse,
  AskStep,
  Citation,
  PassageCitation,
  ReturnItem,
  SqlCitation,
} from '../api/models';
import { MOCK_DOCUMENTS, MOCK_FUND_SEEDS, MOCK_RETURNS } from './mock-data';
import { MockResponse, err } from './mock-http';
import { MOCK_PASSAGES } from './mock-passages';

export const MOCK_ASK_LATENCY_MS = 1400;

const FLAGGED = new Set(['needs_review', 'conflict']);
const RETURN_COLUMNS = ['fund_name', 'value_pct', 'status', 'document_name', 'return_id'] as const;

function monthlyNet(year: number, month: number): ReturnItem[] {
  return MOCK_RETURNS.filter((r) => r.period_year === year && r.period_month === month && r.return_type === 'MTD' && r.net_gross === 'net');
}

function sqlCitation(id: number, sql: string, rows: ReturnItem[]): SqlCitation {
  const data = rows.map((r) => RETURN_COLUMNS.map((c) => r[c] as unknown));
  return {
    id,
    type: 'sql',
    sql,
    columns: [...RETURN_COLUMNS],
    rows: data,
    row_count: data.length,
    truncated: false,
    flagged_row_count: rows.filter((r) => FLAGGED.has(r.status)).length,
  };
}

type Link = (documentId: number) => string | null;

function passage(id: number, documentId: number, chunkIndex: number, link: Link): PassageCitation {
  const p = MOCK_PASSAGES.find((x) => x.document_id === documentId && x.chunk_index === chunkIndex);
  const d = MOCK_DOCUMENTS.find((x) => x.document_id === documentId);
  if (!p || !d) throw new Error(`mock passage ${documentId}/${chunkIndex} missing`);
  return {
    id,
    type: 'passage',
    chunk_id: p.chunk_id,
    document_id: documentId,
    document_name: d.document_name,
    document_type: d.document_type,
    page: p.page,
    text: p.text,
    web_view_link: link(documentId),
  };
}

function step(index: number, tool: AskStep['tool'], input: Record<string, unknown>, result_summary: string, citation_ids: number[], duration_ms: number, error: string | null = null, is_guard_error = false): AskStep {
  return { index, tool, input, result_summary, is_guard_error, error, citation_ids, duration_ms };
}

function response(answer: string, citations: Citation[], steps: AskStep[], extra: Partial<AskResponse> = {}): AskResponse {
  const usesFlagged = citations.some((c) => c.type === 'sql' && c.flagged_row_count > 0);
  return {
    answer,
    citations,
    steps,
    unverified_numbers: [],
    uses_flagged_data: usesFlagged,
    stop_reason: 'answered',
    model: 'claude-opus-5-5',
    usage: { input_tokens: 7400 + steps.length * 1300, output_tokens: 260 + Math.round(answer.length / 4) },
    duration_ms: 1800 + steps.reduce((a, s) => a + s.duration_ms, 0) + steps.length * 1400,
    ...extra,
  };
}

const JAN_SQL =
  "SELECT fund_name, value_pct, status, document_name, return_id FROM v_returns WHERE period_year = 2026 AND period_month = 1 AND return_type = 'MTD' AND net_gross = 'net' ORDER BY value_pct DESC LIMIT 200";

export function bestJanuary(): AskResponse {
  const rows = [...monthlyNet(2026, 1)].sort((a, b) => (b.value_pct ?? 0) - (a.value_pct ?? 0));
  const c1 = sqlCitation(1, JAN_SQL, rows);
  const answer = [
    '**Kestrel Quant Equity Fund LP** had the best January 2026 net return at **+2.84%** [1], 0.92 percentage points ahead of Northwind Global Macro Fund at +1.92% [1].',
    '',
    "Northwind's January figure is flagged as a **conflict**: its flash email reported +1.89% [1], so the runner-up is provisional until that is resolved in Review.",
    '',
    '| Fund | Jan 2026 (net) |',
    '|---|---:|',
    '| Kestrel Quant Equity Fund LP | +2.84% |',
    '| Northwind Global Macro Fund | +1.92% (conflict) |',
    '| Alder Asia Fund | +1.10% |',
    '| Harbor Credit Opportunities Fund | +0.71% |',
    '| Alder Dubai Fund | +0.48% |',
    '| Meridian Rates Relative Value Fund | -0.36% |',
  ].join('\n');
  return response(answer, [c1], [step(1, 'query_sql', { sql: JAN_SQL.replace(' LIMIT 200', '') }, `${rows.length} rows`, [1], 24)], {
    unverified_numbers: ['0.92'],
  });
}

export function rateRisk(link: Link): AskResponse {
  const cites = [passage(1, 9, 1, link), passage(2, 12, 1, link), passage(3, 21, 0, link), passage(4, 1, 1, link), passage(5, 13, 1, link)];
  const answer = [
    'Managers describe their rate risk as **low or recently reduced**, though they use different words for it:',
    '',
    '- **Meridian Rates RV** cut **duration** from 3.4 to 2.1 years ahead of the March FOMC and remains cautious on term premium [1]. In February it called portfolio **rate sensitivity** low: a 25bp parallel move in Treasuries would move NAV by roughly 0.5% [2]. The PM note adds that the book runs very little outright rate sensitivity, mostly swap spreads and cash-futures basis [3].',
    '- **Harbor Credit Opportunities** keeps portfolio duration short at roughly 1.3 years because most of the book is floating rate [4].',
    '- **Alder Dubai** kept spread duration below 4 years given rate uncertainty [5].',
    '',
    '_The "rate sensitivity" and "interest-rate exposure" passages were found by the vector half of hybrid search even though they do not use the words "rate risk"._',
  ].join('\n');
  const input = { query: 'rate risk interest rate exposure duration', k: 8 };
  return response(answer, cites, [step(1, 'search_documents', input, '8 passages (FTS + vector, RRF)', [1, 2, 3, 4, 5], 156)]);
}

const MARCH_BAD_SQL = "CREATE TEMP TABLE march AS SELECT fund_name, value_pct FROM v_returns WHERE period_end = '2026-03-31'";
const MARCH_SQL =
  "SELECT fund_name, value_pct, status, document_name, return_id FROM v_returns WHERE period_year = 2026 AND period_month = 3 AND return_type = 'MTD' AND net_gross = 'net' ORDER BY value_pct ASC LIMIT 200";

export function worstMarch(link: Link): AskResponse {
  const rows = [...monthlyNet(2026, 3)].sort((a, b) => (a.value_pct ?? 0) - (b.value_pct ?? 0));
  const cites: Citation[] = [sqlCitation(1, MARCH_SQL, rows), passage(2, 11, 1, link), passage(3, 20, 0, link)];
  const answer = [
    '**Alder Asia Fund** had the worst March 2026 net return at **-1.25%** [1]. That value comes from the *Alder Asia Fund SPC – Class A* factsheet and is flagged **needs review**, because that share-class name is still an unconfirmed alias of Alder Asia Fund [1].',
    '',
    "The manager blamed a sell-off in Asian equities on renewed tariff concerns and said the fund's 35% net exposure was too high going into the move; net exposure has since been reduced to 18% [2]. Alder's quarterly report repeats that March was difficult after the tariff headlines [3].",
    '',
    'The next weakest was Meridian Rates Relative Value Fund at +0.27%, an estimate from the administrator CSV [1].',
  ].join('\n');
  const steps = [
    step(1, 'query_sql', { sql: MARCH_BAD_SQL }, 'Rejected by SQL guard', [], 2, 'Only a single SELECT statement is allowed (found CREATE TABLE).', true),
    step(2, 'query_sql', { sql: MARCH_SQL.replace(' LIMIT 200', '') }, `${rows.length} rows`, [1], 19),
    step(3, 'search_documents', { query: 'Alder Asia March performance commentary', k: 8, fund: 'Alder Asia Fund' }, '8 passages (FTS + vector, RRF)', [2, 3], 131),
  ];
  return response(answer, cites, steps);
}

const FUNDS_SQL = 'SELECT fund_name, manager, strategy FROM v_funds ORDER BY fund_name LIMIT 200';

export function fallback(): AskResponse {
  const funds = [...MOCK_FUND_SEEDS].sort((a, b) => a.fund_name.localeCompare(b.fund_name));
  const c1: SqlCitation = {
    id: 1,
    type: 'sql',
    sql: FUNDS_SQL,
    columns: ['fund_name', 'manager', 'strategy'],
    rows: funds.map((f) => [f.fund_name, f.manager, f.strategy]),
    row_count: funds.length,
    truncated: false,
    flagged_row_count: 0,
  };
  const answer =
    'The mock api answers the three example questions in full. For anything else it lists the funds it knows: ' +
    `there are ${funds.length} funds across ${new Set(funds.map((f) => f.manager)).size} managers [1].`;
  return response(answer, [c1], [step(1, 'query_sql', { sql: FUNDS_SQL.replace(' LIMIT 200', '') }, `${funds.length} rows`, [1], 11)]);
}

/** Pick the canned answer for a question. */
export function mockAnswer(question: string, link: Link): AskResponse {
  const q = question.toLowerCase();
  if (q.includes('march') && (q.includes('worst') || q.includes('lowest'))) return worstMarch(link);
  if (q.includes('january') && (q.includes('best') || q.includes('highest') || q.includes('top'))) return bestJanuary();
  if (q.includes('rate risk') || q.includes('duration') || q.includes('interest rate') || q.includes('rate sensitivity')) return rateRisk(link);
  return fallback();
}

/** Validate an AskRequest like the Pydantic model (422 `validation_error`, C1 extra fields). */
export function validateAsk(body: unknown): MockResponse | null {
  const bad = (loc: string, message: string) => err(422, 'validation_error', 'Invalid request parameters.', [{ loc, message }]);
  if (!body || typeof body !== 'object') return bad('body', 'Field required');
  const b = body as Record<string, unknown>;
  const extra = Object.keys(b).find((k) => k !== 'question' && k !== 'history');
  if (extra) return bad(`body.${extra}`, 'Extra inputs are not permitted');
  if (typeof b['question'] !== 'string' || !b['question'].trim()) return bad('body.question', 'question must not be blank');
  if (b['question'].length > ASK_MAX_QUESTION_LENGTH) return bad('body.question', `String should have at most ${ASK_MAX_QUESTION_LENGTH} characters`);
  const h = b['history'] ?? [];
  if (!Array.isArray(h)) return bad('body.history', 'Input should be a valid list');
  if (h.length > ASK_MAX_HISTORY) return bad('body.history', `List should have at most ${ASK_MAX_HISTORY} items`);
  for (let k = 0; k < h.length; k++) {
    const it = h[k] as Record<string, unknown>;
    if (!it || (it['role'] !== 'user' && it['role'] !== 'assistant')) return bad(`body.history.${k}.role`, "Input should be 'user' or 'assistant'");
    const c = it['content'];
    if (typeof c !== 'string' || c.length < 1 || c.length > ASK_MAX_HISTORY_CONTENT) return bad(`body.history.${k}.content`, 'String should have 1-8000 characters');
  }
  return null;
}

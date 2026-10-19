import { ReviewItem } from '../../api/models';
import {
  ACCOUNT_ALIAS_CANONICAL,
  CHOOSE_KEEPS_AGREEING,
  CONTRACT_REVIEW_LIST,
  CONTRACT_REVIEW_STATEMENT,
  MAKE_CANONICAL_RESPONSE,
  NORTHWIND_ALIAS_CANONICAL,
  NORTHWIND_JAN_3,
} from '../../testing/fixtures';
import {
  canonicalSwap,
  makeCanonicalBody,
  makeCanonicalTitle,
  renamedEntities,
  OUTCOME_LABELS,
  acceptBody,
  choosePreview,
  choosePreviewText,
  conflictOutcome,
  conflictTolerance,
  groupCandidates,
  groupSummary,
  outcomeSummary,
  reasonLabel,
  valuesAgree,
  allowedActions,
  assertAllowed,
  can,
  chooseBody,
  confirmAliasBody,
  editBody,
  editSuggestions,
  editTargets,
  explainItem,
  explainReason,
  fractionToPct,
  parseEditInput,
  rejectAliasBody,
  rollforwardRows,
  snapshotRef,
  statementPeriodFields,
} from './review-format';

const [CONFLICT, ALIAS, YTD] = CONTRACT_REVIEW_LIST.items;
const ROLLFORWARD = CONTRACT_REVIEW_STATEMENT;

describe('review-format: request bodies (POST /api/review/{id})', () => {
  it('accept', () => {
    expect(acceptBody()).toEqual({ action: 'accept' });
    expect(acceptBody('  Sign checked.  ')).toEqual({ action: 'accept', note: 'Sign checked.' });
    expect(acceptBody('   ')).toEqual({ action: 'accept' });
  });

  it('edit a percent value sends value_pct (what was typed), not value', () => {
    const [t] = editTargets(YTD);
    expect(editBody(YTD, t, '-0.15', 'Corrected sign per monthly compounding.')).toEqual({
      ok: true,
      body: { action: 'edit', value_pct: -0.15, note: 'Corrected sign per monthly compounding.' },
    });
    expect(editBody(YTD, t, '(0.15)%')).toEqual({ ok: true, body: { action: 'edit', value_pct: -0.15 } });
  });

  it('statement items offer the six money fields, then the period dates (T6 contract)', () => {
    expect(editTargets(ROLLFORWARD).map((t) => t.label)).toEqual([
      'Opening balance', 'Contributions', 'Withdrawals', 'P&L', 'Fees', 'Closing balance', 'Period start', 'Period end',
    ]);
    expect(statementPeriodFields(ROLLFORWARD).map((f) => [f.field, f.value])).toEqual([
      ['period_start', '2026-01-01'],
      ['period_end', '2026-01-31'],
    ]);
    expect(statementPeriodFields(YTD)).toEqual([]);
  });

  it('edit a statement date sends field + "YYYY-MM-DD" value', () => {
    const targets = editTargets(ROLLFORWARD);
    const start = targets.find((x) => x.field === 'period_start')!;
    expect(start.snapshot.value_kind).toBe('date');
    expect(editBody(ROLLFORWARD, start, '2026-01-02', 'Statement starts on the 2nd')).toEqual({
      ok: true,
      body: { action: 'edit', field: 'period_start', value: '2026-01-02', note: 'Statement starts on the 2nd' },
    });
    const end = targets.find((x) => x.field === 'period_end')!;
    expect(editBody(ROLLFORWARD, end, '2026-02-30')).toEqual({ ok: false, error: 'Enter a date as YYYY-MM-DD.' });
    expect(editBody(ROLLFORWARD, end, '31/01/2026')).toEqual({ ok: false, error: 'Enter a date as YYYY-MM-DD.' });
    expect(editBody(ROLLFORWARD, end, '')).toEqual({ ok: false, error: 'Enter a value.' });
  });

  it('edit a statement field sends field + value', () => {
    const targets = editTargets(ROLLFORWARD);
    const closing = targets.find((x) => x.field === 'closing_balance')!;
    expect(editBody(ROLLFORWARD, closing, '13,080,000.00')).toEqual({
      ok: true,
      body: { action: 'edit', field: 'closing_balance', value: 13080000 },
    });
    const fees = targets.find((x) => x.field === 'fees')!;
    expect(editBody(ROLLFORWARD, fees, '20000')).toEqual({ ok: true, body: { action: 'edit', field: 'fees', value: 20000 } });
  });

  it('edit a conflict sends value_pct + chosen_id of the row that keeps it', () => {
    const targets = editTargets(CONFLICT);
    expect(targets.map((t) => t.snapshot.id)).toEqual([214, 231]);
    expect(editBody(CONFLICT, targets[1], '1.9')).toEqual({ ok: true, body: { action: 'edit', value_pct: 1.9, chosen_id: 231 } });
  });

  it('edit refuses invalid input instead of building a body', () => {
    const [t] = editTargets(YTD);
    expect(editBody(YTD, t, 'abc')).toEqual({ ok: false, error: 'Enter a percentage, e.g. -0.15 for -0.15%.' });
    expect(editBody(YTD, t, '')).toEqual({ ok: false, error: 'Enter a value.' });
  });

  it('choose', () => {
    expect(chooseBody(214, 'Final factsheet supersedes the flash estimate.')).toEqual({
      action: 'choose',
      chosen_id: 214,
      note: 'Final factsheet supersedes the flash estimate.',
    });
  });

  it('confirm_alias / reject_alias (new entity or reassign)', () => {
    expect(confirmAliasBody()).toEqual({ action: 'confirm_alias' });
    expect(rejectAliasBody(null)).toEqual({ action: 'reject_alias', reassign_to_id: null });
    expect(rejectAliasBody(5, 'sibling fund')).toEqual({ action: 'reject_alias', reassign_to_id: 5, note: 'sibling fund' });
  });

  it('caps notes at 2000 characters', () => {
    expect((acceptBody('x'.repeat(2100)) as { note: string }).note.length).toBe(2000);
  });
});

describe('review-format: allowed actions per kind', () => {
  it('follows the contract per kind', () => {
    expect(allowedActions(YTD)).toEqual(['accept', 'edit']);
    expect(allowedActions(CONFLICT)).toEqual(['choose', 'edit']);
    expect(allowedActions(ALIAS)).toEqual(['confirm_alias', 'reject_alias']);
  });

  it('never offers an action the kind does not allow, even if the server listed it', () => {
    const odd: ReviewItem = { ...YTD, allowed_actions: ['accept', 'edit', 'choose', 'confirm_alias'] };
    expect(allowedActions(odd)).toEqual(['accept', 'edit']);
    expect(can(odd, 'choose')).toBe(false);
  });

  it('honours a narrower server list', () => {
    expect(allowedActions({ ...YTD, allowed_actions: ['edit'] })).toEqual(['edit']);
  });

  it('assertAllowed blocks a disallowed body', () => {
    expect(() => assertAllowed(YTD, chooseBody(1))).toThrow("Action 'choose' is not allowed for a value item.");
    expect(() => assertAllowed(ALIAS, acceptBody())).toThrow();
    expect(() => assertAllowed(CONFLICT, chooseBody(214))).not.toThrow();
  });
});

describe('review-format: edit validation', () => {
  it('percent: number, accounting negatives, range', () => {
    expect(parseEditInput('percent', '+1.92')).toEqual({ ok: true, value: 1.92 });
    expect(parseEditInput('percent', '−0.36%')).toEqual({ ok: true, value: -0.36 });
    expect(parseEditInput('percent', '-100').ok).toBe(false);
    expect(parseEditInput('percent', '1e9').ok).toBe(false);
    expect(parseEditInput('percent', '1..2').ok).toBe(false);
  });

  it('money: thousands separators', () => {
    expect(parseEditInput('money', '13,080,000.00')).toEqual({ ok: true, value: 13080000 });
    expect(parseEditInput('money', '12abc').ok).toBe(false);
  });

  it('date: real YYYY-MM-DD only', () => {
    expect(parseEditInput('date', '2026-01-31')).toEqual({ ok: true, value: '2026-01-31' });
    expect(parseEditInput('date', '2026-02-30').ok).toBe(false);
    expect(parseEditInput('date', '31/01/2026').ok).toBe(false);
  });

  it('text: non-empty', () => {
    expect(parseEditInput('text', '  USD ')).toEqual({ ok: true, value: 'USD' });
    expect(parseEditInput('text', '   ').ok).toBe(false);
  });
});

describe('review-format: review_detail explanations', () => {
  it('YTD: stated vs compounded, the months, and the sign hint', () => {
    expect(explainReason(YTD, 'ytd_mismatch')).toBe(
      'Stated 2026 YTD +0.15% vs -0.15% compounded from Jan 2026 -0.36%, Feb 2026 +0.21% ' +
        '(difference 0.30 pp, tolerance 0.05 pp). Same size, opposite sign: likely a sign error.',
    );
  });

  it('roll-forward: expected vs stated, including fees', () => {
    expect(explainReason(ROLLFORWARD, 'rollforward_mismatch')).toBe(
      'Opening 12,400,000.00 + contributions 500,000.00 − withdrawals 0.00 + P&L 180,000.00 − fees 0.00 = expected ' +
        '13,080,000.00 USD, but the stated closing balance is 13,100,000.00 USD (difference +20,000.00 USD, tolerance 1.00).',
    );
    const withFees = {
      ...ROLLFORWARD,
      detail: { rollforward_mismatch: { ...ROLLFORWARD.detail.rollforward_mismatch!, fees: 4200, expected_closing: 13075800, difference: 24200 } },
    };
    expect(explainReason(withFees, 'rollforward_mismatch')).toContain('− fees 4,200.00 = expected 13,075,800.00 USD');
  });

  it('roll-forward rows carry the statement field for the source panel', () => {
    const rows = rollforwardRows(ROLLFORWARD.detail.rollforward_mismatch!);
    expect(rows.map((r) => `${r.op}${r.label}=${r.value}`)).toEqual([
      'Opening balance=12,400,000.00',
      '+Contributions=500,000.00',
      '−Withdrawals=0.00',
      '+P&L=180,000.00',
      '−Fees=0.00',
      '=Expected closing=13,080,000.00 USD',
      'Stated closing=13,100,000.00 USD',
      'Difference=+20,000.00 USD',
    ]);
    expect(rows.filter((r) => r.field).map((r) => r.field)).toEqual(['opening_balance', 'contributions', 'withdrawals', 'pnl', 'fees', 'closing_balance']);
  });

  it('conflict: both values with their source documents', () => {
    expect(explainReason(CONFLICT, 'conflict')).toBe(
      '2 documents disagree: +1.92% (northwind_monthly_jan26.pdf p.1) vs +1.89% (northwind_flash_update_jan2026.eml p.1).',
    );
  });

  it('alias: suggested canonical name and match score', () => {
    expect(explainReason(ALIAS, 'alias_match')).toBe(
      '“Alder Asia Fund SPC – Class A” looks like Alder Asia Fund · match score 91.4 · from alder_asia_fund_march2026.pdf · 4 linked values',
    );
  });

  it('the other reasons read their detail, with the server message as fallback', () => {
    const base: ReviewItem = { ...YTD, review_reasons: [], detail: {} };
    const ex = (detail: ReviewItem['detail'], code: ReviewItem['review_reasons'][number]) => explainReason({ ...base, detail }, code);
    expect(ex({ low_confidence: { confidence: 0.62, threshold: 0.7 } }, 'low_confidence')).toBe('Extraction confidence 0.62 is below the 0.70 review threshold.');
    expect(ex({ low_confidence: { confidence: 0.6, threshold: 0.7, fields: ['pnl', 'fees'] } }, 'low_confidence')).toContain('(fields: pnl, fees)');
    expect(ex({ out_of_range: { value: 0.62, min: -0.5, max: 0.5 } }, 'out_of_range')).toBe('+62.00% is outside the plausible range -50.00% to +50.00%.');
    expect(ex({ future_date: { date: '2027-01-31', today: '2026-10-16' } }, 'future_date')).toBe('31 Jan 2027 is after today (16 Oct 2026).');
    expect(ex({ snippet_not_found: { snippet: 'YTD 0.15%' } }, 'snippet_not_found')).toBe('The quoted snippet “YTD 0.15%” was not found in the document text.');
    expect(ex({ net_gross_unknown: { raw_label: 'Perf.' } }, 'net_gross_unknown')).toBe('The document does not say whether this return is net or gross (label as written: “Perf.”).');
    expect(ex({ return_type_unknown: { raw_label: null } }, 'return_type_unknown')).toBe('The return period (MTD, QTD, YTD, ...) could not be determined.');
    expect(ex({ unparseable_value: { raw: 'n/a', field: 'closing_balance' } }, 'unparseable_value')).toBe('Could not read “n/a” as a number for closing_balance.');
    expect(ex({}, 'ytd_mismatch')).toBe('Monthly returns for 2026 compound to -0.15% but the stated YTD is +0.15%.');
  });

  it('explainItem returns one line per reason with its label', () => {
    expect(explainItem(CONFLICT).map((e) => [e.code, e.label])).toEqual([['conflict', 'Conflict']]);
  });

  it('suggests the compounded YTD and the expected closing as one-click edits', () => {
    expect(editSuggestions(YTD)).toEqual([{ label: 'Use compounded -0.15%', text: '-0.15', target: 0 }]);
    const [s] = editSuggestions(ROLLFORWARD);
    expect(s.label).toBe('Use expected 13,080,000.00 USD');
    expect(editTargets(ROLLFORWARD)[s.target].field).toBe('closing_balance');
  });

  it('fractionToPct avoids float noise', () => {
    expect(fractionToPct(0.0015)).toBe(0.15);
    expect(fractionToPct(-0.0015076)).toBe(-0.15076);
    expect(fractionToPct(null)).toBeNull();
  });

  it('snapshotRef: statements carry the field, returns do not', () => {
    expect(snapshotRef(ROLLFORWARD.fields![0])).toEqual({ table: 'statements', id: 12, field: 'opening_balance' });
    expect(snapshotRef(CONFLICT.candidates![1])).toEqual({ table: 'returns', id: 231, field: null });
  });
});

describe('review-format: conflict candidates that agree (Q2 fix: choose keeps agreeing rows)', () => {
  const ids = (xs: { id: number }[]) => xs.map((x) => x.id);

  it('reads the tolerance from detail.conflict, null when not stated', () => {
    expect(conflictTolerance(NORTHWIND_JAN_3)).toBe(0.0001);
    expect(conflictTolerance({ detail: {} })).toBeNull();
  });

  it('valuesAgree mirrors abs(a - b) > tolerance, without float noise at the edge', () => {
    expect(valuesAgree(0.0192, 0.0192, 0.0001)).toBe(true);
    expect(valuesAgree(0.0192, 0.0191, 0.0001)).toBe(true); // exactly at the tolerance: the server keeps it
    expect(valuesAgree(0.0192, 0.0189, 0.0001)).toBe(false);
    expect(valuesAgree(0.0192, 0.01925, null)).toBe(false); // no tolerance: exact only
    expect(valuesAgree(0.1 + 0.2, 0.3, null)).toBe(true);
    expect(valuesAgree(13_100_000, 13_100_001, 1)).toBe(true);
    expect(valuesAgree('A USD', 'A USD', 0.0001)).toBe(true);
    expect(valuesAgree('A USD', 'Class A', 0.0001)).toBe(false);
    expect(valuesAgree(null, null, 0.0001)).toBe(true);
  });

  it('groups equal values, largest group first: "+1.92% (2 sources) vs +1.89% (1 source)"', () => {
    const g = groupCandidates(NORTHWIND_JAN_3.candidates!, 0.0001);
    expect(g.map((x) => [x.label, ids(x.members)])).toEqual([
      ['+1.92%', [214, 219]],
      ['+1.89%', [231]],
    ]);
    expect(groupSummary(g)).toBe('+1.92% (2 sources) vs +1.89% (1 source)');
    // the 2-candidate contract example: every value stands alone
    expect(groupSummary(groupCandidates(CONFLICT.candidates!, 0.0001))).toBe('+1.92% (1 source) vs +1.89% (1 source)');
  });

  it('groups within the tolerance, and only exactly equal values without one', () => {
    const near = NORTHWIND_JAN_3.candidates!.map((c, i) => (i === 1 ? { ...c, value: 0.01915, value_pct: 1.915 } : c));
    expect(groupCandidates(near, 0.0001).map((x) => ids(x.members))).toEqual([[214, 219], [231]]);
    expect(groupCandidates(near, null).map((x) => ids(x.members))).toEqual([[214], [219], [231]]);
  });

  it('choosePreview: choosing 1.92 keeps the other 1.92 and removes only the 1.89', () => {
    expect(ids(choosePreview(NORTHWIND_JAN_3, 214).kept)).toEqual([219]);
    expect(ids(choosePreview(NORTHWIND_JAN_3, 214).removed)).toEqual([231]);
    expect(ids(choosePreview(NORTHWIND_JAN_3, 231).kept)).toEqual([]);
    expect(ids(choosePreview(NORTHWIND_JAN_3, 231).removed)).toEqual([214, 219]);
    expect(choosePreviewText(NORTHWIND_JAN_3, 219)).toBe('Use this value; keeps 1 agreeing source, removes 1 with a different value.');
    expect(choosePreviewText(NORTHWIND_JAN_3, 231)).toBe('Use this value; removes 2 with a different value.');
  });

  it('conflictOutcome reads affected: status_changed on another row is "kept", not deleted', () => {
    const out = conflictOutcome(NORTHWIND_JAN_3, 214, CHOOSE_KEEPS_AGREEING.affected);
    expect(out.map((o) => [o.snapshot.id, o.outcome, o.label])).toEqual([
      [214, 'chosen', 'chosen'],
      [219, 'kept', 'kept (agrees with chosen value)'],
      [231, 'removed', 'removed (different value)'],
    ]);
    expect(outcomeSummary(out)).toBe('kept 1 agreeing source, removed 1 with a different value');
    // the old semantics (every other row deleted) still read correctly
    const old = conflictOutcome(CONFLICT, 214, [
      { table: 'returns', id: 214, change: 'status_changed', status: 'ok' },
      { table: 'returns', id: 231, change: 'deleted', status: null },
    ]);
    expect(old.map((o) => o.outcome)).toEqual(['chosen', 'removed']);
    expect(conflictOutcome(CONFLICT, 214, []).map((o) => o.label)).toEqual([OUTCOME_LABELS.unchanged, OUTCOME_LABELS.unchanged]);
  });

  it('explains a conflict by distinct value with its sources', () => {
    expect(explainReason(NORTHWIND_JAN_3, 'conflict')).toBe(
      '3 documents report 2 different values: +1.92% (2 sources: northwind_monthly_jan26.pdf p.1, northwind_monthly_feb26.pdf p.1) vs +1.89% (1 source: northwind_flash_update_jan2026.eml p.1).',
    );
  });
});

describe('review-format: snippet_not_found with value_missing (Q2 fix)', () => {
  const item = (d: ReviewItem['detail']['snippet_not_found']): ReviewItem => ({
    ...YTD,
    review_reasons: ['snippet_not_found'],
    reasons: [{ code: 'snippet_not_found', label: 'Snippet not found', message: 'The value is not written in its source snippet, so it cannot be traced.' }],
    detail: { snippet_not_found: d },
  });

  it('says the snippet was found but lacks the value', () => {
    const t = explainReason(item({ snippet: 'Alder Asia Fund  Q1', value_missing: true }), 'snippet_not_found');
    expect(t.startsWith("Snippet found, but it doesn't contain the value")).toBe(true);
    expect(t).toContain('“Alder Asia Fund  Q1”');
    expect(t).not.toContain('not found');
    expect(explainReason(item({ snippet: null, value_missing: true, fields: ['weight'] }), 'snippet_not_found')).toBe(
      "Snippet found, but it doesn't contain the value (fields: weight).",
    );
  });

  it('labels the chip "Value not in snippet"; without value_missing it stays "Snippet not found"', () => {
    expect(reasonLabel(item({ snippet: 'x', value_missing: true }), 'snippet_not_found')).toBe('Value not in snippet');
    expect(explainItem(item({ snippet: 'x', value_missing: true }))[0].label).toBe('Value not in snippet');
    const plain = item({ snippet: 'YTD 0.15%' });
    expect(reasonLabel(plain, 'snippet_not_found')).toBe('Snippet not found');
    expect(explainReason(plain, 'snippet_not_found')).toBe('The quoted snippet “YTD 0.15%” was not found in the document text.');
    expect(explainReason(item({ snippet: 'YTD 0.15%', value_missing: false }), 'snippet_not_found')).toContain('was not found');
  });
});

describe('review-format: make_canonical ("Use as main name", BUG-Q3-01)', () => {
  it('the body is exactly {"action":"make_canonical"}, with no note', () => {
    expect(JSON.stringify(makeCanonicalBody())).toBe('{"action":"make_canonical"}');
  });

  it('is offered only when the server lists it for an alias item', () => {
    expect(can(ALIAS, 'make_canonical')).toBe(false); // the T6 contract example does not list it
    expect(allowedActions(NORTHWIND_ALIAS_CANONICAL)).toEqual(['confirm_alias', 'reject_alias', 'make_canonical']);
    expect(can({ ...YTD, allowed_actions: ['accept', 'make_canonical'] }, 'make_canonical')).toBe(false); // never for a value item
    expect(() => assertAllowed(ALIAS, makeCanonicalBody())).toThrow("Action 'make_canonical' is not allowed for a alias item.");
    expect(() => assertAllowed(NORTHWIND_ALIAS_CANONICAL, makeCanonicalBody())).not.toThrow();
  });

  it('tooltip names the alias and the name it keeps as an alias (fund and account)', () => {
    expect(makeCanonicalTitle(canonicalSwap(NORTHWIND_ALIAS_CANONICAL)!)).toBe(
      "Makes 'Northwind Global Macro Fund' the fund's display name everywhere; 'Northwind Macro Fund Ltd' is kept as an alias.",
    );
    expect(makeCanonicalTitle(canonicalSwap(ACCOUNT_ALIAS_CANONICAL)!)).toBe(
      "Makes 'Acme Family Office' the account's display name everywhere; 'Acme Family Office LLC' is kept as an alias.",
    );
    expect(canonicalSwap(YTD)).toBeNull();
  });

  it('renamedEntities picks the renamed funds / accounts rows from affected', () => {
    expect(renamedEntities(MAKE_CANONICAL_RESPONSE.affected)).toEqual([{ table: 'funds', id: 2, change: 'renamed', status: null }]);
    expect(renamedEntities([{ table: 'fund_aliases', id: 10, change: 'alias_confirmed', status: null }])).toEqual([]);
    expect(renamedEntities(null)).toEqual([]);
  });
});

/**
 * Review queue helpers (FR-13), kept pure so they are unit-tested directly:
 * - human-readable explanations of `review_detail`, one per reason
 * - allowed-actions gating per item kind
 * - validation of the inline Edit input
 * - request bodies for every action, exactly as POST /api/review/{id} expects them
 *
 * Note on units: `review_detail` carries returns as fractions (contract table), so the
 * percent shown here is fraction * 100, rounded to 6 dp before C6 formatting. Every
 * other place in the UI displays the server's `value_pct`.
 */
import {
  ALLOWED_ACTIONS,
  AcceptAction,
  AffectedRow,
  ChooseAction,
  ConfirmAliasAction,
  EditAction,
  MakeCanonicalAction,
  REVIEW_REASON_LABELS,
  RejectAliasAction,
  ReviewActionName,
  ReviewActionRequest,
  ReviewItem,
  ReviewReason,
  RollforwardMismatchDetail,
  ValueKind,
  ValueSnapshot,
  YtdMismatchDetail,
} from '../../api/models';
import { SourceRef } from '../../shared/source-loader';
import { EMPTY, formatAmount, formatDate, formatKindedValue, formatPct, formatPeriod } from '../../shared/format';

// ---- units --------------------------------------------------------------------------

/** fraction (0.0015) -> value_pct (0.15), without float noise. */
export function fractionToPct(f: number | null | undefined): number | null {
  if (typeof f !== 'number' || !Number.isFinite(f)) return null;
  return Math.round(f * 100 * 1e6) / 1e6;
}

/** A fraction as a signed percent, `+0.15%`. */
export function pctOfFraction(f: number | null | undefined): string {
  return formatPct(fractionToPct(f));
}

/** A fraction difference in percentage points, `0.30 pp`. */
export function ppOfFraction(f: number): string {
  const v = Math.round(Math.abs(f) * 100 * 100) / 100;
  return `${v.toFixed(2)} pp`;
}

function signedAmount(n: number, currency: string | null): string {
  const s = formatAmount(n);
  const signed = n > 0 ? `+${s}` : s;
  return currency ? `${signed} ${currency}` : signed;
}

function money(n: number, currency: string | null): string {
  const s = formatAmount(n);
  return currency ? `${s} ${currency}` : s;
}

const quote = (s: string) => `“${s}”`;

// ---- explanations ------------------------------------------------------------------------

export interface Explanation {
  code: ReviewReason;
  label: string;
  text: string;
}

/** YTD: stated vs compounded, with the months that were compounded. */
export function explainYtd(d: YtdMismatchDetail): string {
  const months = d.months.map((m) => `${formatPeriod(m.period_end)} ${pctOfFraction(m.value)}`).join(', ');
  const signFlip = Math.abs(d.stated + d.computed) <= d.tolerance && Math.abs(d.stated) > d.tolerance;
  return (
    `Stated ${d.year} YTD ${pctOfFraction(d.stated)} vs ${pctOfFraction(d.computed)} compounded from ` +
    `${months || 'no monthly returns'} (difference ${ppOfFraction(d.difference)}, tolerance ${ppOfFraction(d.tolerance)}).` +
    (signFlip ? ' Same size, opposite sign: likely a sign error.' : '')
  );
}

/** Roll-forward: opening + contributions - withdrawals + P&L - fees = expected vs stated. */
export function explainRollforward(d: RollforwardMismatchDetail): string {
  const c = d.currency;
  return (
    `Opening ${formatAmount(d.opening_balance)} + contributions ${formatAmount(d.contributions)} − withdrawals ` +
    `${formatAmount(d.withdrawals)} + P&L ${formatAmount(d.pnl)} − fees ${formatAmount(d.fees)} = expected ` +
    `${money(d.expected_closing, c)}, but the stated closing balance is ${money(d.stated_closing, c)} ` +
    `(difference ${signedAmount(d.difference, c)}, tolerance ${formatAmount(d.tolerance)}).`
  );
}

function snapshotSource(s: ValueSnapshot): string {
  return s.provenance.page !== null ? `${s.document_name} p.${s.provenance.page}` : s.document_name;
}

/**
 * Conflict: the distinct values, each with the documents that report it, e.g.
 * "3 documents report 2 different values: +1.92% (2 sources: a p.1, b p.1) vs +1.89% (1 source: c p.1)."
 */
export function explainConflict(candidates: ValueSnapshot[], tolerance: number | null = null): string {
  const groups = groupCandidates(candidates, tolerance);
  if (groups.length === candidates.length) {
    const parts = candidates.map((c) => `${formatKindedValue(c)} (${snapshotSource(c)})`);
    return `${candidates.length} documents disagree: ${parts.join(' vs ')}.`;
  }
  const parts = groups.map((g) => `${g.label} (${sourcesLabel(g.members.length)}: ${g.members.map(snapshotSource).join(', ')})`);
  return `${candidates.length} documents report ${groups.length} different values: ${parts.join(' vs ')}.`;
}

// ---- conflict candidates: which agree with which ------------------------------------------

/**
 * The conflict tolerance the server applies (`detail.conflict.tolerance`, in the unit of
 * `value`: a fraction for returns, currency units for money). null when not stated, in
 * which case only exactly equal values agree.
 */
export function conflictTolerance(item: Pick<ReviewItem, 'detail'>): number | null {
  const t = item.detail.conflict?.tolerance;
  return typeof t === 'number' && Number.isFinite(t) && t >= 0 ? t : null;
}

/**
 * Do two candidate values agree, i.e. would `choose` keep the other one? Mirrors the
 * server's `abs(a - b) > tolerance` → differ (`app/ingest/conflicts.py`), with a tiny
 * epsilon so float noise (0.0192 - 0.0191) does not split values exactly at the tolerance.
 * Non-numbers (text, dates) agree only when equal.
 */
export function valuesAgree(a: ValueSnapshot['value'], b: ValueSnapshot['value'], tolerance: number | null): boolean {
  if (typeof a === 'number' && typeof b === 'number') {
    return Math.abs(a - b) <= (tolerance ?? 0) + 1e-12 * Math.max(1, Math.abs(a), Math.abs(b));
  }
  return a === b;
}

export interface CandidateGroup {
  /** the first member's display value, e.g. "+1.92%" */
  label: string;
  members: ValueSnapshot[];
}

/**
 * Candidates grouped by agreeing value, in first-seen order, largest group first
 * (ties keep that order). Each candidate joins the first group whose first member it
 * agrees with.
 */
export function groupCandidates(candidates: ValueSnapshot[], tolerance: number | null): CandidateGroup[] {
  const groups: CandidateGroup[] = [];
  for (const c of candidates) {
    const g = groups.find((x) => valuesAgree(x.members[0].value, c.value, tolerance));
    if (g) g.members.push(c);
    else groups.push({ label: formatKindedValue(c), members: [c] });
  }
  return groups.map((g, i) => ({ g, i })).sort((a, b) => b.g.members.length - a.g.members.length || a.i - b.i).map((x) => x.g);
}

export function sourcesLabel(n: number): string {
  return `${n} source${n === 1 ? '' : 's'}`;
}

/** "+1.92% (2 sources) vs +1.89% (1 source)". */
export function groupSummary(groups: CandidateGroup[]): string {
  return groups.map((g) => `${g.label} (${sourcesLabel(g.members.length)})`).join(' vs ');
}

/** What `choose` would do with each candidate if `chosenId` were chosen (contract, Q2 fix). */
export function choosePreview(item: Pick<ReviewItem, 'candidates' | 'detail'>, chosenId: number): { kept: ValueSnapshot[]; removed: ValueSnapshot[] } {
  const all = item.candidates ?? [];
  const chosen = all.find((c) => c.id === chosenId);
  const tol = conflictTolerance(item);
  const others = all.filter((c) => c.id !== chosenId);
  if (!chosen) return { kept: [], removed: others };
  return {
    kept: others.filter((c) => valuesAgree(chosen.value, c.value, tol)),
    removed: others.filter((c) => !valuesAgree(chosen.value, c.value, tol)),
  };
}

/** Tooltip for a Choose button: what happens to the other candidates. */
export function choosePreviewText(item: Pick<ReviewItem, 'candidates' | 'detail'>, chosenId: number): string {
  const { kept, removed } = choosePreview(item, chosenId);
  const parts: string[] = [];
  if (kept.length) parts.push(`keeps ${kept.length} agreeing ${kept.length === 1 ? 'source' : 'sources'}`);
  if (removed.length) parts.push(`removes ${removed.length} with a different value`);
  return parts.length ? `Use this value; ${parts.join(', ')}.` : 'Use this value.';
}

export type CandidateOutcomeKind = 'chosen' | 'kept' | 'removed' | 'unchanged';

export interface CandidateOutcome {
  snapshot: ValueSnapshot;
  outcome: CandidateOutcomeKind;
  label: string;
}

export const OUTCOME_LABELS: Record<CandidateOutcomeKind, string> = {
  chosen: 'chosen',
  kept: 'kept (agrees with chosen value)',
  removed: 'removed (different value)',
  unchanged: 'unchanged',
};

/**
 * What a decision on a conflict did to each candidate, read from the response's
 * `affected` (not predicted): `deleted` → removed; `status_changed` / `value_edited` on
 * the chosen row → chosen, on any other row → kept (it agreed with the chosen value).
 */
export function conflictOutcome(item: Pick<ReviewItem, 'candidates'>, chosenId: number | null, affected: AffectedRow[]): CandidateOutcome[] {
  return (item.candidates ?? []).map((c) => {
    const a = affected.find((x) => x.table === c.table && x.id === c.id);
    let outcome: CandidateOutcomeKind = 'unchanged';
    if (a?.change === 'deleted') outcome = 'removed';
    else if (a) outcome = c.id === chosenId ? 'chosen' : 'kept';
    return { snapshot: c, outcome, label: OUTCOME_LABELS[outcome] };
  });
}

/** One-line toast text for a conflict decision, e.g. "kept 1 agreeing source, removed 1 with a different value". */
export function outcomeSummary(outcomes: CandidateOutcome[]): string {
  const kept = outcomes.filter((o) => o.outcome === 'kept').length;
  const removed = outcomes.filter((o) => o.outcome === 'removed').length;
  const parts: string[] = [];
  if (kept) parts.push(`kept ${kept} agreeing ${kept === 1 ? 'source' : 'sources'}`);
  if (removed) parts.push(`removed ${removed} with a different value`);
  return parts.join(', ');
}

export function explainAlias(item: ReviewItem): string {
  const a = item.alias;
  const d = item.detail.alias_match;
  const alias = a?.alias ?? d?.alias ?? '';
  const target = a?.suggested_name ?? item.subject.fund_name ?? item.subject.account_name ?? 'an existing entity';
  const score = a?.match_score ?? d?.match_score ?? null;
  const parts = [`${quote(alias)} looks like ${target}`];
  if (score !== null) parts.push(`match score ${score.toFixed(1)}`);
  if (a?.source_document_name) parts.push(`from ${a.source_document_name}`);
  if (a) parts.push(`${a.affected_rows} linked value${a.affected_rows === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

/** How a raw detail number reads for this item (out_of_range has no unit of its own). */
function itemValue(item: ReviewItem, v: number): string {
  const kind = item.current?.value_kind ?? item.candidates?.[0]?.value_kind;
  if (kind === 'percent') return pctOfFraction(v);
  if (kind === 'money') return formatAmount(v);
  return String(v);
}

export function explainReason(item: ReviewItem, code: ReviewReason): string {
  const d = item.detail;
  const fallback = item.reasons.find((r) => r.code === code)?.message ?? REVIEW_REASON_LABELS[code];
  switch (code) {
    case 'ytd_mismatch':
      return d.ytd_mismatch ? explainYtd(d.ytd_mismatch) : fallback;
    case 'rollforward_mismatch':
      return d.rollforward_mismatch ? explainRollforward(d.rollforward_mismatch) : fallback;
    case 'conflict':
      return item.candidates?.length ? explainConflict(item.candidates, conflictTolerance(item)) : fallback;
    case 'alias_match':
      return item.alias || d.alias_match ? explainAlias(item) : fallback;
    case 'low_confidence': {
      const x = d.low_confidence;
      if (!x) return fallback;
      const fields = x.fields?.length ? ` (fields: ${x.fields.join(', ')})` : '';
      return `Extraction confidence ${x.confidence.toFixed(2)} is below the ${x.threshold.toFixed(2)} review threshold${fields}.`;
    }
    case 'out_of_range': {
      const x = d.out_of_range;
      if (!x) return fallback;
      return `${itemValue(item, x.value)} is outside the plausible range ${itemValue(item, x.min)} to ${itemValue(item, x.max)}.`;
    }
    case 'future_date': {
      const x = d.future_date;
      return x ? `${formatDate(x.date)} is after today (${formatDate(x.today)}).` : fallback;
    }
    case 'snippet_not_found': {
      const x = d.snippet_not_found;
      if (!x) return fallback;
      const fields = x.fields?.length ? ` (fields: ${x.fields.join(', ')})` : '';
      if (x.value_missing) {
        return x.snippet
          ? `Snippet found, but it doesn't contain the value: ${quote(x.snippet)} is in the document text, but the extracted value is not written in it${fields}.`
          : `Snippet found, but it doesn't contain the value${fields}.`;
      }
      return x.snippet
        ? `The quoted snippet ${quote(x.snippet)} was not found in the document text${fields}.`
        : `No snippet could be matched in the document text${fields}.`;
    }
    case 'net_gross_unknown': {
      const raw = d.net_gross_unknown?.raw_label;
      return `The document does not say whether this return is net or gross${raw ? ` (label as written: ${quote(raw)})` : ''}.`;
    }
    case 'return_type_unknown': {
      const raw = d.return_type_unknown?.raw_label;
      return `The return period (MTD, QTD, YTD, ...) could not be determined${raw ? ` (label as written: ${quote(raw)})` : ''}.`;
    }
    case 'unparseable_value': {
      const x = d.unparseable_value;
      if (!x) return fallback;
      return `Could not read ${x.raw ? quote(x.raw) : 'the value'} as a number${x.field ? ` for ${x.field}` : ''}.`;
    }
    default:
      return fallback;
  }
}

/** Chip label for a reason; `snippet_not_found` with `value_missing` reads differently. */
export function reasonLabel(item: ReviewItem, code: ReviewReason): string {
  if (code === 'snippet_not_found' && item.detail.snippet_not_found?.value_missing) return SNIPPET_VALUE_MISSING_LABEL;
  return item.reasons.find((r) => r.code === code)?.label ?? REVIEW_REASON_LABELS[code] ?? code;
}

export const SNIPPET_VALUE_MISSING_LABEL = 'Value not in snippet';

export function explainItem(item: ReviewItem): Explanation[] {
  return item.review_reasons.map((code) => ({ code, label: reasonLabel(item, code), text: explainReason(item, code) }));
}

/** Chip class for a reason: conflicts red, everything else amber (mockup `.st`). */
export function reasonClass(code: ReviewReason): 'cf' | 'rev' {
  return code === 'conflict' ? 'cf' : 'rev';
}

// ---- roll-forward table -------------------------------------------------------------------

export interface RollforwardRow {
  op: '' | '+' | '−' | '=';
  label: string;
  value: string;
  /** the statement field, so the cell can open its source */
  field: string | null;
  emphasis?: 'expected' | 'stated' | 'diff';
}

export function rollforwardRows(d: RollforwardMismatchDetail): RollforwardRow[] {
  const c = d.currency;
  return [
    { op: '', label: 'Opening balance', value: formatAmount(d.opening_balance), field: 'opening_balance' },
    { op: '+', label: 'Contributions', value: formatAmount(d.contributions), field: 'contributions' },
    { op: '−', label: 'Withdrawals', value: formatAmount(d.withdrawals), field: 'withdrawals' },
    { op: '+', label: 'P&L', value: formatAmount(d.pnl), field: 'pnl' },
    { op: '−', label: 'Fees', value: formatAmount(d.fees), field: 'fees' },
    { op: '=', label: 'Expected closing', value: money(d.expected_closing, c), field: null, emphasis: 'expected' },
    { op: '', label: 'Stated closing', value: money(d.stated_closing, c), field: 'closing_balance', emphasis: 'stated' },
    { op: '', label: 'Difference', value: signedAmount(d.difference, c), field: null, emphasis: 'diff' },
  ];
}

// ---- sources ------------------------------------------------------------------------------

/** The `/api/sources` reference for a value snapshot (returns / fund facts take no field). */
export function snapshotRef(s: Pick<ValueSnapshot, 'table' | 'id' | 'field'>): SourceRef {
  const needsField = s.table === 'statements' || s.table === 'statement_holdings';
  return { table: s.table, id: s.id, field: needsField ? s.field : null };
}

// ---- actions ------------------------------------------------------------------------------

/**
 * Actions offered for an item: the server's `allowed_actions`, intersected with what
 * the contract allows for its kind (value: accept/edit; conflict: choose/edit;
 * alias: confirm_alias/reject_alias), so a malformed item can never show, e.g., Choose
 * on a value.
 */
export function allowedActions(item: Pick<ReviewItem, 'kind' | 'allowed_actions'>): ReviewActionName[] {
  const byKind = ALLOWED_ACTIONS[item.kind] ?? [];
  return byKind.filter((a) => item.allowed_actions.includes(a));
}

export function can(item: Pick<ReviewItem, 'kind' | 'allowed_actions'>, action: ReviewActionName): boolean {
  return allowedActions(item).includes(action);
}

const withNote = <T extends object>(body: T, note?: string | null): T & { note?: string } => {
  const n = note?.trim();
  return n ? { ...body, note: n.slice(0, 2000) } : body;
};

export function acceptBody(note?: string | null): AcceptAction {
  return withNote({ action: 'accept' as const }, note);
}

export function chooseBody(chosenId: number, note?: string | null): ChooseAction {
  return withNote({ action: 'choose' as const, chosen_id: chosenId }, note);
}

export function confirmAliasBody(note?: string | null): ConfirmAliasAction {
  return withNote({ action: 'confirm_alias' as const }, note);
}

/** `null` creates a new fund / account named after the alias; an id moves the alias there. */
export function rejectAliasBody(reassignToId: number | null, note?: string | null): RejectAliasAction {
  return withNote({ action: 'reject_alias' as const, reassign_to_id: reassignToId }, note);
}

/** Exactly `{"action":"make_canonical"}`: the contract takes no `note` or other fields. */
export function makeCanonicalBody(): MakeCanonicalAction {
  return { action: 'make_canonical' };
}

/** What "Use as main name" does to an alias item: the alias text and the name it replaces. */
export interface CanonicalSwap {
  entity: 'fund' | 'account';
  /** the alias text that becomes the display name */
  alias: string;
  /** the current canonical name, which is kept as an alias */
  current: string;
}

export function canonicalSwap(item: Pick<ReviewItem, 'alias'>): CanonicalSwap | null {
  const a = item.alias;
  return a ? { entity: a.entity, alias: a.alias, current: a.suggested_name } : null;
}

/** Tooltip on "Use as main name". */
export function makeCanonicalTitle(swap: CanonicalSwap): string {
  return `Makes '${swap.alias}' the ${swap.entity}'s display name everywhere; '${swap.current}' is kept as an alias.`;
}

/** The `funds` / `accounts` rows renamed by a `make_canonical` response. */
export function renamedEntities(affected: AffectedRow[] | null | undefined): AffectedRow[] {
  return (affected ?? []).filter((r) => r.change === 'renamed' && (r.table === 'funds' || r.table === 'accounts'));
}

/** Edit: what the user typed, parsed per `value_kind`. */
export type ParsedInput = { ok: true; value: number | string } | { ok: false; error: string };

/** Accepts `-0.15`, `+0.15`, `0.15%`, `(0.36)` (accounting negative) and `1,234.5`. */
function parseNumber(raw: string): number | null {
  let s = raw.trim().replace(/[\s,]/g, '').replace(/%$/, '');
  let neg = false;
  const paren = /^\((.*)\)$/.exec(s);
  if (paren) {
    neg = true;
    s = paren[1];
  }
  s = s.replace(/^−/, '-');
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s)) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return neg ? -Math.abs(n) : n;
}

export function parseEditInput(kind: ValueKind, raw: string): ParsedInput {
  const s = (raw ?? '').trim();
  if (!s) return { ok: false, error: 'Enter a value.' };
  switch (kind) {
    case 'percent': {
      const n = parseNumber(s);
      if (n === null) return { ok: false, error: 'Enter a percentage, e.g. -0.15 for -0.15%.' };
      if (n <= -100 || n > 1000) return { ok: false, error: 'A return must be above -100% and at most 1000%.' };
      return { ok: true, value: n };
    }
    case 'money':
    case 'number': {
      const n = parseNumber(s);
      if (n === null) return { ok: false, error: 'Enter a number, e.g. 13080000 or 13,080,000.00.' };
      if (Math.abs(n) >= 1e15) return { ok: false, error: 'That number is too large.' };
      return { ok: true, value: n };
    }
    case 'date': {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
      const d = m ? new Date(`${s}T00:00:00Z`) : null;
      if (!m || !d || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== s) {
        return { ok: false, error: 'Enter a date as YYYY-MM-DD.' };
      }
      return { ok: true, value: s };
    }
    case 'text':
    default:
      return s.length > 2000 ? { ok: false, error: 'At most 2000 characters.' } : { ok: true, value: s };
  }
}

export interface EditTarget {
  /** what the `field` param should be (null: omit, server default `value`) */
  field: string | null;
  label: string;
  snapshot: ValueSnapshot;
}

const FIELD_LABELS: Record<string, string> = {
  opening_balance: 'Opening balance',
  contributions: 'Contributions',
  withdrawals: 'Withdrawals',
  pnl: 'P&L',
  fees: 'Fees',
  closing_balance: 'Closing balance',
  period_start: 'Period start',
  period_end: 'Period end',
  market_value: 'Market value',
  weight: 'Weight',
  quantity: 'Quantity',
  value: 'Value',
};

/** Statement date fields in `ReviewItem.fields` (T6: after the six money fields). */
export const STATEMENT_DATE_FIELDS = ['period_start', 'period_end'] as const;

/** The statement's `period_start` / `period_end` snapshots, in that order ([] otherwise). */
export function statementPeriodFields(item: ReviewItem): ValueSnapshot[] {
  if (item.target_table !== 'statements' || !item.fields) return [];
  return STATEMENT_DATE_FIELDS.map((f) => item.fields!.find((s) => s.field === f)).filter((s): s is ValueSnapshot => !!s);
}

/** Which values the Edit form can change for this item (statements: money fields, then the period dates). */
export function editTargets(item: ReviewItem): EditTarget[] {
  if (item.kind === 'conflict') {
    return (item.candidates ?? []).map((c) => ({ field: null, label: `${formatKindedValue(c)} · ${c.document_name}`, snapshot: c }));
  }
  if (item.target_table === 'statements') {
    const fields = item.fields?.length ? item.fields : item.current ? [item.current] : [];
    return fields.map((f) => ({ field: f.field, label: FIELD_LABELS[f.field] ?? f.field, snapshot: f }));
  }
  if (!item.current) return [];
  const f = item.current.field;
  return [{ field: f && f !== 'value' ? f : null, label: FIELD_LABELS[f] ?? 'Value', snapshot: item.current }];
}

/** The field the Edit form opens on (statements: the closing balance). */
export function defaultEditTarget(item: ReviewItem): number {
  const t = editTargets(item);
  if (item.target_table === 'statements') {
    const i = t.findIndex((x) => x.field === (item.current?.field ?? 'closing_balance'));
    return i >= 0 ? i : 0;
  }
  if (item.kind === 'conflict') {
    const i = t.findIndex((x) => x.snapshot.id === item.target_id);
    return i >= 0 ? i : 0;
  }
  return 0;
}

/** The input's initial text: the current value, as a plain editable number. */
export function editInitialText(s: ValueSnapshot): string {
  if (s.value_kind === 'percent') return s.value_pct === null ? '' : String(s.value_pct);
  if (s.value === null || s.value === undefined) return '';
  return String(s.value);
}

/** A one-click fix the checks already computed (compounded YTD, expected closing). */
export interface Suggestion {
  label: string;
  text: string;
  /** index into editTargets() it applies to */
  target: number;
}

export function editSuggestions(item: ReviewItem): Suggestion[] {
  const out: Suggestion[] = [];
  const targets = editTargets(item);
  const ytd = item.detail.ytd_mismatch;
  if (ytd && targets.length) {
    const pct = fractionToPct(ytd.computed);
    if (pct !== null) {
      const rounded = Math.round(pct * 100) / 100;
      out.push({ label: `Use compounded ${formatPct(rounded)}`, text: String(rounded), target: 0 });
    }
  }
  const rf = item.detail.rollforward_mismatch;
  if (rf) {
    const i = targets.findIndex((t) => t.field === 'closing_balance');
    if (i >= 0) out.push({ label: `Use expected ${money(rf.expected_closing, rf.currency)}`, text: String(rf.expected_closing), target: i });
  }
  return out;
}

/**
 * The Edit body: `value_pct` for percent values (the number typed, e.g. -0.15),
 * `value` otherwise; `field` for statements; `chosen_id` for conflicts.
 */
export function editBody(item: ReviewItem, target: EditTarget, raw: string, note?: string | null): { ok: true; body: EditAction } | { ok: false; error: string } {
  const parsed = parseEditInput(target.snapshot.value_kind, raw);
  if (!parsed.ok) return parsed;
  const body: EditAction = { action: 'edit' };
  if (item.target_table === 'statements') body.field = target.field ?? target.snapshot.field;
  else if (target.field) body.field = target.field;
  if (target.snapshot.value_kind === 'percent') body.value_pct = parsed.value as number;
  else body.value = parsed.value;
  if (item.kind === 'conflict') body.chosen_id = target.snapshot.id;
  return { ok: true, body: withNote(body, note) };
}

/** Guard used before any POST: the action must be allowed for the item's kind. */
export function assertAllowed(item: ReviewItem, body: ReviewActionRequest): void {
  if (!can(item, body.action)) {
    throw new Error(`Action '${body.action}' is not allowed for a ${item.kind} item.`);
  }
}

/** "−0.15%" style display for a snapshot; em dash when missing. */
export function snapshotValue(s: ValueSnapshot | null | undefined): string {
  return s ? formatKindedValue(s) : EMPTY;
}

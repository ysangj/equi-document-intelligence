/**
 * Dev-only review queue for the mock api (`GET /api/review`, `POST /api/review/{id}`).
 *
 * The queue is derived from the current value rows, exactly as the real `review_queue`
 * view is: one `value` item per flagged row, one `conflict` item per conflict group
 * (lowest id), and one `alias` item per unconfirmed fuzzy alias. Decisions are kept as
 * per-instance overrides, so the Data tables, sources and funds reflect them too.
 *
 * Planted cases (sample_docs/MANIFEST.md): Northwind Jan conflict (+1.92% in the Jan and Feb
 * factsheets vs +1.89% in the flash email), an Alder Asia value whose snippet lacks the value,
 * Northwind and Alder Asia alias suggestions, Meridian YTD sign error, Acme
 * roll-forward mismatch, plus a low-confidence row from the scanned Northwind PDF.
 */
import {
  ALLOWED_ACTIONS,
  AffectedRow,
  AliasItem,
  ReasonInfo,
  REVIEW_REASON_LABELS,
  ReturnItem,
  ReviewActionName,
  ReviewActionResponse,
  ReviewDetail,
  ReviewHistoryEntry,
  ReviewItem,
  ReviewReason,
  StatementField,
  StatementItem,
  ValueSnapshot,
} from '../api/models';
import { FundSeed, MOCK_ALIASES, MOCK_FUND_SEEDS, MOCK_RETURNS, MOCK_STATEMENTS, docById } from './mock-data';
import { MockResponse, err, monthLabel, money, pctText } from './mock-http';

export const LOW_CONFIDENCE_THRESHOLD = 0.7;
const YTD_TOLERANCE = 0.0005;
const CONFLICT_TOLERANCE = 0.0001;
const ROLLFORWARD_TOLERANCE = 1;

/** Value rows linked to a fund through an alias (fund_alias_id). */
const ALIAS_ROWS: Record<number, number[]> = { 4: [231], 9: [403] };

export const MONEY_FIELDS = ['opening_balance', 'contributions', 'withdrawals', 'pnl', 'fees', 'closing_balance'] as const;
const STATEMENT_EDIT_FIELDS: readonly string[] = [...MONEY_FIELDS, 'period_start', 'period_end'];
const SNIPPET_LABEL: Record<string, string> = {
  opening_balance: 'Opening balance',
  contributions: 'Contributions',
  withdrawals: 'Withdrawals',
  pnl: 'Investment gain / (loss)',
  fees: 'Fees',
  closing_balance: 'Closing balance',
};

const BODY_KEYS: Record<ReviewActionName, string[]> = {
  accept: ['action', 'note'],
  edit: ['action', 'note', 'field', 'value', 'value_pct', 'chosen_id'],
  choose: ['action', 'note', 'chosen_id'],
  confirm_alias: ['action', 'note'],
  reject_alias: ['action', 'note', 'reassign_to_id'],
  make_canonical: ['action'], // exactly {"action":"make_canonical"} (BUG-Q3-01 contract)
};

export interface ReviewContext {
  visibleIds(): Set<number>;
  link(documentId: number): string | null;
  iso(offsetSeconds: number): string;
}

interface AliasDecision {
  confirmed: boolean;
  fund_id: number;
  match_method: AliasItem['match_method'];
}

type Body = Record<string, unknown>;

/** Server `returns_differ`: both present and further apart than the conflict tolerance. */
const returnsDiffer = (a: number | null, b: number | null) => a !== null && b !== null && Math.abs(a - b) > CONFLICT_TOLERANCE + 1e-12;
const key = (r: ReturnItem) => `${r.fund_id}|${r.period_end}|${r.return_type}|${r.net_gross}`;
const reasonInfo = (code: ReviewReason, message: string): ReasonInfo => ({ code, label: REVIEW_REASON_LABELS[code], message });
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export class MockReview {
  private readonly returnPatch = new Map<number, Partial<ReturnItem>>();
  private readonly deletedReturns = new Set<number>();
  private readonly statementPatch = new Map<number, Partial<StatementItem>>();
  private readonly aliasDecisions = new Map<number, AliasDecision>();
  readonly extraFunds: FundSeed[] = [];
  /** fund_id -> canonical name set by `make_canonical` */
  private readonly renamedFunds = new Map<number, string>();
  private readonly history = new Map<string, ReviewHistoryEntry[]>();
  private nextActionId = 31;

  constructor(private readonly ctx: ReviewContext) {}

  // ---- current data (overrides applied) ------------------------------------------

  /** Visible return rows with review decisions and re-run conflict checks applied. */
  returns(): ReturnItem[] {
    const ids = this.ctx.visibleIds();
    const rows = this.withFundNames(
      MOCK_RETURNS.filter((r) => ids.has(r.document_id) && !this.deletedReturns.has(r.return_id)).map((r) => ({
        ...r,
        ...this.returnPatch.get(r.return_id),
      })),
    );
    // Alias decisions clear the alias_match flag of the rows linked through the alias.
    for (const [aliasId, rowIds] of Object.entries(ALIAS_ROWS)) {
      if (!this.aliasDecisions.has(Number(aliasId))) continue;
      for (const r of rows) {
        if (!rowIds.includes(r.return_id) || !r.review_reasons.includes('alias_match')) continue;
        r.review_reasons = r.review_reasons.filter((x) => x !== 'alias_match');
        if (!r.review_reasons.length && r.status === 'needs_review') r.status = 'ok';
      }
    }
    // Conflict re-check: a `conflict` row without a differing partner is no longer one.
    const groups = new Map<string, ReturnItem[]>();
    for (const r of rows) if (r.status === 'conflict') groups.set(key(r), [...(groups.get(key(r)) ?? []), r]);
    for (const g of groups.values()) {
      if (g.length >= 2 && g.some((a) => g.some((b) => returnsDiffer(a.value, b.value)))) continue;
      for (const r of g) {
        r.review_reasons = r.review_reasons.filter((x) => x !== 'conflict');
        r.status = r.review_reasons.length ? 'needs_review' : 'ok';
      }
    }
    return rows;
  }

  statements(): StatementItem[] {
    const ids = this.ctx.visibleIds();
    return this.withFundNames(MOCK_STATEMENTS.filter((s) => ids.has(s.document_id)).map((s) => ({ ...s, ...this.statementPatch.get(s.statement_id) })));
  }

  /** `/api/funds` aliases with decisions applied. */
  aliases(): Record<number, AliasItem[]> {
    const out: Record<number, AliasItem[]> = {};
    for (const [fid, list] of Object.entries(MOCK_ALIASES)) out[Number(fid)] = list.map((a) => ({ ...a }));
    for (const [aliasId, d] of this.aliasDecisions) {
      for (const [fid, list] of Object.entries(out)) {
        const i = list.findIndex((a) => a.alias_id === aliasId);
        if (i < 0) continue;
        const a = { ...list[i], confirmed: d.confirmed, match_method: d.match_method };
        if (Number(fid) === d.fund_id) list[i] = a;
        else {
          list.splice(i, 1);
          out[d.fund_id] = [...(out[d.fund_id] ?? []), a];
        }
        break;
      }
    }
    return out;
  }

  fundSeeds(): FundSeed[] {
    return this.withFundNames([...MOCK_FUND_SEEDS, ...this.extraFunds]);
  }

  /** Applies `make_canonical` renames to any rows that carry `fund_id` + `fund_name`. */
  withFundNames<T extends { fund_id: number | null; fund_name: string | null }>(rows: T[]): T[] {
    if (!this.renamedFunds.size) return rows;
    return rows.map((r) => {
      const name = r.fund_id === null ? undefined : this.renamedFunds.get(r.fund_id);
      return name === undefined ? r : { ...r, fund_name: name };
    });
  }

  historyFor(table: string, id: number): ReviewHistoryEntry[] {
    return [...(this.history.get(`${table}:${id}`) ?? [])].reverse();
  }

  isDeleted(returnId: number): boolean {
    return this.deletedReturns.has(returnId);
  }

  // ---- snapshots ---------------------------------------------------------------------

  snapshotReturn(r: ReturnItem): ValueSnapshot {
    const d = docById(r.document_id);
    return {
      table: 'returns',
      id: r.return_id,
      field: 'value',
      value: r.value,
      value_pct: r.value_pct,
      value_kind: 'percent',
      unit: null,
      currency: null,
      status: r.status,
      review_reasons: r.review_reasons,
      provenance: {
        page: r.page,
        source_snippet: r.source_snippet,
        confidence: r.confidence,
        snippet_verified: d.image_only ? null : !r.review_reasons.includes('snippet_not_found'),
      },
      document_id: r.document_id,
      document_name: r.document_name,
      document_type: r.document_type,
      web_view_link: this.ctx.link(r.document_id),
      extraction_model: 'claude-opus-5-5',
    };
  }

  /** T6: `fields` ends with the period dates (`value_kind: "date"`). */
  snapshotStatementDate(s: StatementItem, field: 'period_start' | 'period_end'): ValueSnapshot {
    const v = s[field];
    return {
      ...this.snapshotStatement(s, 'closing_balance'),
      field,
      value: v,
      value_kind: 'date',
      currency: null,
      provenance: {
        page: v === null ? null : 1,
        source_snippet: v === null ? null : `Statement period: ${s.period_start ?? '?'} to ${s.period_end}`,
        confidence: v === null ? null : 0.95,
        snippet_verified: v === null ? null : true,
      },
    };
  }

  snapshotStatement(s: StatementItem, field: (typeof MONEY_FIELDS)[number]): ValueSnapshot {
    const v = s[field];
    return {
      table: 'statements',
      id: s.statement_id,
      field,
      value: v,
      value_pct: null,
      value_kind: 'money',
      unit: null,
      currency: s.currency,
      status: s.status,
      review_reasons: s.review_reasons,
      provenance: {
        page: v === null ? null : 1,
        source_snippet: v === null ? null : `${SNIPPET_LABEL[field]} ${money(v)}`,
        confidence: v === null ? null : 0.93 + (MONEY_FIELDS.indexOf(field) % 3) / 100,
        snippet_verified: v === null ? null : true,
      },
      document_id: s.document_id,
      document_name: s.document_name,
      document_type: s.document_type,
      web_view_link: this.ctx.link(s.document_id),
      extraction_model: 'claude-opus-5-5',
    };
  }

  // ---- the queue -----------------------------------------------------------------------

  items(): ReviewItem[] {
    const returns = this.returns();
    const out: ReviewItem[] = [];

    // conflict groups
    const groups = new Map<string, ReturnItem[]>();
    for (const r of returns) if (r.status === 'conflict') groups.set(key(r), [...(groups.get(key(r)) ?? []), r]);
    for (const g of groups.values()) {
      const members = [...g].sort((a, b) => a.return_id - b.return_id);
      const head = members[0];
      const title = this.returnTitle(head);
      out.push({
        ...this.base('returns', head.return_id, 'conflict', head.document_id, head.document_name, 'conflict', ['conflict']),
        reasons: [
          reasonInfo(
            'conflict',
            `${members.length} documents disagree on ${title}: ${members.map((m) => pctText(m.value ?? 0)).join(', ').replace(/, ([^,]*)$/, ' and $1')}.`,
          ),
        ],
        title,
        subject: this.returnSubject(head),
        detail: { conflict: { conflicting_ids: members.slice(1).map((m) => m.return_id), tolerance: CONFLICT_TOLERANCE } },
        candidates: members.map((m) => this.snapshotReturn(m)),
      });
    }

    // flagged return rows
    for (const r of returns) {
      if (r.status !== 'needs_review') continue;
      const reasons = r.review_reasons.filter((x) => x !== 'conflict' && x !== 'alias_match');
      if (!reasons.length) continue; // alias-only rows are covered by the alias item
      const { detail, infos } = this.returnDetail(r, reasons, returns);
      out.push({
        ...this.base('returns', r.return_id, 'value', r.document_id, r.document_name, r.status, reasons),
        reasons: infos,
        title: this.returnTitle(r),
        subject: this.returnSubject(r),
        detail,
        current: this.snapshotReturn(r),
      });
    }

    // flagged statements
    for (const s of this.statements()) {
      if (s.status !== 'needs_review') continue;
      const detail: ReviewDetail = {};
      const infos: ReasonInfo[] = [];
      for (const code of s.review_reasons) {
        if (code === 'rollforward_mismatch') {
          const z = (v: number | null) => v ?? 0;
          const expected = z(s.opening_balance) + z(s.contributions) - z(s.withdrawals) + z(s.pnl) - z(s.fees);
          const stated = z(s.closing_balance);
          detail.rollforward_mismatch = {
            opening_balance: z(s.opening_balance),
            contributions: z(s.contributions),
            withdrawals: z(s.withdrawals),
            pnl: z(s.pnl),
            fees: z(s.fees),
            expected_closing: expected,
            stated_closing: stated,
            difference: stated - expected,
            tolerance: ROLLFORWARD_TOLERANCE,
            currency: s.currency,
          };
          infos.push(
            reasonInfo(
              code,
              `Opening ${money(z(s.opening_balance))} + contributions ${money(z(s.contributions))} - withdrawals ${money(z(s.withdrawals))} + P&L ${money(z(s.pnl))} - fees ${money(z(s.fees))} = ${money(expected)}, but the stated closing balance is ${money(stated)} (difference ${money(stated - expected)} ${s.currency ?? ''}).`.trim(),
            ),
          );
        } else infos.push(reasonInfo(code, REVIEW_REASON_LABELS[code]));
      }
      out.push({
        ...this.base('statements', s.statement_id, 'value', s.document_id, s.document_name, s.status, s.review_reasons),
        reasons: infos,
        title: `${s.account_name} · ${s.account_number_masked ?? ''} · ${monthLabel(s.period_end)}`,
        subject: {
          fund_id: s.fund_id, fund_name: s.fund_name, account_id: s.account_id, account_name: s.account_name,
          period_end: s.period_end, period_label: monthLabel(s.period_end), return_type: null, net_gross: null, metric: null, holding_name: null,
        },
        detail,
        current: this.snapshotStatement(s, 'closing_balance'),
        fields: [...MONEY_FIELDS.map((f) => this.snapshotStatement(s, f)), ...(['period_start', 'period_end'] as const).map((f) => this.snapshotStatementDate(s, f))],
      });
    }

    // unconfirmed fuzzy aliases
    const ids = this.ctx.visibleIds();
    for (const [fid, list] of Object.entries(MOCK_ALIASES)) {
      const fund = this.fundSeeds().find((f) => f.fund_id === Number(fid))!;
      for (const a of list) {
        if (a.confirmed || a.match_method !== 'fuzzy' || this.aliasDecisions.has(a.alias_id)) continue;
        if (a.source_document_id === null || !ids.has(a.source_document_id)) continue;
        const affected = (ALIAS_ROWS[a.alias_id] ?? []).filter((rid) => returns.some((r) => r.return_id === rid)).length;
        out.push({
          ...this.base('fund_aliases', a.alias_id, 'alias', a.source_document_id, a.source_document_name, 'needs_review', ['alias_match']),
          reasons: [reasonInfo('alias_match', `“${a.alias}” was matched to ${fund.fund_name} (score ${a.match_score}).`)],
          title: `“${a.alias}” → ${fund.fund_name}`,
          subject: {
            fund_id: fund.fund_id, fund_name: fund.fund_name, account_id: null, account_name: null,
            period_end: null, period_label: null, return_type: null, net_gross: null, metric: null, holding_name: null,
          },
          detail: { alias_match: { alias: a.alias, match_score: a.match_score, fund_id: fund.fund_id } },
          alias: {
            entity: 'fund', alias_id: a.alias_id, alias: a.alias, match_method: a.match_method, match_score: a.match_score,
            suggested_id: fund.fund_id, suggested_name: fund.fund_name,
            source_document_id: a.source_document_id, source_document_name: a.source_document_name, affected_rows: affected,
          },
        });
      }
    }
    return out;
  }

  private base(
    table: ReviewItem['target_table'], id: number, kind: ReviewItem['kind'], documentId: number | null, documentName: string | null,
    status: ReviewItem['status'], reasons: ReviewReason[],
  ): ReviewItem {
    return {
      id: `${table}:${id}`,
      kind,
      target_table: table,
      target_id: id,
      status,
      review_reasons: reasons,
      reasons: [],
      title: '',
      subject: {
        fund_id: null, fund_name: null, account_id: null, account_name: null, period_end: null, period_label: null,
        return_type: null, net_gross: null, metric: null, holding_name: null,
      },
      detail: {},
      current: null,
      fields: null,
      candidates: null,
      alias: null,
      allowed_actions: [...ALLOWED_ACTIONS[kind]],
      document_id: documentId,
      document_name: documentName,
      // stable per item, newest = most recently processed document
      created_at: this.ctx.iso(-600 + ((documentId ?? 0) * 13 + id) % 400),
    };
  }

  private returnTitle(r: ReturnItem): string {
    return `${r.fund_name} · ${monthLabel(r.period_end)} · ${r.return_type} ${r.net_gross}`;
  }

  private returnSubject(r: ReturnItem): ReviewItem['subject'] {
    return {
      fund_id: r.fund_id, fund_name: r.fund_name, account_id: null, account_name: null, period_end: r.period_end,
      period_label: r.period_label, return_type: r.return_type, net_gross: r.net_gross, metric: null, holding_name: null,
    };
  }

  private returnDetail(r: ReturnItem, reasons: ReviewReason[], all: ReturnItem[]): { detail: ReviewDetail; infos: ReasonInfo[] } {
    const detail: ReviewDetail = {};
    const infos: ReasonInfo[] = [];
    for (const code of reasons) {
      if (code === 'ytd_mismatch') {
        const months = all
          .filter((m) => m.fund_id === r.fund_id && m.return_type === 'MTD' && m.net_gross === r.net_gross && m.period_year === r.period_year && m.period_end <= r.period_end)
          .sort((a, b) => a.period_end.localeCompare(b.period_end));
        const computed = months.reduce((acc, m) => acc * (1 + (m.value ?? 0)), 1) - 1;
        const stated = r.value ?? 0;
        detail.ytd_mismatch = {
          year: r.period_year,
          stated,
          computed: Math.round(computed * 1e7) / 1e7,
          difference: Math.round((stated - computed) * 1e7) / 1e7,
          tolerance: YTD_TOLERANCE,
          months: months.map((m) => ({ period_end: m.period_end, value: m.value ?? 0, return_id: m.return_id })),
        };
        infos.push(reasonInfo(code, `Monthly returns for ${r.period_year} compound to ${pctText(computed)} but the stated YTD is ${pctText(stated)}.`));
      } else if (code === 'snippet_not_found') {
        // the mock only plants the Q2-fix case: the snippet is in the document, the value is not
        detail.snippet_not_found = { snippet: r.source_snippet, value_missing: true };
        infos.push(reasonInfo(code, 'The value is not written in its source snippet, so it cannot be traced.'));
      } else if (code === 'low_confidence') {
        detail.low_confidence = { confidence: r.confidence ?? 0, threshold: LOW_CONFIDENCE_THRESHOLD };
        infos.push(reasonInfo(code, `Extraction confidence ${(r.confidence ?? 0).toFixed(2)} is below the review threshold ${LOW_CONFIDENCE_THRESHOLD.toFixed(2)}.`));
      } else infos.push(reasonInfo(code, REVIEW_REASON_LABELS[code]));
    }
    return { detail, infos };
  }

  // ---- actions --------------------------------------------------------------------------

  act(id: string, raw: unknown): MockResponse {
    const items = this.items();
    const item = items.find((i) => i.id === id || (i.kind === 'conflict' && i.candidates?.some((c) => `returns:${c.id}` === id)));
    if (!item) return err(404, 'not_found', `Review item ${id} not found or already resolved.`);
    if (!raw || typeof raw !== 'object') return err(422, 'validation_error', 'Invalid request body.', [{ loc: 'body', message: 'expected a JSON object' }]);
    const body = raw as Body;
    const action = body['action'] as ReviewActionName;
    if (!(action in BODY_KEYS)) {
      return err(422, 'validation_error', 'Invalid request body.', [{ loc: 'body.action', message: `unknown action '${String(action)}'` }]);
    }
    const extra = Object.keys(body).filter((k) => !BODY_KEYS[action].includes(k));
    if (extra.length) return err(422, 'validation_error', 'Invalid request body.', extra.map((k) => ({ loc: `body.${k}`, message: 'extra fields not permitted' })));
    const note = body['note'];
    if (note !== undefined && note !== null && (typeof note !== 'string' || note.length > 2000)) {
      return err(422, 'validation_error', 'Invalid request body.', [{ loc: 'body.note', message: 'note must be a string of at most 2000 characters' }]);
    }
    if (!item.allowed_actions.includes(action)) {
      return err(422, 'invalid_action', `Action '${action}' is not allowed for a ${item.kind} item; allowed: ${item.allowed_actions.join(', ')}.`, [
        { loc: 'body.action', message: `not allowed for kind '${item.kind}'` },
      ]);
    }
    const noteText = typeof note === 'string' ? note : null;
    let affected: AffectedRow[];
    try {
      affected = this.apply(item, action, body, noteText);
    } catch (e) {
      if (e instanceof InvalidAction) return err(422, 'invalid_action', e.message, [{ loc: e.loc, message: e.message }]);
      throw e;
    }
    const remaining = this.items();
    const res: ReviewActionResponse = {
      id: item.id,
      action,
      resolved: !remaining.some((i) => i.id === item.id),
      review_action_ids: affected.filter((a) => a.change !== 'created').map(() => this.nextActionId++),
      affected,
      remaining_open: remaining.length,
    };
    return { status: 200, body: res };
  }

  private apply(item: ReviewItem, action: ReviewActionName, body: Body, note: string | null): AffectedRow[] {
    switch (action) {
      case 'accept':
        return this.accept(item, note);
      case 'edit':
        return this.edit(item, body, note);
      case 'choose': {
        const chosen = this.candidateId(item, body['chosen_id']);
        return this.keepOnly(item, chosen, 'choose', note);
      }
      case 'confirm_alias':
        return this.decideAlias(item, item.alias!.suggested_id, true, 'confirm_alias', note);
      case 'reject_alias':
        return this.rejectAlias(item, body['reassign_to_id'], note);
      case 'make_canonical':
        return this.makeCanonical(item);
    }
  }

  private record(table: string, id: number, action: ReviewActionName, oldValue: unknown, newValue: unknown, note: string | null): void {
    const k = `${table}:${id}`;
    this.history.set(k, [
      ...(this.history.get(k) ?? []),
      { review_action_id: this.nextActionId, action, old_value: oldValue, new_value: newValue, note, created_at: this.ctx.iso(0) },
    ]);
  }

  private accept(item: ReviewItem, note: string | null): AffectedRow[] {
    if (item.target_table === 'statements') {
      const s = this.statements().find((x) => x.statement_id === item.target_id)!;
      this.statementPatch.set(s.statement_id, { ...this.statementPatch.get(s.statement_id), status: 'ok', review_reasons: [], flagged_fields: [] });
      this.record('statements', s.statement_id, 'accept', { closing_balance: s.closing_balance }, { closing_balance: s.closing_balance }, note);
      return [{ table: 'statements', id: s.statement_id, change: 'status_changed', status: 'ok' }];
    }
    const r = this.returns().find((x) => x.return_id === item.target_id)!;
    this.patchReturn(r.return_id, { status: 'ok', review_reasons: [] });
    this.record('returns', r.return_id, 'accept', { value: r.value }, { value: r.value }, note);
    return [{ table: 'returns', id: r.return_id, change: 'status_changed', status: 'ok' }];
  }

  private edit(item: ReviewItem, body: Body, note: string | null): AffectedRow[] {
    const hasValue = body['value'] !== undefined && body['value'] !== null;
    const hasPct = body['value_pct'] !== undefined && body['value_pct'] !== null;
    if (hasValue === hasPct) throw new InvalidAction('body.value', 'Send exactly one of value or value_pct.');

    if (item.target_table === 'statements') {
      const field = body['field'];
      if (typeof field !== 'string' || !STATEMENT_EDIT_FIELDS.includes(field)) {
        throw new InvalidAction('body.field', `field is required for statements; one of ${STATEMENT_EDIT_FIELDS.join(', ')}.`);
      }
      if (hasPct) throw new InvalidAction('body.value_pct', 'Statement fields take value, not value_pct.');
      const v = body['value'];
      const isDate = field.startsWith('period_');
      if (isDate ? typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v) : !isNum(v)) {
        throw new InvalidAction('body.value', isDate ? 'value must be a YYYY-MM-DD date.' : 'value must be a number.');
      }
      const s = this.statements().find((x) => x.statement_id === item.target_id)!;
      const old = s[field as StatementField];
      this.statementPatch.set(s.statement_id, {
        ...this.statementPatch.get(s.statement_id),
        [field]: v,
        status: 'ok',
        review_reasons: [],
        flagged_fields: [],
      });
      this.record('statements', s.statement_id, 'edit', { [field]: old }, { [field]: v }, note);
      return [{ table: 'statements', id: s.statement_id, change: 'value_edited', status: 'ok' }];
    }

    const field = body['field'];
    if (field !== undefined && field !== null && field !== 'value') throw new InvalidAction('body.field', `field must be 'value' for ${item.target_table}.`);
    if (hasValue) throw new InvalidAction('body.value', 'Send value_pct for percent values.');
    const pct = body['value_pct'];
    if (!isNum(pct)) throw new InvalidAction('body.value_pct', 'value_pct must be a number.');
    const target = item.kind === 'conflict' ? this.candidateId(item, body['chosen_id']) : item.target_id;
    if (item.kind !== 'conflict' && body['chosen_id'] !== undefined && body['chosen_id'] !== null) {
      throw new InvalidAction('body.chosen_id', 'chosen_id is only used for conflict items.');
    }
    const r = this.returns().find((x) => x.return_id === target)!;
    const value = Math.round((pct / 100) * 1e8) / 1e8;
    this.patchReturn(target, { value, value_pct: pct, status: 'ok', review_reasons: [] });
    this.record('returns', target, 'edit', { value: r.value }, { value }, note);
    const rows: AffectedRow[] = [{ table: 'returns', id: target, change: 'value_edited', status: 'ok' }];
    if (item.kind === 'conflict') rows.push(...this.deleteOthers(item, target));
    return rows;
  }

  private candidateId(item: ReviewItem, raw: unknown): number {
    if (!isNum(raw) || !item.candidates?.some((c) => c.id === raw)) {
      throw new InvalidAction('body.chosen_id', `chosen_id must be one of ${item.candidates?.map((c) => c.id).join(', ')}.`);
    }
    return raw;
  }

  /**
   * `choose` (Q2 fix): the chosen row and every candidate that agrees with it (within
   * the conflict tolerance) become `ok` and are listed as `status_changed`; only the
   * candidates with a different value are deleted.
   */
  private keepOnly(item: ReviewItem, chosen: number, action: ReviewActionName, note: string | null): AffectedRow[] {
    const rows = this.returns();
    const r = rows.find((x) => x.return_id === chosen)!;
    const agreeing = (item.candidates ?? []).filter((c) => c.id !== chosen && !returnsDiffer(r.value, c.value as number | null));
    const out: AffectedRow[] = [];
    for (const id of [chosen, ...agreeing.map((c) => c.id)]) {
      const row = rows.find((x) => x.return_id === id)!;
      this.patchReturn(id, { status: 'ok', review_reasons: [] });
      this.record('returns', id, action, { value: row.value }, { value: row.value }, note);
      out.push({ table: 'returns', id, change: 'status_changed', status: 'ok' });
    }
    return [...out, ...this.deleteOthers(item, [chosen, ...agreeing.map((c) => c.id)])];
  }

  private deleteOthers(item: ReviewItem, keep: number | number[]): AffectedRow[] {
    const kept = Array.isArray(keep) ? keep : [keep];
    return (item.candidates ?? [])
      .filter((c) => !kept.includes(c.id))
      .map((c) => {
        this.deletedReturns.add(c.id);
        return { table: 'returns' as const, id: c.id, change: 'deleted' as const, status: null };
      });
  }

  private patchReturn(id: number, p: Partial<ReturnItem>): void {
    this.returnPatch.set(id, { ...this.returnPatch.get(id), ...p });
  }

  private rejectAlias(item: ReviewItem, raw: unknown, note: string | null): AffectedRow[] {
    const a = item.alias!;
    if (raw === undefined || raw === null) {
      const suggested = this.fundSeeds().find((f) => f.fund_id === a.suggested_id)!;
      const created: FundSeed = { ...suggested, fund_id: 7 + this.extraFunds.length, fund_name: a.alias };
      this.extraFunds.push(created);
      return [{ table: 'funds', id: created.fund_id, change: 'created', status: null }, ...this.decideAlias(item, created.fund_id, false, 'reject_alias', note)];
    }
    if (!isNum(raw) || !this.fundSeeds().some((f) => f.fund_id === raw)) {
      throw new InvalidAction('body.reassign_to_id', `reassign_to_id ${String(raw)} is not a known fund.`);
    }
    return this.decideAlias(item, raw, false, 'reject_alias', note);
  }

  /**
   * `make_canonical`: the alias text becomes the fund's name, the old name stays a
   * confirmed alias (its alias row already exists), and the alias is confirmed.
   */
  private makeCanonical(item: ReviewItem): AffectedRow[] {
    const a = item.alias!;
    this.renamedFunds.set(a.suggested_id, a.alias);
    this.aliasDecisions.set(a.alias_id, { confirmed: true, fund_id: a.suggested_id, match_method: 'manual' });
    this.record('fund_aliases', a.alias_id, 'make_canonical', { canonical_name: a.suggested_name }, { canonical_name: a.alias }, null);
    return [
      { table: 'funds', id: a.suggested_id, change: 'renamed', status: null },
      { table: 'fund_aliases', id: a.alias_id, change: 'alias_confirmed', status: null },
    ];
  }

  private decideAlias(item: ReviewItem, fundId: number, confirm: boolean, action: ReviewActionName, note: string | null): AffectedRow[] {
    const a = item.alias!;
    const moved = fundId !== a.suggested_id;
    this.aliasDecisions.set(a.alias_id, { confirmed: true, fund_id: fundId, match_method: confirm ? 'fuzzy' : 'manual' });
    this.record('fund_aliases', a.alias_id, action, { fund_id: a.suggested_id }, { fund_id: fundId }, note);
    const rows: AffectedRow[] = [{ table: 'fund_aliases', id: a.alias_id, change: confirm ? 'alias_confirmed' : 'alias_reassigned', status: null }];
    if (!moved) return rows;
    const fund = this.fundSeeds().find((f) => f.fund_id === fundId)!;
    for (const rid of ALIAS_ROWS[a.alias_id] ?? []) {
      this.patchReturn(rid, { fund_id: fund.fund_id, fund_name: fund.fund_name, manager: fund.manager, strategy: fund.strategy, fund_currency: fund.currency });
    }
    const now = this.returns();
    for (const rid of ALIAS_ROWS[a.alias_id] ?? []) {
      const r = now.find((x) => x.return_id === rid);
      if (r) rows.push({ table: 'returns', id: rid, change: 'relinked', status: r.status });
    }
    return rows;
  }
}

class InvalidAction extends Error {
  constructor(
    readonly loc: string,
    message: string,
  ) {
    super(message);
  }
}

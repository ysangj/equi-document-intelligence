/**
 * Dev-only in-memory implementation of the API Contract (every endpoint U1 and U2 use).
 * It implements C3 (pagination + sort with NULLs last and a primary-key tie-breaker),
 * C4 (repeated-key filters), C5 (CSV), C2 (error envelope) and a simulated worker
 * (mock-worker.ts): every `/api/progress` poll finishes one more document, so rows
 * appear progressively, exactly like FR-9 against the real api. Review decisions
 * (mock-review.ts) feed back into the Data tables, sources and funds.
 */
import {
  ConfigResponse,
  DOCUMENT_STATUSES,
  DOCUMENTS_SORTABLE,
  DocumentItem,
  FundFactItem,
  Page,
  ProgressResponse,
  REVIEW_KINDS,
  REVIEW_REASONS,
  REVIEW_REASON_LABELS,
  REVIEW_SORTABLE,
  ReasonInfo,
  ReturnItem,
  ReviewCounts,
  ReviewListResponse,
  SEARCH_MAX_QUERY_LENGTH,
  SnippetHighlight,
  SourceResponse,
  SourceTable,
  StatementItem,
  SyncRequestResponse,
  ValueKind,
  ValueSnapshot,
  FUND_FACTS_SORTABLE,
  HOLDINGS_SORTABLE,
  RETURNS_SORTABLE,
  STATEMENTS_SORTABLE,
} from '../api/models';
import { MOCK_DOCUMENTS, MOCK_FUND_FACTS, MOCK_HOLDINGS, MOCK_RETURNS, MOCK_STATEMENTS, buildAccounts, buildFunds, docById } from './mock-data';
import { MOCK_ASK_LATENCY_MS, mockAnswer, validateAsk } from './mock-ask';
import { MockDrive } from './mock-drive';
import { MockRequest, MockResponse, ValidationError, err, money, monthLabel } from './mock-http';
import { MOCK_PASSAGES } from './mock-passages';
import { MockReview } from './mock-review';
import { mockSearch } from './mock-search';
import { MockWorker, documentItem } from './mock-worker';

export type { MockRequest, MockResponse } from './mock-http';

export interface MockOptions {
  demoMode: boolean;
  /** documents already processed when the mock starts */
  initialProcessed: number;
  /** fixed clock for tests */
  now?: () => Date;
  /** Drive mode: start with Google connected and "Fund Docs" watched (default true). false = first run. */
  connected?: boolean;
  /** Ask enabled; default: off in demo mode (no ANTHROPIC_API_KEY), on in Drive mode */
  askEnabled?: boolean;
  /** `?ask_delay=<ms>`: slower Ask answers, e.g. to navigate while one is in flight */
  askDelayMs?: number;
  /** `?error=` code the simulated OAuth callback returns instead of success */
  oauthError?: string | null;
}

const STATEMENT_FIELDS = [
  'closing_balance', 'opening_balance', 'contributions', 'withdrawals', 'pnl', 'fees',
  'period_start', 'period_end', 'account_name', 'account_number_masked', 'custodian', 'fund_name', 'currency',
] as const;
const HOLDING_FIELDS = ['market_value', 'weight', 'quantity', 'holding_name', 'identifier'] as const;

const FIELD_LABELS: Record<string, string> = {
  value: 'Value',
  closing_balance: 'Closing balance',
  opening_balance: 'Opening balance',
  contributions: 'Contributions',
  withdrawals: 'Withdrawals',
  pnl: 'P&L',
  fees: 'Fees',
  period_start: 'Period start',
  period_end: 'Period end',
  account_name: 'Account',
  account_number_masked: 'Account number',
  custodian: 'Custodian / manager',
  fund_name: 'Fund',
  currency: 'Currency',
  market_value: 'Market value',
  weight: 'Weight',
  quantity: 'Quantity',
  holding_name: 'Holding',
  identifier: 'Identifier',
};

/** Simulated sync interval: the "worker" syncs on its own after this long idle. */
const SYNC_INTERVAL_S = 30;
/** polls until a requested sync / reconcile completes */
const REQUEST_POLLS = 2;

type Row = Record<string, unknown>;

interface PendingRequest {
  requestedAt: string;
  pollsLeft: number;
}

export class MockBackend {
  private readonly now: () => Date;
  private readonly startedAt: Date;
  readonly worker: MockWorker;
  readonly review: MockReview;
  readonly drive: MockDrive;
  private lastSyncedAt: string;
  private lastReconciledAt: string;
  private syncReq: PendingRequest | null = null;
  private reconcileReq: PendingRequest | null = null;

  constructor(private readonly opts: MockOptions) {
    this.now = opts.now ?? (() => new Date());
    this.startedAt = this.now();
    const iso = (o: number) => this.iso(o);
    const firstRun = !opts.demoMode && opts.connected === false;
    this.drive = new MockDrive({ demoMode: opts.demoMode, connected: opts.connected !== false, oauthError: opts.oauthError ?? null, iso });
    this.worker = new MockWorker(firstRun ? 0 : opts.initialProcessed, iso, !firstRun);
    this.review = new MockReview({ visibleIds: () => this.worker.visibleIds(), link: (id) => this.link(id), iso });
    this.lastSyncedAt = this.iso(-12);
    this.lastReconciledAt = this.iso(-300);
  }

  /** done + needs_review documents (the progress "processed" count). */
  get processedCount(): number {
    return this.worker.processedCount();
  }

  /** Advance the simulated worker by one step (one document, plus pending sync requests). */
  tick(): void {
    this.worker.tick();
    const now = this.iso(0);
    if (this.syncReq && --this.syncReq.pollsLeft <= 0) {
      this.syncReq = null;
      this.lastSyncedAt = now;
    }
    if (this.reconcileReq && --this.reconcileReq.pollsLeft <= 0) {
      this.reconcileReq = null;
      this.worker.discover();
      this.lastReconciledAt = now;
      this.lastSyncedAt = now;
    }
    if (Date.parse(now) - Date.parse(this.lastSyncedAt) > SYNC_INTERVAL_S * 1000) this.lastSyncedAt = now;
  }

  handle(req: MockRequest): MockResponse {
    const { method, path, params } = req;
    const get = method === 'GET';
    const post = method === 'POST';
    try {
      if (get && path === '/api/health') return { status: 200, body: { status: 'ok', db: 'ok', alembic_revision: '0002' } };
      if (get && path === '/api/config') return { status: 200, body: this.config() };
      if (get && path === '/api/auth/status') return { status: 200, body: this.drive.status() };
      if (post && path === '/api/__mock/oauth/callback') return { status: 200, body: this.drive.oauthCallback() };
      if (post && path === '/api/auth/disconnect') {
        const res = this.drive.disconnect();
        if (res.status === 200) {
          this.syncReq = null;
          this.reconcileReq = null;
        }
        return res;
      }
      if (get && path === '/api/drive/folders') return this.drive.folders(params);
      if (post && path === '/api/drive/watch') {
        const res = this.drive.watch(req.body);
        if (res.status === 200) this.reconcileReq = { requestedAt: this.iso(0), pollsLeft: REQUEST_POLLS };
        return res;
      }
      if (post && path === '/api/ask') return this.ask(req.body);
      if (get && path === '/api/progress') {
        const body = this.progress();
        this.tick();
        return { status: 200, body };
      }
      if (post && path === '/api/sync') return this.drive.syncBlocked() ?? this.requestSync('sync');
      if (post && path === '/api/sync/reconcile') return this.drive.syncBlocked() ?? this.requestSync('reconcile');
      if (get && path === '/api/documents') return this.list('documents', this.filterDocuments(params), params, DOCUMENTS_SORTABLE, '-created_at', 'document_id');
      const rp = /^\/api\/documents\/(\d+)\/reprocess$/.exec(path);
      if (post && rp) return this.reprocess(Number(rp[1]));
      if (get && path === '/api/search') return this.search(params);
      if (get && path === '/api/review') return this.reviewList(params);
      const ra = /^\/api\/review\/([^/]+)$/.exec(path);
      if (post && ra) return this.review.act(decodeURIComponent(ra[1]), req.body);
      if (get && path === '/api/funds') {
        const items = this.funds();
        return { status: 200, body: { items, total: items.length } };
      }
      if (get && path === '/api/accounts') {
        const items = buildAccounts(this.review.statements());
        return { status: 200, body: { items, total: items.length } };
      }
      if (get && path === '/api/returns') return this.list('returns', this.filterReturns(params), params, RETURNS_SORTABLE, '-period_end,fund_name', 'return_id');
      if (get && path === '/api/fund-facts') return this.list('fund-facts', this.filterFacts(params), params, FUND_FACTS_SORTABLE, '-as_of_date,fund_name,metric', 'fact_id');
      if (get && path === '/api/statements') return this.list('statements', this.filterStatements(params), params, STATEMENTS_SORTABLE, '-period_end,account_name', 'statement_id');
      const h = /^\/api\/statements\/(\d+)\/holdings$/.exec(path);
      if (get && h) {
        const sid = Number(h[1]);
        if (!this.review.statements().some((s) => s.statement_id === sid)) return err(404, 'not_found', `statement ${sid} not found.`);
        const status = params.getAll('status');
        const rows = MOCK_HOLDINGS.filter((x) => x.statement_id === sid && (!status.length || status.includes(x.status)));
        return this.list(`holdings`, rows, params, HOLDINGS_SORTABLE, 'line_no', 'holding_id');
      }
      const s = /^\/api\/sources\/([a-z_]+)\/(\d+)$/.exec(path);
      if (get && s) return this.source(s[1], Number(s[2]), params.get('field'));
      if (path.startsWith('/api/')) return err(404, 'not_found', `No mock for ${method} ${path}.`);
      return err(404, 'not_found', 'Unknown route.');
    } catch (e) {
      if (e instanceof ValidationError) {
        return err(422, 'validation_error', 'Invalid request parameters.', [{ loc: e.loc, message: e.message }]);
      }
      return err(500, 'internal_error', 'Internal error.');
    }
  }

  // ---- state helpers -------------------------------------------------------------

  private get mode() {
    return this.opts.demoMode ? ('demo' as const) : ('drive' as const);
  }

  private iso(offsetSeconds: number): string {
    return new Date(this.now().getTime() + offsetSeconds * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  }

  private visible<T extends { document_id: number }>(rows: T[]): T[] {
    const ids = this.worker.visibleIds();
    return rows.filter((r) => ids.has(r.document_id));
  }

  private funds() {
    return buildFunds(this.review.returns(), this.review.aliases(), this.review.fundSeeds());
  }

  // ---- endpoints -----------------------------------------------------------------

  private config(): ConfigResponse {
    const demo = this.opts.demoMode;
    return {
      demo_mode: demo,
      demo_reason: demo ? 'GOOGLE_CLIENT_ID_UNSET' : null,
      extraction_source: demo && !this.askEnabled ? 'fixture' : 'claude',
      claude_model: 'claude-opus-5-5',
      sync_interval_seconds: SYNC_INTERVAL_S,
      features: {
        browse: { enabled: true, reason: null },
        search: { enabled: true, reason: null },
        review: { enabled: true, reason: null },
        ask: this.askEnabled
          ? { enabled: true, reason: null }
          : { enabled: false, reason: 'Ask is disabled because ANTHROPIC_API_KEY is not set.' },
        drive: demo
          ? { enabled: false, reason: 'Demo mode: documents are read from ./sample_docs instead of Google Drive.' }
          : { enabled: true, reason: null },
      },
    };
  }

  progress(): ProgressResponse {
    const states = this.worker.all;
    const n = (s: string) => states.filter((x) => x.status === s).length;
    const counts = {
      pending: n('pending'),
      processing: n('processing'),
      done: n('done'),
      needs_review: n('needs_review'),
      failed: n('failed'),
      unsupported: n('unsupported'),
    };
    const processed = counts.done + counts.needs_review;
    const inFlight = counts.pending + counts.processing;
    const demo = this.opts.demoMode;
    const tokens = states.reduce(
      (acc, s) => ({ input_tokens: acc.input_tokens + (s.tokens?.input ?? 0), output_tokens: acc.output_tokens + (s.tokens?.output ?? 0) }),
      { input_tokens: 0, output_tokens: 0 },
    );
    const lastSuccess = this.lastSyncedAt > this.lastReconciledAt ? this.lastSyncedAt : this.lastReconciledAt;
    return {
      mode: this.mode,
      counts,
      total: states.length,
      processed,
      finished: processed + counts.failed + counts.unsupported,
      in_flight: inFlight,
      active: inFlight > 0,
      review_open: this.review.items().length,
      tokens,
      sync: {
        source: demo ? 'local' : 'drive',
        folder_id: demo ? null : (this.drive.folder?.folder_id ?? null),
        folder_name: demo ? 'sample_docs' : (this.drive.folder?.folder_name ?? null),
        last_synced_at: this.lastSyncedAt,
        last_reconciled_at: this.lastReconciledAt,
        last_success_at: lastSuccess,
        last_error: null,
        sync_requested_at: this.syncReq?.requestedAt ?? null,
        reconcile_requested_at: this.reconcileReq?.requestedAt ?? null,
        sync_pending: this.syncReq !== null,
        reconcile_pending: this.reconcileReq !== null,
      },
      connection: demo ? { connected: false, google_email: null } : { connected: this.drive.connected, google_email: this.drive.email },
      generated_at: this.iso(0),
    };
  }

  private get askEnabled(): boolean {
    return this.opts.askEnabled ?? !this.opts.demoMode;
  }

  private ask(body: unknown): MockResponse {
    if (!this.askEnabled) return err(503, 'ask_disabled', 'Ask is disabled because ANTHROPIC_API_KEY is not set.');
    const invalid = validateAsk(body);
    if (invalid) return invalid;
    const question = String((body as { question: string }).question).trim();
    return { status: 200, body: mockAnswer(question, (id) => this.link(id)), delayMs: this.opts.askDelayMs ?? MOCK_ASK_LATENCY_MS };
  }

  private requestSync(kind: 'sync' | 'reconcile'): MockResponse {
    const now = this.iso(0);
    const cur = kind === 'sync' ? this.syncReq : this.reconcileReq;
    const already = cur !== null;
    const next: PendingRequest = { requestedAt: now, pollsLeft: cur?.pollsLeft ?? REQUEST_POLLS };
    if (kind === 'sync') this.syncReq = next;
    else this.reconcileReq = next;
    const lastSuccess = this.lastSyncedAt > this.lastReconciledAt ? this.lastSyncedAt : this.lastReconciledAt;
    const body: SyncRequestResponse = { kind, mode: this.mode, requested_at: now, already_pending: already, last_success_at: lastSuccess };
    return { status: 202, body };
  }

  private documentItems(): DocumentItem[] {
    const open = this.review.items();
    const valueCount = (id: number) =>
      [MOCK_RETURNS, MOCK_FUND_FACTS, MOCK_STATEMENTS].reduce((a, rows) => a + (rows as { document_id: number }[]).filter((r) => r.document_id === id).length, 0);
    return this.worker.all.map((s) =>
      documentItem(s, this.link(s.doc.document_id), this.opts.demoMode, {
        value_count: valueCount(s.doc.document_id),
        review_count: open.filter((i) => i.document_id === s.doc.document_id).length,
      }),
    );
  }

  private filterDocuments(p: URLSearchParams): DocumentItem[] {
    const status = p.getAll('status');
    for (const s of status) if (!(DOCUMENT_STATUSES as readonly string[]).includes(s)) throw new ValidationError('query.status', `unknown status '${s}'`);
    const types = p.getAll('document_type');
    const q = p.get('q');
    if (q !== null && q.length > 200) throw new ValidationError('query.q', 'q must be at most 200 characters');
    const idx = p.get('search_indexed');
    if (idx !== null && idx !== 'true' && idx !== 'false') throw new ValidationError('query.search_indexed', 'Input should be a valid boolean');
    return this.documentItems().filter(
      (d) =>
        (!status.length || status.includes(d.status)) &&
        (!types.length || (d.document_type !== null && types.includes(d.document_type))) &&
        (!q || d.document_name.toLowerCase().includes(q.toLowerCase())) &&
        (idx === null || String(d.search_indexed) === idx),
    );
  }

  private reprocess(id: number): MockResponse {
    const s = this.worker.states.get(id);
    if (!s) return err(404, 'not_found', `document ${id} not found.`);
    if (s.status === 'pending' || s.status === 'processing') {
      return err(409, 'document_busy', `${s.doc.document_name} is already queued for processing.`);
    }
    if (s.status === 'unsupported') return err(409, 'unsupported_document', `${s.doc.document_name} is not a supported file type.`);
    this.worker.requeue(id);
    return { status: 202, body: { document_id: id, status: 'pending', attempts: 0, queued_at: this.iso(0) } };
  }

  private search(p: URLSearchParams): MockResponse {
    const q = p.get('q') ?? '';
    if (q.length > SEARCH_MAX_QUERY_LENGTH) throw new ValidationError('query.q', `q must be at most ${SEARCH_MAX_QUERY_LENGTH} characters`);
    const limit = intParam(p, 'limit', 5, 1, 20);
    const visible = this.worker.visibleIds();
    const body = mockSearch(q, limit, {
      funds: this.funds(),
      accounts: buildAccounts(this.review.statements()),
      documents: this.worker.all.map((s) => ({
        document_id: s.doc.document_id,
        document_name: s.doc.document_name,
        document_type: s.has_data ? s.doc.document_type : null,
        status: s.status,
        web_view_link: this.link(s.doc.document_id),
      })),
      passages: MOCK_PASSAGES.filter((x) => visible.has(x.document_id)),
    });
    return { status: 200, body };
  }

  private reviewList(p: URLSearchParams): MockResponse {
    const kinds = p.getAll('kind');
    for (const k of kinds) if (!(REVIEW_KINDS as readonly string[]).includes(k)) throw new ValidationError('query.kind', `unknown kind '${k}'`);
    const reasons = p.getAll('reason');
    for (const r of reasons) if (!(REVIEW_REASONS as readonly string[]).includes(r)) throw new ValidationError('query.reason', `unknown reason '${r}'`);
    const docIds = p.getAll('document_id').map(Number);
    const all = this.review.items();
    const counts: ReviewCounts = { by_kind: { value: 0, conflict: 0, alias: 0 }, by_reason: {} };
    for (const i of all) {
      counts.by_kind[i.kind] = (counts.by_kind[i.kind] ?? 0) + 1;
      for (const r of i.review_reasons) counts.by_reason[r] = (counts.by_reason[r] ?? 0) + 1;
    }
    const rows = all.filter(
      (i) =>
        (!kinds.length || kinds.includes(i.kind)) &&
        (!reasons.length || i.review_reasons.some((r) => reasons.includes(r))) &&
        (!docIds.length || (i.document_id !== null && docIds.includes(i.document_id))),
    );
    const res = this.list('review', rows, p, REVIEW_SORTABLE, '-created_at', 'target_id');
    if (res.status !== 200) return res;
    const body: ReviewListResponse = { ...(res.body as Page<ReviewListResponse['items'][number]>), counts };
    return { status: 200, body };
  }

  private commonFilter<T extends { document_id: number; document_type: string | null; status: string; review_reasons: string[] }>(rows: T[], p: URLSearchParams): T[] {
    const docIds = p.getAll('document_id').map(Number);
    const types = p.getAll('document_type');
    const status = p.getAll('status');
    const reasons = p.getAll('review_reason');
    return rows.filter(
      (r) =>
        (!docIds.length || docIds.includes(r.document_id)) &&
        (!types.length || (r.document_type !== null && types.includes(r.document_type))) &&
        (!status.length || status.includes(r.status)) &&
        (!reasons.length || r.review_reasons.some((x) => reasons.includes(x))),
    );
  }

  private dateFilter<T>(rows: T[], p: URLSearchParams, key: (r: T) => string | null): T[] {
    const from = p.get('date_from');
    const to = p.get('date_to');
    for (const [name, v] of [['date_from', from], ['date_to', to]] as const) {
      if (v !== null && !/^\d{4}-\d{2}-\d{2}$/.test(v)) throw new ValidationError(`query.${name}`, 'Input should be a valid date (YYYY-MM-DD).');
    }
    if (from && to && from > to) throw new ValidationError('query', 'date_from must be on or before date_to');
    return rows.filter((r) => {
      const d = key(r);
      if (d === null) return !from && !to;
      return (!from || d >= from) && (!to || d <= to);
    });
  }

  private filterReturns(p: URLSearchParams): ReturnItem[] {
    const fund = p.getAll('fund_id').map(Number);
    const rt = p.getAll('return_type');
    const ng = p.getAll('net_gross');
    let rows = this.review.returns().filter(
      (r) => (!fund.length || fund.includes(r.fund_id)) && (!rt.length || rt.includes(r.return_type)) && (!ng.length || ng.includes(r.net_gross)),
    );
    rows = this.commonFilter(rows, p);
    return this.dateFilter(rows, p, (r) => r.period_end);
  }

  private filterFacts(p: URLSearchParams): FundFactItem[] {
    const fund = p.getAll('fund_id').map(Number);
    const metric = p.getAll('metric');
    let rows = this.review.withFundNames(this.visible(MOCK_FUND_FACTS)).filter((r) => (!fund.length || fund.includes(r.fund_id)) && (!metric.length || metric.includes(r.metric)));
    rows = this.commonFilter(rows, p);
    return this.dateFilter(rows, p, (r) => r.as_of_date);
  }

  private filterStatements(p: URLSearchParams): StatementItem[] {
    const acct = p.getAll('account_id').map(Number);
    const fund = p.getAll('fund_id').map(Number);
    let rows = this.review.statements().filter(
      (r) => (!acct.length || acct.includes(r.account_id)) && (!fund.length || (r.fund_id !== null && fund.includes(r.fund_id))),
    );
    rows = this.commonFilter(rows, p);
    return this.dateFilter(rows, p, (r) => r.period_end);
  }

  private list<T extends object>(
    name: string,
    rows: T[],
    p: URLSearchParams,
    sortable: readonly string[],
    defaultSort: string,
    pk: string,
  ): MockResponse {
    const keys = parseSort(p.get('sort'), sortable, defaultSort);
    const sorted = sortRows(rows as Row[], keys, pk) as T[];
    const sortStr = keys.map(([c, d]) => (d ? '-' : '') + c).join(',');
    if (p.get('format') === 'csv') {
      const date = this.iso(0).slice(0, 10);
      return {
        status: 200,
        body: toCsv(sorted as Row[]),
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': `attachment; filename="${name}_${date}.csv"`,
        },
      };
    }
    const page = intParam(p, 'page', 1, 1, Number.MAX_SAFE_INTEGER);
    const size = intParam(p, 'page_size', 50, 1, 500);
    const body: Page<T> = {
      items: sorted.slice((page - 1) * size, page * size),
      total: sorted.length,
      page,
      page_size: size,
      sort: sortStr,
    };
    return { status: 200, body };
  }

  private source(table: string, id: number, field: string | null): MockResponse {
    const tables: SourceTable[] = ['returns', 'fund_facts', 'statements', 'statement_holdings'];
    if (!tables.includes(table as SourceTable)) throw new ValidationError('path.table', `unknown table '${table}'`);
    const t = table as SourceTable;
    if (t === 'returns') {
      if (field && field !== 'value') throw new ValidationError('query.field', `field '${field}' not allowed for returns`);
      const rows = this.review.returns();
      const r = rows.find((x) => x.return_id === id);
      if (!r) return err(404, 'not_found', `returns row ${id} not found.`);
      const conflicts = r.status === 'conflict'
        ? rows.filter((x) => x.return_id !== r.return_id && x.fund_id === r.fund_id && x.period_end === r.period_end && x.return_type === r.return_type && x.net_gross === r.net_gross).map((x) => this.review.snapshotReturn(x))
        : [];
      return {
        status: 200,
        body: this.sourceBody(t, id, 'value', `${r.fund_name} · ${monthLabel(r.period_end)} · ${r.return_type} ${r.net_gross}`, 'Return', r.value, r.value_pct, 'percent', null, null, r, r.document_id, conflicts),
      };
    }
    if (t === 'fund_facts') {
      if (field && field !== 'value') throw new ValidationError('query.field', `field '${field}' not allowed for fund_facts`);
      const f = this.review.withFundNames(this.visible(MOCK_FUND_FACTS)).find((x) => x.fact_id === id);
      if (!f) return err(404, 'not_found', `fund_facts row ${id} not found.`);
      const isMoney = !!f.unit && /^[A-Z]{3}$/.test(f.unit);
      const kind: ValueKind = f.unit === 'fraction' ? 'percent' : isMoney ? 'money' : f.value === null ? 'text' : 'number';
      return {
        status: 200,
        body: this.sourceBody(t, id, 'value', `${f.fund_name} · ${f.metric} · ${f.as_of_date}`, f.metric, f.value ?? f.value_text, f.value_pct, kind, isMoney ? null : f.unit, isMoney ? f.unit : null, f, f.document_id, []),
      };
    }
    if (t === 'statements') {
      const fld = field ?? 'closing_balance';
      if (!(STATEMENT_FIELDS as readonly string[]).includes(fld)) throw new ValidationError('query.field', `field '${fld}' not allowed for statements`);
      const s = this.review.statements().find((x) => x.statement_id === id);
      if (!s) return err(404, 'not_found', `statements row ${id} not found.`);
      const raw = (s as unknown as Row)[fld] as number | string | null;
      const isMoneyField = ['closing_balance', 'opening_balance', 'contributions', 'withdrawals', 'pnl', 'fees'].includes(fld);
      const kind: ValueKind = isMoneyField ? 'money' : fld.startsWith('period_') ? 'date' : 'text';
      const label = `${s.account_name} · ${s.account_number_masked ?? ''} · ${monthLabel(s.period_end)}`;
      const flagged = s.flagged_fields.includes(fld as never);
      const snippet = raw === null ? null : `${FIELD_LABELS[fld]} ${typeof raw === 'number' ? money(raw) : raw}`;
      const body = this.sourceBody(
        t, id, fld, label, FIELD_LABELS[fld], raw, null, kind, null, isMoneyField ? s.currency : null,
        { ...s, status: flagged ? s.status : s.status, review_reasons: s.review_reasons, confidence: 0.95, page: 1, source_snippet: snippet },
        s.document_id, [],
      );
      if (raw === null) body.provenance = { page: null, source_snippet: null, confidence: null, snippet_verified: null };
      return { status: 200, body };
    }
    const fld = field ?? 'market_value';
    if (!(HOLDING_FIELDS as readonly string[]).includes(fld)) throw new ValidationError('query.field', `field '${fld}' not allowed for statement_holdings`);
    const h = MOCK_HOLDINGS.find((x) => x.holding_id === id);
    if (!h || !this.worker.visibleIds().has(h.document_id)) return err(404, 'not_found', `statement_holdings row ${id} not found.`);
    const raw = (h as unknown as Row)[fld] as number | string | null;
    const kind: ValueKind = fld === 'weight' ? 'percent' : fld === 'market_value' ? 'money' : fld === 'quantity' ? 'number' : 'text';
    return {
      status: 200,
      body: this.sourceBody(t, id, fld, `${h.account_name} · ${h.holding_name} · ${monthLabel(h.period_end)}`, FIELD_LABELS[fld], raw, fld === 'weight' ? h.weight_pct : null, kind, null, fld === 'market_value' ? h.currency : null, h, h.document_id, []),
    };
  }

  /**
   * Reason messages for a source panel: the open review item's server-written text
   * when there is one (same wording as Review), otherwise the label.
   */
  private reasons(table: SourceTable, id: number, codes: string[]): ReasonInfo[] {
    const item = this.review
      .items()
      .find((i) => (i.target_table === table && i.target_id === id) || (table === 'returns' && i.candidates?.some((c) => c.id === id)));
    return codes.map((c) => {
      const code = c as ReasonInfo['code'];
      const fromItem = item?.reasons.find((r) => r.code === code);
      if (fromItem) return fromItem;
      const message =
        code === 'alias_match'
          ? 'This value is linked through an alias that is awaiting confirmation in Review.'
          : (REVIEW_REASON_LABELS[code] ?? c);
      return { code, label: REVIEW_REASON_LABELS[code] ?? c, message };
    });
  }

  private link(docId: number): string | null {
    return this.opts.demoMode ? null : `https://drive.google.com/file/d/mock${docId}/view?usp=drivesdk`;
  }

  private sourceBody(
    table: SourceTable, id: number, field: string, label: string, fieldLabel: string,
    value: number | string | null, valuePct: number | null, kind: ValueKind, unit: string | null, currency: string | null,
    row: { status: string; review_reasons: string[]; confidence: number | null; page: number | null; source_snippet: string | null } & object,
    documentId: number, conflicts: ValueSnapshot[],
  ): SourceResponse {
    const d = docById(documentId);
    const processedAt = this.iso(-120);
    return {
      table,
      id,
      field,
      label,
      field_label: fieldLabel,
      value,
      value_pct: valuePct,
      value_kind: kind,
      unit,
      currency,
      status: row.status as SourceResponse['status'],
      review_reasons: row.review_reasons as SourceResponse['review_reasons'],
      reasons: this.reasons(table, id, row.review_reasons),
      provenance: {
        page: row.page,
        source_snippet: row.source_snippet,
        confidence: row.confidence,
        snippet_verified: row.source_snippet === null || d.image_only ? null : !row.review_reasons.includes('snippet_not_found'),
      },
      snippet_highlight: mockSnippetHighlight(row.source_snippet, value, valuePct, kind),
      document: {
        document_id: d.document_id,
        document_name: d.document_name,
        document_type: d.document_type,
        mime_type: d.mime_type,
        source: this.opts.demoMode ? 'local' : 'drive',
        web_view_link: this.link(d.document_id),
        version: 1,
        processed_at: processedAt,
      },
      extraction: {
        extraction_id: d.document_id,
        model: 'claude-opus-5-5',
        prompt_version: 'v1',
        source: this.opts.demoMode ? 'fixture' : 'claude',
        created_at: processedAt,
      },
      conflicts,
      review_history: this.review.historyFor(table, id),
    };
  }
}

/**
 * Mock of the server's `snippet_highlight`: the first number token in the snippet whose
 * magnitude equals the value (percent: `value_pct`; money/number: `value`). Null for
 * text/date values, no snippet, or no match, like the api.
 */
export function mockSnippetHighlight(
  snippet: string | null, value: number | string | null, valuePct: number | null, kind: ValueKind,
): SnippetHighlight | null {
  if (!snippet || kind === 'text' || kind === 'date') return null;
  const target = kind === 'percent' ? valuePct : typeof value === 'number' ? value : null;
  if (target === null) return null;
  const re = /[+\-\u2212]?\(?\d[\d,]*(?:\.\d+)?\)?%?/g;
  for (let m = re.exec(snippet); m; m = re.exec(snippet)) {
    const n = Number(m[0].replace(/[^\d.]/g, ''));
    if (Number.isFinite(n) && Math.abs(n - Math.abs(target)) < 1e-6 * Math.max(1, Math.abs(target))) {
      return { start: m.index, end: m.index + m[0].length, text: m[0] };
    }
  }
  return null;
}

export function parseSort(sort: string | null, allowed: readonly string[], def: string): [string, boolean][] {
  const raw = (sort ?? '').trim() || def;
  const seen = new Set<string>();
  return raw.split(',').map((part) => {
    const p = part.trim();
    const desc = p.startsWith('-');
    const col = desc ? p.slice(1) : p;
    if (!allowed.includes(col)) throw new ValidationError('query.sort', `cannot sort by '${col}'; allowed: ${allowed.join(', ')}`);
    if (seen.has(col)) throw new ValidationError('query.sort', `sort key '${col}' repeated`);
    seen.add(col);
    return [col, desc];
  });
}

/** C3: NULLs last in both directions; primary key ascending as the final tie-breaker. */
export function sortRows(rows: Row[], keys: [string, boolean][], pk: string): Row[] {
  const cmp = (a: unknown, b: unknown): number => {
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    return String(a).localeCompare(String(b), 'en', { sensitivity: 'base' });
  };
  return [...rows].sort((x, y) => {
    for (const [col, desc] of keys) {
      const a = x[col];
      const b = y[col];
      const an = a === null || a === undefined;
      const bn = b === null || b === undefined;
      if (an && bn) continue;
      if (an) return 1;
      if (bn) return -1;
      const c = cmp(a, b);
      if (c !== 0) return desc ? -c : c;
    }
    return (x[pk] as number) - (y[pk] as number);
  });
}

function intParam(p: URLSearchParams, name: string, def: number, min: number, max: number): number {
  const v = p.get(name);
  if (v === null) return def;
  const n = Number(v);
  if (!Number.isInteger(n) || n < min || n > max) throw new ValidationError(`query.${name}`, `${name} must be an integer between ${min} and ${max}`);
  return n;
}

/** C5 cell rules: lists joined with `;`, null empty, RFC 4180 quoting, CRLF. */
export function toCsv(rows: Row[]): string {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  const cell = (v: unknown): string => {
    if (v === null || v === undefined) return '';
    const s = Array.isArray(v) ? v.join(';') : String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\r\n') + '\r\n';
}

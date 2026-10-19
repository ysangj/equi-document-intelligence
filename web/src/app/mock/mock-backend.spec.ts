import {
  DocumentItem,
  FundFactItem,
  FundsList,
  Page,
  ProgressResponse,
  ReturnItem,
  ReviewListResponse,
  SearchResponse,
  SourceResponse,
  StatementItem,
} from '../api/models';
import { MOCK_DOCUMENTS } from './mock-data';
import { MockBackend, sortRows, toCsv } from './mock-backend';

function get(b: MockBackend, url: string) {
  const u = new URL(url, 'http://x');
  return b.handle({ method: 'GET', path: u.pathname, params: u.searchParams });
}

describe('MockBackend (dev-only contract mock)', () => {
  let b: MockBackend;
  beforeEach(() => {
    b = new MockBackend({ demoMode: true, initialProcessed: MOCK_DOCUMENTS.length, now: () => new Date('2026-10-16T18:52:10Z') });
  });

  it('returns the ground-truth best January return (Kestrel +2.84%)', () => {
    const r = get(b, '/api/returns?date_from=2026-01-01&date_to=2026-01-31&return_type=MTD&net_gross=net&sort=-value_pct&page_size=3');
    const page = r.body as Page<ReturnItem>;
    expect(r.status).toBe(200);
    expect(page.items[0].fund_name).toBe('Kestrel Quant Equity Fund LP');
    expect(page.items[0].value_pct).toBe(2.84);
    expect(page.items.length).toBe(3);
    expect(page.sort).toBe('-value_pct');
    expect(page.total).toBe(8); // Northwind Jan is reported by 3 documents
  });

  it('ORs repeated keys and ANDs different keys', () => {
    const page = get(b, '/api/returns?fund_id=1&fund_id=2&status=conflict').body as Page<ReturnItem>;
    expect(page.total).toBe(3);
    expect(page.items.every((x) => x.status === 'conflict')).toBe(true);
  });

  it('paginates with a real total', () => {
    const p1 = get(b, '/api/returns?page=1&page_size=10').body as Page<ReturnItem>;
    const p9 = get(b, '/api/returns?page=99&page_size=10').body as Page<ReturnItem>;
    expect(p1.items.length).toBe(10);
    expect(p9.items).toEqual([]);
    expect(p9.total).toBe(p1.total);
  });

  it('rejects an unknown sort column with the C2 envelope', () => {
    const r = get(b, '/api/returns?sort=colour');
    expect(r.status).toBe(422);
    expect(r.body).toMatchObject({ error: { code: 'validation_error', details: [{ loc: 'query.sort' }] } });
  });

  it('serves CSV with the contract filename', () => {
    const r = get(b, '/api/returns?status=conflict&format=csv');
    expect(r.headers?.['Content-Disposition']).toBe('attachment; filename="returns_2026-10-16.csv"');
    const lines = (r.body as string).trim().split('\r\n');
    expect(lines[0].startsWith('return_id,fund_id,fund_name')).toBe(true);
    expect(lines.length).toBe(4);
  });

  it('filters statements by account and serves holdings pages', () => {
    const s = get(b, '/api/statements?account_id=1').body as Page<StatementItem>;
    expect(s.items.map((x) => x.statement_id)).toEqual([12]);
    const h = get(b, '/api/statements/13/holdings?page=2&page_size=10').body as Page<unknown>;
    expect(h.total).toBe(12);
    expect(h.items.length).toBe(2);
    expect(get(b, '/api/statements/999/holdings').status).toBe(404);
  });

  it('builds provenance with conflicts and per-field statement sources', () => {
    const r = get(b, '/api/sources/returns/214').body as SourceResponse;
    expect(r.conflicts.map((c) => c.id).sort()).toEqual([219, 231]);
    expect(r.document.web_view_link).toBeNull(); // demo mode
    const s = get(b, '/api/sources/statements/12?field=closing_balance').body as SourceResponse;
    expect(s.value_kind).toBe('money');
    expect(s.reasons[0].code).toBe('rollforward_mismatch');
    expect(get(b, '/api/sources/statements/12?field=colour').status).toBe(422);
    expect(get(b, '/api/sources/nope/1').status).toBe(422);
  });

  it('simulates progressive processing through /api/progress', () => {
    const fresh = new MockBackend({ demoMode: true, initialProcessed: 0 });
    expect((get(fresh, '/api/returns').body as Page<ReturnItem>).total).toBe(0);
    const p1 = get(fresh, '/api/progress').body as ProgressResponse;
    expect(p1.processed).toBe(0);
    expect(p1.active).toBe(true);
    const p2 = get(fresh, '/api/progress').body as ProgressResponse;
    expect(p2.processed).toBe(1);
    expect((get(fresh, '/api/returns').body as Page<ReturnItem>).total).toBeGreaterThan(0);
  });

  it('reports drive mode when demo is off', () => {
    const d = new MockBackend({ demoMode: false, initialProcessed: 3 });
    expect((get(d, '/api/config').body as { demo_mode: boolean }).demo_mode).toBe(false);
    expect((get(d, '/api/sources/returns/101').body as SourceResponse).document.web_view_link).toContain('drive.google.com');
  });

  it('sortRows puts NULLs last in both directions and breaks ties by pk', () => {
    const rows = [
      { id: 1, v: null },
      { id: 2, v: 5 },
      { id: 3, v: 1 },
      { id: 4, v: 5 },
    ];
    expect(sortRows(rows, [['v', false]], 'id').map((r) => r['id'])).toEqual([3, 2, 4, 1]);
    expect(sortRows(rows, [['v', true]], 'id').map((r) => r['id'])).toEqual([2, 4, 3, 1]);
  });

  it('toCsv quotes and joins lists', () => {
    expect(toCsv([{ a: 'x,y', b: ['p', 'q'], c: null }])).toBe('a,b,c\r\n"x,y",p;q,\r\n');
  });
});

describe('MockBackend U2 endpoints (ingestion, search, review)', () => {
  const NOW = () => new Date('2026-10-16T18:52:10Z');
  let b: MockBackend;
  const post = (url: string, body: unknown = null) => {
    const u = new URL(url, 'http://x');
    return b.handle({ method: 'POST', path: u.pathname, params: u.searchParams, body });
  };
  beforeEach(() => {
    b = new MockBackend({ demoMode: true, initialProcessed: MOCK_DOCUMENTS.length, now: NOW });
  });

  it('lists all 21 documents with the scanned PDF failed after 3 attempts', () => {
    const page = get(b, '/api/documents?page_size=50').body as Page<DocumentItem>;
    expect(page.total).toBe(21);
    const failed = page.items.find((d) => d.document_id === 8)!;
    expect(failed).toMatchObject({ status: 'failed', attempts: 3, search_indexed: false, total_tokens: null, can_reprocess: true });
    expect(failed.last_error).toContain('429');
    expect(get(b, '/api/documents?status=failed').body).toMatchObject({ total: 1 });
    expect(get(b, '/api/documents?status=bogus').status).toBe(422);
    expect((get(b, '/api/documents?sort=-total_tokens').body as Page<DocumentItem>).items[0].total_tokens).toBeGreaterThan(0);
  });

  it('reprocess: 202 and pending, 409 document_busy while queued, then done after polls', () => {
    const r = post('/api/documents/8/reprocess');
    expect(r.status).toBe(202);
    expect(r.body).toMatchObject({ document_id: 8, status: 'pending', attempts: 0 });
    expect(post('/api/documents/8/reprocess')).toMatchObject({ status: 409, body: { error: { code: 'document_busy' } } });
    expect(post('/api/documents/999/reprocess').status).toBe(404);
    expect((get(b, '/api/progress').body as ProgressResponse).active).toBe(true);
    get(b, '/api/progress');
    const d = (get(b, '/api/documents?q=scanned').body as Page<DocumentItem>).items[0];
    expect(d.status).toBe('done');
    expect(d.search_indexed).toBe(true);
  });

  it('sync / reconcile are asynchronous: 202, pending in progress, then cleared', () => {
    expect(post('/api/sync')).toMatchObject({ status: 202, body: { kind: 'sync', already_pending: false } });
    expect(post('/api/sync').body).toMatchObject({ already_pending: true });
    expect(post('/api/sync/reconcile').body).toMatchObject({ kind: 'reconcile', mode: 'demo' });
    const p1 = get(b, '/api/progress').body as ProgressResponse;
    expect(p1.sync.sync_pending).toBe(true);
    expect(p1.sync.reconcile_pending).toBe(true);
    get(b, '/api/progress');
    const p3 = get(b, '/api/progress').body as ProgressResponse;
    expect(p3.sync.sync_pending).toBe(false);
    expect(p3.sync.reconcile_pending).toBe(false);
  });

  it('search: short queries are empty; headlines only contain balanced <mark> around escaped text', () => {
    expect(get(b, '/api/search?q=d').body).toMatchObject({ total: 0, took_ms: 0, min_query_length: 2 });
    expect(get(b, '/api/search?q=' + 'x'.repeat(201)).status).toBe(422);
    const r = get(b, '/api/search?q=duration').body as SearchResponse;
    expect(r.groups.chunks.length).toBeGreaterThanOrEqual(4);
    for (const c of r.groups.chunks) {
      const rest = c.headline_html.replace(/<\/?mark>/g, '');
      expect(rest).not.toMatch(/[<>"]/);
      expect(c.headline_html).toContain('<mark>duration</mark>');
    }
    const amp = get(b, '/api/search?q=healthcare').body as SearchResponse;
    expect(amp.groups.chunks[0].headline_html).toContain('software &amp; <mark>healthcare</mark>');
    const csv = (get(b, '/api/search?q=estimate').body as SearchResponse).groups.chunks.find((c) => c.document_id === 19);
    expect(csv?.page).toBeNull();
  });

  it('search: Northwind finds the fund (with its alias) and its documents', () => {
    const r = get(b, '/api/search?q=Northwind').body as SearchResponse;
    expect(r.groups.funds.map((f) => f.fund_name)).toEqual(['Northwind Global Macro Fund']);
    expect(r.groups.funds[0].matched_alias).toBe('Northwind Macro Fund Ltd');
    expect(r.groups.funds[0].aliases).toEqual([{ alias: 'Northwind Macro Fund Ltd', confirmed: false }]);
    expect(r.groups.documents.length).toBe(4);
  });

  it('T6 fields: snippet_highlight slices the snippet, statement review fields end with the dates', () => {
    const s = get(b, '/api/sources/returns/101').body as SourceResponse;
    const h = s.snippet_highlight!;
    expect(s.provenance.source_snippet!.slice(h.start, h.end)).toBe(h.text);
    expect(h.text).toContain('2.84');
    const st = get(b, '/api/sources/statements/12?field=closing_balance').body as SourceResponse;
    expect(st.provenance.source_snippet!.slice(st.snippet_highlight!.start, st.snippet_highlight!.end)).toBe(st.snippet_highlight!.text);
    expect((get(b, '/api/sources/statements/12?field=period_end').body as SourceResponse).snippet_highlight).toBeNull();
    const item = (get(b, '/api/review?reason=rollforward_mismatch').body as ReviewListResponse).items[0];
    expect(item.fields!.map((f) => f.field).slice(-2)).toEqual(['period_start', 'period_end']);
    expect(item.fields!.slice(-2).every((f) => f.value_kind === 'date')).toBe(true);
    expect((get(b, '/api/config').body as { extraction_source: string }).extraction_source).toBe('fixture');
  });

  it('reject_alias relinks the rows that came through the alias and re-runs the conflict check', () => {
    const rej = post('/api/review/fund_aliases:4', { action: 'reject_alias', reassign_to_id: null }).body as { affected: { change: string; id: number }[] };
    expect(rej.affected.map((a) => a.change)).toEqual(['created', 'alias_reassigned', 'relinked']);
    // 231 now belongs to the new fund, so the Northwind conflict is gone
    expect((get(b, '/api/review?kind=conflict').body as ReviewListResponse).total).toBe(0);
    expect((get(b, '/api/sources/returns/214').body as SourceResponse).status).toBe('ok');
    expect((get(b, '/api/funds').body as { items: { fund_name: string }[] }).items.map((f) => f.fund_name)).toContain('Northwind Macro Fund Ltd');
  });

  it('review: the planted conflict, aliases, YTD and roll-forward items with counts', () => {
    const r = get(b, '/api/review').body as ReviewListResponse;
    expect(r.items.map((i) => i.id).sort()).toEqual(['fund_aliases:4', 'fund_aliases:9', 'returns:214', 'returns:306', 'returns:404', 'statements:12']);
    expect(r.counts.by_kind).toEqual({ value: 3, conflict: 1, alias: 2 });
    const c = r.items.find((i) => i.kind === 'conflict')!;
    expect(c.candidates!.map((x) => [x.id, x.value_pct])).toEqual([[214, 1.92], [219, 1.92], [231, 1.89]]);
    expect(c.detail.conflict).toEqual({ conflicting_ids: [219, 231], tolerance: 0.0001 });
    // Q2 fix: a snippet that is in the document but lacks the value
    const sn = r.items.find((i) => i.id === 'returns:404')!;
    expect(sn.detail.snippet_not_found).toEqual({ snippet: 'Alder Asia Fund  Q1', value_missing: true });
    expect(sn.current!.provenance.snippet_verified).toBe(false);
    expect((get(b, '/api/sources/returns/404').body as SourceResponse).provenance.snippet_verified).toBe(false);
    const y = r.items.find((i) => i.id === 'returns:306')!;
    expect(y.detail.ytd_mismatch).toMatchObject({ stated: 0.0015, computed: -0.0015076 });
    expect((get(b, '/api/progress').body as ProgressResponse).review_open).toBe(6);
    expect((get(b, '/api/review?kind=alias').body as ReviewListResponse).total).toBe(2);
    expect((get(b, '/api/review?reason=rollforward_mismatch').body as ReviewListResponse).items[0].id).toBe('statements:12');
  });

  it('review actions: invalid_action gating, choose keeps agreeing rows and deletes differing ones, aliases clear flags', () => {
    expect(post('/api/review/returns:306', { action: 'choose', chosen_id: 1 })).toMatchObject({ status: 422, body: { error: { code: 'invalid_action' } } });
    expect(post('/api/review/returns:306', { action: 'edit', value: 0.1 })).toMatchObject({ status: 422, body: { error: { code: 'invalid_action' } } });
    expect(post('/api/review/returns:214', { action: 'choose', chosen_id: 999 }).status).toBe(422);
    expect(post('/api/review/returns:214', { action: 'choose', chosen_id: 214, bogus: 1 })).toMatchObject({ status: 422, body: { error: { code: 'validation_error' } } });

    const res = post('/api/review/returns:231', { action: 'choose', chosen_id: 214 });
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ id: 'returns:214', resolved: true, remaining_open: 5 });
    // Q2 fix: the Feb factsheet's 1.92 agrees with the chosen 1.92, so it is kept and set ok
    expect((res.body as { affected: unknown[] }).affected).toEqual([
      { table: 'returns', id: 214, change: 'status_changed', status: 'ok' },
      { table: 'returns', id: 219, change: 'status_changed', status: 'ok' },
      { table: 'returns', id: 231, change: 'deleted', status: null },
    ]);
    expect(get(b, '/api/sources/returns/231').status).toBe(404);
    const kept = get(b, '/api/sources/returns/219').body as SourceResponse;
    expect(kept.status).toBe('ok');
    expect(kept.review_history[0].action).toBe('choose');
    expect((get(b, '/api/sources/returns/214').body as SourceResponse).review_history[0].action).toBe('choose');

    expect(post('/api/review/returns:306', { action: 'edit', value_pct: -0.15 }).body).toMatchObject({ resolved: true });
    expect((get(b, '/api/sources/returns/306').body as SourceResponse).value_pct).toBe(-0.15);
    expect(post('/api/review/statements:12', { action: 'edit', field: 'closing_balance', value: 13080000 }).body).toMatchObject({ resolved: true });

    expect(post('/api/review/fund_aliases:9', { action: 'confirm_alias' }).body).toMatchObject({ resolved: true });
    const alder = (get(b, '/api/returns?fund_id=4&date_from=2026-03-01&return_type=MTD').body as Page<ReturnItem>).items[0];
    expect(alder.status).toBe('ok');
    expect(post('/api/review/returns:404', { action: 'accept' }).body).toMatchObject({ resolved: true });

    const rej = post('/api/review/fund_aliases:4', { action: 'reject_alias', reassign_to_id: null }).body as { affected: { change: string }[]; remaining_open: number };
    // row 231 (linked through this alias) was already deleted by the `choose` above
    expect(rej.affected.map((a) => a.change)).toEqual(['created', 'alias_reassigned']);
    expect(rej.remaining_open).toBe(0);
    expect(post('/api/review/fund_aliases:4', { action: 'confirm_alias' }).status).toBe(404);
  });
  it('make_canonical: alias items allow it, and it renames the mock fund everywhere', () => {
    const post = (url: string, body: unknown) => b.handle({ method: 'POST', path: url, params: new URLSearchParams(), body });
    const aliases = (get(b, '/api/review?kind=alias').body as ReviewListResponse).items;
    expect(aliases.map((i) => i.id).sort()).toEqual(['fund_aliases:4', 'fund_aliases:9']);
    expect(aliases.every((i) => i.allowed_actions.includes('make_canonical'))).toBe(true);
    const value = (get(b, '/api/review?kind=value').body as ReviewListResponse).items[0];
    expect(value.allowed_actions).not.toContain('make_canonical');
    expect(post(`/api/review/${value.id}`, { action: 'make_canonical' }).status).toBe(422);

    // the body takes no other fields
    expect(post('/api/review/fund_aliases:4', { action: 'make_canonical', note: 'x' }).status).toBe(422);

    const res = post('/api/review/fund_aliases:4', { action: 'make_canonical' });
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({
      id: 'fund_aliases:4',
      action: 'make_canonical',
      resolved: true,
      affected: [
        { table: 'funds', id: 2, change: 'renamed', status: null },
        { table: 'fund_aliases', id: 4, change: 'alias_confirmed', status: null },
      ],
    });

    const nw = (get(b, '/api/funds').body as FundsList).items.find((f) => f.fund_id === 2)!;
    expect(nw.fund_name).toBe('Northwind Macro Fund Ltd');
    expect(nw.aliases.map((a) => [a.alias, a.confirmed])).toEqual([
      ['Northwind Global Macro Fund', true],
      ['Northwind Macro Fund Ltd', true],
    ]);
    expect(nw.has_unconfirmed_alias).toBe(false);
    const rows = (get(b, '/api/returns?fund_id=2&page_size=100').body as Page<ReturnItem>).items;
    expect(rows.length).toBeGreaterThan(0);
    expect(new Set(rows.map((r) => r.fund_name))).toEqual(new Set(['Northwind Macro Fund Ltd']));
    const facts = (get(b, '/api/fund-facts?fund_id=2').body as Page<FundFactItem>).items;
    expect(facts.every((f) => f.fund_name === 'Northwind Macro Fund Ltd')).toBe(true);
    // the alias item left the queue; a second rename is a 404
    expect((get(b, '/api/review?kind=alias').body as ReviewListResponse).items.map((i) => i.id)).toEqual(['fund_aliases:9']);
    expect(post('/api/review/fund_aliases:4', { action: 'make_canonical' }).status).toBe(404);
  });
});

import { AskResponse, AuthStatusResponse, ConfigResponse, DriveFoldersResponse, ProgressResponse } from '../api/models';
import { MOCK_DOCUMENTS } from './mock-data';
import { MockBackend } from './mock-backend';
import { bestJanuary, rateRisk, worstMarch } from './mock-ask';

const NOW = () => new Date('2026-10-16T18:52:10Z');

function call(b: MockBackend, method: string, url: string, body?: unknown) {
  const u = new URL(url, 'http://x');
  return b.handle({ method, path: u.pathname, params: u.searchParams, body });
}

describe('MockBackend: OAuth + Drive (T7 contract)', () => {
  it('first run: not connected → sync 409 not_connected → simulated consent → folder picker → watch → reconcile discovers documents', () => {
    const b = new MockBackend({ demoMode: false, initialProcessed: 14, connected: false, now: NOW });
    expect(call(b, 'GET', '/api/auth/status').body).toMatchObject<Partial<AuthStatusResponse>>({ mode: 'drive', connected: false, folder: null });
    expect(call(b, 'POST', '/api/sync').body).toMatchObject({ error: { code: 'not_connected' } });
    expect(call(b, 'GET', '/api/drive/folders').status).toBe(409);
    expect((call(b, 'GET', '/api/progress').body as ProgressResponse).total).toBe(0);

    expect(call(b, 'POST', '/api/__mock/oauth/callback').body).toEqual({ location: '/connect/folder' });
    expect(call(b, 'GET', '/api/auth/status').body).toMatchObject({ connected: true, google_email: 'sj.demo@gmail.com', folder: null });
    expect(call(b, 'POST', '/api/sync').body).toMatchObject({ error: { code: 'no_folder' } });

    const root = call(b, 'GET', '/api/drive/folders?parent=root').body as DriveFoldersResponse;
    expect(root.parent).toEqual({ folder_id: 'root', folder_name: 'My Drive', parent_id: null, is_root: true });
    expect(root.items.map((f) => f.folder_name)).toEqual(['board packs', 'Fund Docs', 'Monthly exports', 'Personal', 'Tax archive']);
    const sub = call(b, 'GET', '/api/drive/folders?parent=1Q8vXk2mFundDocs').body as DriveFoldersResponse;
    expect(sub.parent.parent_id).toBe('root');
    expect(call(b, 'GET', '/api/drive/folders?parent=nope').status).toBe(404);

    const w = call(b, 'POST', '/api/drive/watch', { folder_id: '1Q8vXk2mFundDocs' });
    expect(w.status).toBe(200);
    expect(w.body).toMatchObject({ folder: { source: 'drive', folder_id: '1Q8vXk2mFundDocs', folder_name: 'Fund Docs' } });
    const p1 = call(b, 'GET', '/api/progress').body as ProgressResponse;
    expect(p1.sync).toMatchObject({ reconcile_pending: true, folder_name: 'Fund Docs' });
    expect(p1.connection).toEqual({ connected: true, google_email: 'sj.demo@gmail.com' });
    call(b, 'GET', '/api/progress');
    const p3 = call(b, 'GET', '/api/progress').body as ProgressResponse;
    expect(p3.sync.reconcile_pending).toBe(false);
    expect(p3.total).toBe(MOCK_DOCUMENTS.length);
    expect(p3.active).toBe(true);
  });

  it('paginates folders, validates watch, and disconnects', () => {
    const b = new MockBackend({ demoMode: false, initialProcessed: 14, now: NOW });
    const p1 = call(b, 'GET', '/api/drive/folders?parent=1Me7MonthlyExports').body as DriveFoldersResponse;
    expect(p1.items.length).toBe(100);
    expect(p1.next_page_token).toBe('100');
    const p2 = call(b, 'GET', '/api/drive/folders?parent=1Me7MonthlyExports&page_token=100').body as DriveFoldersResponse;
    expect(p2.items.length).toBe(30);
    expect(p2.next_page_token).toBeNull();
    expect(call(b, 'GET', '/api/drive/folders?colour=1').status).toBe(422);
    expect(call(b, 'POST', '/api/drive/watch', {}).body).toMatchObject({ error: { code: 'validation_error', details: [{ loc: 'body.folder_id' }] } });
    expect(call(b, 'POST', '/api/drive/watch', { folder_id: 'x' }).status).toBe(404);

    expect(call(b, 'POST', '/api/auth/disconnect').body).toEqual({ connected: false });
    expect(call(b, 'GET', '/api/auth/status').body).toMatchObject({ connected: false, folder: null });
    expect((call(b, 'GET', '/api/progress').body as ProgressResponse).connection.connected).toBe(false);
    // ingested documents stay browsable
    expect((call(b, 'GET', '/api/returns').body as { total: number }).total).toBeGreaterThan(0);
  });

  it('demo mode: C7 behaviour', () => {
    const b = new MockBackend({ demoMode: true, initialProcessed: 14, now: NOW });
    expect(call(b, 'GET', '/api/auth/status').body).toMatchObject({ mode: 'demo', folder: { source: 'local', folder_name: 'sample_docs' } });
    expect(call(b, 'POST', '/api/auth/disconnect').body).toMatchObject({ error: { code: 'demo_mode' } });
    expect(call(b, 'GET', '/api/drive/folders').body).toMatchObject({ error: { code: 'demo_mode' } });
    expect(call(b, 'POST', '/api/__mock/oauth/callback').body).toEqual({ location: '/connect?error=demo_mode' });
    expect(call(b, 'POST', '/api/sync').status).toBe(202);
  });

  it('a configured OAuth error code comes back on /connect', () => {
    const b = new MockBackend({ demoMode: false, initialProcessed: 0, connected: false, oauthError: 'access_denied', now: NOW });
    expect(call(b, 'POST', '/api/__mock/oauth/callback').body).toEqual({ location: '/connect?error=access_denied' });
    expect((call(b, 'GET', '/api/auth/status').body as AuthStatusResponse).connected).toBe(false);
  });
});

/** The contract's AskResponse invariants (app/schemas/ask.py validators). */
function checkContract(r: AskResponse) {
  const ids = r.citations.map((c) => c.id);
  expect(ids).toEqual(ids.map((_, k) => k + 1));
  const markers = [...r.answer.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1]));
  expect(new Set(markers)).toEqual(new Set(ids));
  // numbered in order of first appearance
  expect([...new Set(markers)]).toEqual(ids);
  for (const s of r.steps) for (const c of s.citation_ids) expect(ids).toContain(c);
  for (const n of r.unverified_numbers) expect(r.answer).toContain(n);
  for (const c of r.citations) if (c.type === 'sql') {
    expect(c.row_count).toBe(c.rows.length);
    for (const row of c.rows) expect(row.length).toBe(c.columns.length);
  }
}

describe('MockBackend: POST /api/ask (T8 contract)', () => {
  const link = (id: number) => `https://drive.google.com/file/d/mock${id}/view`;

  it('best January: query_sql with Kestrel +2.84% first, a flagged conflict and an unverified number', () => {
    const r = bestJanuary();
    checkContract(r);
    expect(r.steps.map((s) => s.tool)).toEqual(['query_sql']);
    const sql = r.citations[0];
    expect(sql.type === 'sql' && sql.rows[0].slice(0, 2)).toEqual(['Kestrel Quant Equity Fund LP', 2.84]);
    expect(sql.type === 'sql' && sql.flagged_row_count).toBe(3); // the Northwind Jan conflict rows (3 documents)
    expect(r.uses_flagged_data).toBe(true);
    expect(r.unverified_numbers).toEqual(['0.92']);
  });

  it('rate risk: search_documents passages including the "rate sensitivity" synonym hit', () => {
    const r = rateRisk(link);
    checkContract(r);
    expect(r.steps.map((s) => s.tool)).toEqual(['search_documents']);
    const texts = r.citations.map((c) => (c.type === 'passage' ? c.text : ''));
    expect(texts.some((t) => t.includes('duration'))).toBe(true);
    expect(texts.some((t) => t.includes('rate sensitivity') && !t.includes('duration'))).toBe(true);
    expect(r.citations.every((c) => c.type === 'passage' && c.web_view_link?.startsWith('https://'))).toBe(true);
  });

  it('worst March: both tools, a guard-rejected non-SELECT then the corrected query', () => {
    const r = worstMarch(link);
    checkContract(r);
    expect(r.steps.map((s) => s.tool)).toEqual(['query_sql', 'query_sql', 'search_documents']);
    expect(r.steps[0]).toMatchObject({ is_guard_error: true, citation_ids: [] });
    expect(String(r.steps[0].input['sql'])).toMatch(/^CREATE/);
    expect(r.steps[1].is_guard_error).toBe(false);
    const sql = r.citations[0];
    expect(sql.type === 'sql' && sql.rows[0].slice(0, 3)).toEqual(['Alder Asia Fund', -1.25, 'needs_review']);
    expect(r.uses_flagged_data).toBe(true);
  });

  it('routes questions, validates the request and is 503 ask_disabled without a key', () => {
    const drive = new MockBackend({ demoMode: false, initialProcessed: 14, now: NOW });
    const ok = call(drive, 'POST', '/api/ask', { question: 'What did managers say about rate risk?', history: [] });
    expect(ok.status).toBe(200);
    expect((ok.body as AskResponse).steps[0].tool).toBe('search_documents');
    expect(call(drive, 'POST', '/api/ask', { question: '  ' }).body).toMatchObject({ error: { code: 'validation_error', details: [{ loc: 'body.question' }] } });
    const tooMany = Array.from({ length: 21 }, () => ({ role: 'user', content: 'x' }));
    expect(call(drive, 'POST', '/api/ask', { question: 'q', history: tooMany }).body).toMatchObject({ error: { details: [{ loc: 'body.history' }] } });
    expect(call(drive, 'POST', '/api/ask', { question: 'q', extra: 1 }).status).toBe(422);
    checkContract(call(drive, 'POST', '/api/ask', { question: 'something else' }).body as AskResponse);

    const demo = new MockBackend({ demoMode: true, initialProcessed: 14, now: NOW });
    expect((call(demo, 'GET', '/api/config').body as ConfigResponse).features.ask.enabled).toBe(false);
    expect(call(demo, 'POST', '/api/ask', { question: 'q' })).toMatchObject({ status: 503, body: { error: { code: 'ask_disabled' } } });
    const demoAsk = new MockBackend({ demoMode: true, initialProcessed: 14, askEnabled: true, now: NOW });
    expect(call(demoAsk, 'POST', '/api/ask', { question: 'Which fund had the best January return?' }).status).toBe(200);
  });
});

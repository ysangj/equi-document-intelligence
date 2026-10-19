import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { DocumentItem, DocumentsPage, ProgressResponse } from '../../api/models';
import { NotifyService } from '../../core/notify.service';
import { POLL_ACTIVE_MS, POLL_IDLE_MS, ProgressService } from '../../core/progress.service';
import { CONTRACT_DOCUMENTS, PROGRESS } from '../../testing/fixtures';
import { connectionLine } from './ingestion-summary.component';
import { IngestionPageComponent } from './ingestion-page.component';
import { truncate } from './documents-table.component';

const IDLE: ProgressResponse = { ...PROGRESS, active: false, in_flight: 0, counts: { ...PROGRESS.counts, processing: 0 } };
const DEMO: ProgressResponse = {
  ...IDLE,
  mode: 'demo',
  connection: { connected: false, google_email: null },
  sync: { ...PROGRESS.sync, source: 'local', folder_id: null, folder_name: 'sample_docs' },
};

const page = (items: DocumentItem[] = CONTRACT_DOCUMENTS.items): DocumentsPage => ({ ...CONTRACT_DOCUMENTS, items });

describe('IngestionPageComponent (FR-12)', () => {
  let http: HttpTestingController;
  let progress: ProgressService;
  let info: string[];
  let errors: string[];

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
    progress = TestBed.inject(ProgressService);
    info = [];
    errors = [];
    const notify = TestBed.inject(NotifyService);
    vi.spyOn(notify, 'info').mockImplementation((t: string) => void info.push(t));
    vi.spyOn(notify, 'error').mockImplementation((e, ctx) => void errors.push(`${ctx}: ${typeof e === 'string' ? e : e.message}`));
  });

  afterEach(() => {
    progress.stop();
    vi.useRealTimers();
  });

  const docsReq = () => http.match((r) => r.url === '/api/documents');
  const flushAuth = () => http.match('/api/auth/status').forEach((r) => r.flush({}, { status: 404, statusText: 'Not Found' }));

  /** Fake timers stop the zoneless scheduler, so change detection is driven explicitly. */
  async function settle(f: { detectChanges(): void }) {
    f.detectChanges();
    await vi.advanceTimersByTimeAsync(0);
    f.detectChanges();
  }

  async function render() {
    const fixture = TestBed.createComponent(IngestionPageComponent);
    await settle(fixture);
    flushAuth();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  /** Answer the pending /api/progress and the /api/documents refresh it triggers. */
  async function pollOnce(fixture: { detectChanges(): void }, p: ProgressResponse): Promise<TestRequest[]> {
    http.expectOne('/api/progress').flush(p);
    await settle(fixture);
    const reqs = docsReq();
    reqs.forEach((r) => r.flush(page()));
    await settle(fixture);
    return reqs;
  }

  it('loads documents with the contract query and renders the FR-12 columns', async () => {
    const { fixture, el } = await render();
    const [req] = docsReq();
    expect(req.request.urlWithParams).toBe('/api/documents?page=1&page_size=50');
    req.flush(page());
    await settle(fixture);
    const rows = el.querySelectorAll('app-documents-table tbody tr');
    expect(rows.length).toBe(2);
    const kestrel = rows[0].textContent!;
    expect(kestrel).toContain('kestrel_factsheet_2026-01.pdf');
    expect(kestrel).toContain('Factsheet');
    expect(rows[0].querySelector('[data-testid=doc-status]')!.textContent).toBe('done');
    expect(rows[0].querySelector('[data-testid=tokens]')!.textContent!.trim()).toBe('6.4k / 812');
    expect(kestrel).toContain('✓ indexed');
    const failed = rows[1];
    expect(failed.querySelector('[data-testid=doc-status]')!.textContent).toBe('failed');
    expect(failed.querySelector('[data-testid=last-error]')!.textContent).toContain('Anthropic API 429 rate_limit_e');
    (failed.querySelector('[data-testid=error-more]') as HTMLButtonElement).click();
    await settle(fixture);
    expect(failed.querySelector('[data-testid=last-error]')!.textContent).toContain('(gave up after 3 attempts)');
  });

  it('refreshes documents on the progress cadence: 3 s while active, 15 s when idle (no second timer)', async () => {
    progress.start();
    const { fixture } = await render();
    docsReq().forEach((r) => r.flush(page())); // initial load

    expect((await pollOnce(fixture, PROGRESS)).length).toBe(1); // active

    vi.advanceTimersByTime(POLL_ACTIVE_MS - 1);
    http.expectNone('/api/progress');
    expect(docsReq().length).toBe(0);
    vi.advanceTimersByTime(1);
    expect((await pollOnce(fixture, IDLE)).length).toBe(1); // now idle

    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    http.expectNone('/api/progress');
    expect(docsReq().length).toBe(0);
    vi.advanceTimersByTime(POLL_IDLE_MS - POLL_ACTIVE_MS);
    expect((await pollOnce(fixture, PROGRESS)).length).toBe(1);

    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    expect((await pollOnce(fixture, PROGRESS)).length).toBe(1);
  });

  it('keeps refreshing at the idle cadence when /api/progress fails', async () => {
    progress.start();
    const { fixture } = await render();
    docsReq().forEach((r) => r.flush(page()));
    http.expectOne('/api/progress').flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
    await settle(fixture);
    expect(docsReq().length).toBe(1);
    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    http.expectNone('/api/progress');
  });

  it('Reprocess posts /api/documents/{id}/reprocess and shows pending at once', async () => {
    progress.start();
    const { fixture, el } = await render();
    docsReq().forEach((r) => r.flush(page()));
    await pollOnce(fixture, IDLE);

    const failedRow = el.querySelector('tr[data-doc="8"]')!;
    (failedRow.querySelector('[data-testid=reprocess]') as HTMLButtonElement).click();
    await settle(fixture);
    expect(failedRow.querySelector('[data-testid=doc-status]')!.textContent).toBe('pending'); // optimistic
    expect((failedRow.querySelector('[data-testid=reprocess]') as HTMLButtonElement).disabled).toBe(true);

    const req = http.expectOne('/api/documents/8/reprocess');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBeNull();
    req.flush({ document_id: 8, status: 'pending', attempts: 0, queued_at: '2026-10-16T18:55:00Z' }, { status: 202, statusText: 'Accepted' });
    await settle(fixture);
    expect(info).toContain('northwind_dec25_scanned.pdf queued for reprocessing.');

    // the immediate poll refreshes the list; the server row replaces the optimistic one
    http.expectOne('/api/progress').flush(PROGRESS);
    await settle(fixture);
    const pending = { ...CONTRACT_DOCUMENTS.items[1], status: 'pending' as const, attempts: 0, last_error: null, can_reprocess: false };
    docsReq().forEach((r) => r.flush(page([CONTRACT_DOCUMENTS.items[0], pending])));
    await settle(fixture);
    expect(el.querySelector('tr[data-doc="8"] [data-testid=doc-status]')!.textContent).toBe('pending');
  });

  it('reverts the optimistic status and shows the C2 message when Reprocess fails', async () => {
    const { fixture, el } = await render();
    docsReq().forEach((r) => r.flush(page()));
    await settle(fixture);
    (el.querySelector('tr[data-doc="3"] [data-testid=reprocess]') as HTMLButtonElement).click();
    await settle(fixture);
    expect(el.querySelector('tr[data-doc="3"] [data-testid=doc-status]')!.textContent).toBe('pending');
    http
      .expectOne('/api/documents/3/reprocess')
      .flush(
        { error: { code: 'document_busy', message: 'kestrel_factsheet_2026-01.pdf is already queued for processing.', details: null } },
        { status: 409, statusText: 'Conflict' },
      );
    await settle(fixture);
    docsReq().forEach((r) => r.flush(page()));
    await settle(fixture);
    expect(el.querySelector('tr[data-doc="3"] [data-testid=doc-status]')!.textContent).toBe('done');
    expect(errors).toEqual(['Reprocess failed: kestrel_factsheet_2026-01.pdf is already queued for processing.']);
  });

  it('Sync now and Full reconcile post their endpoints, disable while in flight and toast "requested"', async () => {
    progress.progress.set(IDLE);
    const { fixture, el } = await render();
    docsReq().forEach((r) => r.flush(page()));
    await settle(fixture);

    const sync = el.querySelector('[data-testid=sync-now]') as HTMLButtonElement;
    sync.click();
    await settle(fixture);
    expect(sync.disabled).toBe(true);
    const s = http.expectOne('/api/sync');
    expect(s.request.method).toBe('POST');
    s.flush({ kind: 'sync', mode: 'drive', requested_at: PROGRESS.generated_at, already_pending: false, last_success_at: null }, { status: 202, statusText: 'Accepted' });
    await settle(fixture);
    expect(sync.disabled).toBe(false);
    expect(info.at(-1)).toContain('Sync requested');

    const rec = el.querySelector('[data-testid=reconcile]') as HTMLButtonElement;
    rec.click();
    await settle(fixture);
    expect(rec.disabled).toBe(true);
    const r = http.expectOne('/api/sync/reconcile');
    expect(r.request.method).toBe('POST');
    r.flush({ kind: 'reconcile', mode: 'drive', requested_at: PROGRESS.generated_at, already_pending: true, last_success_at: null }, { status: 202, statusText: 'Accepted' });
    await settle(fixture);
    expect(info.at(-1)).toBe('A full reconcile is already queued.');
  });

  it('shows the C2 message when sync fails (e.g. not connected)', async () => {
    progress.progress.set(IDLE);
    const { fixture, el } = await render();
    docsReq().forEach((r) => r.flush(page()));
    (el.querySelector('[data-testid=sync-now]') as HTMLButtonElement).click();
    http
      .expectOne('/api/sync')
      .flush({ error: { code: 'not_connected', message: 'Google Drive is not connected.', details: null } }, { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    expect(errors).toEqual(['Sync failed: Google Drive is not connected.']);
  });

  it('header: demo folder, last sync, counts by status; a status card filters the list', async () => {
    progress.progress.set(DEMO);
    progress.receivedAt.set(Date.now());
    const { fixture, el } = await render();
    docsReq().forEach((r) => r.flush(page()));
    await settle(fixture);
    expect(el.querySelector('[data-testid=ing-connection]')!.textContent).toContain('Demo mode: watching ./sample_docs');
    expect(el.querySelector('[data-testid=ing-last-sync]')!.textContent).toContain('12s ago');
    expect(el.querySelector('[data-testid=stat-failed] b')!.textContent).toBe('1');
    expect(el.querySelector('[data-testid=stat-needs_review] b')!.textContent).toBe('4');
    expect(el.querySelector('[data-testid=stat-tokens]')!.textContent).toContain('190.3k');

    (el.querySelector('[data-testid=stat-failed]') as HTMLButtonElement).click();
    await settle(fixture);
    const [req] = docsReq();
    expect(req.request.urlWithParams).toBe('/api/documents?status=failed&page=1&page_size=50');
  });

  it('connectionLine: account, demo folder, or not connected', () => {
    expect(connectionLine(PROGRESS)).toBe('sj.demo@gmail.com');
    expect(connectionLine(DEMO)).toBe('Demo mode: watching ./sample_docs');
    expect(connectionLine({ ...PROGRESS, connection: { connected: false, google_email: null } })).toBe('Not connected');
  });

  it('truncate keeps short errors and ellipsizes long ones', () => {
    expect(truncate('short')).toBe('short');
    expect(truncate('x'.repeat(60), 10)).toBe('xxxxxxxxxx…');
  });
});

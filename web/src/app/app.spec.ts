import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { SqlCitation, SourceResponse } from './api/models';
import { App } from './app';
import { routes } from './app.routes';
import { ConfigService } from './core/config.service';
import { NotifyService } from './core/notify.service';
import { ProgressService } from './core/progress.service';
import { ViewportService } from './core/viewport.service';
import { ASK_PANEL_STORAGE_KEY } from './shell/ask-panel.service';
import { NO_MATERIAL_ANIMATIONS, flushAsync } from './testing/dialog';
import { CONTRACT_ASK, DEMO_CONFIG, DRIVE_CONFIG } from './testing/fixtures';
import { fakeViewport } from './testing/viewport';
import { installMemoryStorage } from './testing/storage';

installMemoryStorage();

@Component({ template: '<p data-testid="page">data page</p>' })
class DataStub {}
@Component({ template: '<p data-testid="page">ingestion page</p>' })
class IngestionStub {}
@Component({ template: '<p data-testid="page">review page</p>' })
class ReviewStub {}

const shellRoutes = [
  { path: 'data', component: DataStub },
  { path: 'ingestion', component: IngestionStub },
  { path: 'review', component: ReviewStub },
  routes.find((r) => r.path === 'ask')!,
];

/** CONTRACT_ASK with `return_id` on the SQL rows, so each row gets a Source button. */
function askWithIds() {
  const sql = CONTRACT_ASK.citations[0] as SqlCitation;
  return { ...CONTRACT_ASK, citations: [{ ...sql, columns: [...sql.columns, 'return_id'], rows: sql.rows.map((r, k) => [...r, 101 + k]) }, CONTRACT_ASK.citations[1]] };
}

const KESTREL_SOURCE = {
  table: 'returns',
  id: 101,
  field: 'value',
  label: 'Kestrel Quant Equity Fund LP · Jan 2026 · MTD net',
  field_label: 'Return',
  value: 0.0284,
  value_pct: 2.84,
  value_kind: 'percent',
  unit: null,
  currency: null,
  status: 'ok',
  review_reasons: [],
  reasons: [],
  provenance: { page: 1, source_snippet: 'MTD % (net) +2.84%', confidence: 0.97, snippet_verified: true },
  snippet_highlight: { start: 12, end: 18, text: '+2.84%' },
  document: {
    document_id: 3,
    document_name: 'kestrel_factsheet_2026-01.pdf',
    document_type: 'factsheet',
    mime_type: 'application/pdf',
    source: 'drive',
    web_view_link: null,
    version: 1,
    processed_at: '2026-10-16T18:44:31Z',
  },
  extraction: { extraction_id: 3, model: 'claude-opus-5-5', prompt_version: 'v1', source: 'claude', created_at: '2026-10-16T18:44:30Z' },
  conflicts: [],
  review_history: [],
} satisfies SourceResponse;

describe('App shell with the docked Ask panel (U9)', () => {
  let http: HttpTestingController;
  let router: Router;
  let viewport: ReturnType<typeof fakeViewport>;
  let fixture: ComponentFixture<App>;
  let el: HTMLElement;

  async function settle() {
    await fixture.whenStable();
    await flushAsync();
    await fixture.whenStable();
  }

  async function boot(opts: { wide?: boolean; config?: typeof DRIVE_CONFIG; url?: string; prefs?: object } = {}) {
    localStorage.clear();
    if (opts.prefs) localStorage.setItem(ASK_PANEL_STORAGE_KEY, JSON.stringify(opts.prefs));
    viewport = fakeViewport(opts.wide ?? true);
    vi.spyOn(ProgressService.prototype, 'start').mockImplementation(() => undefined);
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter(shellRoutes), viewport.provider, NO_MATERIAL_ANIMATIONS],
    });
    vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
    TestBed.inject(ConfigService).config.set(opts.config ?? DRIVE_CONFIG);
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(App);
    el = fixture.nativeElement as HTMLElement;
    await router.navigateByUrl(opts.url ?? '/data');
    await settle();
  }

  const q = <T extends Element = HTMLElement>(sel: string) => el.querySelector<T & HTMLElement>(sel);
  const dock = () => q('[data-testid=ask-dock]')!;
  const toggle = () => q<HTMLButtonElement>('[data-testid=ask-toggle]')!;
  const page = () => q('[data-testid=page]')?.textContent;

  async function go(url: string) {
    await router.navigateByUrl(url);
    await settle();
  }

  afterEach(() => vi.restoreAllMocks());

  it('the header Ask button opens and closes the docked panel beside the main area', async () => {
    await boot();
    expect(dock().hidden).toBe(true);
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    toggle().click();
    await settle();
    expect(dock().hidden).toBe(false);
    expect(dock().dataset['mode']).toBe('docked');
    expect(dock().style.width).toBe('440px');
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    expect(q('[data-testid=ask-pinned]')).not.toBeNull();
    // the main area still renders the routed page next to the panel
    expect(page()).toBe('data page');
    expect(q('[data-testid=ask-input]')).not.toBeNull();
    q<HTMLButtonElement>('[data-testid=ask-close]')!.click();
    await settle();
    expect(dock().hidden).toBe(true);
  });

  it('pinned: the conversation survives navigation mid-request: one POST, and the answer renders afterwards', async () => {
    await boot();
    toggle().click();
    await settle();
    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    const req = http.expectOne('/api/ask');
    expect(req.request.body.question).toBe('Which fund had the best January return?');
    expect(q('[data-testid=ask-pending]')).not.toBeNull();

    for (const [url, text] of [
      ['/ingestion', 'ingestion page'],
      ['/review', 'review page'],
      ['/data', 'data page'],
    ]) {
      await go(url);
      expect(page()).toBe(text);
      expect(dock().hidden).toBe(false);
      expect(q('[data-testid=ask-pending]')).not.toBeNull();
    }
    expect(req.cancelled).toBe(false);
    http.expectNone('/api/ask');

    req.flush(CONTRACT_ASK);
    await settle();
    expect(q('[data-testid=ask-pending]')).toBeNull();
    expect(el.querySelectorAll('[data-testid=question]').length).toBe(1);
    expect(el.querySelectorAll('app-citation-card').length).toBe(2);

    // and still there after more navigation; nothing is re-sent
    await go('/review');
    expect(el.querySelectorAll('app-citation-card').length).toBe(2);
    http.expectNone('/api/ask');
    http.verify();
  });

  it('unpinned: clicking another tab collapses the panel; the conversation comes back when reopened', async () => {
    await boot({ prefs: { pinned: false, open: true } });
    expect(dock().hidden).toBe(false);
    expect(q<HTMLButtonElement>('[data-testid=ask-pin]')!.getAttribute('aria-pressed')).toBe('false');
    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    const req = http.expectOne('/api/ask');
    q<HTMLAnchorElement>('nav a[href="/review"]')!.click();
    await settle();
    expect(page()).toBe('review page');
    expect(dock().hidden).toBe(true);
    // the request keeps going while collapsed
    req.flush(CONTRACT_ASK);
    toggle().click();
    await settle();
    expect(el.querySelectorAll('app-citation-card').length).toBe(2);
    // pin it: now navigation keeps it open
    q<HTMLButtonElement>('[data-testid=ask-pin]')!.click();
    await settle();
    expect(JSON.parse(localStorage.getItem(ASK_PANEL_STORAGE_KEY)!).pinned).toBe(true);
    q<HTMLAnchorElement>('nav a[href="/ingestion"]')!.click();
    await settle();
    expect(page()).toBe('ingestion page');
    expect(dock().hidden).toBe(false);
  });

  it('a passage link navigates the main area to Data without closing an unpinned panel', async () => {
    await boot({ url: '/review', prefs: { pinned: false, open: true } });
    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await settle();
    q<HTMLAnchorElement>('[data-testid=passage-data-link], a[data-testid=passage-link]:not([target])')!.click();
    await settle();
    expect(router.url).toContain('/data?document_id=');
    expect(dock().hidden).toBe(false);
  });

  it('citation → source: the sheet opens in the main area left of the open Ask panel; Escape closes only the sheet', async () => {
    await boot();
    toggle().click();
    await settle();
    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    http.expectOne('/api/ask').flush(askWithIds());
    await settle();
    q<HTMLButtonElement>('[data-testid=row-source]')!.click();
    await settle();
    http.expectOne('/api/sources/returns/101').flush(KESTREL_SOURCE);
    await settle();

    const sheet = q('[data-testid=ask-source-sheet]')!;
    expect(sheet).not.toBeNull();
    expect(dock().contains(sheet)).toBe(false);
    expect(sheet.style.right).toBe('440px');
    expect(sheet.querySelector('[data-testid=pv-value]')!.textContent).toContain('+2.84%');
    expect(sheet.querySelector('[data-testid=pv-doc]')!.textContent).toContain('kestrel_factsheet_2026-01.pdf');
    expect(dock().hidden).toBe(false);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle();
    expect(q('[data-testid=ask-source-sheet]')).toBeNull();
    expect(dock().hidden).toBe(false);

    // reopen, then closing the Ask panel takes the sheet with it
    q<HTMLButtonElement>('[data-testid=row-source]')!.click();
    await settle();
    http.expectOne('/api/sources/returns/101').flush(KESTREL_SOURCE);
    await settle();
    q<HTMLButtonElement>('[data-testid=ask-close]')!.click();
    await settle();
    expect(q('[data-testid=ask-source-sheet]')).toBeNull();
  });

  it('New conversation in the panel title bar resets the session and the composer', async () => {
    await boot({ prefs: { open: true } });
    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await settle();
    q<HTMLButtonElement>('[data-testid=new-conversation]')!.click();
    await settle();
    expect(el.querySelectorAll('section.turn').length).toBe(0);
    expect(q('[data-testid=new-conversation]')).toBeNull();
    expect(el.querySelectorAll('[data-testid=example]').length).toBe(3);
  });

  it('disabled (no key): the header button is dimmed but still opens the panel, which shows the reason', async () => {
    await boot({ config: DEMO_CONFIG });
    expect(toggle().classList).toContain('disabled');
    expect(toggle().getAttribute('title')).toContain('Ask is disabled because ANTHROPIC_API_KEY is not set.');
    toggle().click();
    await settle();
    expect(dock().hidden).toBe(false);
    expect(q('[data-testid=ask-disabled]')!.textContent).toContain('ANTHROPIC_API_KEY is not set');
    expect(q<HTMLTextAreaElement>('[data-testid=ask-input]')!.disabled).toBe(true);
    expect(dock().querySelector('.st.rev')!.textContent).toContain('unavailable');
    http.expectNone('/api/ask');
  });

  it('/ask deep link: opens the panel and shows Data in the main area', async () => {
    await boot({ url: '/ask' });
    expect(router.url).toBe('/data');
    expect(page()).toBe('data page');
    expect(dock().hidden).toBe(false);
  });

  it('responsive: below 1024 px the panel is an overlay drawer (backdrop, pin disabled); wide again docks it', async () => {
    await boot();
    toggle().click();
    await settle();
    viewport.set(false);
    await settle();
    // switching to narrow closes it (it would cover the page)
    expect(dock().hidden).toBe(true);
    toggle().click();
    await settle();
    expect(dock().dataset['mode']).toBe('overlay');
    expect(dock().classList).toContain('overlay');
    expect(dock().getAttribute('role')).toBe('dialog');
    expect(dock().style.width).toBe('');
    expect(q('[data-testid=ask-resize]')).toBeNull();
    expect(q<HTMLButtonElement>('[data-testid=ask-pin]')!.disabled).toBe(true);
    q('[data-testid=ask-backdrop]')!.click();
    await settle();
    expect(dock().hidden).toBe(true);
    expect(q('[data-testid=ask-backdrop]')).toBeNull();

    viewport.set(true);
    await settle();
    expect(dock().hidden).toBe(false);
    expect(dock().dataset['mode']).toBe('docked');
    expect(q('[data-testid=ask-resize]')).not.toBeNull();
  });

  it('the resize handle changes and persists the width from the keyboard, over the window-based range', async () => {
    await boot({ prefs: { open: true } });
    TestBed.inject(ViewportService).width.set(1440);
    await settle();
    const handle = q('[data-testid=ask-resize]')!;
    const key = async (k: string, shiftKey = false) => {
      handle.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey, bubbles: true }));
      await settle();
    };
    expect(handle.getAttribute('aria-valuemin')).toBe('320');
    expect(handle.getAttribute('aria-valuemax')).toBe('1080');
    await key('ArrowLeft');
    expect(dock().style.width).toBe('464px');
    expect(handle.getAttribute('aria-valuenow')).toBe('464');
    expect(JSON.parse(localStorage.getItem(ASK_PANEL_STORAGE_KEY)!).width).toBe(464);
    await key('ArrowLeft', true);
    expect(dock().style.width).toBe('560px');
    await key('PageDown');
    expect(dock().style.width).toBe('464px');
    await key('End');
    expect(dock().style.width).toBe('1080px');
    expect(JSON.parse(localStorage.getItem(ASK_PANEL_STORAGE_KEY)!).width).toBe(1080);
    await key('ArrowLeft');
    expect(dock().style.width).toBe('1080px');
    await key('Home');
    expect(dock().style.width).toBe('320px');
    await key('ArrowRight');
    expect(dock().style.width).toBe('320px');
  });

  it('window resize re-clamps a wide panel and aria-valuemax follows; the source sheet stays beside it', async () => {
    await boot({ prefs: { open: true, width: 1080 } });
    const vp = TestBed.inject(ViewportService);
    vp.width.set(1440);
    await settle();
    const handle = q('[data-testid=ask-resize]')!;
    expect(dock().style.width).toBe('1080px');
    vp.width.set(1100);
    await settle();
    expect(dock().style.width).toBe('740px');
    expect(handle.getAttribute('aria-valuemax')).toBe('740');
    expect(handle.getAttribute('aria-valuenow')).toBe('740');

    q<HTMLButtonElement>('[data-testid=example]')!.click();
    await settle();
    http.expectOne('/api/ask').flush(askWithIds());
    await settle();
    q<HTMLButtonElement>('[data-testid=row-source]')!.click();
    await settle();
    http.expectOne('/api/sources/returns/101').flush(KESTREL_SOURCE);
    await settle();
    const sheet = q('[data-testid=ask-source-sheet]')!;
    expect(sheet.dataset['placement']).toBe('beside');
    expect(sheet.style.right).toBe('740px');
    expect(sheet.style.width).toBe('360px');
    expect(sheet.textContent).toContain('+2.84%');
    vp.width.set(1440);
    await settle();
    expect(dock().style.width).toBe('1080px');
    expect(sheet.style.right).toBe('1080px');
    // the stored choice was never overwritten by the narrower window
    expect(JSON.parse(localStorage.getItem(ASK_PANEL_STORAGE_KEY)!).width).toBe(1080);
  });
});

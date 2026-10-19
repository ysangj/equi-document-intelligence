import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { DriveFoldersResponse } from '../../api/models';
import { ConfigService } from '../../core/config.service';
import { NotifyService } from '../../core/notify.service';
import { NO_MATERIAL_ANIMATIONS, clickDialog, dialogText, flushAsync } from '../../testing/dialog';
import { AUTH_CONNECTED, AUTH_DEMO, AUTH_DISCONNECTED, DEMO_CONFIG, DRIVE_CONFIG, DRIVE_FUND_DOCS_EMPTY, DRIVE_ROOT } from '../../testing/fixtures';
import { FolderPickerPageComponent } from './folder-picker-page.component';

describe('FolderPickerPageComponent (/connect/folder, FR-3)', () => {
  let http: HttpTestingController;
  let nav: ReturnType<typeof vi.spyOn>;
  let info: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS] });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(ConfigService).config.set(DRIVE_CONFIG);
    nav = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    info = vi.spyOn(TestBed.inject(NotifyService), 'info').mockImplementation(() => undefined);
    vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
  });

  afterEach(() => document.querySelectorAll('.cdk-overlay-container').forEach((n) => (n.innerHTML = '')));

  async function settle(fixture: { whenStable(): Promise<unknown> }) {
    await flushAsync();
    await fixture.whenStable();
  }

  async function render(status: object = { ...AUTH_CONNECTED, folder: null }) {
    const fixture = TestBed.createComponent(FolderPickerPageComponent);
    await fixture.whenStable();
    http.expectOne('/api/auth/status').flush(status);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  /** breadcrumb labels joined with " › " */
  const crumbs = (el: HTMLElement) =>
    Array.from(el.querySelectorAll('[data-testid=crumbs] > button.link, [data-testid=crumbs] > b'))
      .map((n) => n.textContent!.trim())
      .join(' › ');
  const rows = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLElement>('[data-testid=folder-row]'));

  it('starts at My Drive, says the watch is non-recursive and lists folders', async () => {
    const { fixture, el } = await render();
    const req = http.expectOne((r) => r.url === '/api/drive/folders');
    expect(req.request.urlWithParams).toBe('/api/drive/folders?parent=root');
    expect(el.querySelector('[aria-busy=true]')).not.toBeNull();
    req.flush(DRIVE_ROOT);
    await settle(fixture);
    expect(el.querySelector('[data-testid=non-recursive]')!.textContent).toContain('only files directly in this folder');
    // T7: only My Drive is browsable
    expect(el.querySelector('[data-testid=my-drive-only]')!.textContent).toContain("My Drive only: shared folders and shared drives aren't listed");
    expect(crumbs(el)).toBe('My Drive');
    expect(rows(el).map((r) => r.querySelector('.name')!.textContent)).toEqual(['Fund Docs', 'Tax archive']);
    expect(el.querySelector('[data-testid=up]')).toBeNull();
    expect(el.querySelector<HTMLButtonElement>('[data-testid=watch]')!.disabled).toBe(true);
  });

  it('opens a folder with ?parent=, shows breadcrumbs, and a crumb goes back up', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/drive/folders').flush(DRIVE_ROOT);
    await settle(fixture);
    rows(el)[0].querySelector<HTMLButtonElement>('[data-testid=open]')!.click();
    await settle(fixture);
    const req = http.expectOne((r) => r.url === '/api/drive/folders');
    expect(req.request.urlWithParams).toBe('/api/drive/folders?parent=1Q8vXk2mFundDocs');
    expect(el.querySelector('[data-testid=folder-list]')!.textContent).toContain('Loading folders');
    req.flush({ ...DRIVE_FUND_DOCS_EMPTY, items: [{ folder_id: '1Fd2025', folder_name: '2025 archive', modified_at: null }] });
    await settle(fixture);
    expect(crumbs(el)).toBe('My Drive › Fund Docs');
    expect(rows(el).map((r) => r.querySelector('.name')!.textContent)).toEqual(['2025 archive']);
    el.querySelector<HTMLButtonElement>('[data-testid=crumbs] button.link')!.click();
    await settle(fixture);
    http.expectOne('/api/drive/folders?parent=root').flush(DRIVE_ROOT);
    await settle(fixture);
    expect(crumbs(el)).toBe('My Drive');
  });

  it('empty folder: offers to select the folder itself; crumb and Up return to the parent', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/drive/folders').flush(DRIVE_ROOT);
    await settle(fixture);
    rows(el)[0].dispatchEvent(new Event('dblclick'));
    await settle(fixture);
    http.expectOne('/api/drive/folders?parent=1Q8vXk2mFundDocs').flush(DRIVE_FUND_DOCS_EMPTY);
    await settle(fixture);
    expect(el.querySelector('[data-testid=picker-empty]')!.textContent).toContain('No subfolders here');
    el.querySelector<HTMLButtonElement>('[data-testid=select-current]')!.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid=selection]')!.textContent).toContain('Fund Docs');

    el.querySelector<HTMLButtonElement>('[data-testid=up]')!.click();
    await settle(fixture);
    http.expectOne('/api/drive/folders?parent=root').flush(DRIVE_ROOT);
    await settle(fixture);
    expect(crumbs(el)).toBe('My Drive');
    expect(el.querySelector('[data-testid=selection]')).toBeNull();
  });

  it('pages with "Load more" using next_page_token', async () => {
    const { fixture, el } = await render();
    const page1: DriveFoldersResponse = { ...DRIVE_ROOT, next_page_token: 'tok2' };
    http.expectOne((r) => r.url === '/api/drive/folders').flush(page1);
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid=load-more]')!.click();
    await settle(fixture);
    http
      .expectOne('/api/drive/folders?parent=root&page_token=tok2')
      .flush({ ...DRIVE_ROOT, items: [{ folder_id: '1x', folder_name: 'Zeta', modified_at: null }], next_page_token: null });
    await settle(fixture);
    expect(rows(el).length).toBe(3);
    expect(el.querySelector('[data-testid=load-more]')).toBeNull();
  });

  it('select → confirm → POST /api/drive/watch {folder_id} → Ingestion', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/drive/folders').flush(DRIVE_ROOT);
    await settle(fixture);
    rows(el)[0].click();
    await settle(fixture);
    expect(rows(el)[0].getAttribute('aria-selected')).toBe('true');
    el.querySelector<HTMLButtonElement>('[data-testid=watch]')!.click();
    await settle(fixture);
    expect(dialogText()).toContain('Watch “Fund Docs”?');
    expect(dialogText()).toContain('Only files directly in “Fund Docs” are processed');
    clickDialog('confirm-ok');
    await settle(fixture);
    const req = http.expectOne('/api/drive/watch');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ folder_id: '1Q8vXk2mFundDocs' });
    req.flush({ folder: { source: 'drive', folder_id: '1Q8vXk2mFundDocs', folder_name: 'Fund Docs' }, reconcile_requested_at: '2026-10-16T18:41:15Z' });
    await settle(fixture);
    expect(nav).toHaveBeenCalledWith('/ingestion');
    expect(info).toHaveBeenCalledWith('Watching “Fund Docs”. A full reconcile was requested.');
  });

  it('warns that switching folders removes the old documents; cancel sends nothing', async () => {
    const { fixture, el } = await render(AUTH_CONNECTED);
    expect(el.querySelector('[data-testid=current-watch]')!.textContent).toContain('Fund Docs');
    http.expectOne((r) => r.url === '/api/drive/folders').flush(DRIVE_ROOT);
    await settle(fixture);
    rows(el)[1].click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid=watch]')!.click();
    await settle(fixture);
    expect(dialogText()).toContain('Documents from “Fund Docs” will be marked removed');
    clickDialog('confirm-cancel');
    await settle(fixture);
    http.expectNone('/api/drive/watch');
  });

  it('a watch error is shown inline, with a Connect link for 409 not_connected', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/drive/folders').flush(DRIVE_ROOT);
    await settle(fixture);
    rows(el)[0].click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('[data-testid=watch]')!.click();
    await settle(fixture);
    clickDialog('confirm-ok');
    await settle(fixture);
    http.expectOne('/api/drive/watch').flush({ error: { code: 'not_connected', message: 'Google Drive is not connected.', details: null } }, { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    const err = el.querySelector('[data-testid=watch-error]')!;
    expect(err.textContent).toContain('Google Drive is not connected.');
    expect(err.querySelector('a')!.getAttribute('href')).toBe('/connect');
    expect(nav).not.toHaveBeenCalledWith('/ingestion');
  });

  it('not connected: points to /connect and lists nothing', async () => {
    const { el } = await render(AUTH_DISCONNECTED);
    http.expectNone((r) => r.url === '/api/drive/folders');
    expect(el.querySelector('[data-testid=picker-not-connected] a')!.getAttribute('href')).toBe('/connect');
  });

  it('a folders error shows the C2 message with Retry', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/drive/folders').flush({ error: { code: 'drive_error', message: 'The Google Drive API failed.', details: null } }, { status: 502, statusText: 'Bad Gateway' });
    await settle(fixture);
    expect(el.querySelector('[data-testid=picker-error]')!.textContent).toContain('The Google Drive API failed.');
    el.querySelector<HTMLButtonElement>('[data-testid=picker-error] button')!.click();
    await settle(fixture);
    http.expectOne('/api/drive/folders?parent=root').flush(DRIVE_ROOT);
  });

  it('demo mode (config): no status or folders request, the drive reason, no My Drive note', async () => {
    TestBed.inject(ConfigService).config.set(DEMO_CONFIG);
    const fixture = TestBed.createComponent(FolderPickerPageComponent);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    http.expectNone('/api/auth/status');
    http.expectNone((r) => r.url === '/api/drive/folders');
    expect(el.querySelector('[data-testid=picker-demo]')!.textContent).toBe(DEMO_CONFIG.features.drive.reason);
    expect(el.querySelector('[data-testid=my-drive-only]')).toBeNull();
    expect(el.querySelector('[data-testid=folder-list]')).toBeNull();
  });

  it('demo mode from the api: status mode "demo" lists nothing; a 409 demo_mode listing shows the demo state, not an error', async () => {
    const { el } = await render(AUTH_DEMO);
    http.expectNone((r) => r.url === '/api/drive/folders');
    expect(el.querySelector('[data-testid=picker-demo]')).not.toBeNull();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS] });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(ConfigService).config.set(DRIVE_CONFIG);
    const r2 = await render();
    http
      .expectOne((r) => r.url === '/api/drive/folders')
      .flush({ error: { code: 'demo_mode', message: 'Demo mode: Google Drive is not used.', details: null } }, { status: 409, statusText: 'Conflict' });
    await settle(r2.fixture);
    expect(r2.el.querySelector('[data-testid=picker-demo]')).not.toBeNull();
    expect(r2.el.querySelector('[data-testid=picker-error]')).toBeNull();
  });

  it('an api without the Drive endpoints shows the not-available state', async () => {
    const fixture = TestBed.createComponent(FolderPickerPageComponent);
    await fixture.whenStable();
    http.expectOne('/api/auth/status').flush({ error: { code: 'not_found', message: 'Not found.', details: null } }, { status: 404, statusText: 'Not Found' });
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid=picker-error]')!.textContent).toContain('not available on the api yet');
    http.expectNone((r) => r.url === '/api/drive/folders');
  });
});

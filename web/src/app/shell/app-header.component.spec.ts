import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AuthService } from '../core/auth.service';
import { ConfigService } from '../core/config.service';
import { ProgressService } from '../core/progress.service';
import { NO_MATERIAL_ANIMATIONS, flushAsync } from '../testing/dialog';
import { AUTH_CONNECTED, DEMO_CONFIG, PROGRESS } from '../testing/fixtures';
import { AppHeaderComponent } from './app-header.component';
import { AskPanelService } from './ask-panel.service';
import { installMemoryStorage } from '../testing/storage';

installMemoryStorage();

describe('AppHeaderComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS],
    });
    http = TestBed.inject(HttpTestingController);
  });

  async function render() {
    const fixture = TestBed.createComponent(AppHeaderComponent);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('shows account, folder, last sync and the Review badge from /api/progress', async () => {
    TestBed.inject(ProgressService).progress.set(PROGRESS);
    TestBed.inject(ProgressService).receivedAt.set(Date.now());
    const { el } = await render();
    const conn = el.querySelector('[data-testid=conn]')!.textContent!;
    expect(conn).toContain('sj.demo@gmail.com');
    expect(conn).toContain('Folder: Fund Docs');
    expect(conn).toMatch(/synced 1\ds ago/);
    expect(el.querySelector('[data-testid=review-badge]')!.textContent!.trim()).toBe('6');
  });

  it('Ask is a panel toggle, not a tab: no /ask link; aria-expanded follows the panel', async () => {
    localStorage.clear();
    const { el, fixture } = await render();
    expect(el.querySelector('nav a[href="/ask"]')).toBeNull();
    expect(Array.from(el.querySelectorAll('nav a')).map((a) => a.textContent!.trim().split(/\s/)[0])).toEqual(['Data', 'Ingestion', 'Review']);
    const ask = el.querySelector<HTMLButtonElement>('[data-testid=ask-toggle]')!;
    expect(ask.getAttribute('aria-controls')).toBe('ask-panel');
    expect(ask.getAttribute('aria-expanded')).toBe('false');
    expect(ask.getAttribute('title')).toBe('Open the Ask panel');
    ask.click();
    await fixture.whenStable();
    expect(TestBed.inject(AskPanelService).open()).toBe(true);
    expect(ask.getAttribute('aria-expanded')).toBe('true');
    expect(ask.classList).toContain('open');
    expect(el.querySelector('[data-testid=ask-pinned]')).not.toBeNull();
    ask.click();
    await fixture.whenStable();
    expect(TestBed.inject(AskPanelService).open()).toBe(false);
  });

  it('marks Ask as unavailable with the config reason, but the button still opens the panel', async () => {
    localStorage.clear();
    TestBed.inject(ConfigService).config.set(DEMO_CONFIG);
    const { el, fixture } = await render();
    const ask = el.querySelector<HTMLButtonElement>('[data-testid=ask-toggle]')!;
    expect(ask.classList).toContain('disabled');
    expect(ask.disabled).toBe(false);
    expect(ask.getAttribute('title')).toBe('Ask is disabled because ANTHROPIC_API_KEY is not set. Open the Ask panel to see why.');
    ask.click();
    await fixture.whenStable();
    expect(TestBed.inject(AskPanelService).open()).toBe(true);
  });

  it('shows Demo instead of an account in demo mode and hides the badge at 0', async () => {
    TestBed.inject(ProgressService).progress.set({
      ...PROGRESS,
      mode: 'demo',
      review_open: 0,
      connection: { connected: false, google_email: null },
      sync: { ...PROGRESS.sync, source: 'local', folder_id: null, folder_name: 'sample_docs' },
    });
    const { el } = await render();
    expect(el.querySelector('[data-testid=conn]')!.textContent).toContain('Demo');
    expect(el.querySelector('[data-testid=conn]')!.textContent).toContain('sample_docs');
    expect(el.querySelector('[data-testid=review-badge]')).toBeNull();
  });

  it('"Sync now" posts /api/sync', async () => {
    TestBed.inject(ProgressService).progress.set(PROGRESS);
    const { el, fixture } = await render();
    el.querySelector<HTMLButtonElement>('[data-testid=header-sync]')!.click();
    const req = http.expectOne('/api/sync');
    expect(req.request.method).toBe('POST');
    req.flush({ kind: 'sync', mode: 'drive', requested_at: PROGRESS.generated_at, already_pending: false, last_success_at: null });
    await fixture.whenStable();
  });

  it('the account menu offers Change folder and Disconnect (confirmed in AuthService)', async () => {
    TestBed.inject(ProgressService).progress.set(PROGRESS);
    const confirm = vi.spyOn(TestBed.inject(AuthService), 'confirmDisconnect').mockResolvedValue(true);
    const { el, fixture } = await render();
    expect(el.querySelector('[data-testid=header-folder]')!.getAttribute('title')).toContain('only files directly in it');
    el.querySelector<HTMLButtonElement>('[data-testid=account]')!.click();
    await fixture.whenStable();
    await flushAsync();
    const items = Array.from(document.querySelectorAll('.mat-mdc-menu-item')).map((i) => i.textContent!.trim());
    expect(items).toEqual(['Change folder', 'Connection settings', 'Disconnect…']);
    // opening the menu loads /api/auth/status once for the granted scopes
    http.expectOne('/api/auth/status').flush(AUTH_CONNECTED);
    await flushAsync();
    await fixture.whenStable();
    const scope = document.querySelector('[data-testid=header-scope]')!;
    expect(scope.textContent!.trim()).toBe('Read-only access to Google Drive');
    expect(scope.getAttribute('title')).toBe(AUTH_CONNECTED.scopes.join('\n'));
    document.querySelector<HTMLButtonElement>('[data-testid=header-disconnect]')!.click();
    expect(confirm).toHaveBeenCalled();
    document.querySelectorAll('.cdk-overlay-container').forEach((n) => (n.innerHTML = ''));
  });

  it('the account menu reuses an already loaded status (no extra request)', async () => {
    TestBed.inject(ProgressService).progress.set(PROGRESS);
    TestBed.inject(AuthService).status.set(AUTH_CONNECTED);
    const { el, fixture } = await render();
    el.querySelector<HTMLButtonElement>('[data-testid=account]')!.click();
    await fixture.whenStable();
    await flushAsync();
    http.expectNone('/api/auth/status');
    expect(document.querySelector('[data-testid=header-scope]')!.textContent!.trim()).toBe('Read-only access to Google Drive');
    document.querySelectorAll('.cdk-overlay-container').forEach((n) => (n.innerHTML = ''));
  });

  it('Drive mode: not connected links to /connect; connected without a folder links to the picker', async () => {
    const progress = TestBed.inject(ProgressService);
    progress.progress.set({ ...PROGRESS, connection: { connected: false, google_email: null }, sync: { ...PROGRESS.sync, folder_id: null, folder_name: null } });
    const { el, fixture } = await render();
    expect(el.querySelector('[data-testid=header-connect]')!.getAttribute('href')).toBe('/connect');
    progress.progress.set({ ...PROGRESS, sync: { ...PROGRESS.sync, folder_id: null, folder_name: null } });
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=header-choose-folder]')!.getAttribute('href')).toBe('/connect/folder');
  });
});

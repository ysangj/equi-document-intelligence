import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { OAuthErrorCode } from '../../api/models';
import { AuthService } from '../../core/auth.service';
import { BrowserLocation } from '../../core/browser-location';
import { ConfigService } from '../../core/config.service';
import { NotifyService } from '../../core/notify.service';
import { NO_MATERIAL_ANIMATIONS, clickDialog, dialogText, flushAsync } from '../../testing/dialog';
import { AUTH_CONNECTED, AUTH_DISCONNECTED, DEMO_CONFIG, DRIVE_CONFIG } from '../../testing/fixtures';
import { CONNECT_ERRORS, connectErrorMessage } from './connect-errors';
import { ConnectPageComponent } from './connect-page.component';

describe('ConnectPageComponent (/connect, FR-2)', () => {
  let http: HttpTestingController;
  let assign: ReturnType<typeof vi.fn>;
  let info: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    assign = vi.fn();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS, { provide: BrowserLocation, useValue: { assign } }],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(ConfigService).config.set(DRIVE_CONFIG);
    info = vi.spyOn(TestBed.inject(NotifyService), 'info').mockImplementation(() => undefined);
    vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
  });

  afterEach(() => document.querySelectorAll('.cdk-overlay-container').forEach((n) => (n.innerHTML = '')));

  async function render(error?: string) {
    const fixture = TestBed.createComponent(ConnectPageComponent);
    if (error) fixture.componentRef.setInput('error', error);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  async function flushStatus(fixture: { whenStable(): Promise<unknown> }, body: object, status = 200) {
    http.expectOne('/api/auth/status').flush(body, { status, statusText: status === 200 ? 'OK' : 'Not Found' });
    await flushAsync();
    await fixture.whenStable();
  }

  it('shows "Not connected" from /api/auth/status; Connect does a full-page navigation to the login URL', async () => {
    const { fixture, el } = await render();
    await flushStatus(fixture, AUTH_DISCONNECTED);
    expect(el.querySelector('[data-testid=connect-disconnected]')!.textContent).toContain('Not connected');
    expect(el.querySelector('[data-testid=connect-disconnected]')!.textContent).toContain('read-only access to Google Drive');
    expect(el.querySelector('[data-testid=connect-disconnected]')!.textContent).not.toContain('drive.readonly');
    el.querySelector<HTMLButtonElement>('[data-testid=connect-btn]')!.click();
    expect(assign).toHaveBeenCalledWith('/api/auth/google/login');
    // a real redirect, not an XHR
    http.expectNone('/api/auth/google/login');
  });

  it('shows the account, scope and watched folder when connected (non-recursive note)', async () => {
    const { fixture, el } = await render();
    await flushStatus(fixture, AUTH_CONNECTED);
    const box = el.querySelector('[data-testid=connect-connected]')!;
    expect(box.textContent).toContain('Connected as sj.demo@gmail.com');
    // the real api's three scopes (drive.readonly + identity) read as one sentence; the raw URLs are in a tooltip and details
    const scope = el.querySelector('[data-testid=connect-scope]')!;
    const label = scope.querySelector('span')!;
    expect(label.textContent!.trim()).toBe('Read-only access to Google Drive');
    expect(label.getAttribute('title')).toBe(AUTH_CONNECTED.scopes.join('\n'));
    const details = scope.querySelector<HTMLDetailsElement>('[data-testid=connect-scope-details]')!;
    expect(details.open).toBe(false);
    expect(Array.from(details.querySelectorAll('li code')).map((c) => c.textContent)).toEqual(AUTH_CONNECTED.scopes);
    // the sentence itself never shows a scope URL
    expect(label.textContent).not.toContain('googleapis.com');
    expect(el.querySelector('[data-testid=connect-folder]')!.textContent).toContain('Fund Docs');
    expect(el.querySelector('[data-testid=connect-folder]')!.textContent).toContain('only files directly in this folder');
    expect(el.querySelector('[data-testid=choose-folder]')!.getAttribute('href')).toBe('/connect/folder');
  });

  it.each(Object.keys(CONNECT_ERRORS) as OAuthErrorCode[])('shows a friendly message for ?error=%s', async (code) => {
    const { fixture, el } = await render(code);
    if (code !== 'demo_mode') await flushStatus(fixture, AUTH_DISCONNECTED);
    expect(el.querySelector('[data-testid=connect-error]')!.textContent).toBe(CONNECT_ERRORS[code]);
  });

  it('messages: unknown codes name the code; no code, no message', () => {
    expect(connectErrorMessage('weird_thing')).toBe('Connecting Google Drive failed (weird_thing). Please try again.');
    expect(connectErrorMessage(undefined)).toBeNull();
    expect(CONNECT_ERRORS.access_denied).toContain('cancelled');
  });

  it('demo mode explains itself with features.drive.reason and hides Connect', async () => {
    TestBed.inject(ConfigService).config.set(DEMO_CONFIG);
    const { el } = await render();
    http.expectNone('/api/auth/status');
    expect(el.querySelector('[data-testid=connect-demo]')!.textContent).toContain(DEMO_CONFIG.features.drive.reason!);
    expect(el.querySelector('[data-testid=connect-btn]')).toBeNull();
  });

  it.each([
    ['FastAPI default body', { detail: 'Not Found' }],
    ['C2 not_found envelope', { error: { code: 'not_found', message: 'Not found.', details: null } }],
  ])('shows the not-available state when the api has no auth endpoints yet (%s)', async (_label, body) => {
    const { fixture, el } = await render();
    await flushStatus(fixture, body, 404);
    expect(el.querySelector('[data-testid=connect-unavailable]')!.textContent).toContain('not available on the api yet');
    expect(el.querySelector('[data-testid=connect-btn]')).toBeNull();
  });

  it('Disconnect confirms, posts /api/auth/disconnect and returns to /connect', async () => {
    const router = TestBed.inject(Router);
    const nav = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const { fixture, el } = await render();
    await flushStatus(fixture, AUTH_CONNECTED);

    el.querySelector<HTMLButtonElement>('[data-testid=disconnect]')!.click();
    await flushAsync();
    expect(dialogText()).toContain('Disconnect Google Drive?');
    expect(dialogText()).toContain('sj.demo@gmail.com');
    clickDialog('confirm-cancel');
    await flushAsync();
    http.expectNone('/api/auth/disconnect');

    el.querySelector<HTMLButtonElement>('[data-testid=disconnect]')!.click();
    await flushAsync();
    clickDialog('confirm-ok');
    await flushAsync();
    const req = http.expectOne('/api/auth/disconnect');
    expect(req.request.method).toBe('POST');
    req.flush({ connected: false });
    await flushAsync();
    await fixture.whenStable();
    expect(nav).toHaveBeenCalledWith('/connect');
    expect(info).toHaveBeenCalledWith('Disconnected from Google Drive.');
    expect(TestBed.inject(AuthService).status()!.connected).toBe(false);
    expect(el.querySelector('[data-testid=connect-disconnected]')).not.toBeNull();
  });
});

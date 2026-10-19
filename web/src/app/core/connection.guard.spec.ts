import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { AUTH_CONNECTED, AUTH_DEMO, AUTH_DISCONNECTED, DEMO_CONFIG, DRIVE_CONFIG } from '../testing/fixtures';
import { flushAsync } from '../testing/dialog';
import { AuthService, connectTarget } from './auth.service';
import { ConfigService } from './config.service';
import { connectionGuard } from './connection.guard';

@Component({ template: '' })
class Blank {}

describe('connectionGuard (first run → /connect, §4 steps 3-4)', () => {
  let http: HttpTestingController;
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'data', component: Blank, canActivate: [connectionGuard] },
          { path: 'ask', component: Blank, canActivate: [connectionGuard] },
          { path: 'connect', component: Blank },
          { path: 'connect/folder', component: Blank },
        ]),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    TestBed.inject(ConfigService).config.set(DRIVE_CONFIG);
  });

  async function go(url: string, status?: object, code = 200) {
    const done = router.navigateByUrl(url);
    await flushAsync(2);
    if (status) http.expectOne('/api/auth/status').flush(status, { status: code, statusText: code === 200 ? 'OK' : 'Not Found' });
    await done;
    await flushAsync(2);
    return router.url;
  }

  it('Drive mode, not connected → /connect, only once per session', async () => {
    expect(await go('/data', AUTH_DISCONNECTED)).toBe('/connect');
    expect(await go('/ask')).toBe('/ask');
    http.expectNone('/api/auth/status');
  });

  it('connected without a folder → /connect/folder', async () => {
    expect(await go('/data', { ...AUTH_CONNECTED, folder: null })).toBe('/connect/folder');
  });

  it('returning run (connected with a folder) → no prompt', async () => {
    expect(await go('/data', AUTH_CONNECTED)).toBe('/data');
  });

  it('demo mode → no prompt and no status call', async () => {
    TestBed.inject(ConfigService).config.set(DEMO_CONFIG);
    expect(await go('/data')).toBe('/data');
    http.expectNone('/api/auth/status');
  });

  it('status unavailable (api without auth yet) → never blocks the app', async () => {
    expect(await go('/data', { detail: 'Not Found' }, 404)).toBe('/data');
    expect(TestBed.inject(AuthService).error()!.code).toBe('not_implemented');
  });

  it('connectTarget follows the contract UI logic', () => {
    expect(connectTarget(AUTH_DEMO)).toBeNull();
    expect(connectTarget(AUTH_DISCONNECTED)).toBe('/connect');
    expect(connectTarget({ ...AUTH_CONNECTED, folder: null })).toBe('/connect/folder');
    expect(connectTarget(AUTH_CONNECTED)).toBeNull();
    expect(connectTarget(null)).toBeNull();
  });
});

import { HttpErrorResponse, HttpHeaders, HttpInterceptorFn, HttpRequest, HttpResponse } from '@angular/common/http';
import { Observable, delay, from, mergeMap, of, throwError, timer } from 'rxjs';

import { isMockEnabled, mockOption } from '../core/mock-flag';
import type { MockBackend } from './mock-backend';

let backend: Promise<MockBackend> | null = null;

/** The mock backend is loaded lazily, so it is not in the bundle's hot path unless the flag is on. */
function getBackend(): Promise<MockBackend> {
  if (!backend) {
    backend = import('./mock-backend').then(
      (m) =>
        new m.MockBackend({
          demoMode: mockOption('demo') !== '0',
          initialProcessed: mockOption('empty') === '1' ? 0 : 14,
          connected: mockOption('connected') !== '0',
          askEnabled: flag(mockOption('ask')),
          oauthError: mockOption('oauth_error') || null,
          askDelayMs: Number(mockOption('ask_delay')) || undefined,
        }),
    );
  }
  return backend;
}

/** `?ask=1` / `?ask=0` → true / false; absent → undefined (mode default). */
function flag(v: string | null): boolean | undefined {
  return v === null ? undefined : v === '1' || v === 'true';
}

/** Replace the singleton (tests). */
export function resetMockBackend(b: MockBackend | null = null): void {
  backend = b ? Promise.resolve(b) : null;
}

export const MOCK_LATENCY_MS = 120;

export function respond(b: MockBackend, req: HttpRequest<unknown>): Observable<HttpResponse<unknown>> {
  const url = new URL(req.urlWithParams, 'http://mock.local');
  const res = b.handle({ method: req.method, path: url.pathname, params: url.searchParams, body: req.body });
  const headers = new HttpHeaders(res.headers ?? { 'Content-Type': 'application/json' });
  if (res.status >= 400) {
    const error = new HttpErrorResponse({ status: res.status, error: res.body, headers, url: req.urlWithParams });
    return timer(res.delayMs ?? MOCK_LATENCY_MS).pipe(mergeMap(() => throwError(() => error)));
  }
  const body = req.responseType === 'text' && typeof res.body !== 'string' ? JSON.stringify(res.body) : res.body;
  return of(new HttpResponse({ status: res.status, body, headers, url: req.urlWithParams })).pipe(delay(res.delayMs ?? MOCK_LATENCY_MS));
}

/**
 * Dev-only HTTP interceptor: when the mock flag is on (see core/mock-flag.ts) every
 * `/api/...` request is answered by the in-memory MockBackend instead of the network.
 * When the flag is off it is a pass-through, so the real api is always used by default.
 */
export const mockApiInterceptor: HttpInterceptorFn = (req, next) => {
  if (!isMockEnabled()) return next(req);
  if (!new URL(req.urlWithParams, 'http://mock.local').pathname.startsWith('/api/')) return next(req);
  return from(getBackend()).pipe(mergeMap((b) => respond(b, req)));
};

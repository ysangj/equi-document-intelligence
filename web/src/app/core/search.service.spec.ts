import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { CONTRACT_SEARCH } from '../testing/fixtures';
import { SEARCH_DEBOUNCE_MS, SearchService, normalizeQuery } from './search.service';

describe('SearchService (FR-10 as-you-type search)', () => {
  let svc: SearchService;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    svc = TestBed.inject(SearchService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  const isSearch = (q: string) => (r: { url: string; params: { get(k: string): string | null } }) =>
    r.url === '/api/search' && r.params.get('q') === q;

  it('debounces typing by 250 ms and sends only the latest text', () => {
    svc.type('du');
    vi.advanceTimersByTime(100);
    svc.type('dur');
    vi.advanceTimersByTime(100);
    svc.type('duration');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
    http.expectNone('/api/search');
    vi.advanceTimersByTime(1);
    const req = http.expectOne(isSearch('duration'));
    expect(req.request.params.get('limit')).toBe('8');
    expect(svc.state().status).toBe('loading');
    req.flush({ ...CONTRACT_SEARCH, query: 'duration' });
    expect(svc.state().status).toBe('ok');
    expect(svc.state().response?.groups.chunks.length).toBe(2);
    expect(svc.settledQuery()).toBe('duration');
  });

  it('cancels the in-flight request when a newer query settles (switchMap)', () => {
    svc.type('meridian');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    const stale = http.expectOne(isSearch('meridian'));
    svc.type('northwind');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    const fresh = http.expectOne(isSearch('northwind'));
    expect(stale.cancelled).toBe(true);
    expect(fresh.cancelled).toBe(false);
    fresh.flush({ ...CONTRACT_SEARCH, query: 'northwind' });
    expect(svc.state().query).toBe('northwind');
  });

  it('does not send queries shorter than 2 characters (after trimming)', () => {
    svc.type('d');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectNone('/api/search');
    expect(svc.state().status).toBe('short');

    svc.type('  d  ');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectNone('/api/search');

    svc.type('   ');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectNone('/api/search');
    expect(svc.state().status).toBe('idle');

    svc.type(' du ');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectOne(isSearch('du')).flush({ ...CONTRACT_SEARCH, query: 'du' });
  });

  it('clearing the box cancels a pending request and returns to idle', () => {
    svc.type('duration');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    const req = http.expectOne(isSearch('duration'));
    svc.type('');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    expect(req.cancelled).toBe(true);
    expect(svc.state().status).toBe('idle');
  });

  it('submit (Enter / ?q=) skips the debounce and re-runs the same query', () => {
    svc.submit('duration');
    vi.advanceTimersByTime(0);
    http.expectOne(isSearch('duration')).flush(CONTRACT_SEARCH);
    svc.submit('duration');
    vi.advanceTimersByTime(0);
    http.expectOne(isSearch('duration')).flush(CONTRACT_SEARCH);
  });

  it('typing the same query again does not re-send it', () => {
    svc.type('duration');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectOne(isSearch('duration')).flush(CONTRACT_SEARCH);
    svc.type('duration ');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectNone('/api/search');
  });

  it('keeps errors in state (C2 message) and recovers on the next query', () => {
    svc.submit('duration');
    vi.advanceTimersByTime(0);
    http
      .expectOne(isSearch('duration'))
      .flush({ error: { code: 'db_unavailable', message: 'Database unavailable.', details: null } }, { status: 503, statusText: 'x' });
    expect(svc.state().status).toBe('error');
    expect(svc.state().error?.message).toBe('Database unavailable.');
    svc.type('rate');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    http.expectOne(isSearch('rate')).flush({ ...CONTRACT_SEARCH, query: 'rate' });
    expect(svc.state().status).toBe('ok');
  });

  it('normalizeQuery trims and caps at the 200-character contract limit', () => {
    expect(normalizeQuery('  x  ')).toBe('x');
    expect(normalizeQuery(null)).toBe('');
    expect(normalizeQuery('a'.repeat(250)).length).toBe(200);
  });
});

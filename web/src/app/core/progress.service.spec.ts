import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { PROGRESS } from '../testing/fixtures';
import { POLL_ACTIVE_MS, POLL_IDLE_MS, ProgressService } from './progress.service';

describe('ProgressService (C9 polling)', () => {
  let svc: ProgressService;
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    svc = TestBed.inject(ProgressService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    svc.stop();
    vi.useRealTimers();
  });

  it('polls every 3 s while active and every 15 s when idle', () => {
    svc.start();
    http.expectOne('/api/progress').flush(PROGRESS); // active
    expect(svc.progress()?.processed).toBe(17);

    vi.advanceTimersByTime(POLL_ACTIVE_MS - 1);
    http.expectNone('/api/progress');
    vi.advanceTimersByTime(1);
    http.expectOne('/api/progress').flush({ ...PROGRESS, active: false });

    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    http.expectNone('/api/progress');
    vi.advanceTimersByTime(POLL_IDLE_MS - POLL_ACTIVE_MS);
    http.expectOne('/api/progress').flush(PROGRESS);
  });

  it('emits processedChanged$ only when processed changes after the first poll', () => {
    const seen: number[] = [];
    svc.processedChanged$.subscribe((n) => seen.push(n));
    svc.start();
    http.expectOne('/api/progress').flush(PROGRESS);
    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    http.expectOne('/api/progress').flush(PROGRESS);
    vi.advanceTimersByTime(POLL_ACTIVE_MS);
    http.expectOne('/api/progress').flush({ ...PROGRESS, processed: 18 });
    expect(seen).toEqual([18]);
  });

  it('keeps polling at the idle cadence after an error and records it', () => {
    svc.start();
    http.expectOne('/api/progress').flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
    expect(svc.error()?.code).toBe('not_implemented');
    expect(svc.progress()).toBeNull();
    vi.advanceTimersByTime(POLL_IDLE_MS);
    http.expectOne('/api/progress').flush(PROGRESS);
    expect(svc.error()).toBeNull();
  });

  it('refresh() polls immediately', () => {
    svc.start();
    http.expectOne('/api/progress').flush({ ...PROGRESS, active: false });
    svc.refresh();
    http.expectOne('/api/progress').flush(PROGRESS);
  });

  it('nextDelay follows active', () => {
    expect(ProgressService.nextDelay(PROGRESS)).toBe(3000);
    expect(ProgressService.nextDelay({ ...PROGRESS, active: false })).toBe(15000);
    expect(ProgressService.nextDelay(null)).toBe(15000);
    // a pending reconcile (e.g. right after POST /api/drive/watch) also polls fast
    expect(ProgressService.nextDelay({ ...PROGRESS, active: false, sync: { ...PROGRESS.sync, reconcile_pending: true } })).toBe(3000);
    expect(ProgressService.nextDelay({ ...PROGRESS, active: false, sync: { ...PROGRESS.sync, sync_pending: true } })).toBe(3000);
  });
});

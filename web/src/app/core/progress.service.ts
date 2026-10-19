import { DestroyRef, Injectable, inject, signal } from '@angular/core';
import { Observable, Subject, Subscription } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { ApiService } from '../api/api.service';
import { ProgressResponse } from '../api/models';

/** C9 / FR-12 cadence. */
export const POLL_ACTIVE_MS = 3_000;
export const POLL_IDLE_MS = 15_000;

/**
 * The one poll of `GET /api/progress` (C9). It drives the global progress bar, the
 * Review badge and the header, and tells data views when `processed` changes so they
 * can re-fetch their current page (FR-9 progressive fill).
 */
@Injectable({ providedIn: 'root' })
export class ProgressService {
  private readonly api = inject(ApiService);

  readonly progress = signal<ProgressResponse | null>(null);
  readonly error = signal<ApiError | null>(null);
  /** local clock (ms) when `progress` was last received */
  readonly receivedAt = signal<number | null>(null);

  private readonly processedSubject = new Subject<number>();
  /** Emits the new `processed` count whenever it changes (not on the first poll). */
  readonly processedChanged$: Observable<number> = this.processedSubject.asObservable();

  private readonly polledSubject = new Subject<ProgressResponse | null>();
  /**
   * Emits after every poll attempt (the response, or null when it failed). Views that
   * refresh "on the same cadence" (C9: the Ingestion document list) subscribe to this
   * instead of running a second timer.
   */
  readonly polled$: Observable<ProgressResponse | null> = this.polledSubject.asObservable();

  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight: Subscription | null = null;
  private running = false;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stop());
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.poll();
  }

  stop(): void {
    this.running = false;
    this.clearTimer();
    this.inFlight?.unsubscribe();
    this.inFlight = null;
  }

  /** Poll now (e.g. after "Sync now"), then continue on the normal cadence. */
  refresh(): void {
    if (!this.running) return;
    this.clearTimer();
    this.inFlight?.unsubscribe();
    this.poll();
  }

  /**
   * Update the Review badge without waiting for the next poll, e.g. from
   * `ReviewActionResponse.remaining_open` or the review list's counts.
   */
  patchReviewOpen(n: number): void {
    const p = this.progress();
    if (p && p.review_open !== n) this.progress.set({ ...p, review_open: n });
  }

  /**
   * The server's clock now, for relative times (C6: computed from `generated_at`),
   * advanced by the local time elapsed since the response arrived.
   */
  serverNow(localNowMs: number = Date.now()): number | null {
    const p = this.progress();
    if (!p) return null;
    const received = this.receivedAt() ?? localNowMs;
    return Date.parse(p.generated_at) + Math.max(0, localNowMs - received);
  }

  /**
   * Delay before the next poll for the given response: 3 s while documents are in
   * flight (C9), and also while a sync or reconcile request is still pending (for
   * example right after `POST /api/drive/watch`), so the reconcile and the first
   * queued documents show up within seconds instead of after the 15 s idle wait.
   */
  static nextDelay(p: ProgressResponse | null): number {
    if (!p) return POLL_IDLE_MS;
    return p.active || p.sync.sync_pending || p.sync.reconcile_pending ? POLL_ACTIVE_MS : POLL_IDLE_MS;
  }

  private poll(): void {
    this.inFlight = this.api.progress().subscribe({
      next: (p) => {
        const prev = this.progress();
        this.progress.set(p);
        this.receivedAt.set(Date.now());
        this.error.set(null);
        if (prev && prev.processed !== p.processed) this.processedSubject.next(p.processed);
        this.schedule(ProgressService.nextDelay(p));
        this.polledSubject.next(p);
      },
      error: (e) => {
        this.error.set(toApiError(e));
        this.schedule(POLL_IDLE_MS);
        this.polledSubject.next(null);
      },
    });
  }

  private schedule(ms: number): void {
    this.inFlight = null;
    if (!this.running) return;
    this.clearTimer();
    this.timer = setTimeout(() => this.poll(), ms);
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}

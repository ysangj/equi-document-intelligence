import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Observable, Subject, catchError, debounce, distinctUntilChanged, map, of, startWith, switchMap, timer } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { ApiService } from '../api/api.service';
import { SEARCH_MAX_QUERY_LENGTH, SEARCH_MIN_QUERY_LENGTH, SearchResponse } from '../api/models';

/** FR-10: results as you type, debounced 250 ms. */
export const SEARCH_DEBOUNCE_MS = 250;
/** hits per group (contract default 5, max 20) */
export const SEARCH_LIMIT = 8;

export type SearchStatus = 'idle' | 'short' | 'loading' | 'ok' | 'error';

export interface SearchState {
  status: SearchStatus;
  /** trimmed query this state belongs to */
  query: string;
  response: SearchResponse | null;
  error: ApiError | null;
  /** browser round trip in ms (the server's own time is `response.took_ms`) */
  roundTripMs: number | null;
}

interface Input {
  text: string;
  immediate: boolean;
}

const IDLE: SearchState = { status: 'idle', query: '', response: null, error: null, roundTripMs: null };

/** Trim and cap like the server does (it rejects > 200 chars with a 422). */
export function normalizeQuery(text: string | null | undefined): string {
  return (text ?? '').trim().slice(0, SEARCH_MAX_QUERY_LENGTH);
}

/**
 * The one search box (header) and the Search page share this state.
 *
 * - Typing goes through a 250 ms debounce; Enter / a URL change is applied at once.
 * - `switchMap` cancels the in-flight request when a newer query arrives, so a slow
 *   stale response can never overwrite a newer one.
 * - Queries shorter than the contract minimum (2 characters after trimming) are not
 *   sent: the server would return empty groups anyway, and as-you-type input never
 *   shows an error. An empty box is `idle`, 1 character is `short`.
 * - Errors are kept in the state (C2 message) rather than thrown, so the next query works.
 */
@Injectable({ providedIn: 'root' })
export class SearchService {
  private readonly api = inject(ApiService);

  /** raw text in the box (what the user typed) */
  readonly text = signal('');
  readonly state = signal<SearchState>(IDLE);
  /** the query the current results belong to (after debounce, trimmed) */
  readonly settledQuery = computed(() => this.state().query);
  readonly minLength = signal(SEARCH_MIN_QUERY_LENGTH);

  private readonly input = new Subject<Input>();

  constructor() {
    const sub = this.input
      .pipe(
        debounce((i) => timer(i.immediate ? 0 : SEARCH_DEBOUNCE_MS)),
        map((i) => ({ q: normalizeQuery(i.text), force: i.immediate })),
        // typing the same query again is a no-op; Enter always re-runs it (e.g. after an error)
        distinctUntilChanged((a, b) => a.q === b.q && !b.force),
        switchMap(({ q }) => this.run(q)),
      )
      .subscribe((s) => {
        this.state.set(s);
        if (s.response) this.minLength.set(s.response.min_query_length);
      });
    inject(DestroyRef).onDestroy(() => sub.unsubscribe());
  }

  /** As-you-type input (debounced). */
  type(text: string): void {
    this.text.set(text);
    this.input.next({ text, immediate: false });
  }

  /** Enter, a link, or the `?q=` URL: search now, skipping the debounce. */
  submit(text: string): void {
    this.text.set(text);
    this.input.next({ text, immediate: true });
  }

  /** Re-run the settled query (names changed on the server); no-op without results. */
  refresh(): void {
    const s = this.state();
    if (s.status === 'ok' || s.status === 'error') this.input.next({ text: s.query, immediate: true });
  }

  private run(q: string): Observable<SearchState> {
    if (!q) return of(IDLE);
    if (q.length < this.minLength()) return of<SearchState>({ ...IDLE, status: 'short', query: q });
    const started = performance.now();
    return this.api.search(q, SEARCH_LIMIT).pipe(
      map((response): SearchState => ({
        status: 'ok',
        query: q,
        response,
        error: null,
        roundTripMs: Math.round(performance.now() - started),
      })),
      catchError((e) => of<SearchState>({ status: 'error', query: q, response: null, error: toApiError(e), roundTripMs: null })),
      startWith<SearchState>({ ...this.state(), status: 'loading', query: q }),
    );
  }
}

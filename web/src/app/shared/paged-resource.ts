import { DestroyRef, signal } from '@angular/core';
import { Observable, Subject, catchError, map, of, switchMap, tap } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { Page } from '../api/models';

type Result<T> = { ok: true; page: Page<T> } | { ok: false; error: ApiError };

/**
 * Loads one page of a paginated list (C3). The latest request wins (switchMap), and
 * a background refresh keeps the current rows on screen until the new page arrives,
 * so progressive refreshes (FR-9) do not flicker.
 */
export class PagedResource<T> {
  readonly page = signal<Page<T> | null>(null);
  readonly error = signal<ApiError | null>(null);
  /** true while the first load (or a load after an error) is in flight */
  readonly loading = signal(false);
  /** true while any request is in flight */
  readonly busy = signal(false);

  private readonly trigger = new Subject<void>();

  constructor(
    fetch: () => Observable<Page<T>>,
    destroyRef: DestroyRef,
    onError?: (e: ApiError) => void,
  ) {
    const sub = this.trigger
      .pipe(
        tap(() => {
          this.busy.set(true);
          if (!this.page()) this.loading.set(true);
        }),
        switchMap(() =>
          fetch().pipe(
            map((page): Result<T> => ({ ok: true, page })),
            catchError((e) => of<Result<T>>({ ok: false, error: toApiError(e) })),
          ),
        ),
      )
      .subscribe((r) => {
        this.busy.set(false);
        this.loading.set(false);
        if (r.ok) {
          this.page.set(r.page);
          this.error.set(null);
        } else {
          this.page.set(null);
          this.error.set(r.error);
          onError?.(r.error);
        }
      });
    destroyRef.onDestroy(() => sub.unsubscribe());
  }

  load(): void {
    this.trigger.next();
  }
}

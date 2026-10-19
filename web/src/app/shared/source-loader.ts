import { DestroyRef, computed, signal } from '@angular/core';
import { Subject, catchError, map, of, switchMap } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { ApiService } from '../api/api.service';
import { SourceResponse, SourceTable } from '../api/models';

/** Which provenance to load: `/api/sources/{table}/{id}?field=`. */
export interface SourceRef {
  table: SourceTable;
  id: number;
  field: string | null;
}

export function sameRef(a: SourceRef | null, b: SourceRef | null): boolean {
  return !!a && !!b && a.table === b.table && a.id === b.id && (a.field ?? null) === (b.field ?? null);
}

type Result = { source: SourceResponse | null; error: ApiError | null };

/**
 * State for the FR-9 source side panel: the selected value, its provenance, loading
 * and error. The latest selection wins (switchMap). Shared by the Data and Review pages.
 */
export class SourceLoader {
  readonly selected = signal<SourceRef | null>(null);
  readonly source = signal<SourceResponse | null>(null);
  readonly error = signal<ApiError | null>(null);
  readonly loading = signal(false);
  readonly open = computed(() => this.selected() !== null);

  private readonly requests = new Subject<SourceRef>();

  constructor(api: ApiService, destroyRef: DestroyRef, onError?: (e: ApiError) => void) {
    const sub = this.requests
      .pipe(
        switchMap((ref) =>
          api.source(ref.table, ref.id, ref.field).pipe(
            map((source): Result => ({ source, error: null })),
            catchError((e) => of<Result>({ source: null, error: toApiError(e) })),
          ),
        ),
      )
      .subscribe((r) => {
        this.loading.set(false);
        this.source.set(r.source);
        this.error.set(r.error);
        if (r.error) onError?.(r.error);
      });
    destroyRef.onDestroy(() => sub.unsubscribe());
  }

  show(ref: SourceRef): void {
    this.selected.set(ref);
    this.loading.set(true);
    this.error.set(null);
    this.requests.next(ref);
  }

  /** Re-load the current selection (e.g. after `processed` changes or a review action). */
  refresh(): void {
    const sel = this.selected();
    if (sel) this.requests.next(sel);
  }

  close(): void {
    this.selected.set(null);
    this.source.set(null);
    this.error.set(null);
    this.loading.set(false);
  }
}

import { DestroyRef, Directive, computed, effect, inject, input, linkedSignal, output, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { PageEvent } from '@angular/material/paginator';
import { Sort } from '@angular/material/sort';
import { Observable } from 'rxjs';

import { CsvEndpoint, ApiService } from '../../api/api.service';
import { DEFAULT_PAGE_SIZE, Page } from '../../api/models';
import { QueryObject, toApiPage, toSortParam } from '../../api/query-params';
import { CsvExportService } from '../../core/csv-export.service';
import { NotifyService } from '../../core/notify.service';
import { ProgressService } from '../../core/progress.service';
import { PagedResource } from '../../shared/paged-resource';
import { DataFilters, DataView, PageState, SourceRef, hasActiveFilters, sameRef } from './data-filters';

export const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];

/**
 * Shared behaviour of the Data tables:
 * - server-side pagination and sort (C3): the query is rebuilt from filters + MatSort +
 *   MatPaginator, and any change of filters / sort / page size goes back to page 1
 * - re-fetch the current page whenever `/api/progress.processed` changes (C9, FR-9)
 * - CSV export of the current view (C5)
 */
@Directive()
export abstract class DataTableBase<T, Q extends QueryObject> {
  protected readonly api = inject(ApiService);
  protected readonly notify = inject(NotifyService);
  protected readonly progress = inject(ProgressService);
  private readonly csv = inject(CsvExportService);

  readonly filters = input.required<DataFilters>();
  readonly selected = input<SourceRef | null>(null);
  readonly source = output<SourceRef>();
  readonly clearFilters = output<void>();

  protected abstract readonly view: DataView;
  protected abstract readonly csvEndpoint: CsvEndpoint;
  abstract readonly noun: string;
  protected abstract buildQuery(f: DataFilters, p: PageState): Q;
  protected abstract fetch(q: Q): Observable<Page<T>>;

  readonly sort = signal<Sort>({ active: '', direction: '' });
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);
  readonly pageIndex = linkedSignal({
    source: () => ({ f: this.filters(), s: this.sort(), z: this.pageSize() }),
    computation: () => 0,
  });
  readonly pageSizeOptions = PAGE_SIZE_OPTIONS;

  readonly query = computed<Q>(() =>
    this.buildQuery(this.filters(), {
      page: toApiPage(this.pageIndex()),
      pageSize: this.pageSize(),
      sort: toSortParam(this.sort().active, this.sort().direction),
    }),
  );

  readonly resource = new PagedResource<T>(
    () => this.fetch(this.query()),
    inject(DestroyRef),
    (e) => this.notify.error(e, `Could not load ${this.noun}`),
  );

  readonly rows = computed<T[]>(() => this.resource.page()?.items ?? []);
  readonly total = computed(() => this.resource.page()?.total ?? 0);
  readonly filtered = computed(() => hasActiveFilters(this.filters(), this.view));
  readonly processing = computed(() => this.progress.progress()?.active === true);
  readonly rangeInvalid = computed(() => {
    const { dateFrom, dateTo } = this.filters();
    return !!dateFrom && !!dateTo && dateFrom > dateTo;
  });

  constructor() {
    effect(() => {
      this.query();
      if (this.rangeInvalid()) return; // would be a 422; the filter bar explains it
      untracked(() => this.resource.load());
    });
    this.progress.processedChanged$.pipe(takeUntilDestroyed()).subscribe(() => this.resource.load());
  }

  onSort(s: Sort): void {
    this.sort.set(s.direction ? s : { active: '', direction: '' });
  }

  /** For headers that sort by another column's key (e.g. "As of" = `period_end`). */
  sortBy(active: string): void {
    const cur = this.sort();
    const direction = cur.active === active && cur.direction === 'desc' ? 'asc' : 'desc';
    this.sort.set({ active, direction });
  }

  onPage(e: PageEvent): void {
    if (e.pageSize !== this.pageSize()) this.pageSize.set(e.pageSize);
    this.pageIndex.set(e.pageIndex);
  }

  reload(): void {
    this.resource.load();
  }

  exportCsv(): void {
    this.csv.download(this.csvUrl());
  }

  csvUrl(): string {
    return this.api.csvUrl(this.csvEndpoint, this.query());
  }

  open(ref: SourceRef, e?: Event): void {
    e?.stopPropagation();
    this.source.emit(ref);
  }

  isSelected(ref: SourceRef): boolean {
    return sameRef(this.selected(), ref);
  }
}

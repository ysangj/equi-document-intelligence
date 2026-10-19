import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { Sort } from '@angular/material/sort';
import { Subject, debounceTime, defer, tap } from 'rxjs';

import { toApiError } from '../../api/api-error';
import { ApiService } from '../../api/api.service';
import { AuthStatusResponse, DEFAULT_PAGE_SIZE, DocumentItem, DocumentStatus, DocumentsQuery } from '../../api/models';
import { toApiPage, toSortParam } from '../../api/query-params';
import { CsvExportService } from '../../core/csv-export.service';
import { NotifyService } from '../../core/notify.service';
import { ProgressService } from '../../core/progress.service';
import { SyncActionsService } from '../../core/sync-actions.service';
import { PagedResource } from '../../shared/paged-resource';
import { TableStateComponent } from '../data/table-state.component';
import { DocumentsTableComponent } from './documents-table.component';
import { IngestionSummaryComponent } from './ingestion-summary.component';

export const INGESTION_PAGE_SIZES = [25, 50, 100];

/** Optimistic Reprocess: the row shows `pending` from the click until a fresh list confirms it. */
interface Optimistic {
  /** list load generation after which the server state can replace the override */
  settledAfter: number | null;
}

/**
 * `/ingestion` (FR-12). Header from `/api/progress`; table from `/api/documents`.
 * The list refreshes on the same cadence as the progress poll (C9: every 3 s while any
 * document is pending/processing, else every 15 s) by listening to
 * `ProgressService.polled$`; there is no second timer.
 */
@Component({
  selector: 'app-ingestion-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [IngestionSummaryComponent, DocumentsTableComponent, TableStateComponent, MatPaginatorModule],
  template: `
    @if (progress.progress(); as p) {
      <app-ingestion-summary
        [progress]="p"
        [auth]="auth()"
        [now]="serverNow()"
        [selected]="statusFilter()"
        (pick)="setStatus($event)"
      />
    } @else if (progress.error(); as e) {
      <div class="card warn" role="status" data-testid="progress-unavailable">Progress unavailable: {{ e.message }}</div>
    }

    <div class="card">
      <div class="filters">
        <b>{{ folderName() }}</b>
        <input
          class="name-filter"
          type="search"
          placeholder="Filter by file name"
          aria-label="Filter by file name"
          maxlength="200"
          (input)="nameInput.next($any($event.target).value)"
        />
        @if (statusFilter(); as s) {
          <button type="button" class="chip on" (click)="setStatus(null)" data-testid="status-filter">Status: <b>{{ s }}</b> ×</button>
        }
        <span class="spacer"></span>
        @if (resource.busy()) {
          <span class="muted small" aria-live="polite">Updating…</span>
        }
        <button type="button" class="btn" (click)="exportCsv()" data-testid="docs-csv">Export CSV</button>
        <button
          type="button"
          class="btn"
          (click)="sync.reconcile()"
          [disabled]="sync.reconciling()"
          data-testid="reconcile"
          title="Re-list the whole folder: picks up anything missed and marks deleted files removed"
        >
          {{ sync.reconciling() ? 'Requesting…' : reconcilePending() ? 'Reconcile queued…' : 'Full reconcile' }}
        </button>
        <button type="button" class="btn primary" (click)="sync.sync()" [disabled]="sync.syncing()" data-testid="sync-now">
          {{ sync.syncing() ? 'Requesting…' : syncPending() ? 'Sync queued…' : 'Sync now' }}
        </button>
      </div>

      @if (rows().length) {
        <app-documents-table
          [rows]="rows()"
          [sort]="sort()"
          [now]="serverNow()"
          [busy]="reprocessing()"
          (sortChange)="onSort($event)"
          (reprocess)="reprocess($event)"
        />
        <mat-paginator
          [length]="total()"
          [pageIndex]="pageIndex()"
          [pageSize]="pageSize()"
          [pageSizeOptions]="pageSizes"
          (page)="onPage($event)"
          aria-label="Documents pages"
        />
      } @else {
        <app-table-state
          noun="documents"
          [loading]="resource.loading()"
          [error]="resource.error()"
          [filtered]="filtered()"
          [processing]="progress.progress()?.active === true"
          (retry)="resource.load()"
          (clearFilters)="clearFilters()"
        />
      }
    </div>
  `,
  styles: `
    .warn {
      padding: 10px 14px;
      margin-bottom: 12px;
      color: var(--warn);
      background: var(--warn-soft);
    }
    .name-filter {
      font: inherit;
      font-size: 12.5px;
      padding: 5px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--bg);
      color: var(--ink);
      width: 200px;
      max-width: 100%;
    }
    .chip.on {
      cursor: pointer;
      border-color: #cdd8fb;
      background: var(--accent-soft);
    }
    .small {
      font-size: 12px;
    }
  `,
})
export class IngestionPageComponent {
  private readonly api = inject(ApiService);
  private readonly notify = inject(NotifyService);
  private readonly csv = inject(CsvExportService);
  readonly progress = inject(ProgressService);
  readonly sync = inject(SyncActionsService);

  readonly pageSizes = INGESTION_PAGE_SIZES;
  readonly sort = signal<Sort>({ active: '', direction: '' });
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);
  readonly pageIndex = signal(0);
  readonly statusFilter = signal<DocumentStatus | null>(null);
  readonly nameFilter = signal('');
  readonly nameInput = new Subject<string>();
  readonly auth = signal<AuthStatusResponse | null>(null);

  /** ticks every second so relative times stay current */
  private readonly clock = signal(Date.now());
  readonly serverNow = computed(() => this.progress.serverNow(this.clock()));

  readonly query = computed<DocumentsQuery>(() => ({
    status: this.statusFilter() ? [this.statusFilter()!] : [],
    q: this.nameFilter() || null,
    page: toApiPage(this.pageIndex()),
    page_size: this.pageSize(),
    sort: toSortParam(this.sort().active, this.sort().direction),
  }));

  private generation = 0;
  private completed = 0;
  readonly resource = new PagedResource<DocumentItem>(
    () =>
      defer(() => {
        const gen = ++this.generation;
        return this.api.documents(this.query()).pipe(tap(() => (this.completed = gen)));
      }),
    inject(DestroyRef),
    (e) => this.notify.error(e, 'Could not load documents'),
  );

  private readonly optimistic = signal<ReadonlyMap<number, Optimistic>>(new Map());
  readonly reprocessing = signal<ReadonlySet<number>>(new Set());

  /** Server rows with optimistic Reprocess overrides applied. */
  readonly rows = computed<DocumentItem[]>(() => {
    const items = this.resource.page()?.items ?? [];
    const o = this.optimistic();
    if (!o.size) return items;
    return items.map((d) =>
      o.has(d.document_id) ? { ...d, status: 'pending' as const, attempts: 0, last_error: null, can_reprocess: false } : d,
    );
  });
  readonly total = computed(() => this.resource.page()?.total ?? 0);
  readonly filtered = computed(() => this.statusFilter() !== null || this.nameFilter() !== '');
  readonly folderName = computed(() => {
    const p = this.progress.progress();
    if (!p) return 'Documents';
    return p.mode === 'demo' ? `./${p.sync.folder_name ?? 'sample_docs'}` : (p.sync.folder_name ?? 'No folder selected');
  });
  readonly syncPending = computed(() => this.progress.progress()?.sync.sync_pending === true);
  readonly reconcilePending = computed(() => this.progress.progress()?.sync.reconcile_pending === true);

  constructor() {
    const destroyRef = inject(DestroyRef);
    effect(() => {
      this.query();
      untracked(() => this.resource.load());
    });
    // C9: same cadence as the progress poll.
    this.progress.polled$.pipe(takeUntilDestroyed(destroyRef)).subscribe(() => this.resource.load());
    this.nameInput.pipe(debounceTime(250), takeUntilDestroyed(destroyRef)).subscribe((v) => {
      this.nameFilter.set(v.trim().slice(0, 200));
      this.pageIndex.set(0);
    });
    // Drop optimistic overrides once a list requested after the 202 has arrived.
    effect(() => {
      this.resource.page();
      untracked(() => {
        const o = this.optimistic();
        const keep = new Map([...o].filter(([, v]) => v.settledAfter === null || this.completed <= v.settledAfter));
        if (keep.size !== o.size) this.optimistic.set(keep);
      });
    });
    const id = setInterval(() => this.clock.set(Date.now()), 1000);
    destroyRef.onDestroy(() => clearInterval(id));
    // Optional: "connected since" (T7 endpoint; the page works without it).
    this.api.authStatus().subscribe({ next: (a) => this.auth.set(a), error: () => this.auth.set(null) });
  }

  setStatus(s: DocumentStatus | null): void {
    this.statusFilter.set(s);
    this.pageIndex.set(0);
  }

  clearFilters(): void {
    this.statusFilter.set(null);
    this.nameFilter.set('');
    this.pageIndex.set(0);
  }

  onSort(s: Sort): void {
    this.sort.set(s.direction ? s : { active: '', direction: '' });
    this.pageIndex.set(0);
  }

  onPage(e: PageEvent): void {
    if (e.pageSize !== this.pageSize()) {
      this.pageSize.set(e.pageSize);
      this.pageIndex.set(0);
    } else this.pageIndex.set(e.pageIndex);
  }

  exportCsv(): void {
    this.csv.download(this.api.csvUrl('documents', this.query()));
  }

  /** Optimistic: the row turns `pending` at once and reverts if the request fails. */
  reprocess(d: DocumentItem): void {
    if (this.reprocessing().has(d.document_id)) return;
    this.setOptimistic(d.document_id, { settledAfter: null });
    this.reprocessing.set(new Set([...this.reprocessing(), d.document_id]));
    this.api.reprocess(d.document_id).subscribe({
      next: () => {
        this.doneReprocessing(d.document_id);
        this.setOptimistic(d.document_id, { settledAfter: this.generation });
        this.notify.info(`${d.document_name} queued for reprocessing.`);
        this.progress.refresh(); // the poll (and so this list) picks up the new state now
      },
      error: (e) => {
        this.doneReprocessing(d.document_id);
        const o = new Map(this.optimistic());
        o.delete(d.document_id);
        this.optimistic.set(o);
        this.notify.error(toApiError(e), 'Reprocess failed');
        this.resource.load();
      },
    });
  }

  private setOptimistic(id: number, v: Optimistic): void {
    this.optimistic.set(new Map([...this.optimistic(), [id, v]]));
  }

  private doneReprocessing(id: number): void {
    const s = new Set(this.reprocessing());
    s.delete(id);
    this.reprocessing.set(s);
  }
}

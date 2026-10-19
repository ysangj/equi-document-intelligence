import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal, untracked, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';

import { ApiService } from '../../api/api.service';
import { AccountItem, FundItem } from '../../api/models';
import { EntityNamesService } from '../../core/entity-names.service';
import { NotifyService } from '../../core/notify.service';
import { ProgressService } from '../../core/progress.service';
import { DataFilterBarComponent } from './data-filter-bar.component';
import { SourceLoader } from '../../shared/source-loader';
import { DataFilters, DataView, EMPTY_FILTERS, SourceRef, clearFilters } from './data-filters';
import { FundFactsTableComponent } from './fund-facts-table.component';
import { ReturnsTableComponent } from './returns-table.component';
import { SourcePanelComponent } from './source-panel.component';
import { StatementsTableComponent } from './statements-table.component';

const VIEWS: DataView[] = ['returns', 'facts', 'statements'];

/** `?fund_id=2` -> 2; anything that is not a positive integer -> null. */
export function idParam(v: string | undefined): number | null {
  if (v === undefined || !/^\d+$/.test(v)) return null;
  const n = Number(v);
  return n > 0 ? n : null;
}

/**
 * FR-9 Data page: filter bar + one of the three tables + the provenance side panel.
 * Fund / account filter options come from `/api/funds` and `/api/accounts` and are
 * refreshed when `/api/progress.processed` changes.
 */
@Component({
  selector: 'app-data-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DataFilterBarComponent,
    ReturnsTableComponent,
    FundFactsTableComponent,
    StatementsTableComponent,
    SourcePanelComponent,
  ],
  template: `
    <div class="layout" [class.with-panel]="panelOpen()">
      <div class="card main">
        <app-data-filter-bar
          [view]="current()"
          (viewChange)="setView($event)"
          [(filters)]="filters"
          [funds]="funds()"
          [accounts]="accounts()"
          [busy]="busy()"
          [documentLabel]="documentLabel()"
          (exportCsv)="exportCsv()"
        />
        @switch (current()) {
          @case ('returns') {
            <app-returns-table
              [filters]="filters()"
              [selected]="selected()"
              (source)="openSource($event)"
              (clearFilters)="resetFilters()"
            />
          }
          @case ('facts') {
            <app-fund-facts-table
              [filters]="filters()"
              [selected]="selected()"
              (source)="openSource($event)"
              (clearFilters)="resetFilters()"
            />
          }
          @case ('statements') {
            <app-statements-table
              [filters]="filters()"
              [selected]="selected()"
              (source)="openSource($event)"
              (clearFilters)="resetFilters()"
            />
          }
        }
      </div>
      <app-source-panel
        [source]="sources.source()"
        [loading]="sources.loading()"
        [error]="sources.error()"
        (closed)="closeSource()"
      />
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 16px;
      align-items: start;
    }
    .main {
      min-width: 0;
      overflow: hidden;
    }
    @container (max-width: 852px) {
      .layout {
        grid-template-columns: minmax(0, 1fr);
      }
    }
  `,
})
export class DataPageComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly notify = inject(NotifyService);
  private readonly progress = inject(ProgressService);

  /** `?view=returns|facts|statements` (router input binding) */
  readonly view = input<string | undefined>(undefined);
  readonly current = computed<DataView>(() => {
    const v = this.view();
    return VIEWS.includes(v as DataView) ? (v as DataView) : 'returns';
  });

  /**
   * Deep links from Search (FR-10): `?fund_id=`, `?account_id=` (with
   * `view=statements`) and `?document_id=` (+ `document_name` for the chip). They
   * replace the matching filter once and are then removed from the URL, so later
   * filter changes in the UI are not fighting a stale query string.
   */
  readonly fund_id = input<string | undefined>(undefined);
  readonly account_id = input<string | undefined>(undefined);
  readonly document_id = input<string | undefined>(undefined);
  readonly document_name = input<string | undefined>(undefined);
  readonly documentLabel = signal<string | null>(null);

  readonly filters = signal<DataFilters>(EMPTY_FILTERS);
  readonly funds = signal<FundItem[]>([]);
  readonly accounts = signal<AccountItem[]>([]);

  readonly sources = new SourceLoader(inject(ApiService), inject(DestroyRef), (e) =>
    this.notify.error(e, 'Could not load the source'),
  );
  readonly selected = this.sources.selected;
  readonly panelOpen = this.sources.open;

  private readonly returnsTable = viewChild(ReturnsTableComponent);
  private readonly factsTable = viewChild(FundFactsTableComponent);
  private readonly statementsTable = viewChild(StatementsTableComponent);
  private readonly activeTable = computed(() => this.returnsTable() ?? this.factsTable() ?? this.statementsTable());
  readonly busy = computed(() => this.activeTable()?.resource.busy() ?? false);

  constructor() {
    const destroyRef = inject(DestroyRef);
    this.loadOptions();
    this.progress.processedChanged$.pipe(takeUntilDestroyed(destroyRef)).subscribe(() => {
      this.loadOptions();
      this.sources.refresh();
    });
    // A fund / account was renamed in Review ("Use as main name"): reload the filter names.
    inject(EntityNamesService).changed$.pipe(takeUntilDestroyed(destroyRef)).subscribe(() => this.loadOptions());

    // A view switch clears the panel selection that belonged to the other table.
    effect(() => {
      this.current();
      untracked(() => this.sources.close());
    });

    effect(() => {
      const fund = idParam(this.fund_id());
      const account = idParam(this.account_id());
      const doc = idParam(this.document_id());
      if (fund === null && account === null && doc === null) return;
      const name = this.document_name();
      untracked(() => {
        const f = this.filters();
        this.filters.set({
          ...f,
          fundIds: fund !== null ? [fund] : f.fundIds,
          accountIds: account !== null ? [account] : f.accountIds,
          documentIds: doc !== null ? [doc] : f.documentIds,
        });
        if (doc !== null) this.documentLabel.set(name ?? `#${doc}`);
        this.router.navigate([], {
          queryParams: { fund_id: null, account_id: null, document_id: null, document_name: null },
          queryParamsHandling: 'merge',
          replaceUrl: true,
        });
      });
    });
  }

  setView(v: DataView): void {
    this.router.navigate([], { queryParams: { view: v === 'returns' ? null : v }, queryParamsHandling: 'merge' });
  }

  openSource(ref: SourceRef): void {
    this.sources.show(ref);
  }

  closeSource(): void {
    this.sources.close();
  }

  resetFilters(): void {
    this.filters.set(clearFilters(this.filters(), this.current()));
  }

  exportCsv(): void {
    this.activeTable()?.exportCsv();
  }

  private loadOptions(): void {
    this.api.funds().subscribe({
      next: (r) => this.funds.set(r.items),
      error: () => this.funds.set([]), // the table shows the error; options just stay empty
    });
    this.api.accounts().subscribe({
      next: (r) => this.accounts.set(r.items),
      error: () => this.accounts.set([]),
    });
  }
}

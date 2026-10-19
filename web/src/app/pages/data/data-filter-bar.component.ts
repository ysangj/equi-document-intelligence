import { ChangeDetectionStrategy, Component, computed, input, model, output } from '@angular/core';

import {
  AccountItem,
  DOCUMENT_TYPES,
  DOCUMENT_TYPE_LABELS,
  DocumentType,
  FundItem,
  NET_GROSS,
  NetGross,
  RETURN_TYPES,
  ReturnType,
  VALUE_STATUSES,
  VALUE_STATUS_LABELS,
  ValueStatus,
} from '../../api/models';
import { CheckListComponent, Option } from '../../shared/check-list.component';
import { FilterChipComponent } from '../../shared/filter-chip.component';
import { formatDate } from '../../shared/format';
import { DATA_VIEWS, DataFilters, DataView, summarize } from './data-filters';

/** Filter bar from the mockup: view toggle, filter chips, Export CSV. */
@Component({
  selector: 'app-data-filter-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FilterChipComponent, CheckListComponent],
  template: `
    <div class="filters">
      <div class="seg" role="tablist" aria-label="Table">
        @for (v of views; track v.id) {
          <button
            type="button"
            role="tab"
            [class.on]="view() === v.id"
            [attr.aria-selected]="view() === v.id"
            (click)="view.set(v.id)"
          >
            {{ v.label }}
          </button>
        }
      </div>

      @if (view() === 'statements') {
        <app-filter-chip label="Account" [value]="accountSummary()" [active]="filters().accountIds.length > 0">
          <app-check-list
            [options]="accountOptions()"
            [selected]="filters().accountIds"
            (selectedChange)="patch({ accountIds: $event })"
            emptyText="No accounts yet"
          />
        </app-filter-chip>
      } @else {
        <app-filter-chip label="Fund" [value]="fundSummary()" [active]="filters().fundIds.length > 0">
          <app-check-list
            [options]="fundOptions()"
            [selected]="filters().fundIds"
            (selectedChange)="patch({ fundIds: $event })"
            emptyText="No funds yet"
          />
        </app-filter-chip>
      }

      <app-filter-chip label="Period" [value]="periodSummary()" [active]="!!filters().dateFrom || !!filters().dateTo">
        <div class="range">
          <label>
            From
            <input
              type="date"
              aria-label="From date"
              [value]="filters().dateFrom ?? ''"
              (change)="patch({ dateFrom: dateValue($event) })"
            />
          </label>
          <label>
            To
            <input
              type="date"
              aria-label="To date"
              [value]="filters().dateTo ?? ''"
              (change)="patch({ dateTo: dateValue($event) })"
            />
          </label>
          @if (rangeInvalid()) {
            <div class="bad">"From" must be on or before "To".</div>
          }
          <button type="button" class="btn link" (click)="patch({ dateFrom: null, dateTo: null })">Any date</button>
        </div>
      </app-filter-chip>

      @if (view() === 'returns') {
        <app-filter-chip label="Type" [value]="typeSummary()" [active]="filters().returnTypes.length + filters().netGross.length > 0">
          <div class="cols">
            <div>
              <div class="k">Return type</div>
              <app-check-list
                [options]="returnTypeOptions"
                [selected]="filters().returnTypes"
                (selectedChange)="patch({ returnTypes: $event })"
              />
            </div>
            <div>
              <div class="k">Net / gross</div>
              <app-check-list
                [options]="netGrossOptions"
                [selected]="filters().netGross"
                (selectedChange)="patch({ netGross: $event })"
              />
            </div>
          </div>
        </app-filter-chip>
      }

      <app-filter-chip label="Document" [value]="docTypeSummary()" [active]="filters().documentTypes.length > 0">
        <app-check-list
          [options]="docTypeOptions"
          [selected]="filters().documentTypes"
          (selectedChange)="patch({ documentTypes: $event })"
        />
      </app-filter-chip>

      <app-filter-chip label="Status" [value]="statusSummary()" [active]="filters().statuses.length > 0">
        <app-check-list
          [options]="statusOptions"
          [selected]="filters().statuses"
          (selectedChange)="patch({ statuses: $event })"
        />
      </app-filter-chip>

      @if (filters().documentIds.length) {
        <button
          type="button"
          class="chip on"
          data-testid="doc-filter"
          title="Only values from this document. Click to remove."
          (click)="patch({ documentIds: [] })"
        >
          Source: <b>{{ documentLabel() ?? filters().documentIds.length + ' document(s)' }}</b> ×
        </button>
      }

      <span class="spacer"></span>
      @if (busy()) {
        <span class="muted small" aria-live="polite">Updating…</span>
      }
      <button type="button" class="btn" (click)="exportCsv.emit()" [disabled]="rangeInvalid()" data-testid="export-csv">
        Export CSV
      </button>
    </div>
  `,
  styles: `
    .range {
      display: grid;
      gap: 8px;
    }
    .range label {
      display: grid;
      gap: 2px;
      font-size: 12px;
      color: var(--muted);
    }
    .range input {
      font: inherit;
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
    }
    .bad {
      color: var(--bad);
      font-size: 12px;
    }
    .chip.on {
      cursor: pointer;
      border-color: #cdd8fb;
      background: var(--accent-soft);
    }
    .cols {
      display: flex;
      gap: 18px;
    }
    .k {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin-bottom: 2px;
    }
    .small {
      font-size: 12px;
    }
  `,
})
export class DataFilterBarComponent {
  readonly view = model.required<DataView>();
  readonly filters = model.required<DataFilters>();
  readonly funds = input<FundItem[]>([]);
  readonly accounts = input<AccountItem[]>([]);
  readonly busy = input(false);
  /** label for the `document_id` filter chip (from a Search link) */
  readonly documentLabel = input<string | null>(null);
  readonly exportCsv = output<void>();

  readonly views = DATA_VIEWS;

  readonly fundOptions = computed<Option<number>[]>(() =>
    this.funds().map((f) => ({ value: f.fund_id, label: f.fund_name, hint: f.manager })),
  );
  readonly accountOptions = computed<Option<number>[]>(() =>
    this.accounts().map((a) => ({ value: a.account_id, label: a.account_name, hint: a.account_number_masked })),
  );
  readonly docTypeOptions: Option<DocumentType>[] = DOCUMENT_TYPES.map((t) => ({ value: t, label: DOCUMENT_TYPE_LABELS[t] }));
  readonly statusOptions: Option<ValueStatus>[] = VALUE_STATUSES.map((s) => ({ value: s, label: VALUE_STATUS_LABELS[s] }));
  readonly returnTypeOptions: Option<ReturnType>[] = RETURN_TYPES.map((t) => ({ value: t, label: t }));
  readonly netGrossOptions: Option<NetGross>[] = NET_GROSS.map((t) => ({ value: t, label: t }));

  readonly fundSummary = computed(() => summarize(this.filters().fundIds, this.fundOptions(), this.funds().length));
  readonly accountSummary = computed(() =>
    summarize(this.filters().accountIds, this.accountOptions(), this.accounts().length),
  );
  readonly docTypeSummary = computed(() => summarize(this.filters().documentTypes, this.docTypeOptions));
  readonly statusSummary = computed(() => summarize(this.filters().statuses, this.statusOptions));
  readonly typeSummary = computed(() => {
    const f = this.filters();
    const parts = [...f.returnTypes, ...f.netGross];
    return parts.length ? parts.join(', ') : 'All';
  });
  readonly periodSummary = computed(() => {
    const { dateFrom, dateTo } = this.filters();
    if (!dateFrom && !dateTo) return 'Any';
    if (dateFrom && dateTo) return `${formatDate(dateFrom)} – ${formatDate(dateTo)}`;
    return dateFrom ? `from ${formatDate(dateFrom)}` : `to ${formatDate(dateTo)}`;
  });
  readonly rangeInvalid = computed(() => {
    const { dateFrom, dateTo } = this.filters();
    return !!dateFrom && !!dateTo && dateFrom > dateTo;
  });

  patch(p: Partial<DataFilters>): void {
    this.filters.set({ ...this.filters(), ...p });
  }

  dateValue(e: Event): string | null {
    const v = (e.target as HTMLInputElement).value;
    return /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null;
  }
}

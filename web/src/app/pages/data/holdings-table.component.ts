import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatSortModule, Sort } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';

import { ApiService } from '../../api/api.service';
import { HoldingItem, HoldingsQuery } from '../../api/models';
import { toApiPage, toSortParam } from '../../api/query-params';
import { NotifyService } from '../../core/notify.service';
import { EMPTY, formatAmount, formatNumber, formatWeight, negClass } from '../../shared/format';
import { PagedResource } from '../../shared/paged-resource';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { SourceRef, sameRef } from './data-filters';

/** §17 default: extract all holdings, paginate in the UI. */
export const HOLDINGS_PAGE_SIZE = 10;

/**
 * Holdings of one statement (expanded Statements row):
 * `GET /api/statements/{id}/holdings`, paginated server-side. Each cell opens
 * `/api/sources/statement_holdings/{holding_id}?field=...`.
 */
@Component({
  selector: 'app-holdings-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatTableModule, MatSortModule, MatPaginatorModule, StatusChipComponent],
  template: `
    <div class="holdings">
      <div class="title">
        Holdings <span class="muted">({{ total() }})</span>
        @if (currency()) {
          <span class="muted">· {{ currency() }}</span>
        }
      </div>
      @if (resource.error(); as e) {
        <div class="muted err" role="alert">
          Couldn't load holdings: {{ e.message }}
          <button type="button" class="btn link" (click)="resource.load()">Retry</button>
        </div>
      } @else if (resource.loading()) {
        <div class="muted">Loading holdings…</div>
      } @else if (!rows().length) {
        <div class="muted">No holdings were extracted from this statement.</div>
      } @else {
        <table
          mat-table
          class="data mini"
          [dataSource]="rows()"
          matSort
          [matSortActive]="sort().active"
          [matSortDirection]="sort().direction"
          (matSortChange)="onSort($event)"
          aria-label="Holdings"
        >
          <ng-container matColumnDef="line_no">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">#</th>
            <td mat-cell *matCellDef="let h" class="num muted">{{ h.line_no + 1 }}</td>
          </ng-container>
          <ng-container matColumnDef="holding_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Holding</th>
            <td mat-cell *matCellDef="let h" class="cell-click" [class.selected-cell]="sel(h, 'holding_name')" (click)="open(h, 'holding_name', $event)">
              {{ h.holding_name }}
            </td>
          </ng-container>
          <ng-container matColumnDef="identifier">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Identifier</th>
            <td mat-cell *matCellDef="let h" class="cell-click" [class.selected-cell]="sel(h, 'identifier')" (click)="open(h, 'identifier', $event)">
              {{ h.identifier ?? dash }}
            </td>
          </ng-container>
          <ng-container matColumnDef="quantity">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Quantity</th>
            <td mat-cell *matCellDef="let h" class="num cell-click" [class.selected-cell]="sel(h, 'quantity')" (click)="open(h, 'quantity', $event)">
              {{ num(h.quantity) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="market_value">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Market value</th>
            <td
              mat-cell
              *matCellDef="let h"
              class="num cell-click"
              [class]="neg(h.market_value)"
              [class.selected-cell]="sel(h, 'market_value')"
              (click)="open(h, 'market_value', $event)"
            >
              {{ amount(h.market_value) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="weight_pct">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Weight</th>
            <td mat-cell *matCellDef="let h" class="num cell-click" [class.selected-cell]="sel(h, 'weight')" (click)="open(h, 'weight', $event)">
              {{ weight(h.weight_pct) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="status">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Status</th>
            <td mat-cell *matCellDef="let h"><app-status-chip [status]="h.status" [reasons]="h.review_reasons" /></td>
          </ng-container>
          <tr mat-header-row *matHeaderRowDef="columns"></tr>
          <tr mat-row *matRowDef="let h; columns: columns" class="row"></tr>
        </table>
        @if (total() > pageSize) {
          <mat-paginator
            [length]="total()"
            [pageIndex]="pageIndex()"
            [pageSize]="pageSize"
            [hidePageSize]="true"
            (page)="onPage($event)"
            aria-label="Holdings pages"
          />
        }
      }
    </div>
  `,
  styles: `
    .holdings {
      padding: 10px 14px 12px 40px;
      background: var(--head-bg);
    }
    .title {
      font-weight: 600;
      font-size: 12.5px;
      margin-bottom: 6px;
    }
    table.mini th,
    table.mini td {
      padding: 5px 8px;
      font-size: 12px;
    }
    .err {
      color: var(--bad);
    }
  `,
})
export class HoldingsTableComponent {
  private readonly api = inject(ApiService);
  private readonly notify = inject(NotifyService);

  readonly statementId = input.required<number>();
  readonly currency = input<string | null>(null);
  readonly selected = input<SourceRef | null>(null);
  readonly source = output<SourceRef>();

  readonly pageSize = HOLDINGS_PAGE_SIZE;
  readonly pageIndex = signal(0);
  readonly sort = signal<Sort>({ active: '', direction: '' });
  readonly columns = ['line_no', 'holding_name', 'identifier', 'quantity', 'market_value', 'weight_pct', 'status'];
  readonly dash = EMPTY;
  readonly amount = formatAmount;
  readonly num = (v: number | null) => formatNumber(v);
  readonly weight = formatWeight;
  readonly neg = negClass;

  readonly query = computed<HoldingsQuery>(() => ({
    page: toApiPage(this.pageIndex()),
    page_size: this.pageSize,
    sort: toSortParam(this.sort().active, this.sort().direction),
  }));

  readonly resource = new PagedResource<HoldingItem>(
    () => this.api.holdings(this.statementId(), this.query()),
    inject(DestroyRef),
    (e) => this.notify.error(e, 'Could not load holdings'),
  );
  readonly rows = computed(() => this.resource.page()?.items ?? []);
  readonly total = computed(() => this.resource.page()?.total ?? 0);

  constructor() {
    effect(() => {
      this.statementId();
      this.query();
      untracked(() => this.resource.load());
    });
  }

  onSort(s: Sort): void {
    this.pageIndex.set(0);
    this.sort.set(s.direction ? s : { active: '', direction: '' });
  }

  onPage(e: PageEvent): void {
    this.pageIndex.set(e.pageIndex);
  }

  ref(h: HoldingItem, field: string): SourceRef {
    return { table: 'statement_holdings', id: h.holding_id, field };
  }

  sel(h: HoldingItem, field: string): boolean {
    return sameRef(this.selected(), this.ref(h, field));
  }

  open(h: HoldingItem, field: string, e: Event): void {
    e.stopPropagation();
    this.source.emit(this.ref(h, field));
  }
}

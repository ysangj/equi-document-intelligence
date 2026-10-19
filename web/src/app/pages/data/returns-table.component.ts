import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MatPaginatorModule } from '@angular/material/paginator';
import { MatSortModule } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { Observable } from 'rxjs';

import { ReturnItem, ReturnsPage, ReturnsQuery } from '../../api/models';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { formatDate, formatPct, formatPeriod, signClass } from '../../shared/format';
import { DataFilters, PageState, SourceRef, returnsQuery } from './data-filters';
import { DataTableBase } from './data-table-base';
import { TableStateComponent } from './table-state.component';

/** FR-9 "Funds & Returns": `GET /api/returns`. Any cell opens `/api/sources/returns/{id}`. */
@Component({
  selector: 'app-returns-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatTableModule, MatSortModule, MatPaginatorModule, StatusChipComponent, TableStateComponent],
  template: `
    @if (rows().length) {
      <div class="table-scroll">
        <table
          mat-table
          class="data"
          [dataSource]="rows()"
          matSort
          [matSortActive]="sort().active"
          [matSortDirection]="sort().direction"
          (matSortChange)="onSort($event)"
          aria-label="Funds and returns"
        >
          <ng-container matColumnDef="fund_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Fund</th>
            <td mat-cell *matCellDef="let r">{{ r.fund_name }}</td>
          </ng-container>
          <ng-container matColumnDef="manager">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Manager</th>
            <td mat-cell *matCellDef="let r">{{ r.manager ?? dash }}</td>
          </ng-container>
          <ng-container matColumnDef="strategy">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Strategy</th>
            <td mat-cell *matCellDef="let r">{{ r.strategy ?? dash }}</td>
          </ng-container>
          <ng-container matColumnDef="period_end">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Period</th>
            <td mat-cell *matCellDef="let r" [attr.title]="r.period_label ? 'As written: ' + r.period_label : null">
              {{ period(r.period_end) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="return_type">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Type</th>
            <td mat-cell *matCellDef="let r">{{ r.return_type }}</td>
          </ng-container>
          <ng-container matColumnDef="net_gross">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Net/gross</th>
            <td mat-cell *matCellDef="let r">{{ r.net_gross }}</td>
          </ng-container>
          <ng-container matColumnDef="value_pct">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Return</th>
            <td
              mat-cell
              *matCellDef="let r"
              class="num cell-click"
              [class]="sign(r.value_pct)"
              [class.selected-cell]="isSelected(ref(r))"
              data-testid="return-value"
            >
              {{ pct(r.value_pct) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="as_of">
            <th mat-header-cell *matHeaderCellDef>
              <button type="button" class="hdr-btn" (click)="sortBy('period_end')" title="Sort by period end">As of</button>
            </th>
            <td mat-cell *matCellDef="let r">{{ date(r.period_end) }}</td>
          </ng-container>
          <ng-container matColumnDef="status">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Status</th>
            <td mat-cell *matCellDef="let r"><app-status-chip [status]="r.status" [reasons]="r.review_reasons" /></td>
          </ng-container>
          <ng-container matColumnDef="document_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Source</th>
            <td mat-cell *matCellDef="let r" class="doc">{{ r.document_name }}</td>
          </ng-container>

          <tr mat-header-row *matHeaderRowDef="columns"></tr>
          <tr
            mat-row
            *matRowDef="let r; columns: columns"
            class="row"
            [class.selected]="isSelected(ref(r))"
            (click)="open(ref(r))"
            tabindex="0"
            (keydown.enter)="open(ref(r))"
          ></tr>
        </table>
      </div>
      <mat-paginator
        [length]="total()"
        [pageIndex]="pageIndex()"
        [pageSize]="pageSize()"
        [pageSizeOptions]="pageSizeOptions"
        (page)="onPage($event)"
        aria-label="Returns pages"
      />
    } @else {
      <app-table-state
        noun="returns"
        [loading]="resource.loading()"
        [error]="resource.error()"
        [filtered]="filtered()"
        [processing]="processing()"
        (retry)="reload()"
        (clearFilters)="clearFilters.emit()"
      />
    }
  `,
  styles: `
    .hdr-btn {
      border: 0;
      background: none;
      font: inherit;
      color: inherit;
      text-transform: inherit;
      letter-spacing: inherit;
      padding: 0;
      cursor: pointer;
    }
  `,
})
export class ReturnsTableComponent extends DataTableBase<ReturnItem, ReturnsQuery> {
  protected readonly view = 'returns' as const;
  protected readonly csvEndpoint = 'returns' as const;
  readonly noun = 'returns';

  readonly columns = [
    'fund_name', 'manager', 'strategy', 'period_end', 'return_type', 'net_gross', 'value_pct', 'as_of', 'status', 'document_name',
  ];
  readonly dash = '—';
  readonly pct = formatPct;
  readonly sign = signClass;
  readonly period = formatPeriod;
  readonly date = formatDate;

  protected buildQuery(f: DataFilters, p: PageState): ReturnsQuery {
    return returnsQuery(f, p);
  }

  protected fetch(q: ReturnsQuery): Observable<ReturnsPage> {
    return this.api.returns(q);
  }

  ref(r: ReturnItem): SourceRef {
    return { table: 'returns', id: r.return_id, field: null };
  }
}

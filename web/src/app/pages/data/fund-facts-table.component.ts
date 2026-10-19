import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MatPaginatorModule } from '@angular/material/paginator';
import { MatSortModule } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { Observable } from 'rxjs';

import { FundFactItem, FundFactsPage, FundFactsQuery } from '../../api/models';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { factSignValue, formatDate, formatFact, formatMetric, signClass } from '../../shared/format';
import { DataFilters, PageState, SourceRef, factsQuery } from './data-filters';
import { DataTableBase } from './data-table-base';
import { TableStateComponent } from './table-state.component';

/** FR-9 fund metrics (AUM, Sharpe, drawdown, vol, ...): `GET /api/fund-facts`. */
@Component({
  selector: 'app-fund-facts-table',
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
          aria-label="Fund facts"
        >
          <ng-container matColumnDef="fund_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Fund</th>
            <td mat-cell *matCellDef="let r">{{ r.fund_name }}</td>
          </ng-container>
          <ng-container matColumnDef="manager">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Manager</th>
            <td mat-cell *matCellDef="let r">{{ r.manager ?? dash }}</td>
          </ng-container>
          <ng-container matColumnDef="metric">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Metric</th>
            <td mat-cell *matCellDef="let r" [attr.title]="r.metric">{{ metric(r.metric) }}</td>
          </ng-container>
          <ng-container matColumnDef="value">
            <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Value</th>
            <td
              mat-cell
              *matCellDef="let r"
              class="num cell-click"
              [class]="sign(signValue(r))"
              [class.selected-cell]="isSelected(ref(r))"
            >
              {{ fact(r) }}
            </td>
          </ng-container>
          <ng-container matColumnDef="as_of_date">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>As of</th>
            <td mat-cell *matCellDef="let r">{{ date(r.as_of_date) }}</td>
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
        aria-label="Fund facts pages"
      />
    } @else {
      <app-table-state
        noun="fund facts"
        [loading]="resource.loading()"
        [error]="resource.error()"
        [filtered]="filtered()"
        [processing]="processing()"
        (retry)="reload()"
        (clearFilters)="clearFilters.emit()"
      />
    }
  `,
})
export class FundFactsTableComponent extends DataTableBase<FundFactItem, FundFactsQuery> {
  protected readonly view = 'facts' as const;
  protected readonly csvEndpoint = 'fund-facts' as const;
  readonly noun = 'fund facts';

  readonly columns = ['fund_name', 'manager', 'metric', 'value', 'as_of_date', 'status', 'document_name'];
  readonly dash = '—';
  readonly fact = formatFact;
  readonly signValue = factSignValue;
  readonly sign = signClass;
  readonly metric = formatMetric;
  readonly date = formatDate;

  protected buildQuery(f: DataFilters, p: PageState): FundFactsQuery {
    return factsQuery(f, p);
  }

  protected fetch(q: FundFactsQuery): Observable<FundFactsPage> {
    return this.api.fundFacts(q);
  }

  ref(r: FundFactItem): SourceRef {
    return { table: 'fund_facts', id: r.fact_id, field: null };
  }
}

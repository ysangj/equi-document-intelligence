import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { MatPaginatorModule } from '@angular/material/paginator';
import { MatSortModule } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { Observable } from 'rxjs';

import { StatementField, StatementItem, StatementsPage, StatementsQuery } from '../../api/models';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { EMPTY, formatAmount, formatDate, negClass } from '../../shared/format';
import { DataFilters, PageState, SourceRef, statementsQuery } from './data-filters';
import { DataTableBase } from './data-table-base';
import { HoldingsTableComponent } from './holdings-table.component';
import { TableStateComponent } from './table-state.component';

type MoneyField = Extract<StatementField, 'opening_balance' | 'contributions' | 'withdrawals' | 'pnl' | 'closing_balance'>;

/**
 * FR-9 "Statements": `GET /api/statements`. Every value cell is clickable and opens
 * `/api/sources/statements/{id}?field=<column>` (contract). The chevron expands the
 * row's holdings.
 */
@Component({
  selector: 'app-statements-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    MatTableModule,
    MatSortModule,
    MatPaginatorModule,
    StatusChipComponent,
    TableStateComponent,
    HoldingsTableComponent,
  ],
  template: `
    @if (rows().length) {
      <div class="table-scroll">
        <table
          mat-table
          class="data"
          [dataSource]="rows()"
          multiTemplateDataRows
          matSort
          [matSortActive]="sort().active"
          [matSortDirection]="sort().direction"
          (matSortChange)="onSort($event)"
          aria-label="Statements"
        >
          <ng-container matColumnDef="expand">
            <th mat-header-cell *matHeaderCellDef class="x"><span class="sr-only">Holdings</span></th>
            <td mat-cell *matCellDef="let s" class="x">
              <button
                type="button"
                class="exp"
                [attr.aria-expanded]="expanded() === s.statement_id"
                [attr.aria-label]="'Holdings for ' + s.account_name"
                [title]="s.holdings_count + ' holdings'"
                (click)="toggle(s, $event)"
                data-testid="expand"
              >
                {{ expanded() === s.statement_id ? '▾' : '▸' }}
                <small>{{ s.holdings_count }}</small>
              </button>
            </td>
          </ng-container>
          <ng-container matColumnDef="account_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Account</th>
            <td mat-cell *matCellDef="let s" [class]="cellClass(s, 'account_name')" (click)="cell(s, 'account_name', $event)">
              {{ s.account_name }}
              @if (s.account_number_masked) {
                <small class="muted">{{ s.account_number_masked }}</small>
              }
            </td>
          </ng-container>
          <ng-container matColumnDef="custodian">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Custodian / manager</th>
            <td mat-cell *matCellDef="let s" [class]="cellClass(s, 'custodian')" (click)="cell(s, 'custodian', $event)">
              {{ s.custodian ?? s.manager ?? dash }}
              @if (s.fund_name) {
                <div class="sub muted">{{ s.fund_name }}</div>
              }
            </td>
          </ng-container>
          <ng-container matColumnDef="period_end">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>As of</th>
            <td mat-cell *matCellDef="let s" [class]="cellClass(s, 'period_end')" (click)="cell(s, 'period_end', $event)">
              {{ date(s.period_end) }}
            </td>
          </ng-container>
          @for (m of money; track m.field) {
            <ng-container [matColumnDef]="m.field">
              <th mat-header-cell *matHeaderCellDef [mat-sort-header]="m.field" class="num">{{ m.label }}</th>
              <td
                mat-cell
                *matCellDef="let s"
                [class]="cellClass(s, m.field) + ' num ' + neg(s[m.field])"
                (click)="cell(s, m.field, $event)"
                [attr.data-testid]="'cell-' + m.field"
              >
                {{ amount(s[m.field]) }}
              </td>
            </ng-container>
          }
          <ng-container matColumnDef="currency">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Ccy</th>
            <td mat-cell *matCellDef="let s" [class]="cellClass(s, 'currency')" (click)="cell(s, 'currency', $event)">
              {{ s.currency ?? dash }}
            </td>
          </ng-container>
          <ng-container matColumnDef="status">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Status</th>
            <td mat-cell *matCellDef="let s"><app-status-chip [status]="s.status" [reasons]="s.review_reasons" /></td>
          </ng-container>
          <ng-container matColumnDef="document_name">
            <th mat-header-cell *matHeaderCellDef mat-sort-header>Source</th>
            <td mat-cell *matCellDef="let s" class="doc">{{ s.document_name }}</td>
          </ng-container>
          <ng-container matColumnDef="detail">
            <td mat-cell *matCellDef="let s" [attr.colspan]="columns.length" class="detail">
              @if (expanded() === s.statement_id) {
                <app-holdings-table
                  [statementId]="s.statement_id"
                  [currency]="s.currency"
                  [selected]="selected()"
                  (source)="source.emit($event)"
                />
              }
            </td>
          </ng-container>

          <tr mat-header-row *matHeaderRowDef="columns"></tr>
          <tr
            mat-row
            *matRowDef="let s; columns: columns"
            class="row"
            [class.selected]="selected()?.table === 'statements' && selected()?.id === s.statement_id"
            (click)="cell(s, 'closing_balance')"
          ></tr>
          <tr mat-row *matRowDef="let s; columns: ['detail']" class="detail-row" [class.open]="expanded() === s.statement_id"></tr>
        </table>
      </div>
      <mat-paginator
        [length]="total()"
        [pageIndex]="pageIndex()"
        [pageSize]="pageSize()"
        [pageSizeOptions]="pageSizeOptions"
        (page)="onPage($event)"
        aria-label="Statements pages"
      />
    } @else {
      <app-table-state
        noun="statements"
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
    .x {
      width: 1%;
      padding-right: 0 !important;
    }
    .exp {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      font: inherit;
      font-size: 12px;
      padding: 1px 6px;
      cursor: pointer;
      color: var(--muted);
      white-space: nowrap;
    }
    .sub {
      font-size: 11.5px;
    }
    small {
      margin-left: 4px;
    }
    tr.detail-row {
      height: 0;
    }
    tr.detail-row td.detail {
      padding: 0 !important;
      border-bottom-width: 0;
    }
    tr.detail-row.open td.detail {
      border-bottom-width: 1px;
    }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
    }
  `,
})
export class StatementsTableComponent extends DataTableBase<StatementItem, StatementsQuery> {
  protected readonly view = 'statements' as const;
  protected readonly csvEndpoint = 'statements' as const;
  readonly noun = 'statements';

  readonly money: { field: MoneyField; label: string }[] = [
    { field: 'opening_balance', label: 'Opening' },
    { field: 'contributions', label: 'Contributions' },
    { field: 'withdrawals', label: 'Withdrawals' },
    { field: 'pnl', label: 'P&L' },
    { field: 'closing_balance', label: 'Closing / NAV' },
  ];
  readonly columns = [
    'expand', 'account_name', 'custodian', 'period_end',
    ...this.money.map((m) => m.field),
    'currency', 'status', 'document_name',
  ];
  readonly dash = EMPTY;
  readonly amount = formatAmount;
  readonly neg = negClass;
  readonly date = formatDate;
  readonly expanded = signal<number | null>(null);

  protected buildQuery(f: DataFilters, p: PageState): StatementsQuery {
    return statementsQuery(f, p);
  }

  protected fetch(q: StatementsQuery): Observable<StatementsPage> {
    return this.api.statements(q);
  }

  ref(s: StatementItem, field: StatementField): SourceRef {
    return { table: 'statements', id: s.statement_id, field };
  }

  cell(s: StatementItem, field: StatementField, e?: Event): void {
    this.open(this.ref(s, field), e);
  }

  cellClass(s: StatementItem, field: StatementField): string {
    const parts = ['cell-click'];
    if (s.flagged_fields.includes(field)) parts.push('flagged');
    if (this.isSelected(this.ref(s, field))) parts.push('selected-cell');
    return parts.join(' ');
  }

  toggle(s: StatementItem, e: Event): void {
    e.stopPropagation();
    this.expanded.set(this.expanded() === s.statement_id ? null : s.statement_id);
  }
}

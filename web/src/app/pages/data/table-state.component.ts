import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { TitleCasePipe } from '@angular/common';
import { RouterLink } from '@angular/router';

import { ApiError, connectActionLabel, connectRouteFor } from '../../api/api-error';

/**
 * Loading / error / empty states for a Data table. Friendly copy, never blocking:
 * - error: the C2 message (or a classified network / not-implemented message) + Retry
 * - empty, no filters, nothing processed: "No documents processed yet" + Ingestion link
 * - empty while processing: rows will appear as documents complete (FR-9)
 * - empty with filters: "No rows match these filters" + Clear filters
 */
@Component({
  selector: 'app-table-state',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, TitleCasePipe],
  template: `
    @if (loading()) {
      <div class="state" aria-busy="true">Loading {{ noun() }}…</div>
    } @else if (error(); as e) {
      <div class="state error" role="alert" data-testid="table-error">
        <h3>Couldn't load {{ noun() }}</h3>
        <p>{{ e.message }}</p>
        @if (e.details?.length) {
          <ul class="details">
            @for (d of e.details; track $index) {
              <li>{{ d.message }}</li>
            }
          </ul>
        }
        @if (connectRoute(e); as route) {
          <a class="btn primary" [routerLink]="route" data-testid="table-connect">{{ connectLabel(route) }}</a>
        }
        <button type="button" class="btn" (click)="retry.emit()">Retry</button>
      </div>
    } @else if (filtered()) {
      <div class="state" data-testid="table-empty-filtered">
        <h3>No {{ noun() }} match these filters</h3>
        <button type="button" class="btn" (click)="clearFilters.emit()">Clear filters</button>
      </div>
    } @else if (processing()) {
      <div class="state" data-testid="table-empty-processing">
        <h3>Documents are processing</h3>
        <p>{{ noun() | titlecase }} will appear here as each document completes.</p>
      </div>
    } @else {
      <div class="state" data-testid="table-empty">
        <h3>No documents processed yet</h3>
        <p>
          Nothing has been extracted into {{ noun() }} yet. Check
          <a routerLink="/ingestion">Ingestion</a> for document status.
        </p>
      </div>
    }
  `,
  styles: `
    .details {
      list-style: none;
      padding: 0;
      margin: 0 0 10px;
      font-size: 12.5px;
    }
  `,
})
export class TableStateComponent {
  readonly noun = input.required<string>();
  readonly loading = input(false);
  readonly error = input<ApiError | null>(null);
  readonly filtered = input(false);
  readonly processing = input(false);
  readonly retry = output<void>();
  readonly clearFilters = output<void>();

  readonly connectRoute = connectRouteFor;
  readonly connectLabel = connectActionLabel;
}

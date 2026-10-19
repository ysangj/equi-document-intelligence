import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { MatSortModule, Sort } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';

import { DOCUMENT_TYPE_LABELS, DocumentItem } from '../../api/models';
import { DocStatusChipComponent } from '../../shared/doc-status-chip.component';
import { EMPTY, formatConfidence, formatRelative, formatTokens } from '../../shared/format';

/** Characters of `last_error` shown before the expander. */
export const ERROR_PREVIEW_CHARS = 40;

export function truncate(s: string, n = ERROR_PREVIEW_CHARS): string {
  return s.length <= n ? s : `${s.slice(0, n).trimEnd()}…`;
}

export const DOCUMENT_COLUMNS = [
  'document_name',
  'document_type',
  'status',
  'attempts',
  'last_error',
  'processed_at',
  'total_tokens',
  'search_indexed',
  'actions',
] as const;

/**
 * FR-12 document list: filename, detected type, status, attempts, last error (with an
 * expander), processed time, token usage (input / output) and the search-indexed flag,
 * plus Reprocess. Presentational: the Ingestion page owns loading, sort and paging.
 */
@Component({
  selector: 'app-documents-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatTableModule, MatSortModule, DocStatusChipComponent],
  template: `
    <div class="table-scroll">
      <table
        mat-table
        class="data"
        [dataSource]="rows()"
        [trackBy]="trackById"
        matSort
        [matSortActive]="sort().active"
        [matSortDirection]="sort().direction"
        (matSortChange)="sortChange.emit($event)"
        aria-label="Documents"
      >
        <ng-container matColumnDef="document_name">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>File</th>
          <td mat-cell *matCellDef="let d" class="file">
            @if (d.web_view_link) {
              <a [href]="d.web_view_link" target="_blank" rel="noopener noreferrer" title="Open in Drive">{{ d.document_name }}</a>
            } @else {
              <span [attr.title]="d.source === 'local' ? 'Local file (demo mode)' : null">{{ d.document_name }}</span>
            }
            @if (d.version > 1) {
              <span class="ver" title="New version of an edited file">v{{ d.version }}</span>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="document_type">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Detected type</th>
          <td mat-cell *matCellDef="let d" [attr.title]="d.document_type_confidence !== null ? 'confidence ' + conf(d.document_type_confidence) : null">
            {{ typeLabel(d) }}
          </td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Status</th>
          <td mat-cell *matCellDef="let d">
            <app-doc-status-chip [status]="d.status" />
            @if (d.review_count > 0) {
              <span class="muted small" [title]="d.review_count + ' open review item(s)'"> · {{ d.review_count }} to review</span>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="attempts">
          <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Attempts</th>
          <td mat-cell *matCellDef="let d" class="num">{{ d.attempts }}</td>
        </ng-container>
        <ng-container matColumnDef="last_error">
          <th mat-header-cell *matHeaderCellDef>Last error</th>
          <td mat-cell *matCellDef="let d" class="err-cell" data-testid="last-error">
            @if (d.last_error; as e) {
              @if (expanded().has(d.document_id)) {
                <span class="err full">{{ e }}</span>
                <button type="button" class="btn link" (click)="toggle(d.document_id)" [attr.aria-expanded]="true">less</button>
              } @else {
                <span class="err" [title]="e">{{ short(e) }}</span>
                @if (e.length > previewChars) {
                  <button type="button" class="btn link" (click)="toggle(d.document_id)" [attr.aria-expanded]="false" data-testid="error-more">more</button>
                }
              }
            } @else {
              <span class="muted">{{ dash }}</span>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="processed_at">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Processed</th>
          <td mat-cell *matCellDef="let d" [attr.title]="d.processed_at">{{ d.processed_at ? rel(d.processed_at) : dash }}</td>
        </ng-container>
        <ng-container matColumnDef="total_tokens">
          <th mat-header-cell *matHeaderCellDef mat-sort-header class="num">Tokens in / out</th>
          <td mat-cell *matCellDef="let d" class="num" [attr.title]="tokenTitle(d)" data-testid="tokens">
            @if (d.total_tokens !== null) {
              {{ tok(d.input_tokens) }} / {{ tok(d.output_tokens) }}
            } @else {
              {{ dash }}
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="search_indexed">
          <th mat-header-cell *matHeaderCellDef mat-sort-header>Indexed</th>
          <td mat-cell *matCellDef="let d">
            @if (d.search_indexed) {
              <span class="ok" title="Chunks are indexed and embedded (keyword search and Ask)">✓ indexed</span>
            } @else {
              <span class="muted" title="Not fully indexed yet: embeddings pending (keyword search may already find it)">{{ dash }}</span>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="actions">
          <th mat-header-cell *matHeaderCellDef aria-label="Actions"></th>
          <td mat-cell *matCellDef="let d" class="actions">
            <button
              type="button"
              class="btn"
              [disabled]="!d.can_reprocess || busy().has(d.document_id)"
              [attr.title]="reprocessTitle(d)"
              (click)="reprocess.emit(d)"
              data-testid="reprocess"
            >
              {{ busy().has(d.document_id) ? 'Queuing…' : 'Reprocess' }}
            </button>
          </td>
        </ng-container>

        <tr mat-header-row *matHeaderRowDef="columns"></tr>
        <tr mat-row *matRowDef="let d; columns: columns" [attr.data-doc]="d.document_id" [class.failed]="d.status === 'failed'"></tr>
      </table>
    </div>
  `,
  styles: `
    .file {
      font-weight: 500;
    }
    .file a {
      color: var(--ink);
    }
    .ver {
      margin-left: 6px;
      font-size: 11px;
      color: var(--accent);
      background: var(--accent-soft);
      border-radius: 4px;
      padding: 0 5px;
    }
    /* the cell sizes to its content, so nothing overflows into the next column */
    .err-cell {
      white-space: nowrap;
    }
    .err {
      color: var(--bad);
    }
    .err.full {
      display: block;
      width: 320px;
      max-width: 60vw;
      white-space: normal;
      word-break: break-word;
    }
    .err-cell .btn.link {
      margin-left: 6px;
      font-size: 12px;
    }
    .actions {
      text-align: right;
    }
    .small {
      font-size: 12px;
    }
    .ok {
      color: var(--ok);
    }
    tr.failed td {
      background: #fffafa;
    }
  `,
})
export class DocumentsTableComponent {
  readonly rows = input<DocumentItem[]>([]);
  readonly sort = input<Sort>({ active: '', direction: '' });
  /** server clock in ms, for "3 min ago" */
  readonly now = input<number | null>(null);
  /** document ids with a reprocess request in flight */
  readonly busy = input<ReadonlySet<number>>(new Set());
  readonly sortChange = output<Sort>();
  readonly reprocess = output<DocumentItem>();

  readonly columns = [...DOCUMENT_COLUMNS];
  readonly dash = EMPTY;
  readonly previewChars = ERROR_PREVIEW_CHARS;
  readonly expanded = signal<ReadonlySet<number>>(new Set());

  /** Keep row DOM across the 3 s refreshes (expanders, focus, hover stay put). */
  readonly trackById = (_: number, d: DocumentItem) => d.document_id;

  toggle(id: number): void {
    const next = new Set(this.expanded());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.expanded.set(next);
  }

  short(e: string): string {
    return truncate(e);
  }

  typeLabel(d: DocumentItem): string {
    return d.document_type ? DOCUMENT_TYPE_LABELS[d.document_type] : EMPTY;
  }

  conf(c: number): string {
    return formatConfidence(c);
  }

  tok(n: number | null): string {
    return formatTokens(n);
  }

  tokenTitle(d: DocumentItem): string | null {
    if (d.total_tokens === null) return null;
    const parts = [`${d.input_tokens} input + ${d.output_tokens} output = ${d.total_tokens} tokens`];
    if (d.extraction_model) parts.push(d.extraction_model);
    if (d.extraction_source) parts.push(d.extraction_source === 'fixture' ? 'from fixture' : 'live extraction');
    if (d.latency_ms !== null) parts.push(`${(d.latency_ms / 1000).toFixed(1)} s`);
    return parts.join(' · ');
  }

  rel(ts: string): string {
    return formatRelative(ts, this.now() ?? Date.now());
  }

  reprocessTitle(d: DocumentItem): string {
    if (d.status === 'pending' || d.status === 'processing') return 'Already queued for processing';
    if (d.status === 'unsupported') return 'This file type is not supported';
    return 'Extract this document again';
  }
}

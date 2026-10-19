import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Citation, DOCUMENT_TYPE_LABELS, PassageCitation, SourceTable } from '../../api/models';
import { SourceRef } from '../../shared/source-loader';

/** Rows shown before the "Show all" expander (contract: first 20 visible). */
export const SQL_PREVIEW_ROWS = 20;

const FLAGGED = new Set(['needs_review', 'conflict']);

/**
 * Id columns that let a SQL row open the FR-9 source panel, most specific first:
 * a `v_holdings` row carries both `holding_id` and its `statement_id`, and the row
 * is the holding.
 */
const SOURCE_COLUMNS: Record<string, { table: SourceTable; field: string | null }> = {
  return_id: { table: 'returns', field: null },
  fact_id: { table: 'fund_facts', field: null },
  holding_id: { table: 'statement_holdings', field: null },
  statement_id: { table: 'statements', field: 'closing_balance' },
};

export function formatCell(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '—';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'string') return v;
  // e.g. v_returns.review_reasons: ["conflict"] -> "conflict", [] -> "—"
  if (Array.isArray(v)) return v.length ? v.map((x) => formatCell(x)).join(', ') : '—';
  return JSON.stringify(v);
}

/** Passages longer than this (characters or lines) start collapsed with a "Show full passage" toggle. */
export const PASSAGE_PREVIEW_CHARS = 360;
export const PASSAGE_PREVIEW_LINES = 6;

export function isLongPassage(text: string): boolean {
  return text.length > PASSAGE_PREVIEW_CHARS || text.split('\n').length > PASSAGE_PREVIEW_LINES;
}

/** `kestrel_factsheet_2026-01.pdf · p.2`, or `· rows` for a CSV row group (page null). */
export function passageLabel(c: Pick<PassageCitation, 'document_name' | 'page'>): string {
  return `${c.document_name} · ${c.page === null ? 'rows' : `p.${c.page}`}`;
}

/**
 * One citation card (FR-11). `sql`: the executed SQL plus a small result table (first
 * 20 rows, expander, truncated indicator, flagged-row count). `passage`: document ·
 * page, the text, and a link to the document (Drive when `web_view_link` is set,
 * otherwise the Data page filtered to that document).
 */
@Component({
  selector: 'app-citation-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  // SQL result tables need the full row; passages sit two per row
  host: { '[class.wide]': "citation().type === 'sql'" },
  imports: [RouterLink],
  template: `
    @let c = citation();
    <div class="card cite-card" [class.active]="active()" [attr.data-type]="c.type" [attr.data-cite-card]="c.id" tabindex="-1">
      @if (sql(); as s) {
        <h5>{{ s.id }} · SQL result</h5>
        <pre data-testid="sql">{{ s.sql }}</pre>
        <div class="meta">
          <span>{{ s.row_count }} {{ s.row_count === 1 ? 'row' : 'rows' }}</span>
          @if (s.truncated) {
            <span class="st rev" data-testid="truncated" title="The guard's LIMIT was reached; more rows may exist">truncated at LIMIT</span>
          }
          @if (s.flagged_row_count > 0) {
            <span class="st cf" data-testid="flagged-rows">{{ s.flagged_row_count }} flagged {{ s.flagged_row_count === 1 ? 'row' : 'rows' }}</span>
          }
        </div>
        @if (s.columns.length) {
          <div class="tbl">
            <table class="mini" data-testid="sql-table">
              <thead>
                <tr>
                  @for (col of s.columns; track $index) {
                    <th [class.num]="numeric()[$index]">{{ col }}</th>
                  }
                  @if (sourceCol() !== null) {
                    <th class="src"><span class="sr">Source</span></th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (row of visibleRows(); track $index) {
                  <tr [class.flagged]="isFlagged(row)">
                    @for (v of row; track $index; let k = $index) {
                      <td [class.num]="numeric()[k]">
                        @if (k === statusCol() && isFlaggedValue(v)) {
                          <span class="st" [class.cf]="v === 'conflict'" [class.rev]="v === 'needs_review'">{{ v }}</span>
                        } @else {
                          {{ cell(v) }}
                        }
                      </td>
                    }
                    @if (sourceCol() !== null) {
                      <td class="src">
                        @if (sourceRef(row); as ref) {
                          <button type="button" class="btn link small" (click)="openSource.emit(ref)" data-testid="row-source">Source</button>
                        }
                      </td>
                    }
                  </tr>
                }
              </tbody>
            </table>
          </div>
          @if (s.rows.length > preview) {
            <button type="button" class="btn link small more" (click)="expanded.set(!expanded())" data-testid="rows-toggle">
              {{ expanded() ? 'Show first ' + preview + ' rows' : 'Show all ' + s.rows.length + ' rows' }}
            </button>
          }
        } @else {
          <p class="muted small">No columns returned.</p>
        }
      }
      @if (passage(); as p) {
        <h5>{{ p.id }} · Passage</h5>
        @let long = isLong(p.text);
        <div class="snip" [class.clamped]="long && !passageOpen()" [attr.id]="'passage-' + p.id" data-testid="passage-text">“{{ p.text }}”</div>
        @if (long) {
          <button
            type="button"
            class="btn link small more"
            [attr.aria-expanded]="passageOpen()"
            [attr.aria-controls]="'passage-' + p.id"
            (click)="passageOpen.set(!passageOpen())"
            data-testid="passage-toggle"
          >
            {{ passageOpen() ? 'Show less' : 'Show full passage' }}
          </button>
        }
        <div class="doc-line">
          @if (p.web_view_link) {
            <a [href]="p.web_view_link" target="_blank" rel="noopener noreferrer" data-testid="passage-link">{{ label() }} ↗</a>
            <span class="muted small">
              ·
              <a
                [routerLink]="['/data']"
                [queryParams]="{ document_id: p.document_id, document_name: p.document_name }"
                title="Show this document's values in Data (Ask stays open)"
                data-testid="passage-data-link"
                >values in Data</a
              ></span
            >
          } @else {
            <a [routerLink]="['/data']" [queryParams]="{ document_id: p.document_id, document_name: p.document_name }" data-testid="passage-link"
              >{{ label() }}</a
            >
            <span class="muted small"> · values from this document</span>
          }
          @if (p.document_type) {
            <span class="muted small"> · {{ docType(p) }}</span>
          }
        </div>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      min-width: 0;
    }
    .cite-card {
      padding: 12px;
      transition: box-shadow 0.2s, border-color 0.2s;
      height: 100%;
    }
    .cite-card.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-soft);
    }
    .cite-card:focus {
      outline: none;
    }
    h5 {
      margin: 0 0 6px;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    pre {
      font-family: var(--mono);
      font-size: 12px;
      background: #0f1729;
      color: #dbe4ff;
      border-radius: 8px;
      padding: 10px;
      overflow: auto;
      margin: 6px 0 0;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin: 8px 0 4px;
      font-size: 12px;
      color: var(--muted);
    }
    .tbl {
      overflow-x: auto;
      max-height: 420px;
      overflow-y: auto;
    }
    table.mini {
      width: 100%;
      border-collapse: collapse;
    }
    table.mini th,
    table.mini td {
      padding: 5px 8px;
      font-size: 12px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }
    table.mini th {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      font-weight: 600;
      background: var(--head-bg);
      position: sticky;
      top: 0;
    }
    table.mini .num {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    tr.flagged td {
      background: var(--warn-soft);
    }
    /* wide results scroll sideways; keep each row's Source button in view */
    table.mini .src {
      position: sticky;
      right: 0;
      background: var(--panel);
      box-shadow: -1px 0 0 var(--line);
    }
    table.mini th.src {
      z-index: 1;
      background: var(--head-bg);
    }
    tr.flagged td.src {
      background: var(--warn-soft);
    }
    .snip.clamped {
      max-height: 9.6em;
      overflow: hidden;
      -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent);
      mask-image: linear-gradient(to bottom, #000 70%, transparent);
    }
    .small {
      font-size: 12px;
    }
    .more {
      margin-top: 6px;
    }
    .snip {
      margin-top: 0;
    }
    .doc-line {
      margin-top: 8px;
      font-size: 12.5px;
    }
    .sr {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
    }
  `,
})
export class CitationCardComponent {
  readonly citation = input.required<Citation>();
  readonly active = input(false);
  readonly openSource = output<SourceRef>();

  readonly preview = SQL_PREVIEW_ROWS;
  readonly expanded = signal(false);
  readonly passageOpen = signal(false);
  readonly isLong = isLongPassage;

  readonly sql = computed(() => {
    const c = this.citation();
    return c.type === 'sql' ? c : null;
  });
  readonly passage = computed(() => {
    const c = this.citation();
    return c.type === 'passage' ? c : null;
  });

  readonly visibleRows = computed(() => {
    const s = this.sql();
    if (!s) return [];
    return this.expanded() ? s.rows : s.rows.slice(0, SQL_PREVIEW_ROWS);
  });

  readonly statusCol = computed(() => this.sql()?.columns.indexOf('status') ?? -1);

  /** right-align columns whose non-null values are all numbers (ids excluded) */
  readonly numeric = computed(() => {
    const s = this.sql();
    if (!s) return [];
    return s.columns.map((col, k) => {
      if (col.endsWith('_id') || col === 'id') return false;
      const vals = s.rows.map((r) => r[k]).filter((v) => v !== null && v !== undefined);
      return vals.length > 0 && vals.every((v) => typeof v === 'number');
    });
  });

  readonly sourceCol = computed<{ index: number; table: SourceTable; field: string | null } | null>(() => {
    const s = this.sql();
    if (!s) return null;
    for (const [col, spec] of Object.entries(SOURCE_COLUMNS)) {
      const index = s.columns.indexOf(col);
      if (index !== -1) return { index, ...spec };
    }
    return null;
  });

  readonly label = computed(() => {
    const p = this.passage();
    return p ? passageLabel(p) : '';
  });

  cell(v: unknown): string {
    return formatCell(v);
  }

  isFlaggedValue(v: unknown): boolean {
    return typeof v === 'string' && FLAGGED.has(v);
  }

  isFlagged(row: unknown[]): boolean {
    const k = this.statusCol();
    return k !== -1 && this.isFlaggedValue(row[k]);
  }

  sourceRef(row: unknown[]): SourceRef | null {
    const sc = this.sourceCol();
    if (!sc) return null;
    const id = row[sc.index];
    return typeof id === 'number' && Number.isInteger(id) ? { table: sc.table, id, field: sc.field } : null;
  }

  docType(p: PassageCitation): string {
    return p.document_type ? (DOCUMENT_TYPE_LABELS[p.document_type] ?? p.document_type) : '';
  }
}


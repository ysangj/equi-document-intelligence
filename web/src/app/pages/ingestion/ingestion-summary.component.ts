import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AuthStatusResponse, DOCUMENT_STATUS_LABELS, DocumentStatus, ProgressResponse } from '../../api/models';
import { formatRelative, formatTokens } from '../../shared/format';
import { progressPercent, progressText } from '../../shell/progress-indicator.component';

/** Order of the status cards (mockup: done, processing, failed, needs review, ...). */
export const STATUS_CARDS: DocumentStatus[] = ['done', 'needs_review', 'processing', 'pending', 'failed', 'unsupported'];

/** "Demo mode: watching ./sample_docs" / the account, from `/api/progress` (C9). */
export function connectionLine(p: ProgressResponse): string {
  if (p.mode === 'demo') return `Demo mode: watching ./${p.sync.folder_name ?? 'sample_docs'}`;
  if (p.connection.connected) return p.connection.google_email ?? 'Connected';
  return 'Not connected';
}

/**
 * FR-12 Ingestion header: connected account (or demo folder), watched folder, last
 * successful sync, counts by status and overall progress. Everything comes from the
 * single `/api/progress` poll; `/api/auth/status` only adds "connected since".
 */
@Component({
  selector: 'app-ingestion-summary',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    @if (progress(); as p) {
      <div class="card head" data-testid="ingestion-header">
        <div class="line">
          <span class="conn" data-testid="ing-connection">
            <span class="dot" [class.demo]="p.mode === 'demo'" [class.off]="p.mode === 'drive' && !p.connection.connected"></span>
            <b>{{ connection() }}</b>
            @if (p.mode === 'drive' && !p.connection.connected) {
              · <a routerLink="/connect">Connect Google Drive</a>
            }
            @if (connectedSince(); as since) {
              <span class="muted"> · connected {{ since }}</span>
            }
          </span>
          @if (p.mode === 'drive' && p.sync.folder_name) {
            <span data-testid="ing-folder"><span class="muted">Folder</span> <b>{{ p.sync.folder_name }}</b></span>
          }
          <span data-testid="ing-last-sync" [attr.title]="p.sync.last_success_at">
            <span class="muted">Last successful sync</span> {{ rel(p.sync.last_success_at) }}
          </span>
          @if (p.sync.sync_pending) {
            <span class="st proc" data-testid="sync-pending">sync queued</span>
          }
          @if (p.sync.reconcile_pending) {
            <span class="st proc" data-testid="reconcile-pending">reconcile queued</span>
          }
        </div>
        @if (p.sync.last_error) {
          <div class="sync-err" role="alert">Last sync failed: {{ p.sync.last_error }}</div>
        }
        <div class="progress-line">
          <div class="bar" role="progressbar" [attr.aria-valuenow]="pct()" aria-valuemin="0" aria-valuemax="100">
            <i [style.width.%]="pct()"></i>
          </div>
          <span data-testid="ing-progress">{{ text() }}</span>
        </div>
      </div>

      <div class="stats" role="group" aria-label="Documents by status">
        @for (s of cards; track s) {
          <button
            type="button"
            class="card stat"
            [class.on]="selected() === s"
            [class.zero]="p.counts[s] === 0"
            [attr.aria-pressed]="selected() === s"
            [attr.data-testid]="'stat-' + s"
            (click)="pick.emit(selected() === s ? null : s)"
            [title]="selected() === s ? 'Show all documents' : 'Show only ' + label(s)"
          >
            <b>{{ p.counts[s] }}</b><span>{{ label(s) }}</span>
          </button>
        }
        <div class="card stat tokens" data-testid="stat-tokens" [title]="p.tokens.input_tokens + ' input / ' + p.tokens.output_tokens + ' output tokens'">
          <b>{{ tokens(p.tokens.input_tokens + p.tokens.output_tokens) }}</b>
          <span>tokens · {{ tokens(p.tokens.input_tokens) }} in / {{ tokens(p.tokens.output_tokens) }} out</span>
        </div>
      </div>
    }
  `,
  styles: `
    .head {
      padding: 12px 14px;
      margin-bottom: 12px;
    }
    .line {
      display: flex;
      flex-wrap: wrap;
      gap: 6px 18px;
      align-items: center;
      font-size: 13px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      display: inline-block;
      margin-right: 6px;
    }
    .dot.demo {
      background: var(--warn);
    }
    .dot.off {
      background: var(--muted);
    }
    .sync-err {
      margin-top: 8px;
      color: var(--bad);
      font-size: 12.5px;
    }
    .progress-line {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-top: 10px;
      font-size: 12.5px;
      color: var(--accent-ink);
      flex-wrap: wrap;
    }
    .bar {
      flex: 0 0 220px;
      height: 6px;
      background: #cfd9fb;
      border-radius: 4px;
      overflow: hidden;
    }
    .bar i {
      display: block;
      height: 100%;
      background: var(--accent);
      transition: width 0.4s ease;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }
    .stat {
      padding: 12px 14px;
      text-align: left;
      font: inherit;
      color: var(--ink);
      cursor: pointer;
    }
    .stat.tokens {
      cursor: default;
    }
    .stat b {
      display: block;
      font-size: 20px;
      font-variant-numeric: tabular-nums;
    }
    .stat span {
      color: var(--muted);
      font-size: 12px;
    }
    .stat.zero b {
      color: var(--muted);
    }
    .stat.on {
      border-color: var(--accent);
      box-shadow: inset 0 0 0 1px var(--accent);
    }
    button.stat:hover {
      border-color: #cdd8fb;
    }
    @container (max-width: 1052px) {
      .stats {
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }
    }
    @container (max-width: 568px) {
      .stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .bar {
        flex-basis: 100%;
      }
    }
  `,
})
export class IngestionSummaryComponent {
  readonly progress = input<ProgressResponse | null>(null);
  readonly auth = input<AuthStatusResponse | null>(null);
  /** server clock in ms (C6 relative times) */
  readonly now = input<number | null>(null);
  /** status filter currently applied to the table */
  readonly selected = input<DocumentStatus | null>(null);
  readonly pick = output<DocumentStatus | null>();

  readonly cards = STATUS_CARDS;

  readonly connection = computed(() => {
    const p = this.progress();
    return p ? connectionLine(p) : '';
  });
  readonly connectedSince = computed(() => {
    const a = this.auth();
    const p = this.progress();
    if (!a || !p || p.mode !== 'drive' || !a.connected || !a.connected_at) return null;
    return this.rel(a.connected_at);
  });
  readonly pct = computed(() => {
    const p = this.progress();
    return p ? progressPercent(p) : 0;
  });
  readonly text = computed(() => {
    const p = this.progress();
    return p ? progressText(p) : '';
  });

  label(s: DocumentStatus): string {
    return DOCUMENT_STATUS_LABELS[s];
  }

  tokens(n: number): string {
    return formatTokens(n);
  }

  rel(ts: string | null): string {
    const now = this.now() ?? (this.progress() ? Date.parse(this.progress()!.generated_at) : Date.now());
    return formatRelative(ts, now);
  }
}

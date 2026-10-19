import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ApiError } from '../api/api-error';
import { ProgressResponse } from '../api/models';

/**
 * "17 / 20 documents processed · 2 processing · 1 failed" (FR-9 / FR-12 / §4).
 * `processed` / `total` is the headline; the bar width is `finished / total` (C9).
 */
export function progressText(p: ProgressResponse): string {
  if (p.total === 0) return 'No documents yet';
  const parts = [`${p.processed} / ${p.total} documents processed`];
  if (p.counts.processing > 0) parts.push(`${p.counts.processing} processing`);
  if (p.counts.pending > 0) parts.push(`${p.counts.pending} queued`);
  if (p.counts.failed > 0) parts.push(`${p.counts.failed} failed`);
  if (p.counts.unsupported > 0) parts.push(`${p.counts.unsupported} unsupported`);
  return parts.join(' · ');
}

/** Bar width in percent (0-100). */
export function progressPercent(p: ProgressResponse): number {
  if (p.total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((p.finished / p.total) * 100)));
}

@Component({
  selector: 'app-progress-indicator',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (progress(); as p) {
      <div class="progress" [class.idle]="!p.active" role="status" aria-live="polite">
        <div
          class="bar"
          role="progressbar"
          aria-label="Documents finished"
          [attr.aria-valuenow]="percent()"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <i [style.width.%]="percent()"></i>
        </div>
        <span class="text">{{ text() }}</span>
      </div>
    } @else if (error(); as e) {
      <div class="progress unavailable" role="status">
        <span class="text">Progress unavailable: {{ e.message }}</span>
      </div>
    }
  `,
  styles: `
    .progress {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 8px 24px;
      background: var(--accent-soft);
      font-size: 12.5px;
      color: var(--accent-ink);
    }
    .progress.idle {
      background: #eef1f6;
      color: var(--muted);
    }
    .progress.unavailable {
      background: var(--warn-soft);
      color: var(--warn);
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
    @media (max-width: 600px) {
      .progress {
        padding: 8px 16px;
      }
      .bar {
        flex-basis: 90px;
      }
    }
  `,
})
export class ProgressIndicatorComponent {
  readonly progress = input<ProgressResponse | null>(null);
  readonly error = input<ApiError | null>(null);

  readonly text = computed(() => {
    const p = this.progress();
    return p ? progressText(p) : '';
  });
  readonly percent = computed(() => {
    const p = this.progress();
    return p ? progressPercent(p) : 0;
  });
}

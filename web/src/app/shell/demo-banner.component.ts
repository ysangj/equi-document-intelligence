import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ConfigResponse } from '../api/models';

/**
 * FR-14: "Demo mode" banner, shown only when `/api/config.demo_mode` is true. Says the
 * values come from committed fixtures when `extraction_source` is `"fixture"` (demo mode
 * with no ANTHROPIC_API_KEY). The field is typed `ExtractionSource`, so a misspelled
 * literal here no longer compiles.
 */
@Component({
  selector: 'app-demo-banner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (config()?.demo_mode) {
      <div class="demo" role="note" data-testid="demo-banner">
        <span class="tag">Demo mode</span>
        <span>{{ message() }}</span>
      </div>
    }
  `,
  styles: `
    .demo {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 7px 24px;
      background: var(--warn-soft);
      color: #6b4300;
      font-size: 12.5px;
      border-bottom: 1px solid #f3e1bf;
    }
    .tag {
      background: var(--warn);
      color: #fff;
      border-radius: 6px;
      padding: 1px 8px;
      font-weight: 700;
      font-size: 11.5px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    @media (max-width: 600px) {
      .demo {
        padding: 7px 16px;
        align-items: flex-start;
      }
    }
  `,
})
export class DemoBannerComponent {
  readonly config = input<ConfigResponse | null>(null);

  readonly message = computed(() => {
    const c = this.config();
    if (!c) return '';
    const parts = ['Documents are read from ./sample_docs instead of Google Drive.'];
    if (c.extraction_source === 'fixture') parts.push('Values come from committed fixture extractions.');
    if (!c.features.ask.enabled) parts.push('Ask is disabled.');
    return parts.join(' ');
  });
}

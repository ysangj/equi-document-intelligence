import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { ApiError } from '../../api/api-error';
import { DOCUMENT_TYPE_LABELS, SourceResponse, ValueSnapshot } from '../../api/models';
import { StatusChipComponent } from '../../shared/status-chip.component';
import { HighlightComponent } from '../../shared/highlight.component';
import { Segment, snippetSegments } from '../../shared/headline';
import { EMPTY, formatConfidence, formatKindedValue, formatWeight, kindedSignClass } from '../../shared/format';

/**
 * FR-9 side panel: provenance of one value from `GET /api/sources/{table}/{id}`.
 * Shows document, Drive link (hidden when `web_view_link` is null, i.e. demo mode),
 * page, verbatim snippet (the value inside it marked from `snippet_highlight`), confidence, extraction model and the snippet check.
 * Presentational: the Data page loads the response and passes it in.
 */
@Component({
  selector: 'app-source-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StatusChipComponent, HighlightComponent],
  template: `
    <aside class="card side" aria-label="Value source" data-testid="source-panel">
      @if (source(); as s) {
        <div class="head">
          <h4 data-testid="pv-title">{{ s.label }}</h4>
          <button type="button" class="close" aria-label="Close source panel" (click)="closed.emit()">×</button>
        </div>
        <div class="field muted">{{ s.field_label }}</div>
        <div class="val" [class]="valueClass()" data-testid="pv-value">{{ value() }}</div>
        <div class="status">
          <app-status-chip [status]="s.status" [reasons]="s.review_reasons" />
          @if (loading()) {
            <span class="muted small">Refreshing…</span>
          }
        </div>
        @if (s.reasons.length) {
          <ul class="reasons">
            @for (r of s.reasons; track r.code) {
              <li><b>{{ r.label }}.</b> {{ r.message }}</li>
            }
          </ul>
        }

        <div class="k">Source</div>
        <div data-testid="pv-doc">
          <span class="doc">{{ s.document.document_name }}</span>
          @if (s.provenance.page !== null) {
            <span> · page {{ s.provenance.page }}</span>
          }
        </div>
        <div class="muted small">
          {{ docType() }} · version {{ s.document.version }}
          @if (s.document.source === 'local') {
            · local file
          }
        </div>
        @if (s.document.web_view_link) {
          <a class="drive" [href]="s.document.web_view_link" target="_blank" rel="noopener noreferrer" data-testid="pv-drive">
            Open in Drive ↗
          </a>
        }

        <div class="k">Snippet</div>
        @if (s.provenance.source_snippet) {
          <div class="snip" data-testid="pv-snippet"><app-highlight [segments]="snippet()" /></div>
        } @else {
          <div class="muted">No snippet was extracted for this value.</div>
        }

        <div class="k">Extraction</div>
        <div data-testid="pv-meta">{{ meta() }}</div>

        <div class="k">Checks</div>
        <div data-testid="pv-check" [class]="check().cls">{{ check().text }}</div>

        @if (s.conflicts.length) {
          <div class="k">Conflicting values</div>
          @for (c of s.conflicts; track c.table + c.id) {
            <div class="conflict" data-testid="pv-conflict">
              <b [class]="snapClass(c)">{{ snap(c) }}</b>
              <span class="doc">{{ c.document_name }}</span>
              @if (c.provenance.page !== null) {
                <span class="muted"> · p.{{ c.provenance.page }}</span>
              }
              @if (c.provenance.source_snippet) {
                <div class="snip">{{ c.provenance.source_snippet }}</div>
              }
            </div>
          }
        }

        @if (s.review_history.length) {
          <div class="k">Review history</div>
          <ul class="hist">
            @for (h of s.review_history; track h.review_action_id) {
              <li>{{ h.action }} · {{ h.created_at }}@if (h.note) { · {{ h.note }} }</li>
            }
          </ul>
        }
      } @else if (error(); as e) {
        <div class="head">
          <h4>Source unavailable</h4>
          <button type="button" class="close" aria-label="Close source panel" (click)="closed.emit()">×</button>
        </div>
        <p class="bad" role="alert">{{ e.message }}</p>
      } @else if (loading()) {
        <p class="muted">Loading source…</p>
      } @else {
        <h4>Source</h4>
        <p class="muted">Click any value to see its source document, page and snippet.</p>
      }
    </aside>
  `,
  styles: `
    /* sticky on the host: the grid cell is as tall as the page, the host is not.
       --header-h is the shell header's live height (app.ts). Width rules are container
       queries on the main area (or the Ask source sheet), not the window. */
    :host {
      display: block;
      position: sticky;
      top: calc(var(--header-h, 160px) + 16px);
      align-self: start;
    }
    .side {
      padding: 16px;
      max-height: calc(100vh - var(--header-h, 160px) - 32px);
      overflow: auto;
    }
    @container (max-width: 852px) {
      :host {
        position: static;
      }
      .side {
        max-height: none;
      }
    }
    .head {
      display: flex;
      align-items: flex-start;
      gap: 8px;
    }
    h4 {
      margin: 0 0 4px;
      flex: 1;
    }
    .close {
      border: 0;
      background: none;
      font-size: 20px;
      line-height: 1;
      cursor: pointer;
      color: var(--muted);
      padding: 0 2px;
    }
    .field {
      font-size: 12px;
    }
    .val {
      font-size: 22px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      word-break: break-word;
    }
    .status {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 4px;
    }
    .reasons,
    .hist {
      margin: 8px 0 0;
      padding-left: 18px;
      font-size: 12.5px;
    }
    .k {
      color: var(--muted);
      font-size: 12px;
      margin-top: 12px;
    }
    .small {
      font-size: 12px;
    }
    .drive {
      display: inline-block;
      margin-top: 4px;
      font-size: 12.5px;
    }
    .ok {
      color: var(--ok);
    }
    .bad {
      color: var(--bad);
    }
    .skip {
      color: var(--muted);
    }
    .conflict {
      margin-top: 6px;
      font-size: 12.5px;
    }
    .conflict b {
      margin-right: 6px;
    }
    @container (max-width: 852px) {
      .side {
        position: static;
        max-height: none;
      }
    }
  `,
})
export class SourcePanelComponent {
  readonly source = input<SourceResponse | null>(null);
  readonly loading = input(false);
  readonly error = input<ApiError | null>(null);
  readonly closed = output<void>();

  readonly value = computed(() => {
    const s = this.source();
    if (!s) return EMPTY;
    if (s.table === 'statement_holdings' && s.field === 'weight') return formatWeight(s.value_pct);
    return formatKindedValue(s);
  });
  readonly valueClass = computed(() => {
    const s = this.source();
    return s ? kindedSignClass(s) : '';
  });
  /** The snippet with the value in `<mark>`; plain when `snippet_highlight` is null or does not fit. */
  readonly snippet = computed<Segment[]>(() => {
    const s = this.source();
    return s ? snippetSegments(s.provenance.source_snippet, s.snippet_highlight) : [];
  });
  readonly docType = computed(() => {
    const t = this.source()?.document.document_type;
    return t ? DOCUMENT_TYPE_LABELS[t] : 'Unclassified';
  });

  /** "confidence 0.97 · claude-opus-5-5 · prompt v1 · fixture" */
  readonly meta = computed(() => {
    const s = this.source();
    if (!s) return '';
    const parts = [`confidence ${formatConfidence(s.provenance.confidence)}`];
    if (s.extraction.model) parts.push(s.extraction.model);
    if (s.extraction.prompt_version) parts.push(`prompt ${s.extraction.prompt_version}`);
    if (s.extraction.source) parts.push(s.extraction.source === 'fixture' ? 'from fixture' : 'live extraction');
    return parts.join(' · ');
  });

  readonly check = computed<{ text: string; cls: string }>(() => snippetCheck(this.source()));

  snap(c: ValueSnapshot): string {
    return formatKindedValue(c);
  }

  snapClass(c: ValueSnapshot): string {
    return kindedSignClass(c);
  }
}

/**
 * Server message prefix for `snippet_not_found` with `value_missing: true` (app/api/values.py).
 * `SourceResponse` carries `reasons[]` but not `review_detail`, so this is the only signal
 * the panel has; see the U7 request to backend-engineer for a structured flag.
 */
const VALUE_MISSING_MESSAGE = /^The value is not written in its source snippet/;

/** The panel's snippet check line: found / found but without the value / not found / skipped. */
export function snippetCheck(s: Pick<SourceResponse, 'provenance' | 'reasons'> | null | undefined): { text: string; cls: string } {
  const v = s?.provenance.snippet_verified;
  if (v === true) return { text: '✓ Snippet found in the document text', cls: 'ok' };
  if (v === false) {
    const valueMissing = s!.reasons.some((r) => r.code === 'snippet_not_found' && VALUE_MISSING_MESSAGE.test(r.message));
    return valueMissing
      ? { text: "✗ Snippet found, but it doesn't contain the value", cls: 'bad' }
      : { text: '✗ Snippet not found in the document text', cls: 'bad' };
  }
  return { text: 'Snippet check skipped (image-only page or no snippet)', cls: 'skip' };
}

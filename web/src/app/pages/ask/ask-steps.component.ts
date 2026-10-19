import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { AskStep } from '../../api/models';

/** "1 tool call" / "3 tool calls" · "6.4s" */
export function stepsSummary(steps: AskStep[], durationMs: number | null): string {
  const n = steps.length;
  const calls = `${n} tool ${n === 1 ? 'call' : 'calls'}`;
  const guard = steps.filter((s) => s.is_guard_error).length;
  const parts = [calls];
  if (guard) parts.push(`${guard} rejected by SQL guard`);
  if (durationMs !== null) parts.push(formatDuration(durationMs));
  return parts.join(' · ');
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const FILTER_KEYS = ['k', 'document_type', 'fund'] as const;

/**
 * "How I got this" (FR-11): every tool call in order with its input (SQL, or the
 * search query and filters), result summary, duration and the citations it produced.
 * Steps rejected by the SQL guard are styled apart and show the guard message (§15).
 */
@Component({
  selector: 'app-ask-steps',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <details class="how" [open]="open()" (toggle)="onToggle($event)" data-testid="steps">
      <summary>How I got this · {{ summary() }}</summary>
      <ol class="steps">
        @for (s of steps(); track s.index) {
          <li class="step" [class.guard]="s.is_guard_error" [class.failed]="!s.is_guard_error && !!s.error" [attr.data-step]="s.index" data-testid="step">
            <div class="line">
              <span class="n">{{ s.index }}</span>
              <span class="tool" [class.sqltool]="s.tool === 'query_sql'">{{ s.tool }}</span>
              @if (s.is_guard_error) {
                <span class="st cf" data-testid="guard-badge">Rejected by SQL guard</span>
              } @else if (s.error) {
                <span class="st rev">Error</span>
              }
              <span class="arrow" aria-hidden="true">→</span>
              <span class="summary">{{ s.result_summary }}</span>
              <span class="spacer"></span>
              <span class="dur">{{ dur(s.duration_ms) }}</span>
            </div>
            @if (sqlOf(s); as sql) {
              <pre class="input" data-testid="step-sql">{{ sql }}</pre>
            } @else if (queryOf(s); as q) {
              <div class="input search" data-testid="step-search">
                <span class="q">“{{ q }}”</span>
                @for (f of filtersOf(s); track f.key) {
                  <span class="filter">{{ f.key }}={{ f.value }}</span>
                }
              </div>
            } @else {
              <pre class="input">{{ json(s.input) }}</pre>
            }
            @if (s.error) {
              <div class="error" role="note" data-testid="step-error">{{ s.error }}</div>
            }
            @if (s.citation_ids.length) {
              <div class="cites">
                Cited as
                @for (id of s.citation_ids; track id) {
                  <button type="button" class="cite" (click)="cite.emit(id)" [attr.aria-label]="'Show citation ' + id">{{ id }}</button>
                }
              </div>
            }
          </li>
        }
      </ol>
    </details>
  `,
  styles: `
    .how {
      margin-top: 14px;
    }
    summary {
      cursor: pointer;
      color: var(--muted);
    }
    .steps {
      list-style: none;
      margin: 8px 0 0;
      padding: 0;
      display: grid;
      gap: 8px;
    }
    .step {
      border: 1px solid var(--line);
      border-left: 3px solid var(--accent);
      border-radius: 8px;
      padding: 8px 10px;
      background: var(--panel);
      font-size: 12.5px;
      min-width: 0;
    }
    .step.guard {
      border-left-color: var(--bad);
      background: var(--bad-soft);
    }
    .step.failed {
      border-left-color: var(--warn);
    }
    .line {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    .n {
      font-weight: 700;
      color: var(--muted);
    }
    .tool {
      font-family: var(--mono);
      font-weight: 600;
    }
    .arrow,
    .dur {
      color: var(--muted);
    }
    .summary {
      color: var(--ink);
    }
    .input {
      margin: 6px 0 0;
    }
    pre.input {
      font-family: var(--mono);
      font-size: 12px;
      background: #0f1729;
      color: #dbe4ff;
      border-radius: 8px;
      padding: 8px 10px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .step.guard pre.input {
      text-decoration: line-through;
      text-decoration-color: rgba(255, 140, 140, 0.7);
    }
    .search {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      font-family: var(--mono);
      font-size: 12px;
    }
    .filter {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 8px;
      background: var(--head-bg);
    }
    .error {
      margin-top: 6px;
      color: var(--bad);
      font-family: var(--mono);
      font-size: 12px;
    }
    .cites {
      margin-top: 6px;
      color: var(--muted);
    }
    .cite {
      border: 0;
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 5px;
      padding: 0 6px;
      font: inherit;
      font-size: 11.5px;
      font-weight: 700;
      margin-left: 4px;
      cursor: pointer;
    }
  `,
})
export class AskStepsComponent {
  readonly steps = input<AskStep[]>([]);
  readonly durationMs = input<number | null>(null);
  readonly cite = output<number>();
  /** expanded state, kept by the caller (AskSessionService) so it survives navigation */
  readonly open = input(false);
  readonly openChange = output<boolean>();

  readonly summary = computed(() => stepsSummary(this.steps(), this.durationMs()));

  onToggle(e: Event): void {
    const open = (e.target as HTMLDetailsElement).open;
    if (open !== this.open()) this.openChange.emit(open);
  }

  dur(ms: number): string {
    return formatDuration(ms);
  }

  sqlOf(s: AskStep): string | null {
    const v = s.input['sql'];
    return s.tool === 'query_sql' && typeof v === 'string' ? v : null;
  }

  queryOf(s: AskStep): string | null {
    const v = s.input['query'];
    return s.tool === 'search_documents' && typeof v === 'string' ? v : null;
  }

  filtersOf(s: AskStep): { key: string; value: string }[] {
    return FILTER_KEYS.filter((k) => s.input[k] !== undefined && s.input[k] !== null).map((k) => ({ key: k, value: String(s.input[k]) }));
  }

  json(v: unknown): string {
    return JSON.stringify(v, null, 2);
  }
}

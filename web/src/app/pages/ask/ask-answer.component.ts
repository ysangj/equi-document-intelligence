import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, inject, input, output, signal } from '@angular/core';

import { RouterLink } from '@angular/router';

import { AskResponse } from '../../api/models';
import { SourceRef } from '../../shared/source-loader';
import { AskStepsComponent } from './ask-steps.component';
import { CitationCardComponent } from './citation-card.component';
import { MarkdownViewComponent } from './markdown-view.component';

/** How long a citation card stays highlighted after its chip is clicked. */
export const CITE_HIGHLIGHT_MS = 2500;

/**
 * One Ask answer (FR-11): notices (flagged data §17, unverified numbers, step budget),
 * the markdown answer with citation chips, the citation cards and "How I got this".
 * Clicking a chip scrolls to its card and highlights it.
 */
@Component({
  selector: 'app-ask-answer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, MarkdownViewComponent, CitationCardComponent, AskStepsComponent],
  template: `
    @let r = response();
    @if (r.uses_flagged_data || r.unverified_numbers.length || r.stop_reason === 'max_steps') {
      <div class="notices">
        @if (r.uses_flagged_data) {
          <div class="notice warn" role="note" data-testid="flagged-notice">
            <b>Includes flagged data.</b> Some values used here are marked <span class="st rev">needs review</span> or
            <span class="st cf">conflict</span>. Check them in <a routerLink="/review">Review</a>.
          </div>
        }
        @if (r.unverified_numbers.length) {
          <div class="notice warn" role="note" data-testid="unverified-notice">
            {{ r.unverified_numbers.length }} {{ r.unverified_numbers.length === 1 ? 'number' : 'numbers' }} could not be verified against
            the tool results:
            @for (n of r.unverified_numbers; track n; let last = $last) {
              <span class="num">{{ n }}</span>{{ last ? '' : ', ' }}
            }
            . Derived figures (differences, averages) usually show up here.
          </div>
        }
        @if (r.stop_reason === 'max_steps') {
          <div class="notice" role="note" data-testid="max-steps-notice">
            The step budget ran out, so this is a best-effort answer. Try a narrower question.
          </div>
        }
      </div>
    }

    <app-markdown-view
      [markdown]="r.answer"
      [citationIds]="citationIds()"
      [unverified]="r.unverified_numbers"
      [activeCitation]="active()"
      (cite)="focusCitation($event)"
    />

    @if (r.citations.length) {
      <div class="cites" data-testid="citations">
        @for (c of r.citations; track c.id) {
          <app-citation-card [citation]="c" [active]="active() === c.id" (openSource)="openSource.emit($event)" />
        }
      </div>
    }

    @if (r.steps.length) {
      <app-ask-steps
        [steps]="r.steps"
        [durationMs]="r.duration_ms"
        [open]="stepsOpen()"
        (openChange)="stepsOpenChange.emit($event)"
        (cite)="focusCitation($event)"
      />
    }
    <div class="foot muted">{{ r.model }} · {{ tokens() }} tokens</div>
  `,
  styles: `
    :host {
      display: block;
    }
    .notices {
      display: grid;
      gap: 6px;
      margin-bottom: 10px;
    }
    .notice {
      border-radius: 8px;
      padding: 7px 11px;
      font-size: 12.5px;
      background: var(--accent-soft);
      color: var(--accent-ink);
    }
    .notice.warn {
      background: var(--warn-soft);
      color: var(--warn);
    }
    .notice .num {
      font-family: var(--mono);
      font-weight: 600;
    }
    .cites {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    .cites app-citation-card.wide {
      grid-column: 1 / -1;
    }
    .foot {
      margin-top: 8px;
      font-size: 11.5px;
    }
    /* the nearest container is the Ask panel: one column unless it is dragged wide */
    @container (max-width: 640px) {
      .cites {
        grid-template-columns: minmax(0, 1fr);
      }
    }
  `,
})
export class AskAnswerComponent {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly response = input.required<AskResponse>();
  readonly openSource = output<SourceRef>();
  readonly stepsOpen = input(false);
  readonly stepsOpenChange = output<boolean>();

  readonly active = signal<number | null>(null);
  readonly citationIds = computed(() => this.response().citations.map((c) => c.id));
  readonly tokens = computed(() => {
    const u = this.response().usage;
    return (u.input_tokens + u.output_tokens).toLocaleString('en-US');
  });

  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.timer !== null && clearTimeout(this.timer));
  }

  focusCitation(id: number): void {
    this.active.set(id);
    const card = this.host.nativeElement.querySelector<HTMLElement>(`[data-cite-card="${id}"]`);
    card?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    card?.focus?.({ preventScroll: true });
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.active.set(null);
      this.timer = null;
    }, CITE_HIGHLIGHT_MS);
  }
}

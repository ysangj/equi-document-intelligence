import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { ASK_MAX_QUESTION_LENGTH } from '../../api/models';
import { ConfigService } from '../../core/config.service';
import { SourceRef } from '../../shared/source-loader';
import { AskPanelService } from '../../shell/ask-panel.service';
import { AskAnswerComponent } from './ask-answer.component';
import { askDisabledReason } from './ask-disabled';
import { AskSessionService } from './ask-session.service';

/** Example questions from the brief (§15 Ask rows). */
export const EXAMPLE_QUESTIONS = [
  'Which fund had the best January return?',
  'What did managers say about rate risk?',
  'Which fund had the worst March and what did the manager say about it?',
] as const;

/**
 * The Ask conversation (FR-11), rendered inside the docked Ask panel (shell/ask-dock).
 * A scrolling list of turns over a composer pinned to the bottom. All state lives in
 * `AskSessionService`, so the panel can close and the main area can navigate without
 * losing or re-sending anything. Citations that open a source go to
 * `AskPanelService.showSource` (the shell shows the FR-9 panel in the main area).
 */
@Component({
  selector: 'app-ask-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, MatProgressSpinnerModule, AskAnswerComponent],
  template: `
    <div class="scroll" #scroll data-testid="ask-scroll">
      @if (disabledReason(); as reason) {
        <div class="off" role="status" data-testid="ask-disabled">
          <b>Ask is unavailable.</b> {{ reason }}
          <span class="muted"> Browsing, Search and Review still work.</span>
        </div>
      }

      @if (turns().length) {
        <div class="turns" data-testid="turns">
          @for (t of turns(); track t.id) {
            <section class="turn" [attr.data-turn]="t.id">
              <div class="q" data-testid="question">{{ t.question }}</div>
              @switch (t.status) {
                @case ('pending') {
                  <div class="pending" aria-live="polite" data-testid="ask-pending">
                    <mat-spinner diameter="18" />
                    <span>Thinking: querying the data and searching documents… (up to 60 s)</span>
                  </div>
                }
                @case ('error') {
                  <div class="err" role="alert" data-testid="ask-error">
                    <b>Couldn't answer.</b> {{ t.error?.message }}
                    @for (d of t.error?.details ?? []; track $index) {
                      <div class="small">{{ d.loc ? d.loc + ': ' : '' }}{{ d.message }}</div>
                    }
                    @if (t.error?.code !== 'ask_disabled') {
                      <button type="button" class="btn small" (click)="retry(t.id)" [disabled]="pending()">Retry</button>
                    }
                  </div>
                }
                @case ('done') {
                  <div class="answer" data-testid="answer">
                    <app-ask-answer
                      [response]="t.response!"
                      [stepsOpen]="session.isStepsOpen(t.id)"
                      (stepsOpenChange)="session.setStepsOpen(t.id, $event)"
                      (openSource)="openSource($event)"
                    />
                  </div>
                }
              }
            </section>
          }
        </div>
      } @else {
        <div class="intro">
          <p class="muted small">
            Answers come from SQL over the extracted data and from document passages, with citations. The conversation stays here while
            you move between Data, Ingestion and Review.
          </p>
          <div class="sugg" aria-label="Example questions">
            @for (q of examples; track q) {
              <button type="button" class="chip" (click)="askExample(q)" [disabled]="!!disabledReason() || pending()" data-testid="example">{{ q }}</button>
            }
          </div>
        </div>
      }
    </div>

    <form class="composer" (ngSubmit)="submit()" [class.disabled]="!!disabledReason()">
      <label class="sr" for="ask-input">Question</label>
      <textarea
        #box
        id="ask-input"
        name="question"
        [ngModel]="session.draft()"
        (ngModelChange)="session.draft.set($event)"
        [disabled]="!!disabledReason()"
        [attr.maxlength]="maxLength"
        [placeholder]="turns().length ? 'Ask a follow-up…' : 'Ask about returns, statements or what managers said…'"
        (keydown.enter)="onEnter($event)"
        data-testid="ask-input"
      ></textarea>
      <div class="row">
        <span class="muted small hint">Enter to send · Shift+Enter for a new line</span>
        <span class="spacer"></span>
        <button type="submit" class="btn primary" [disabled]="!canSubmit()" data-testid="ask-submit">
          {{ pending() ? 'Asking…' : 'Ask' }}
        </button>
      </div>
    </form>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      /* citation grid and cards respond to the panel width, not the window */
      container-type: inline-size;
    }
    .scroll {
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      overscroll-behavior: contain;
      padding: 12px 16px 8px;
      position: relative;
    }
    .small {
      font-size: 12px;
    }
    .intro p {
      margin: 4px 0 12px;
    }
    .off {
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 8px;
      padding: 8px 12px;
      margin-bottom: 12px;
    }
    .turn {
      border-top: 1px solid var(--line);
      padding: 14px 0;
    }
    .turn:first-child {
      border-top: 0;
      padding-top: 2px;
    }
    .q {
      font-weight: 600;
      margin-bottom: 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .pending {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
    }
    .err {
      background: var(--bad-soft);
      color: var(--bad);
      border-radius: 8px;
      padding: 8px 12px;
    }
    .err .btn {
      margin-top: 6px;
    }
    .btn.small {
      padding: 3px 9px;
      font-size: 12.5px;
    }
    .composer {
      flex: 0 0 auto;
      border-top: 1px solid var(--line);
      padding: 10px 16px 12px;
      background: var(--panel);
    }
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      min-height: 56px;
      max-height: 40vh;
      resize: vertical;
      background: var(--panel);
      color: var(--ink);
    }
    textarea:focus {
      outline: none;
      border-color: var(--accent);
    }
    textarea:disabled {
      background: var(--head-bg);
      cursor: not-allowed;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 8px;
    }
    .sugg {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .sugg .chip {
      cursor: pointer;
      color: var(--accent);
      border-color: #cdd8fb;
      background: var(--accent-soft);
      text-align: left;
    }
    .sugg .chip:disabled {
      opacity: 0.55;
      cursor: default;
    }
    .sr {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
    }
    @container (max-width: 360px) {
      .hint {
        display: none;
      }
    }
  `,
})
export class AskPanelComponent {
  private readonly config = inject(ConfigService);
  readonly session = inject(AskSessionService);
  private readonly panel = inject(AskPanelService);
  private readonly box = viewChild<ElementRef<HTMLTextAreaElement>>('box');
  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroll');

  readonly examples = EXAMPLE_QUESTIONS;
  readonly maxLength = ASK_MAX_QUESTION_LENGTH;
  readonly turns = this.session.turns;
  readonly pending = this.session.pending;

  readonly disabledReason = computed(() => askDisabledReason(this.config, this.session));
  readonly canSubmit = computed(() => !this.disabledReason() && !this.pending() && this.session.draft().trim().length > 0);

  /** last turn id + status: scroll when a turn is added or settles, not on every change */
  private readonly lastTurnKey = computed(() => {
    const t = this.turns().at(-1);
    return t ? `${t.id}:${t.status}` : '';
  });

  constructor() {
    // Bring the newest turn's question to the top of the panel as it is asked and answered.
    effect(() => {
      if (!this.lastTurnKey()) return;
      queueMicrotask(() => {
        const sc = this.scroller()?.nativeElement;
        const last = sc?.querySelector<HTMLElement>('.turn:last-of-type');
        if (sc && last) sc.scrollTo?.({ top: Math.max(0, last.offsetTop - 8), behavior: 'smooth' });
      });
    });
    effect(() => {
      // the shell bumps this when the panel opens
      if (!this.panel.focusRequest()) return;
      queueMicrotask(() => {
        const el = this.box()?.nativeElement;
        if (el && !el.disabled) el.focus({ preventScroll: true });
      });
    });
  }

  submit(): void {
    if (!this.canSubmit()) return;
    if (this.session.ask(this.session.draft())) this.session.draft.set('');
  }

  onEnter(e: Event): void {
    const k = e as KeyboardEvent;
    if (k.shiftKey || k.isComposing) return;
    e.preventDefault();
    this.submit();
  }

  askExample(q: string): void {
    if (this.disabledReason() || this.pending()) return;
    this.session.draft.set(q);
    this.submit();
  }

  retry(id: number): void {
    this.session.retry(id);
  }

  openSource(ref: SourceRef): void {
    this.panel.showSource(ref);
  }

  focus(): void {
    this.box()?.nativeElement.focus();
  }
}

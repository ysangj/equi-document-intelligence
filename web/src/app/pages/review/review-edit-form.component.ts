import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal, untracked } from '@angular/core';

import { EditAction, ReviewItem } from '../../api/models';
import { defaultEditTarget, editBody, editInitialText, editSuggestions, editTargets, parseEditInput } from './review-format';

let uid = 0;

/**
 * Inline Edit (FR-13 "edit value"). Picks the field (statements: any money field or
 * the period dates, which get a date input and are sent as `"YYYY-MM-DD"` in `value`;
 * conflicts: which candidate keeps the value), validates as you type for the value's
 * kind, and emits the exact `edit` body. Percent values are typed as percentages
 * (`-0.15` means -0.15%) and sent as `value_pct`.
 */
@Component({
  selector: 'app-review-edit-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <form class="edit" (submit)="submit($event)" data-testid="edit-form" novalidate>
      @if (targets().length > 1) {
        <label class="f">
          <span>{{ item().kind === 'conflict' ? 'Keep row' : 'Field' }}</span>
          <select (change)="pick($any($event.target).value)" data-testid="edit-field" [attr.id]="id + '-field'">
            @for (t of targets(); track $index) {
              <option [value]="$index" [selected]="$index === index()">{{ t.label }}</option>
            }
          </select>
        </label>
      }
      <label class="f">
        <span>New value{{ unitHint() }}</span>
        <span class="inp">
          <!-- [type] is a property binding before [value] on purpose: switching from a date
               field to a money field must change the type before the new value is set,
               or the date input drops the number. -->
          <input
            [type]="kind() === 'date' ? 'date' : 'text'"
            [value]="text()"
            (input)="onInput($any($event.target).value)"
            [attr.inputmode]="kind() === 'text' || kind() === 'date' ? null : 'decimal'"
            [attr.aria-invalid]="!!shownError()"
            [attr.aria-describedby]="shownError() ? id + '-err' : null"
            data-testid="edit-value"
            autocomplete="off"
          />
          @if (kind() === 'percent') {
            <span class="suffix">%</span>
          }
        </span>
      </label>
      <label class="f note">
        <span>Note (optional)</span>
        <input [value]="note()" (input)="note.set($any($event.target).value)" maxlength="2000" data-testid="edit-note" />
      </label>
      <div class="btns">
        <button type="submit" class="btn primary" [disabled]="busy() || !parsed().ok" data-testid="edit-save">Save</button>
        <button type="button" class="btn" (click)="cancel.emit()" [disabled]="busy()">Cancel</button>
      </div>
      @if (shownError(); as e) {
        <div class="err" [attr.id]="id + '-err'" role="alert" data-testid="edit-error">{{ e }}</div>
      }
      @if (suggestions().length) {
        <div class="sugg">
          @for (s of suggestions(); track s.label) {
            <button type="button" class="chip" (click)="useSuggestion(s.target, s.text)">{{ s.label }}</button>
          }
        </div>
      }
    </form>
  `,
  styles: `
    .edit {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      align-items: flex-end;
      margin-top: 10px;
      padding: 10px 12px;
      background: var(--head-bg);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .f {
      display: grid;
      gap: 2px;
      font-size: 12px;
      color: var(--muted);
    }
    .f.note {
      flex: 1;
      min-width: 160px;
    }
    .inp {
      position: relative;
      display: inline-flex;
      align-items: center;
    }
    input,
    select {
      font: inherit;
      font-size: 13px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      background: var(--panel);
      min-width: 0;
    }
    .inp input {
      width: 150px;
      padding-right: 22px;
      font-variant-numeric: tabular-nums;
    }
    input[aria-invalid='true'] {
      border-color: var(--bad);
    }
    .suffix {
      position: absolute;
      right: 8px;
      color: var(--muted);
    }
    .btns {
      display: flex;
      gap: 6px;
    }
    .err {
      flex-basis: 100%;
      color: var(--bad);
      font-size: 12px;
    }
    .sugg {
      flex-basis: 100%;
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .sugg .chip {
      cursor: pointer;
      color: var(--accent);
      border-color: #cdd8fb;
      background: var(--accent-soft);
    }
  `,
})
export class ReviewEditFormComponent {
  readonly item = input.required<ReviewItem>();
  readonly busy = input(false);
  readonly save = output<EditAction>();
  readonly cancel = output<void>();

  readonly id = `edit-${++uid}`;
  readonly targets = computed(() => editTargets(this.item()));
  readonly suggestions = computed(() => editSuggestions(this.item()));
  readonly index = signal(0);
  readonly text = signal('');
  readonly note = signal('');
  readonly touched = signal(false);

  readonly target = computed(() => this.targets()[this.index()] ?? this.targets()[0]);
  readonly kind = computed(() => this.target()?.snapshot.value_kind ?? 'number');
  readonly unitHint = computed(() => {
    const t = this.target()?.snapshot;
    if (!t) return '';
    if (t.value_kind === 'money' && t.currency) return ` (${t.currency})`;
    return '';
  });
  readonly parsed = computed(() => parseEditInput(this.kind(), this.text()));
  readonly shownError = computed(() => {
    const p = this.parsed();
    return this.touched() && !p.ok ? p.error : null;
  });

  constructor() {
    // (re)initialise when the item changes
    effect(() => {
      const item = this.item();
      untracked(() => {
        const i = defaultEditTarget(item);
        this.index.set(i);
        const t = this.targets()[i];
        this.text.set(t ? editInitialText(t.snapshot) : '');
        this.touched.set(false);
      });
    });
  }

  pick(v: string): void {
    const i = Number(v);
    this.index.set(i);
    const t = this.targets()[i];
    if (t) this.text.set(editInitialText(t.snapshot));
    this.touched.set(false);
  }

  onInput(v: string): void {
    this.text.set(v);
    this.touched.set(true);
  }

  useSuggestion(target: number, text: string): void {
    this.index.set(target);
    this.text.set(text);
    this.touched.set(true);
  }

  submit(e: Event): void {
    e.preventDefault();
    this.touched.set(true);
    const t = this.target();
    if (!t) return;
    const r = editBody(this.item(), t, this.text(), this.note());
    if (r.ok) this.save.emit(r.body);
  }
}

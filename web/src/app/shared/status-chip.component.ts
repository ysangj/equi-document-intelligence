import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { REVIEW_REASON_LABELS, ReviewReason, VALUE_STATUS_LABELS, ValueStatus } from '../api/models';

const CLASS: Record<ValueStatus, string> = { ok: 'ok', needs_review: 'rev', conflict: 'cf' };

/** Value status badge (`ok` / `needs review` / `conflict`), mockup `.st` styling. */
@Component({
  selector: 'app-status-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="st" [class]="'st ' + cls()" [attr.title]="title()">{{ label() }}</span>`,
})
export class StatusChipComponent {
  readonly status = input.required<ValueStatus>();
  readonly reasons = input<ReviewReason[]>([]);

  readonly cls = computed(() => CLASS[this.status()] ?? 'rev');
  readonly label = computed(() => VALUE_STATUS_LABELS[this.status()] ?? this.status());
  readonly title = computed(() => {
    const r = this.reasons();
    return r.length ? r.map((x) => REVIEW_REASON_LABELS[x] ?? x).join(', ') : null;
  });
}

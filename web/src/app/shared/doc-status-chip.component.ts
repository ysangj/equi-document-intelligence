import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { DOCUMENT_STATUS_LABELS, DocumentStatus } from '../api/models';

const CLASS: Record<DocumentStatus, string> = {
  pending: 'pend',
  processing: 'proc',
  done: 'ok',
  needs_review: 'rev',
  failed: 'fail',
  unsupported: 'unsup',
};

/** Document status badge (Ingestion, search hits), mockup `.st` styling. */
@Component({
  selector: 'app-doc-status-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span [class]="'st ' + cls()" data-testid="doc-status">{{ label() }}</span>`,
  styles: `
    .st.pend {
      background: var(--head-bg);
      color: var(--muted);
      border: 1px dashed var(--line);
    }
    .st.unsup {
      background: var(--head-bg);
      color: var(--muted);
    }
  `,
})
export class DocStatusChipComponent {
  readonly status = input.required<DocumentStatus>();
  readonly cls = computed(() => CLASS[this.status()] ?? 'pend');
  readonly label = computed(() => DOCUMENT_STATUS_LABELS[this.status()] ?? this.status());
}

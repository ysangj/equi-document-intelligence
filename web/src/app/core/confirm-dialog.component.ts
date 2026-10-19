import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

export interface ConfirmDialogData {
  title: string;
  /** paragraphs of plain text */
  body: string[];
  confirm: string;
  cancel?: string;
  /** red confirm button (destructive action) */
  danger?: boolean;
}

/** A small yes/no dialog (Disconnect, Watch folder). Resolves true on confirm. */
@Component({
  selector: 'app-confirm-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatDialogModule],
  template: `
    <div class="dlg" data-testid="confirm-dialog">
      <h2>{{ data.title }}</h2>
      @for (p of data.body; track $index) {
        <p>{{ p }}</p>
      }
      <div class="actions">
        <button type="button" class="btn" (click)="ref.close(false)" data-testid="confirm-cancel">{{ data.cancel ?? 'Cancel' }}</button>
        <button
          type="button"
          class="btn primary"
          [class.danger]="data.danger"
          (click)="ref.close(true)"
          data-testid="confirm-ok"
          cdkFocusInitial
        >
          {{ data.confirm }}
        </button>
      </div>
    </div>
  `,
  styles: `
    .dlg {
      padding: 20px 22px 16px;
      max-width: 460px;
      color: var(--ink);
    }
    h2 {
      margin: 0 0 8px;
      font-size: 16px;
    }
    p {
      margin: 0 0 8px;
      color: var(--muted);
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
    }
    .btn.primary.danger {
      background: var(--bad);
      border-color: var(--bad);
    }
  `,
})
export class ConfirmDialogComponent {
  readonly data = inject<ConfirmDialogData>(MAT_DIALOG_DATA);
  readonly ref = inject<MatDialogRef<ConfirmDialogComponent, boolean>>(MatDialogRef);
}

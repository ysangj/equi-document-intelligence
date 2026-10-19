import { ChangeDetectionStrategy, Component, input, model } from '@angular/core';
import { MatCheckboxModule } from '@angular/material/checkbox';

export interface Option<T> {
  value: T;
  label: string;
  hint?: string | null;
}

/** Multi-select list used inside filter chips. Empty selection means "All". */
@Component({
  selector: 'app-check-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [MatCheckboxModule],
  template: `
    <div class="head">
      <span class="muted">{{ selected().length ? selected().length + ' selected' : 'All' }}</span>
      @if (selected().length) {
        <button type="button" class="btn link" (click)="selected.set([])">Clear</button>
      }
    </div>
    @for (o of options(); track o.value) {
      <mat-checkbox [checked]="isOn(o.value)" (change)="toggle(o.value)">
        {{ o.label }}
        @if (o.hint) {
          <small class="muted">{{ o.hint }}</small>
        }
      </mat-checkbox>
    } @empty {
      <div class="muted empty">{{ emptyText() }}</div>
    }
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
    }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      margin-bottom: 4px;
    }
    small {
      margin-left: 4px;
    }
    .empty {
      padding: 6px 0;
    }
  `,
})
export class CheckListComponent<T extends string | number> {
  readonly options = input.required<Option<T>[]>();
  readonly selected = model<T[]>([]);
  readonly emptyText = input('No options yet');

  isOn(v: T): boolean {
    return this.selected().includes(v);
  }

  toggle(v: T): void {
    const cur = this.selected();
    this.selected.set(cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]);
  }
}

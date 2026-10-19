import { CdkConnectedOverlay, CdkOverlayOrigin, ConnectedPosition } from '@angular/cdk/overlay';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';

/**
 * A mockup-style filter chip ("Fund: All (6) ▾") that opens a popover with the
 * projected filter controls. Closes on outside click or Escape.
 */
@Component({
  selector: 'app-filter-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkOverlayOrigin, CdkConnectedOverlay],
  template: `
    <button
      type="button"
      class="chip"
      cdkOverlayOrigin
      #origin="cdkOverlayOrigin"
      [class.active]="active()"
      [attr.aria-expanded]="open()"
      aria-haspopup="dialog"
      (click)="open.set(!open())"
    >
      {{ label() }}: <b>{{ value() }}</b> ▾
    </button>
    <ng-template
      cdkConnectedOverlay
      [cdkConnectedOverlayOrigin]="origin"
      [cdkConnectedOverlayOpen]="open()"
      [cdkConnectedOverlayPositions]="positions"
      [cdkConnectedOverlayHasBackdrop]="true"
      cdkConnectedOverlayBackdropClass="cdk-overlay-transparent-backdrop"
      (backdropClick)="open.set(false)"
      (detach)="open.set(false)"
      (overlayKeydown)="onKey($event)"
    >
      <div class="pop" role="dialog" [attr.aria-label]="label() + ' filter'">
        <ng-content />
      </div>
    </ng-template>
  `,
  styles: `
    .chip.active {
      border-color: #cdd8fb;
      background: var(--accent-soft);
    }
    .pop {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(20, 30, 50, 0.12);
      padding: 10px 12px;
      min-width: 220px;
      max-width: min(360px, calc(100vw - 32px));
      max-height: 60vh;
      overflow: auto;
      font-size: 13px;
    }
  `,
})
export class FilterChipComponent {
  readonly label = input.required<string>();
  readonly value = input.required<string>();
  /** highlighted when a non-default value is set */
  readonly active = input(false);
  readonly open = signal(false);

  readonly positions: ConnectedPosition[] = [
    { originX: 'start', originY: 'bottom', overlayX: 'start', overlayY: 'top', offsetY: 6 },
    { originX: 'end', originY: 'bottom', overlayX: 'end', overlayY: 'top', offsetY: 6 },
  ];

  onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') this.open.set(false);
  }
}

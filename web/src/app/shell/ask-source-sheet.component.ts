import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ViewportService } from '../core/viewport.service';
import { SourcePanelComponent } from '../pages/data/source-panel.component';
import { AskPanelService } from './ask-panel.service';

/** The sheet's usual width, and the narrowest it may get while still sitting beside the panel. */
export const SOURCE_SHEET_WIDTH = 380;
export const SOURCE_SHEET_MIN_WIDTH = 320;

export interface SheetPlacement {
  /** `beside`: flush against the Ask panel's left edge, over the main area; `over`: overlaps the panel */
  mode: 'beside' | 'over';
  right: number;
  width: number;
}

/**
 * Where the docked-layout sheet goes. Beside the panel when the main area left of it
 * is at least `SOURCE_SHEET_MIN_WIDTH` (narrowed to that area if it is under 380 px,
 * which is the case with the panel at its widest). With less room (not reachable while
 * the main area keeps its 360 px minimum, but a safe fallback) it keeps its width and
 * overlays the panel's left part, staying fully on screen.
 */
export function sheetPlacement(viewportWidth: number, panelWidth: number): SheetPlacement {
  const main = viewportWidth - panelWidth;
  if (main >= SOURCE_SHEET_MIN_WIDTH) return { mode: 'beside', right: panelWidth, width: Math.min(SOURCE_SHEET_WIDTH, main) };
  const width = Math.max(0, Math.min(SOURCE_SHEET_WIDTH, viewportWidth - 32));
  return { mode: 'over', right: Math.max(0, Math.min(panelWidth, viewportWidth - width)), width };
}

/**
 * The FR-9 source panel for an Ask citation (a SQL row's "Source"). It does not open
 * inside the Ask panel: it slides over the right edge of the main area, just left of
 * the docked Ask panel, so the answer and its source are side by side and the page
 * underneath keeps its own state. In the narrow (drawer) layout it covers the drawer
 * instead; closing it goes back to the answer. Escape or × closes it.
 */
@Component({
  selector: 'app-ask-source-sheet',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SourcePanelComponent],
  host: { '(document:keydown.escape)': 'onEscape()' },
  template: `
    @if (sources.open()) {
      <div
        class="sheet"
        [class.overlay]="overlay()"
        [class.over-dock]="placement()?.mode === 'over'"
        [style.right.px]="placement()?.right ?? 0"
        [style.width.px]="placement()?.width ?? null"
        [attr.data-placement]="overlay() ? 'drawer' : placement()?.mode"
        role="region"
        aria-label="Source of a cited value"
        data-testid="ask-source-sheet"
      >
        <app-source-panel [source]="sources.source()" [loading]="sources.loading()" [error]="sources.error()" (closed)="sources.close()" />
      </div>
    }
  `,
  styles: `
    .sheet {
      position: fixed;
      top: var(--header-h, 0px);
      bottom: 0;
      width: min(380px, calc(100vw - 32px));
      padding: 12px;
      overflow: auto;
      overscroll-behavior: contain;
      background: var(--bg);
      border-left: 1px solid var(--line);
      box-shadow: -10px 0 28px rgba(20, 30, 50, 0.12);
      z-index: 6;
      /* the panel's own narrow rules (static, no max-height) apply in here */
      container-type: inline-size;
    }
    .sheet.over-dock {
      box-shadow: -12px 0 32px rgba(20, 30, 50, 0.22);
      border-right: 1px solid var(--line);
    }
    .sheet.overlay {
      top: 0;
      width: min(460px, 100vw);
      z-index: 31;
    }
  `,
})
export class AskSourceSheetComponent {
  private readonly panel = inject(AskPanelService);
  readonly sources = this.panel.sources;
  readonly overlay = computed(() => this.panel.mode() === 'overlay');
  private readonly viewport = inject(ViewportService);
  /** docked only (null in the drawer layout, which keeps its own CSS): against the panel's left edge when it fits */
  readonly placement = computed<SheetPlacement | null>(() =>
    this.overlay() ? null : sheetPlacement(this.viewport.width(), this.panel.open() ? (this.panel.width() ?? 0) : 0),
  );

  onEscape(): void {
    if (this.sources.open()) this.sources.close();
  }
}

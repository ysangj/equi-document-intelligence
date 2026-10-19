import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, computed, inject, signal } from '@angular/core';

import { ConfigService } from '../core/config.service';
import { askDisabledReason } from '../pages/ask/ask-disabled';
import { AskPanelComponent } from '../pages/ask/ask-panel.component';
import { AskSessionService } from '../pages/ask/ask-session.service';
import { ASK_PANEL_MIN_WIDTH, AskPanelService } from './ask-panel.service';

/** Arrow key step; Shift+Arrow and PageUp/PageDown move four steps (the range can be ~1000 px). */
const KEY_STEP = 24;
const KEY_STEP_LARGE = KEY_STEP * 4;

/**
 * The frame of the Ask panel: docked on the right of the main area (resizable by the
 * left-edge handle) or, on narrow windows, an overlay drawer with a backdrop. Title
 * bar: New conversation, Pin, Close. The conversation itself (`app-ask-panel`) is
 * loaded on first open and then kept, hidden, while the panel is closed.
 */
@Component({
  selector: 'app-ask-dock',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AskPanelComponent],
  template: `
    @if (overlay() && panel.open()) {
      <div class="backdrop" (click)="panel.close()" data-testid="ask-backdrop" aria-hidden="true"></div>
    }
    <aside
      id="ask-panel"
      class="dock"
      [class.overlay]="overlay()"
      [class.docked]="!overlay()"
      [class.resizing]="dragging()"
      [hidden]="!panel.open()"
      [style.width.px]="panel.width()"
      [attr.role]="overlay() ? 'dialog' : 'complementary'"
      [attr.aria-modal]="overlay() ? 'true' : null"
      aria-labelledby="ask-panel-title"
      [attr.data-mode]="panel.mode()"
      [attr.data-pinned]="panel.pinned()"
      data-testid="ask-dock"
      (keydown.escape)="onEscape($event)"
    >
      @if (!overlay()) {
        <div
          class="handle"
          role="separator"
          tabindex="0"
          aria-orientation="vertical"
          aria-label="Resize the Ask panel"
          aria-controls="ask-panel"
          [attr.aria-valuenow]="panel.width()"
          [attr.aria-valuemin]="minWidth"
          [attr.aria-valuemax]="panel.maxWidth()"
          title="Drag to resize · double-click to reset"
          (pointerdown)="startDrag($event)"
          (dblclick)="panel.resetWidth()"
          (keydown)="onHandleKey($event)"
          data-testid="ask-resize"
        ></div>
      }
      <div class="bar">
        <h2 id="ask-panel-title">Ask</h2>
        @if (disabledReason()) {
          <span class="st rev" [attr.title]="disabledReason()">unavailable</span>
        }
        <span class="spacer"></span>
        @if (session.turns().length) {
          <button type="button" class="btn small" (click)="newConversation()" data-testid="new-conversation">New conversation</button>
        }
        <button
          type="button"
          class="icon"
          [class.on]="panel.pinned()"
          [disabled]="overlay()"
          [attr.aria-pressed]="panel.pinned()"
          [attr.aria-label]="panel.pinned() ? 'Unpin the Ask panel' : 'Pin the Ask panel'"
          [attr.title]="pinTitle()"
          (click)="panel.setPinned(!panel.pinPreference())"
          data-testid="ask-pin"
        >
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <path
              d="M10.2 1.6 14.4 5.8 12.9 6.4 10.6 8.7 10.9 12.1 9.8 13.2 6.9 10.3 3.2 14 2 14 2 12.8 5.7 9.1 2.8 6.2 3.9 5.1 7.3 5.4 9.6 3.1Z"
              [attr.fill]="panel.pinned() ? 'currentColor' : 'none'"
              stroke="currentColor"
              stroke-width="1.3"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <button type="button" class="icon close" aria-label="Close the Ask panel" title="Close" (click)="panel.close()" data-testid="ask-close">×</button>
      </div>
      <div class="content">
        @defer (when panel.open()) {
          <app-ask-panel />
        } @loading (minimum 0ms) {
          <p class="muted loading">Loading…</p>
        }
      </div>
    </aside>
  `,
  styles: `
    :host {
      display: contents;
    }
    .dock {
      display: flex;
      flex-direction: column;
      background: var(--panel);
      min-width: 0;
    }
    .dock[hidden] {
      display: none;
    }
    .dock.docked {
      position: sticky;
      top: var(--header-h, 0px);
      height: calc(100vh - var(--header-h, 0px));
      flex: 0 0 auto;
      border-left: 1px solid var(--line);
    }
    .dock.overlay {
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      width: min(460px, 100vw);
      z-index: 30;
      box-shadow: -12px 0 32px rgba(20, 30, 50, 0.18);
    }
    .dock.resizing {
      user-select: none;
    }
    .backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 41, 0.32);
      z-index: 29;
    }
    .handle {
      position: absolute;
      left: -4px;
      top: 0;
      bottom: 0;
      width: 8px;
      cursor: col-resize;
      z-index: 2;
      touch-action: none;
    }
    .handle::after {
      content: '';
      position: absolute;
      left: 3px;
      top: 0;
      bottom: 0;
      width: 2px;
      background: transparent;
      transition: background 0.15s;
    }
    .handle:hover::after,
    .handle:focus-visible::after,
    .resizing .handle::after {
      background: var(--accent);
    }
    .handle:focus-visible {
      outline: none;
    }
    .bar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px 10px 16px;
      border-bottom: 1px solid var(--line);
      flex: 0 0 auto;
    }
    h2 {
      margin: 0;
      font-size: 15px;
    }
    .st {
      border-radius: 6px;
      padding: 1px 7px;
      font-size: 11.5px;
      font-weight: 600;
    }
    .st.rev {
      background: var(--warn-soft);
      color: var(--warn);
    }
    .btn.small {
      padding: 3px 9px;
      font-size: 12.5px;
    }
    .icon {
      border: 1px solid transparent;
      background: none;
      border-radius: 7px;
      width: 28px;
      height: 28px;
      display: inline-grid;
      place-items: center;
      color: var(--muted);
      cursor: pointer;
      font: inherit;
      padding: 0;
    }
    .icon:hover:not(:disabled) {
      background: var(--bg);
      color: var(--ink);
    }
    .icon.on {
      color: var(--accent);
      background: var(--accent-soft);
    }
    .icon:disabled {
      opacity: 0.4;
      cursor: default;
    }
    .icon.close {
      font-size: 20px;
      line-height: 1;
    }
    .content {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
    }
    .content > app-ask-panel {
      flex: 1 1 auto;
      min-height: 0;
    }
    .loading {
      padding: 16px;
    }
  `,
})
export class AskDockComponent {
  readonly panel = inject(AskPanelService);
  readonly session = inject(AskSessionService);
  private readonly config = inject(ConfigService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly minWidth = ASK_PANEL_MIN_WIDTH;
  readonly overlay = computed(() => this.panel.mode() === 'overlay');
  readonly disabledReason = computed(() => askDisabledReason(this.config, this.session));
  readonly pinTitle = computed(() => {
    if (this.overlay()) return 'Pinning needs a wider window (1024 px or more)';
    return this.panel.pinned() ? 'Pinned: stays open while you move between pages. Click to unpin.' : 'Pin: keep Ask open while you move between pages';
  });

  readonly dragging = signal(false);
  private stopDrag: (() => void) | null = null;

  constructor() {
    // A link followed from inside the panel (e.g. a passage → Data) must not collapse it.
    // Capture phase, so this runs before routerLink starts the navigation.
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.('a[href]');
      if (!a || a.getAttribute('target') === '_blank' || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      this.panel.markPanelNavigation();
    };
    const el = this.host.nativeElement;
    el.addEventListener('click', onClick, true);
    inject(DestroyRef).onDestroy(() => {
      el.removeEventListener('click', onClick, true);
      this.stopDrag?.();
    });
  }

  newConversation(): void {
    this.session.reset();
    this.panel.sources.close();
    this.panel.focusRequest.update((n) => n + 1);
  }

  onEscape(e: Event): void {
    // Escape closes the source sheet first (it handles its own), then the drawer. A docked panel stays.
    if (this.overlay() && !this.panel.sources.open()) {
      e.stopPropagation();
      this.panel.close();
    }
  }

  startDrag(e: PointerEvent): void {
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = this.panel.width() ?? 0;
    this.dragging.set(true);
    const move = (ev: PointerEvent) => this.panel.previewWidth(startW + (startX - ev.clientX));
    const up = () => {
      this.stopDrag?.();
      this.panel.commitWidth();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    this.stopDrag = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      this.dragging.set(false);
      this.stopDrag = null;
    };
  }

  /**
   * Keyboard resize (WAI-ARIA window splitter; the value is the panel width).
   * Left/Right move the edge (Left widens), Shift+Arrow or PageUp/PageDown by four
   * steps, Home = narrowest, End = widest the window allows.
   */
  onHandleKey(e: KeyboardEvent): void {
    const w = this.panel.width() ?? 0;
    const step = e.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
    let next: number | null;
    switch (e.key) {
      case 'ArrowLeft':
        next = w + step;
        break;
      case 'ArrowRight':
        next = w - step;
        break;
      case 'PageUp':
        next = w + KEY_STEP_LARGE;
        break;
      case 'PageDown':
        next = w - KEY_STEP_LARGE;
        break;
      case 'Home':
        next = this.minWidth;
        break;
      case 'End':
        next = this.panel.maxWidth();
        break;
      default:
        next = null;
    }
    if (next === null) return;
    e.preventDefault();
    this.panel.commitWidth(next);
  }
}

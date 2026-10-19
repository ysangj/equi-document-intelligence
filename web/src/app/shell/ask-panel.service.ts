import { DestroyRef, Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { NavigationCancel, NavigationCancellationCode, NavigationEnd, NavigationError, NavigationSkipped, Router } from '@angular/router';

import { ApiService } from '../api/api.service';
import { NotifyService } from '../core/notify.service';
import { ViewportService } from '../core/viewport.service';
import { AskSessionService } from '../pages/ask/ask-session.service';
import { SourceLoader, SourceRef } from '../shared/source-loader';

export const ASK_PANEL_STORAGE_KEY = 'docintel.askPanel';
export const ASK_PANEL_MIN_WIDTH = 320;
export const ASK_PANEL_DEFAULT_WIDTH = 440;
/**
 * The docked panel never squeezes the main area below this. There is no fixed maximum
 * for the panel: it can take everything else (at 1440 px that is 1080 px, 75%).
 * 360 px is the phone-width layout the pages already have (their `@container` rules
 * stack filters, cards and the source panel there, and wide tables scroll inside their
 * card), so the main area stays readable and the citation source sheet still fits
 * beside the panel.
 */
export const MAIN_MIN_WIDTH = 360;

export type AskPanelMode = 'docked' | 'overlay';

/** What is kept in localStorage. `open` is only written while docked. */
export interface AskPanelPrefs {
  open: boolean;
  pinned: boolean;
  width: number;
}

export const DEFAULT_PREFS: AskPanelPrefs = { open: false, pinned: true, width: ASK_PANEL_DEFAULT_WIDTH };

/** The widest the docked panel may be in a window this wide: all but `MAIN_MIN_WIDTH`. */
export function maxPanelWidth(viewportWidth: number): number {
  return Math.max(ASK_PANEL_MIN_WIDTH, Math.floor(viewportWidth - MAIN_MIN_WIDTH));
}

/**
 * The width the docked panel actually gets. Applied to the stored width on every
 * read (reload) and every window resize, so a width saved on a wider window never
 * pushes the main area below `MAIN_MIN_WIDTH` or overflows the page. Not-a-number
 * falls back to the default.
 */
export function clampWidth(width: number, viewportWidth: number): number {
  const w = Number.isNaN(width) ? ASK_PANEL_DEFAULT_WIDTH : width;
  return Math.round(Math.min(maxPanelWidth(viewportWidth), Math.max(ASK_PANEL_MIN_WIDTH, w)));
}

/** Read the stored prefs; anything missing or malformed falls back to the default. Never throws. */
export function readPrefs(): AskPanelPrefs {
  try {
    const raw = localStorage.getItem(ASK_PANEL_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const v = JSON.parse(raw) as Partial<AskPanelPrefs>;
    return {
      open: typeof v.open === 'boolean' ? v.open : DEFAULT_PREFS.open,
      pinned: typeof v.pinned === 'boolean' ? v.pinned : DEFAULT_PREFS.pinned,
      width: typeof v.width === 'number' && Number.isFinite(v.width) ? v.width : DEFAULT_PREFS.width,
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function writePrefs(p: AskPanelPrefs): void {
  try {
    localStorage.setItem(ASK_PANEL_STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* private window / blocked storage: the panel still works, it just isn't remembered */
  }
}

/** First path segment: `/data?view=facts` → `data`. Query changes inside a page are not navigation away. */
export function sectionOf(url: string): string {
  return url.split(/[?#]/)[0].split('/').filter(Boolean)[0] ?? '';
}

/**
 * The Ask panel (FR-11) as a shell-level panel beside the main area.
 *
 * - **Docked** (window ≥ 1024 px): the panel sits on the right and the main area
 *   (Data, Ingestion, Review, Search, Connect) shrinks to fit. **Pinned** keeps it open
 *   while the main area navigates; **unpinned** collapses it when the main area moves
 *   to another page (navigation started from inside the panel, e.g. a passage link to
 *   Data, never collapses it). Open, pinned and width are kept in localStorage.
 * - **Overlay** (narrower): a drawer over the page with a backdrop. Pin is ignored, any
 *   navigation closes it, and it always starts closed.
 *
 * The source panel for citations is owned here too (`sources`), so it can sit in the
 * main area beside the Ask panel instead of inside it.
 */
@Injectable({ providedIn: 'root' })
export class AskPanelService {
  private readonly viewport = inject(ViewportService);
  private readonly router = inject(Router);
  private readonly session = inject(AskSessionService);
  private readonly notify = inject(NotifyService);

  private readonly prefs = signal<AskPanelPrefs>(readPrefs());
  private readonly openState = signal(this.viewport.wide() ? this.prefs().open : false);

  readonly mode = computed<AskPanelMode>(() => (this.viewport.wide() ? 'docked' : 'overlay'));
  readonly open = this.openState.asReadonly();
  /** the stored preference, whatever the mode */
  readonly pinPreference = computed(() => this.prefs().pinned);
  /** pinned in effect: only while docked */
  readonly pinned = computed(() => this.mode() === 'docked' && this.prefs().pinned);
  readonly maxWidth = computed(() => maxPanelWidth(this.viewport.width()));
  /**
   * docked width in px, null in overlay mode. The stored width is the user's choice and
   * is kept as is; this is that choice re-clamped to the current window, so a narrower
   * window (or a reload on one) shrinks the panel and a wider one gives the width back.
   */
  readonly width = computed(() => (this.mode() === 'docked' ? clampWidth(this.prefs().width, this.viewport.width()) : null));
  /** asks the panel content to focus the question box (bumped on open) */
  readonly focusRequest = signal(0);

  readonly sources = new SourceLoader(inject(ApiService), inject(DestroyRef), (e) => this.notify.error(e, 'Could not load the source'));

  /** the main-area URL to show when `/ask` is opened directly */
  private lastMainUrl: string | null = null;
  private currentSection = sectionOf(this.router.url);
  /** the main-area page the panel was opened (or last followed a panel link) on */
  /** null while open = restored on reload: it adopts the first page it lands on */
  private openedSection: string | null = null;
  /** a link inside the panel was clicked; the navigation it starts must not collapse it */
  private fromPanel = false;

  constructor() {
    // Switching to the narrow layout closes the drawer (it would cover the page);
    // back to wide restores the stored open state.
    let lastMode = this.mode();
    effect(() => {
      const mode = this.mode();
      if (mode === lastMode) return;
      lastMode = mode;
      untracked(() => {
        if (mode === 'overlay') {
          this.setOpen(false, false);
          this.sources.close();
        }
        else if (this.prefs().open) this.show();
      });
    });

    const sub = this.router.events.subscribe((e) => {
      if (e instanceof NavigationEnd) this.afterNavigation(e.urlAfterRedirects);
      else if (e instanceof NavigationCancel || e instanceof NavigationError || e instanceof NavigationSkipped) {
        // a redirect (guard UrlTree) restarts the navigation: keep the flag for it
        if (!(e instanceof NavigationCancel && e.code === NavigationCancellationCode.Redirect)) this.fromPanel = false;
      }
    });
    inject(DestroyRef).onDestroy(() => sub.unsubscribe());
  }

  toggle(): void {
    if (this.openState()) this.close();
    else this.show();
  }

  show(): void {
    this.openedSection = this.currentSection;
    this.setOpen(true);
    this.focusRequest.update((n) => n + 1);
  }

  close(): void {
    this.setOpen(false);
    this.sources.close();
  }

  setPinned(pinned: boolean): void {
    this.update({ pinned });
    // unpinning makes the current page "home": leaving it collapses the panel
    if (this.openState()) this.openedSection = this.currentSection;
  }

  /** Live resize (not persisted); call `commitWidth` when the drag ends. */
  previewWidth(width: number): void {
    this.prefs.update((p) => ({ ...p, width: clampWidth(width, this.viewport.width()) }));
  }

  commitWidth(width: number = this.prefs().width): void {
    this.update({ width: clampWidth(width, this.viewport.width()) });
  }

  resetWidth(): void {
    this.commitWidth(ASK_PANEL_DEFAULT_WIDTH);
  }

  /** A link inside the panel is being followed (capture-phase click). */
  markPanelNavigation(): void {
    this.fromPanel = true;
  }

  /**
   * `/ask` deep link: open the panel (with `?q=` as the draft) and return the URL the
   * main area should show: the last page of this session, or Data.
   */
  openFromDeepLink(question?: string | null): string {
    const target = this.lastMainUrl ?? '/data';
    if (question && !this.session.draft()) this.session.draft.set(question);
    this.openedSection = sectionOf(target);
    this.setOpen(true);
    this.focusRequest.update((n) => n + 1);
    return target;
  }

  /** Citation → the FR-9 source panel, shown in the main area beside the Ask panel. */
  showSource(ref: SourceRef): void {
    this.sources.show(ref);
  }

  private afterNavigation(url: string): void {
    const section = sectionOf(url);
    this.currentSection = section;
    if (section !== 'ask') this.lastMainUrl = url;
    if (this.fromPanel) {
      this.fromPanel = false;
      if (this.mode() === 'overlay') this.close();
      else this.openedSection = section;
      return;
    }
    if (!this.openState()) return;
    if (this.openedSection === null || this.pinned()) this.openedSection = section;
    else if (section !== this.openedSection) this.close();
  }

  private setOpen(open: boolean, persist = true): void {
    this.openState.set(open);
    if (!open) this.openedSection = null;
    // the drawer's open state is not remembered: a reload on a phone starts closed
    if (persist && this.mode() === 'docked') this.update({ open });
  }

  private update(patch: Partial<AskPanelPrefs>): void {
    this.prefs.update((p) => ({ ...p, ...patch }));
    writePrefs(this.prefs());
  }
}

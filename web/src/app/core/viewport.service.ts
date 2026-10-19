import { DestroyRef, Injectable, InjectionToken, inject, signal } from '@angular/core';

/** At or above this window width the Ask panel docks beside the main area; below it, it is an overlay drawer. */
export const DOCK_MIN_WIDTH = 1024;

/** Minimal `MediaQueryList` surface used here, so tests can pass a fake. */
export interface MediaQueryLike {
  readonly matches: boolean;
  addEventListener(type: 'change', listener: (e: { matches: boolean }) => void): void;
  removeEventListener(type: 'change', listener: (e: { matches: boolean }) => void): void;
}

/** `window.matchMedia`, or null where it does not exist (jsdom, SSR). Overridable in tests. */
export const MATCH_MEDIA = new InjectionToken<(query: string) => MediaQueryLike | null>('MATCH_MEDIA', {
  providedIn: 'root',
  factory: () => (query: string) => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null),
});

/**
 * The layout width of the window: `documentElement.clientWidth`, which leaves out a
 * classic vertical scrollbar (the docked panel and the main area share only what is
 * left of it). Falls back to `innerWidth`, then 1440 (jsdom, SSR).
 */
export function layoutWidth(): number {
  if (typeof window === 'undefined') return 1440;
  return document.documentElement?.clientWidth || window.innerWidth || 1440;
}

/**
 * Window size facts the shell layout needs: whether the Ask panel can dock (`wide`)
 * and the layout width (to cap the panel so the main area stays usable). The width is
 * re-read on window resize and when the root element's box changes (e.g. a vertical
 * scrollbar appears). Without `matchMedia` the window counts as wide.
 */
@Injectable({ providedIn: 'root' })
export class ViewportService {
  readonly wide = signal(true);
  readonly width = signal(layoutWidth());

  constructor() {
    const destroyRef = inject(DestroyRef);
    const mql = inject(MATCH_MEDIA)(`(min-width: ${DOCK_MIN_WIDTH}px)`);
    if (mql) {
      this.wide.set(mql.matches);
      const onChange = (e: { matches: boolean }) => this.wide.set(e.matches);
      mql.addEventListener('change', onChange);
      destroyRef.onDestroy(() => mql.removeEventListener('change', onChange));
    }
    if (typeof window !== 'undefined') {
      const onResize = () => this.width.set(layoutWidth());
      window.addEventListener('resize', onResize, { passive: true });
      destroyRef.onDestroy(() => window.removeEventListener('resize', onResize));
      if (typeof ResizeObserver !== 'undefined' && document.documentElement) {
        const ro = new ResizeObserver(onResize);
        ro.observe(document.documentElement);
        destroyRef.onDestroy(() => ro.disconnect());
      }
    }
  }
}

import { Provider } from '@angular/core';

import { MATCH_MEDIA, MediaQueryLike } from '../core/viewport.service';

/** A controllable `(min-width: 1024px)` media query: `set(false)` switches the shell to the narrow layout. */
export function fakeViewport(wide = true): { provider: Provider; set(wide: boolean): void } {
  const listeners = new Set<(e: { matches: boolean }) => void>();
  const mql: MediaQueryLike & { matches: boolean } = {
    matches: wide,
    addEventListener: (_t, l) => listeners.add(l),
    removeEventListener: (_t, l) => listeners.delete(l),
  };
  return {
    provider: { provide: MATCH_MEDIA, useValue: () => mql },
    set(next: boolean) {
      mql.matches = next;
      listeners.forEach((l) => l({ matches: next }));
    },
  };
}

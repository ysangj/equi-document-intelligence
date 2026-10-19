import { Injectable } from '@angular/core';

/**
 * Thin wrapper over `window.location` so full-page navigations (the OAuth login
 * redirect, FR-2) can be asserted in tests instead of reloading jsdom.
 */
@Injectable({ providedIn: 'root' })
export class BrowserLocation {
  assign(url: string): void {
    window.location.assign(url);
  }
}

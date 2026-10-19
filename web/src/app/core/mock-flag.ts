import { environment } from '../../environments/environment';

/**
 * Dev-only mock api switch. OFF by default, so `npm run start` talks to the real api.
 *
 * ON when either:
 * - the build uses `environment.mock.ts` (`npm run start:mock`), or
 * - a non-production build is opened with `?mock=1`. The choice is remembered for the
 *   browser tab (sessionStorage) so in-app navigation keeps it; `?mock=0` turns it off.
 *
 * Mock-only knobs (read by the mock backend, remembered for the tab):
 * - `?demo=0`: /api/config reports Drive mode (no demo banner)
 * - `?empty=1`: start with zero documents processed
 * - `?connected=0` (with demo=0): first run, Google not connected and no folder
 * - `?oauth_error=<code>`: the simulated OAuth callback fails with that code (`?oauth_error=` clears it)
 * - `?ask=1` / `?ask=0`: force Ask on or off (default: off in demo mode, on in Drive mode)
 * - `?ask_delay=<ms>`: Ask answer latency (default 1400)
 */
const KEY = 'docintel.mock';

function safeSession(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function readMockFlag(search: string, env: { production: boolean; mock: boolean } = environment): boolean {
  if (env.mock) return true;
  if (env.production) return false;
  const store = safeSession();
  const q = new URLSearchParams(search).get('mock');
  if (q === '1' || q === 'true') {
    store?.setItem(KEY, '1');
    return true;
  }
  if (q === '0' || q === 'false') {
    store?.removeItem(KEY);
    return false;
  }
  return store?.getItem(KEY) === '1';
}

let cached: boolean | null = null;

export function isMockEnabled(): boolean {
  if (cached === null) {
    cached = readMockFlag(typeof location === 'undefined' ? '' : location.search);
  }
  return cached;
}

/** Mock knob from the URL / session (e.g. `demo`, `empty`). */
export function mockOption(name: string): string | null {
  const store = safeSession();
  const key = `docintel.mock.${name}`;
  const q = typeof location === 'undefined' ? null : new URLSearchParams(location.search).get(name);
  if (q !== null) {
    store?.setItem(key, q);
    return q;
  }
  return store?.getItem(key) ?? null;
}

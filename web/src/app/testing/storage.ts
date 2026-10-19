/**
 * Node 22+ defines its own `localStorage` global (empty without --localstorage-file),
 * which shadows jsdom's. Specs that use localStorage call this to get a working
 * in-memory one. Browsers are unaffected.
 */
export function installMemoryStorage(): void {
  const usable = (() => {
    try {
      return typeof globalThis.localStorage?.getItem === 'function';
    } catch {
      return false;
    }
  })();
  if (usable) return;
  const data = new Map<string, string>();
  const mem: Storage = {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k) => (data.has(k) ? data.get(k)! : null),
    key: (i) => Array.from(data.keys())[i] ?? null,
    removeItem: (k) => void data.delete(k),
    setItem: (k, v) => void data.set(k, String(v)),
  };
  Object.defineProperty(globalThis, 'localStorage', { value: mem, configurable: true, writable: true });
}

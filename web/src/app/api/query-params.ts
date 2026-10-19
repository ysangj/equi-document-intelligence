import { HttpParams } from '@angular/common/http';

/** A query object: scalars become one key, arrays become repeated keys (C4). */
export type QueryValue = string | number | boolean | null | undefined | readonly (string | number | boolean)[];
export type QueryObject = { [key: string]: QueryValue } | object;

/**
 * Build HttpParams for the contract's query conventions.
 *
 * - Arrays are repeated keys: `{fund_id: [1, 3]}` -> `fund_id=1&fund_id=3` (C4).
 * - null / undefined / '' / [] are omitted, so "no filter" sends no key (C1 rejects
 *   unknown params, and an empty value would be a 422).
 * - Keys are emitted in a stable order (insertion order of the object).
 */
export function buildParams(query: QueryObject | null | undefined): HttpParams {
  let params = new HttpParams();
  if (!query) return params;
  for (const [key, raw] of Object.entries(query as Record<string, QueryValue>)) {
    if (raw === null || raw === undefined) continue;
    if (Array.isArray(raw)) {
      for (const v of raw) params = params.append(key, String(v));
      continue;
    }
    const s = String(raw);
    if (s === '') continue;
    params = params.append(key, s);
  }
  return params;
}

/** `path?query` with the same encoding HttpClient uses. */
export function buildUrl(path: string, query: QueryObject | null | undefined): string {
  const qs = buildParams(query).toString();
  return qs ? `${path}?${qs}` : path;
}

/**
 * Map an Angular Material sort state to the contract's `sort` param (C3).
 * Returns null when there is no active sort, so the endpoint default applies.
 */
export function toSortParam(active: string | null | undefined, direction: 'asc' | 'desc' | '' | null | undefined): string | null {
  if (!active || !direction) return null;
  return direction === 'desc' ? `-${active}` : active;
}

/** MatPaginator pageIndex is 0-based; the API's `page` is 1-based (C3). */
export function toApiPage(pageIndex: number): number {
  return Math.max(0, Math.floor(pageIndex)) + 1;
}

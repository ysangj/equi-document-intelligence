/**
 * Display rules from the contract (C6). Pure functions so they are trivially testable
 * and reusable by pipes, tables and the side panel.
 *
 * - Percentages come from `value_pct` / `weight_pct` (never `value * 100` in the UI):
 *   2 decimals, explicit sign, `%`: `+2.84%`, `-0.36%`, `0.00%`.
 * - Money: thousands separators, 2 decimals, plus the currency code when given.
 * - Missing values render as an em dash.
 */

import { FundFactItem, ValueKind } from '../api/models';

export const EMPTY = '—'; // em dash

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const money2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** `value_pct` -> `+2.84%`. A value that rounds to zero is `0.00%` (no `-0.00%`). */
export function formatPct(valuePct: number | null | undefined): string {
  if (!isNum(valuePct)) return EMPTY;
  const rounded = Math.round(valuePct * 100) / 100;
  if (rounded === 0) return '0.00%';
  const sign = rounded > 0 ? '+' : '-';
  return `${sign}${Math.abs(rounded).toFixed(2)}%`;
}

/**
 * Holding weight from `weight_pct`: 2 dp and `%`, no `+` (a 25% position is not a
 * gain); short positions keep their `-`.
 */
export function formatWeight(weightPct: number | null | undefined): string {
  if (!isNum(weightPct)) return EMPTY;
  return formatPct(weightPct).replace(/^\+/, '');
}

/** Thousands separators and 2 dp; negative amounts keep a leading `-`. */
export function formatAmount(value: number | null | undefined): string {
  if (!isNum(value)) return EMPTY;
  const rounded = Math.round(value * 100) / 100;
  if (rounded === 0) return money2.format(0);
  return (rounded < 0 ? '-' : '') + money2.format(Math.abs(rounded));
}

/** Money with its currency code: `13,100,000.00 USD`. */
export function formatMoney(value: number | null | undefined, currency?: string | null): string {
  const s = formatAmount(value);
  if (s === EMPTY || !currency) return s;
  return `${s} ${currency}`;
}

/** Plain number (quantities, ratios): up to 4 dp, thousands separators. */
export function formatNumber(value: number | null | undefined, unit?: string | null): string {
  if (!isNum(value)) return EMPTY;
  const s = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(value);
  return unit ? `${s} ${unit}` : s;
}

/** Sign class for coloring: `pos` / `neg` / ''. */
export function signClass(value: number | null | undefined): 'pos' | 'neg' | '' {
  if (!isNum(value)) return '';
  const rounded = Math.round(value * 100) / 100;
  if (rounded > 0) return 'pos';
  if (rounded < 0) return 'neg';
  return '';
}

/** Negative-only class (money columns are not colored green when positive). */
export function negClass(value: number | null | undefined): 'neg' | '' {
  return signClass(value) === 'neg' ? 'neg' : '';
}

/** `2026-01-31` -> `Jan 2026`. Falls back to the input when it is not a date. */
export function formatPeriod(isoDate: string | null | undefined): string {
  if (!isoDate) return EMPTY;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!m) return isoDate;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return isoDate;
  return `${MONTHS[month - 1]} ${m[1]}`;
}

/** `2026-01-31` -> `31 Jan 2026`. */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return EMPTY;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return isoDate;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return isoDate;
  return `${Number(m[3])} ${MONTHS[month - 1]} ${m[1]}`;
}

/** Confidence 0-1 -> `0.97`. */
export function formatConfidence(c: number | null | undefined): string {
  return isNum(c) ? c.toFixed(2) : EMPTY;
}

/** True for a 3-letter ISO currency code used as a fund-fact unit. */
export function isCurrencyUnit(unit: string | null | undefined): boolean {
  return !!unit && /^[A-Z]{3}$/.test(unit);
}

/**
 * Fund facts (C6): `fraction` -> percentage from `value_pct`; a currency unit -> money;
 * otherwise the number with its unit (`ratio` is shown bare, e.g. Sharpe `1.84`).
 * Non-numeric facts use `value_text`.
 */
export function formatFact(f: Pick<FundFactItem, 'value' | 'value_pct' | 'value_text' | 'unit'>): string {
  if (f.unit === 'fraction') return formatPct(f.value_pct);
  if (!isNum(f.value)) return f.value_text ?? EMPTY;
  if (isCurrencyUnit(f.unit)) return formatMoney(f.value, f.unit);
  if (f.unit === 'ratio') return formatNumber(f.value);
  return formatNumber(f.value, f.unit);
}

/** Only percentage facts are colored by sign (an AUM is not "positive"). */
export function factSignValue(f: Pick<FundFactItem, 'value_pct' | 'unit'>): number | null {
  return f.unit === 'fraction' ? f.value_pct : null;
}

/** `max_drawdown` -> `Max drawdown`; known metrics get their usual names. */
export function formatMetric(metric: string): string {
  const known: Record<string, string> = {
    aum: 'AUM',
    nav: 'NAV',
    sharpe: 'Sharpe',
    vol: 'Volatility',
    max_drawdown: 'Max drawdown',
  };
  if (known[metric]) return known[metric];
  const s = metric.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** One value with its `value_kind` (side panel, review cards). */
export interface KindedValue {
  value: number | string | null;
  value_pct: number | null;
  value_kind: ValueKind;
  unit: string | null;
  currency: string | null;
}

export function formatKindedValue(v: KindedValue): string {
  switch (v.value_kind) {
    case 'percent':
      return formatPct(v.value_pct);
    case 'money':
      return typeof v.value === 'number' ? formatMoney(v.value, v.currency) : (v.value ?? EMPTY);
    case 'number':
      return typeof v.value === 'number' ? formatNumber(v.value, v.unit === 'ratio' ? null : v.unit) : (v.value ?? EMPTY);
    case 'date':
      return typeof v.value === 'string' ? formatDate(v.value) : EMPTY;
    case 'text':
    default:
      return v.value === null || v.value === undefined || v.value === '' ? EMPTY : String(v.value);
  }
}

export function kindedSignClass(v: KindedValue): 'pos' | 'neg' | '' {
  if (v.value_kind === 'percent') return signClass(v.value_pct);
  if (v.value_kind === 'money' && typeof v.value === 'number') return negClass(v.value);
  return '';
}

/** Relative time between two instants, e.g. `12s ago`, `3 min ago`, `2 h ago`. */
export function formatRelative(then: string | null | undefined, now: string | number | Date): string {
  if (!then) return 'never';
  const t = Date.parse(then);
  const n = typeof now === 'number' ? now : now instanceof Date ? now.getTime() : Date.parse(now);
  if (!Number.isFinite(t) || !Number.isFinite(n)) return EMPTY;
  const s = Math.max(0, Math.round((n - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

/** Token counts as `7.2k`. */
export function formatTokens(n: number | null | undefined): string {
  if (!isNum(n)) return EMPTY;
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

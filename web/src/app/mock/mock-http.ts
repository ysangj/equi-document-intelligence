/** Shared request/response shapes and C2 helpers for the dev-only mock api. */
import { ErrorCode, ErrorDetail, ErrorResponse } from '../api/models';

export interface MockRequest {
  method: string;
  /** path without query, e.g. `/api/returns` */
  path: string;
  params: URLSearchParams;
  /** parsed JSON body (POST) */
  body?: unknown;
}

export interface MockResponse {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
  /** simulated latency override (Ask is slower than reads) */
  delayMs?: number;
}

export function err(status: number, code: ErrorCode, message: string, details: ErrorDetail[] | null = null): MockResponse {
  const body: ErrorResponse = { error: { code, message, details } };
  return { status, body };
}

/** Thrown by param parsing; the backend turns it into a 422 `validation_error`. */
export class ValidationError extends Error {
  constructor(
    readonly loc: string,
    message: string,
  ) {
    super(message);
  }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const monthLabel = (iso: string) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
export const money = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const pctText = (fraction: number) => {
  const v = Math.round(fraction * 10000) / 100;
  return v === 0 ? '0.00%' : `${v > 0 ? '+' : '-'}${Math.abs(v).toFixed(2)}%`;
};

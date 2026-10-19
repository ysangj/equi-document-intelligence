import { HttpErrorResponse } from '@angular/common/http';
import { TimeoutError } from 'rxjs';

import { ErrorCode, ErrorDetail, ErrorResponse } from './models';

/**
 * A normalized API failure. Built from the C2 envelope when the server sends one;
 * otherwise (network down, proxy error, a route that does not exist yet and returns
 * FastAPI's default `{"detail": ...}`) it is classified here so the UI always has a
 * safe, short message to show.
 */
export interface ApiError {
  status: number;
  code: ErrorCode | 'network_error' | 'not_implemented' | 'timeout' | 'unknown';
  message: string;
  details: ErrorDetail[] | null;
  /** true when the body was the documented C2 envelope */
  envelope: boolean;
}

export function isErrorEnvelope(body: unknown): body is ErrorResponse {
  if (!body || typeof body !== 'object') return false;
  const err = (body as { error?: unknown }).error;
  return (
    !!err &&
    typeof err === 'object' &&
    typeof (err as { code?: unknown }).code === 'string' &&
    typeof (err as { message?: unknown }).message === 'string'
  );
}

export function toApiError(e: unknown): ApiError {
  if (e instanceof HttpErrorResponse) {
    let body: unknown = e.error;
    // responseType 'text' (CSV) or a proxy page delivers the body as a string.
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body);
      } catch {
        /* not JSON */
      }
    }
    if (isErrorEnvelope(body)) {
      return {
        status: e.status,
        code: body.error.code,
        message: body.error.message,
        details: body.error.details,
        envelope: true,
      };
    }
    if (e.status === 0) {
      return {
        status: 0,
        code: 'network_error',
        message: 'Cannot reach the api. Is it running on :8000?',
        details: null,
        envelope: false,
      };
    }
    if (e.status === 404 || e.status === 405) {
      return {
        status: e.status,
        code: 'not_implemented',
        message: 'This endpoint is not available on the api yet.',
        details: null,
        envelope: false,
      };
    }
    if (e.status === 502 || e.status === 503 || e.status === 504) {
      return {
        status: e.status,
        code: 'network_error',
        message: 'The api is not responding. Is it running on :8000?',
        details: null,
        envelope: false,
      };
    }
    return {
      status: e.status,
      code: 'unknown',
      message: `Unexpected error from the api (HTTP ${e.status}).`,
      details: null,
      envelope: false,
    };
  }
  if (e instanceof TimeoutError) {
    return {
      status: 0,
      code: 'timeout',
      message: 'The api did not answer in time. Please try again.',
      details: null,
      envelope: false,
    };
  }
  return {
    status: -1,
    code: 'unknown',
    message: e instanceof Error ? e.message : 'Unexpected error.',
    details: null,
    envelope: false,
  };
}

/**
 * Where to send the user for a Drive-state error (C2): 409 `not_connected` → `/connect`,
 * 409 `no_folder` → `/connect/folder`. Anything else → null.
 */
export function connectRouteFor(e: Pick<ApiError, 'code'> | null | undefined): '/connect' | '/connect/folder' | null {
  if (!e) return null;
  if (e.code === 'not_connected') return '/connect';
  if (e.code === 'no_folder') return '/connect/folder';
  return null;
}

/** Link text for `connectRouteFor`. */
export function connectActionLabel(route: '/connect' | '/connect/folder'): string {
  return route === '/connect' ? 'Connect Google Drive' : 'Choose a folder';
}

/**
 * True when a fixed route (no ids in the path, e.g. `/api/auth/status`, `POST /api/ask`)
 * answered 404/405: the endpoint is not implemented on this api yet. Works with both
 * FastAPI's default body and the C2 `not_found` envelope.
 */
export function isMissingEndpoint(e: Pick<ApiError, 'status' | 'code'> | null | undefined): boolean {
  return !!e && (e.code === 'not_implemented' || e.status === 404 || e.status === 405);
}

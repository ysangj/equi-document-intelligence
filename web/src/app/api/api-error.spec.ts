import { HttpErrorResponse } from '@angular/common/http';

import { isErrorEnvelope, toApiError } from './api-error';

describe('toApiError', () => {
  it('reads the C2 envelope', () => {
    const e = toApiError(
      new HttpErrorResponse({
        status: 422,
        error: {
          error: {
            code: 'validation_error',
            message: 'Invalid request parameters.',
            details: [{ loc: 'query.sort', message: "cannot sort by 'colour'" }],
          },
        },
      }),
    );
    expect(e).toEqual({
      status: 422,
      code: 'validation_error',
      message: 'Invalid request parameters.',
      details: [{ loc: 'query.sort', message: "cannot sort by 'colour'" }],
      envelope: true,
    });
  });

  it('parses an envelope delivered as text (CSV requests)', () => {
    const body = JSON.stringify({ error: { code: 'not_found', message: 'returns row 999 not found.', details: null } });
    const e = toApiError(new HttpErrorResponse({ status: 404, error: body }));
    expect(e.code).toBe('not_found');
    expect(e.message).toBe('returns row 999 not found.');
    expect(e.envelope).toBe(true);
  });

  it('classifies a 404 without envelope as not implemented (planned endpoint)', () => {
    const e = toApiError(new HttpErrorResponse({ status: 404, error: { detail: 'Not Found' } }));
    expect(e.code).toBe('not_implemented');
    expect(e.envelope).toBe(false);
  });

  it('classifies network failures', () => {
    expect(toApiError(new HttpErrorResponse({ status: 0 })).code).toBe('network_error');
    expect(toApiError(new HttpErrorResponse({ status: 504, error: 'proxy' })).code).toBe('network_error');
  });

  it('falls back for other errors', () => {
    expect(toApiError(new HttpErrorResponse({ status: 418 })).message).toContain('HTTP 418');
    expect(toApiError(new Error('boom')).message).toBe('boom');
  });

  it('isErrorEnvelope rejects non-envelopes', () => {
    expect(isErrorEnvelope(null)).toBe(false);
    expect(isErrorEnvelope({ detail: 'x' })).toBe(false);
    expect(isErrorEnvelope({ error: { code: 'x' } })).toBe(false);
  });
});

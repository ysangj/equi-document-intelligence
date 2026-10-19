import { TestBed } from '@angular/core/testing';

import { SourceResponse } from '../../api/models';
import { SourcePanelComponent, snippetCheck } from './source-panel.component';

/** First /api/sources contract example (Drive mode, verified snippet). */
const RETURN_SOURCE: SourceResponse = {
  table: 'returns',
  id: 101,
  field: 'value',
  label: 'Kestrel Quant Equity Fund LP · Jan 2026 · MTD net',
  field_label: 'Return',
  value: 0.0284,
  value_pct: 2.84,
  value_kind: 'percent',
  unit: null,
  currency: null,
  status: 'ok',
  review_reasons: [],
  reasons: [],
  provenance: { page: 1, source_snippet: 'MTD % (net) +2.84%', confidence: 0.97, snippet_verified: true },
  snippet_highlight: { start: 12, end: 18, text: '+2.84%' },
  document: {
    document_id: 3,
    document_name: 'kestrel_factsheet_2026-01.pdf',
    document_type: 'factsheet',
    mime_type: 'application/pdf',
    source: 'drive',
    web_view_link: 'https://drive.google.com/file/d/1kT7aKestrelJan26/view?usp=drivesdk',
    version: 1,
    processed_at: '2026-10-16T18:44:31Z',
  },
  extraction: { extraction_id: 3, model: 'claude-opus-5-5', prompt_version: 'v1', source: 'claude', created_at: '2026-10-16T18:44:30Z' },
  conflicts: [],
  review_history: [],
};

/** Second contract example: a statement field in fixture demo mode (no Drive link). */
const STATEMENT_SOURCE: SourceResponse = {
  table: 'statements',
  id: 12,
  field: 'closing_balance',
  label: 'Acme Family Office LLC · XXXX-4471 · Jan 2026',
  field_label: 'Closing balance',
  value: 13100000.0,
  value_pct: null,
  value_kind: 'money',
  unit: null,
  currency: 'USD',
  status: 'needs_review',
  review_reasons: ['rollforward_mismatch'],
  reasons: [
    {
      code: 'rollforward_mismatch',
      label: 'Roll-forward mismatch',
      message:
        'Opening 12,400,000.00 + contributions 500,000.00 - withdrawals 0.00 + P&L 180,000.00 - fees 0.00 = 13,080,000.00, but the stated closing balance is 13,100,000.00 (difference 20,000.00 USD).',
    },
  ],
  provenance: { page: 1, source_snippet: 'Closing balance 13,100,000.00', confidence: 0.95, snippet_verified: true },
  snippet_highlight: { start: 16, end: 29, text: '13,100,000.00' },
  document: {
    document_id: 15,
    document_name: 'acme_family_office_statement_2026-01.pdf',
    document_type: 'account_statement',
    mime_type: 'application/pdf',
    source: 'local',
    web_view_link: null,
    version: 1,
    processed_at: '2026-10-16T18:45:02Z',
  },
  extraction: { extraction_id: 15, model: 'claude-opus-5-5', prompt_version: 'v1', source: 'fixture', created_at: '2026-10-16T18:45:01Z' },
  conflicts: [],
  review_history: [],
};

describe('SourcePanelComponent', () => {
  function render(inputs: Record<string, unknown>) {
    const fixture = TestBed.createComponent(SourcePanelComponent);
    for (const [k, v] of Object.entries(inputs)) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const text = (id: string) => el.querySelector(`[data-testid=${id}]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? null;
    return { fixture, el, text };
  }

  it('renders a returns provenance response', () => {
    const { el, text } = render({ source: RETURN_SOURCE });
    expect(text('pv-title')).toBe('Kestrel Quant Equity Fund LP · Jan 2026 · MTD net');
    expect(text('pv-value')).toBe('+2.84%');
    expect(el.querySelector('[data-testid=pv-value]')!.classList).toContain('pos');
    expect(text('pv-doc')).toBe('kestrel_factsheet_2026-01.pdf · page 1');
    expect(text('pv-snippet')).toBe('MTD % (net) +2.84%');
    expect(text('pv-meta')).toBe('confidence 0.97 · claude-opus-5-5 · prompt v1 · live extraction');
    expect(text('pv-check')).toContain('Snippet found');
    const link = el.querySelector('[data-testid=pv-drive]') as HTMLAnchorElement;
    expect(link.href).toBe('https://drive.google.com/file/d/1kT7aKestrelJan26/view?usp=drivesdk');
    expect(link.target).toBe('_blank');
  });

  it('renders a statement field and hides the Drive link when web_view_link is null', () => {
    const { el, text } = render({ source: STATEMENT_SOURCE });
    expect(text('pv-value')).toBe('13,100,000.00 USD');
    expect(el.querySelector('[data-testid=pv-drive]')).toBeNull();
    expect(el.textContent).toContain('Roll-forward mismatch.');
    expect(el.textContent).toContain('difference 20,000.00 USD');
    expect(el.textContent).toContain('needs review');
    expect(text('pv-meta')).toContain('from fixture');
  });

  it('shows the snippet check states', () => {
    const notFound = { ...RETURN_SOURCE, provenance: { ...RETURN_SOURCE.provenance, snippet_verified: false } };
    expect(render({ source: notFound }).text('pv-check')).toContain('not found');
    const skipped = { ...RETURN_SOURCE, provenance: { ...RETURN_SOURCE.provenance, snippet_verified: null } };
    expect(render({ source: skipped }).text('pv-check')).toContain('skipped');
  });

  it('says "Snippet found, but it doesn\'t contain the value" for snippet_not_found with value_missing (Q2 fix)', () => {
    const missing: SourceResponse = {
      ...RETURN_SOURCE,
      review_reasons: ['snippet_not_found'],
      reasons: [{ code: 'snippet_not_found', label: 'Snippet not found', message: 'The value is not written in its source snippet, so it cannot be traced.' }],
      provenance: { ...RETURN_SOURCE.provenance, snippet_verified: false },
    };
    const t = render({ source: missing }).text('pv-check');
    expect(t).toContain("Snippet found, but it doesn't contain the value");
    expect(t).not.toContain('not found');
    expect(snippetCheck({ ...missing, reasons: [{ ...missing.reasons[0], message: 'The source snippet was not found in the document text.' }] }).text).toContain(
      'Snippet not found',
    );
  });

  it('renders the snippet as text, not HTML', () => {
    const evil = { ...RETURN_SOURCE, provenance: { ...RETURN_SOURCE.provenance, source_snippet: '<b>x</b>\nline 2' } };
    const { el } = render({ source: evil });
    const snip = el.querySelector('[data-testid=pv-snippet]')!;
    expect(snip.querySelector('b')).toBeNull();
    expect(snip.textContent).toContain('<b>x</b>');
  });

  // BUG-Q2-02: the value is marked inside the snippet from `snippet_highlight`.
  it('marks the snippet_highlight range of the snippet', () => {
    const { el, text } = render({ source: RETURN_SOURCE });
    const snip = el.querySelector('[data-testid=pv-snippet]')!;
    const marks = snip.querySelectorAll('mark');
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe('+2.84%');
    expect(text('pv-snippet')).toBe('MTD % (net) +2.84%');

    const st = render({ source: STATEMENT_SOURCE }).el.querySelector('[data-testid=pv-snippet]')!;
    expect([...st.querySelectorAll('mark')].map((m) => m.textContent)).toEqual(['13,100,000.00']);
    expect(st.textContent).toBe('Closing balance 13,100,000.00');
  });

  it('shows the plain snippet when snippet_highlight is null', () => {
    const { el, text } = render({ source: { ...RETURN_SOURCE, snippet_highlight: null } });
    expect(el.querySelector('[data-testid=pv-snippet] mark')).toBeNull();
    expect(text('pv-snippet')).toBe('MTD % (net) +2.84%');
  });

  it('falls back to the plain snippet for out-of-range offsets', () => {
    for (const h of [
      { start: 12, end: 99, text: 'zzz' },
      { start: -3, end: 2, text: 'qq' },
      { start: 30, end: 31, text: 'x' },
      { start: 5, end: 5, text: '' },
      { start: 1.5, end: 4, text: 'TD' },
    ]) {
      const { el, text } = render({ source: { ...RETURN_SOURCE, snippet_highlight: h } });
      expect(el.querySelector('[data-testid=pv-snippet] mark')).toBeNull();
      expect(text('pv-snippet')).toBe('MTD % (net) +2.84%');
    }
  });

  it('keeps markup in a highlighted snippet as text', () => {
    const snippet = '<img src=x onerror="window.__pwned=1"> NAV <b>1.92%</b>';
    const start = snippet.indexOf('1.92%');
    const evil: SourceResponse = {
      ...RETURN_SOURCE,
      provenance: { ...RETURN_SOURCE.provenance, source_snippet: snippet },
      snippet_highlight: { start, end: start + 5, text: '1.92%' },
    };
    const snip = render({ source: evil }).el.querySelector('[data-testid=pv-snippet]')!;
    expect(snip.querySelector('img, b')).toBeNull();
    expect(snip.querySelector('mark')!.textContent).toBe('1.92%');
    expect(snip.textContent).toBe(snippet);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it('lists conflicting values', () => {
    const withConflict: SourceResponse = {
      ...RETURN_SOURCE,
      status: 'conflict',
      review_reasons: ['conflict'],
      conflicts: [
        {
          table: 'returns',
          id: 231,
          field: 'value',
          value: 0.0189,
          value_pct: 1.89,
          value_kind: 'percent',
          unit: null,
          currency: null,
          status: 'conflict',
          review_reasons: ['conflict'],
          provenance: { page: 1, source_snippet: 'Jan 2026 +1.89%', confidence: 0.9, snippet_verified: true },
          document_id: 18,
          document_name: 'northwind_flash_update_jan2026.eml',
          document_type: 'email',
          web_view_link: null,
          extraction_model: 'claude-opus-5-5',
        },
      ],
    };
    const { text } = render({ source: withConflict });
    expect(text('pv-conflict')).toContain('+1.89%');
    expect(text('pv-conflict')).toContain('northwind_flash_update_jan2026.eml');
  });

  it('shows placeholder, loading and error states', () => {
    expect(render({}).el.textContent).toContain('Click any value');
    expect(render({ loading: true }).el.textContent).toContain('Loading source');
    const err = render({
      error: { status: 404, code: 'not_found', message: 'returns row 999 not found.', details: null, envelope: true },
    });
    expect(err.el.textContent).toContain('returns row 999 not found.');
  });

  it('emits closed', () => {
    const { fixture, el } = render({ source: RETURN_SOURCE });
    let closed = 0;
    fixture.componentInstance.closed.subscribe(() => closed++);
    (el.querySelector('button.close') as HTMLButtonElement).click();
    expect(closed).toBe(1);
  });
});

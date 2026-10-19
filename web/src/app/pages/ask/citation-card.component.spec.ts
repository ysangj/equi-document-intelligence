import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { PassageCitation, SqlCitation } from '../../api/models';
import { SourceRef } from '../../shared/source-loader';
import { CONTRACT_ASK } from '../../testing/fixtures';
import { CitationCardComponent, formatCell, passageLabel } from './citation-card.component';

const SQL = CONTRACT_ASK.citations[0] as SqlCitation;
const PASSAGE = CONTRACT_ASK.citations[1] as PassageCitation;

describe('CitationCardComponent', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  async function render(citation: SqlCitation | PassageCitation) {
    const fixture = TestBed.createComponent(CitationCardComponent);
    fixture.componentRef.setInput('citation', citation);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('sql: shows the executed SQL, the columns, the rows and the flagged-row count', async () => {
    const { el } = await render(SQL);
    expect(el.querySelector('h5')!.textContent).toContain('1 · SQL result');
    expect(el.querySelector('[data-testid=sql]')!.textContent).toBe(SQL.sql);
    const heads = Array.from(el.querySelectorAll('thead th')).map((t) => t.textContent!.trim());
    expect(heads).toEqual(['fund_name', 'value_pct', 'status', 'document_name']);
    const rows = el.querySelectorAll('tbody tr');
    expect(rows.length).toBe(7);
    expect(rows[0].textContent).toContain('2.84');
    expect(el.querySelector('[data-testid=flagged-rows]')!.textContent).toContain('2 flagged rows');
    expect(el.querySelectorAll('tbody tr.flagged').length).toBe(2);
    expect(rows[1].querySelector('.st.cf')!.textContent).toBe('conflict');
    expect(el.querySelector('[data-testid=truncated]')).toBeNull();
    expect(el.querySelector('[data-testid=rows-toggle]')).toBeNull();
    // numeric column right-aligned
    expect(rows[0].querySelectorAll('td')[1].classList).toContain('num');
  });

  it('sql: shows the first 20 rows with an expander, and the truncated indicator', async () => {
    const rows = Array.from({ length: 200 }, (_, k) => [`Fund ${k}`, k / 10, 'ok']);
    const big: SqlCitation = { ...SQL, columns: ['fund_name', 'value_pct', 'status'], rows, row_count: 200, truncated: true, flagged_row_count: 0 };
    const { fixture, el } = await render(big);
    expect(el.querySelectorAll('tbody tr').length).toBe(20);
    expect(el.querySelector('[data-testid=truncated]')).not.toBeNull();
    expect(el.querySelector('[data-testid=flagged-rows]')).toBeNull();
    const toggle = el.querySelector<HTMLButtonElement>('[data-testid=rows-toggle]')!;
    expect(toggle.textContent).toContain('Show all 200 rows');
    toggle.click();
    await fixture.whenStable();
    expect(el.querySelectorAll('tbody tr').length).toBe(200);
    expect(toggle.textContent).toContain('Show first 20 rows');
  });

  it('sql: rows with a return_id open the source panel', async () => {
    const withId: SqlCitation = { ...SQL, columns: [...SQL.columns, 'return_id'], rows: SQL.rows.map((r, k) => [...r, 100 + k]) };
    const { fixture, el } = await render(withId);
    const refs: SourceRef[] = [];
    fixture.componentInstance.openSource.subscribe((r) => refs.push(r));
    el.querySelectorAll<HTMLButtonElement>('[data-testid=row-source]')[1].click();
    expect(refs).toEqual([{ table: 'returns', id: 101, field: null }]);
    // the id column is not right-aligned as a number
    expect(el.querySelector('tbody tr')!.querySelectorAll('td')[4].classList).not.toContain('num');
  });

  it('sql: v_holdings rows (holding_id + statement_id) open the holding; statement_id alone opens the closing balance', async () => {
    const holdings: SqlCitation = {
      ...SQL,
      sql: 'SELECT holding_id, statement_id, holding_name, weight_pct FROM v_holdings LIMIT 200',
      columns: ['holding_id', 'statement_id', 'holding_name', 'weight_pct'],
      rows: [
        [31, 4, 'US Treasury 2y', 32.4],
        [32, 4, 'Bund 10y', 18.1],
      ],
      row_count: 2,
      flagged_row_count: 0,
    };
    const { fixture, el } = await render(holdings);
    const refs: SourceRef[] = [];
    fixture.componentInstance.openSource.subscribe((r) => refs.push(r));
    el.querySelectorAll<HTMLButtonElement>('[data-testid=row-source]')[1].click();
    expect(refs).toEqual([{ table: 'statement_holdings', id: 32, field: null }]);

    const stmts: SqlCitation = { ...holdings, columns: ['statement_id', 'account_name'], rows: [[4, 'Acme']], row_count: 1 };
    fixture.componentRef.setInput('citation', stmts);
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid=row-source]')!.click();
    expect(refs[1]).toEqual({ table: 'statements', id: 4, field: 'closing_balance' });
  });

  it('sql: the Source column is sticky so it stays in view on wide results (real T8 query, 10 columns)', async () => {
    const wide: SqlCitation = {
      ...SQL,
      columns: ['return_id', 'fund_name', 'period_year', 'period_month', 'return_type', 'net_gross', 'value_pct', 'status', 'review_reasons', 'document_name'],
      rows: [
        [31, 'Kestrel Quant Equity Fund LP', 2026, 1, 'MTD', 'net', 2.84, 'ok', [], 'kestrel_factsheet_2026-01.pdf'],
        [74, 'Northwind Global Macro Fund', 2026, 1, 'MTD', 'net', 1.92, 'conflict', ['conflict'], 'northwind_monthly_jan26.pdf'],
      ],
      row_count: 2,
      flagged_row_count: 1,
    };
    const { el } = await render(wide);
    expect(el.querySelector('thead th:last-child')!.classList).toContain('src');
    const lastCells = Array.from(el.querySelectorAll('tbody tr')).map((r) => r.querySelector('td:last-child')!);
    expect(lastCells.every((td) => td.classList.contains('src') && !!td.querySelector('[data-testid=row-source]'))).toBe(true);
    const cells = Array.from(el.querySelectorAll('tbody tr')[1].querySelectorAll('td')).map((t) => t.textContent!.trim());
    expect(cells[8]).toBe('conflict');
    expect(Array.from(el.querySelectorAll('tbody tr')[0].querySelectorAll('td')).map((t) => t.textContent!.trim())[8]).toBe('—');
  });

  it('passage: a long chunk starts clamped with "Show full passage"; a short one has no toggle', async () => {
    const long: PassageCitation = { ...PASSAGE, text: Array.from({ length: 30 }, (_, i) => `line ${i + 1}`).join('\n') };
    const { fixture, el } = await render(long);
    const text = el.querySelector('[data-testid=passage-text]')!;
    const toggle = el.querySelector<HTMLButtonElement>('[data-testid=passage-toggle]')!;
    expect(text.classList).toContain('clamped');
    expect(text.textContent).toContain('line 30'); // the full text is in the DOM, only visually clamped
    expect(toggle.textContent!.trim()).toBe('Show full passage');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBe(text.id);
    toggle.click();
    await fixture.whenStable();
    expect(text.classList).not.toContain('clamped');
    expect(toggle.textContent!.trim()).toBe('Show less');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    const short = await render(PASSAGE);
    expect(short.el.querySelector('[data-testid=passage-text]')!.classList).not.toContain('clamped');
    expect(short.el.querySelector('[data-testid=passage-toggle]')).toBeNull();
  });

  it('passage: document · page, the text and the Drive link', async () => {
    const { el } = await render(PASSAGE);
    expect(el.querySelector('h5')!.textContent).toContain('2 · Passage');
    expect(el.querySelector('[data-testid=passage-text]')!.textContent).toContain('short-term reversal signals');
    const link = el.querySelector<HTMLAnchorElement>('[data-testid=passage-link]')!;
    expect(link.textContent!.trim()).toBe('kestrel_factsheet_2026-01.pdf · p.2 ↗');
    expect(link.getAttribute('href')).toBe(PASSAGE.web_view_link);
    expect(link.getAttribute('target')).toBe('_blank');
  });

  it('passage without a Drive link (demo) opens the document on the Data page; CSV row groups say "rows"', async () => {
    const { el } = await render({ ...PASSAGE, web_view_link: null, page: null, document_name: 'harbor.csv' });
    const link = el.querySelector<HTMLAnchorElement>('[data-testid=passage-link]')!;
    expect(link.textContent!.trim()).toBe('harbor.csv · rows');
    expect(link.getAttribute('href')).toBe('/data?document_id=3&document_name=harbor.csv');
    expect(link.getAttribute('target')).toBeNull();
  });

  it('formats cells and labels', () => {
    expect(formatCell(null)).toBe('—');
    expect(formatCell(-0.36)).toBe('-0.36');
    expect(formatCell(true)).toBe('true');
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    // array cells (v_returns.review_reasons) read as a list, not JSON
    expect(formatCell([])).toBe('—');
    expect(formatCell(['conflict'])).toBe('conflict');
    expect(formatCell(['ytd_mismatch', 'low_confidence'])).toBe('ytd_mismatch, low_confidence');
    expect(passageLabel({ document_name: 'a.pdf', page: 3 })).toBe('a.pdf · p.3');
  });
});

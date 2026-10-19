import {
  EMPTY_FILTERS,
  clearFilters,
  factsQuery,
  hasActiveFilters,
  returnsQuery,
  statementsQuery,
  summarize,
} from './data-filters';

describe('data filters -> contract queries', () => {
  const f = {
    ...EMPTY_FILTERS,
    fundIds: [1, 3],
    accountIds: [2],
    dateFrom: '2026-01-01',
    dateTo: '2026-03-31',
    documentTypes: ['factsheet' as const],
    statuses: ['needs_review' as const, 'conflict' as const],
    returnTypes: ['MTD' as const],
    netGross: ['net' as const],
  };
  const p = { page: 2, pageSize: 50, sort: '-value_pct' };

  it('maps returns filters (fund, type, net/gross, common)', () => {
    expect(returnsQuery(f, p)).toEqual({
      fund_id: [1, 3],
      return_type: ['MTD'],
      net_gross: ['net'],
      document_id: [],
      date_from: '2026-01-01',
      date_to: '2026-03-31',
      document_type: ['factsheet'],
      status: ['needs_review', 'conflict'],
      page: 2,
      page_size: 50,
      sort: '-value_pct',
    });
  });

  it('maps fund facts filters without return-only keys', () => {
    const q = factsQuery(f, p);
    expect(q.fund_id).toEqual([1, 3]);
    expect('return_type' in q).toBe(false);
  });

  it('maps statements filters to account_id, not fund_id', () => {
    const q = statementsQuery(f, p);
    expect(q.account_id).toEqual([2]);
    expect('fund_id' in q).toBe(false);
  });

  it('knows which filters apply to each view', () => {
    const fundOnly = { ...EMPTY_FILTERS, fundIds: [1] };
    expect(hasActiveFilters(fundOnly, 'returns')).toBe(true);
    expect(hasActiveFilters(fundOnly, 'statements')).toBe(false);
    expect(hasActiveFilters(EMPTY_FILTERS, 'returns')).toBe(false);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, statuses: ['ok'] }, 'statements')).toBe(true);
  });

  it('clears only the filters of the current view', () => {
    const c = clearFilters(f, 'statements');
    expect(c.accountIds).toEqual([]);
    expect(c.statuses).toEqual([]);
    expect(c.fundIds).toEqual([1, 3]);
  });

  it('summarizes chip values', () => {
    const opts = [
      { value: 1, label: 'Kestrel' },
      { value: 2, label: 'Northwind' },
    ];
    expect(summarize([], opts, 6)).toBe('All (6)');
    expect(summarize([], opts)).toBe('All');
    expect(summarize([2], opts)).toBe('Northwind');
    expect(summarize([1, 2], opts)).toBe('2 selected');
  });
});

describe('Data page deep links from Search', () => {
  it('idParam accepts positive integers only', async () => {
    const { idParam } = await import('./data-page.component');
    expect(idParam('2')).toBe(2);
    expect(idParam(undefined)).toBeNull();
    expect(idParam('0')).toBeNull();
    expect(idParam('-1')).toBeNull();
    expect(idParam('2x')).toBeNull();
  });

  it('document_id is sent as a common filter and counts as active', () => {
    const f = { ...EMPTY_FILTERS, documentIds: [9] };
    expect(returnsQuery(f, { page: 1, pageSize: 50, sort: null }).document_id).toEqual([9]);
    expect(hasActiveFilters(f, 'statements')).toBe(true);
    expect(clearFilters(f, 'returns').documentIds).toEqual([]);
  });
});

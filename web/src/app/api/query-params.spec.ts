import { buildParams, buildUrl, toApiPage, toSortParam } from './query-params';

describe('buildParams', () => {
  it('repeats keys for arrays (C4 multi-select)', () => {
    const p = buildParams({ fund_id: [1, 3], status: ['ok', 'conflict'] });
    expect(p.getAll('fund_id')).toEqual(['1', '3']);
    expect(p.getAll('status')).toEqual(['ok', 'conflict']);
    expect(p.toString()).toBe('fund_id=1&fund_id=3&status=ok&status=conflict');
  });

  it('omits null, undefined, empty strings and empty arrays', () => {
    const p = buildParams({ a: null, b: undefined, c: '', d: [], e: 0, f: false });
    expect(p.keys()).toEqual(['e', 'f']);
    expect(p.toString()).toBe('e=0&f=false');
  });

  it('handles a missing query', () => {
    expect(buildParams(null).toString()).toBe('');
    expect(buildUrl('/api/returns', undefined)).toBe('/api/returns');
  });

  it('builds a URL with encoded values', () => {
    expect(buildUrl('/api/returns', { sort: '-period_end,fund_name', date_from: '2026-01-01' })).toBe(
      '/api/returns?sort=-period_end,fund_name&date_from=2026-01-01',
    );
  });
});

describe('toSortParam (MatSort -> C3 sort)', () => {
  it('maps asc / desc / none', () => {
    expect(toSortParam('value_pct', 'asc')).toBe('value_pct');
    expect(toSortParam('value_pct', 'desc')).toBe('-value_pct');
    expect(toSortParam('value_pct', '')).toBeNull();
    expect(toSortParam('', 'asc')).toBeNull();
    expect(toSortParam(null, null)).toBeNull();
  });
});

describe('toApiPage', () => {
  it('converts the 0-based paginator index to the 1-based API page', () => {
    expect(toApiPage(0)).toBe(1);
    expect(toApiPage(4)).toBe(5);
    expect(toApiPage(-1)).toBe(1);
  });
});

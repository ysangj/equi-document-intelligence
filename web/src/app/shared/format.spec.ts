import {
  EMPTY,
  factSignValue,
  formatAmount,
  formatConfidence,
  formatDate,
  formatFact,
  formatKindedValue,
  formatMetric,
  formatMoney,
  formatNumber,
  formatPct,
  formatPeriod,
  formatRelative,
  formatTokens,
  formatWeight,
  kindedSignClass,
  negClass,
  signClass,
} from './format';

describe('formatPct (C6: value_pct, 2 dp, explicit sign, %)', () => {
  it('formats the contract examples', () => {
    expect(formatPct(2.84)).toBe('+2.84%');
    expect(formatPct(-0.36)).toBe('-0.36%');
    expect(formatPct(0)).toBe('0.00%');
  });

  it('rounds to 2 dp and never shows -0.00%', () => {
    expect(formatPct(1.006)).toBe('+1.01%');
    expect(formatPct(-0.004)).toBe('0.00%');
    expect(formatPct(12.3)).toBe('+12.30%');
  });

  it('shows an em dash for missing values', () => {
    expect(formatPct(null)).toBe(EMPTY);
    expect(formatPct(undefined)).toBe(EMPTY);
    expect(formatPct(Number.NaN)).toBe(EMPTY);
  });
});

describe('formatWeight', () => {
  it('drops the plus sign but keeps negatives', () => {
    expect(formatWeight(32.4)).toBe('32.40%');
    expect(formatWeight(-5)).toBe('-5.00%');
    expect(formatWeight(null)).toBe(EMPTY);
  });
});

describe('money', () => {
  it('uses thousands separators, 2 dp and the currency code', () => {
    expect(formatMoney(13100000, 'USD')).toBe('13,100,000.00 USD');
    expect(formatMoney(-20000.5, 'USD')).toBe('-20,000.50 USD');
    expect(formatMoney(0, 'EUR')).toBe('0.00 EUR');
    expect(formatMoney(1234.5)).toBe('1,234.50');
    expect(formatMoney(null, 'USD')).toBe(EMPTY);
  });

  it('formatAmount has no currency and a leading minus for negatives', () => {
    expect(formatAmount(-1500)).toBe('-1,500.00');
    expect(formatAmount(-0.001)).toBe('0.00');
  });
});

describe('sign classes', () => {
  it('colors positives green and negatives red', () => {
    expect(signClass(2.84)).toBe('pos');
    expect(signClass(-0.36)).toBe('neg');
    expect(signClass(0)).toBe('');
    expect(signClass(null)).toBe('');
    expect(negClass(-1)).toBe('neg');
    expect(negClass(5)).toBe('');
  });
});

describe('dates', () => {
  it('formats a period end as "Jan 2026"', () => {
    expect(formatPeriod('2026-01-31')).toBe('Jan 2026');
    expect(formatPeriod('2025-12-31')).toBe('Dec 2025');
    expect(formatPeriod(null)).toBe(EMPTY);
    expect(formatPeriod('Q1-26')).toBe('Q1-26');
  });

  it('formats a date as "31 Jan 2026"', () => {
    expect(formatDate('2026-01-31')).toBe('31 Jan 2026');
    expect(formatDate('2026-02-01T00:00:00Z')).toBe('1 Feb 2026');
    expect(formatDate(null)).toBe(EMPTY);
  });
});

describe('fund facts (C6)', () => {
  it('shows fraction units as percentages from value_pct', () => {
    expect(formatFact({ value: -0.068, value_pct: -6.8, value_text: null, unit: 'fraction' })).toBe('-6.80%');
  });
  it('shows currency units as money', () => {
    expect(formatFact({ value: 412600000, value_pct: null, value_text: null, unit: 'USD' })).toBe('412,600,000.00 USD');
  });
  it('shows ratios bare and other units with the unit', () => {
    expect(formatFact({ value: 1.84, value_pct: null, value_text: null, unit: 'ratio' })).toBe('1.84');
    expect(formatFact({ value: 2.1, value_pct: null, value_text: null, unit: 'years' })).toBe('2.1 years');
    expect(formatFact({ value: 3, value_pct: null, value_text: null, unit: null })).toBe('3');
  });
  it('uses value_text for non-numeric facts', () => {
    expect(formatFact({ value: null, value_pct: null, value_text: 'Quarterly', unit: null })).toBe('Quarterly');
    expect(formatFact({ value: null, value_pct: null, value_text: null, unit: null })).toBe(EMPTY);
  });
  it('colors only percentage facts', () => {
    expect(factSignValue({ value_pct: -6.8, unit: 'fraction' })).toBe(-6.8);
    expect(factSignValue({ value_pct: null, unit: 'USD' })).toBeNull();
  });
  it('labels metrics', () => {
    expect(formatMetric('aum')).toBe('AUM');
    expect(formatMetric('max_drawdown')).toBe('Max drawdown');
    expect(formatMetric('spread_duration')).toBe('Spread duration');
  });
});

describe('formatKindedValue (side panel value_kind)', () => {
  const base = { unit: null, currency: null, value_pct: null };
  it('handles every kind', () => {
    expect(formatKindedValue({ ...base, value: 0.0284, value_pct: 2.84, value_kind: 'percent' })).toBe('+2.84%');
    expect(formatKindedValue({ ...base, value: 13100000, value_kind: 'money', currency: 'USD' })).toBe('13,100,000.00 USD');
    expect(formatKindedValue({ ...base, value: 1870000, value_kind: 'number' })).toBe('1,870,000');
    expect(formatKindedValue({ ...base, value: 2.1, value_kind: 'number', unit: 'years' })).toBe('2.1 years');
    expect(formatKindedValue({ ...base, value: '2026-01-31', value_kind: 'date' })).toBe('31 Jan 2026');
    expect(formatKindedValue({ ...base, value: 'Larkspur Trust Company', value_kind: 'text' })).toBe('Larkspur Trust Company');
    expect(formatKindedValue({ ...base, value: null, value_kind: 'money', currency: 'USD' })).toBe(EMPTY);
    expect(formatKindedValue({ ...base, value: null, value_kind: 'text' })).toBe(EMPTY);
  });
  it('sign classes follow the kind', () => {
    expect(kindedSignClass({ ...base, value: -0.0036, value_pct: -0.36, value_kind: 'percent' })).toBe('neg');
    expect(kindedSignClass({ ...base, value: 5, value_kind: 'money' })).toBe('');
    expect(kindedSignClass({ ...base, value: -5, value_kind: 'money' })).toBe('neg');
  });
});

describe('misc', () => {
  it('formats confidence, numbers and tokens', () => {
    expect(formatConfidence(0.9)).toBe('0.90');
    expect(formatConfidence(null)).toBe(EMPTY);
    expect(formatNumber(1234.56789)).toBe('1,234.5679');
    expect(formatTokens(7232)).toBe('7.2k');
    expect(formatTokens(812)).toBe('812');
    expect(formatTokens(null)).toBe(EMPTY);
  });

  it('formats relative times from generated_at', () => {
    const now = '2026-10-16T18:52:10Z';
    expect(formatRelative('2026-10-16T18:51:58Z', now)).toBe('12s ago');
    expect(formatRelative('2026-10-16T18:49:10Z', now)).toBe('3 min ago');
    expect(formatRelative('2026-10-16T16:52:10Z', now)).toBe('2 h ago');
    expect(formatRelative(null, now)).toBe('never');
  });
});

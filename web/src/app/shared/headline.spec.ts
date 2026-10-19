import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { SearchResponse } from '../api/models';
import { SearchResultsComponent } from '../pages/search/search-results.component';
import { CONTRACT_SEARCH } from '../testing/fixtures';
import { decodeEntities, headlineText, highlightTerms, parseHeadline, snippetSegments } from './headline';

describe('parseHeadline (headline_html: only <mark> survives)', () => {
  it('splits marked and plain text', () => {
    expect(parseHeadline('We reduced <mark>duration</mark> from 3.4')).toEqual([
      { text: 'We reduced ', mark: false },
      { text: 'duration', mark: true },
      { text: ' from 3.4', mark: false },
    ]);
  });

  it('decodes the escaped entities into plain text', () => {
    expect(headlineText('S&amp;P 500 &lt;b&gt; &quot;q&quot; it&#x27;s &#39;x&#39;')).toBe('S&P 500 <b> "q" it\'s \'x\'');
  });

  it('drops <script> elements and their content', () => {
    const segs = parseHeadline('a <script>alert(1)</script><mark>b</mark>');
    expect(segs).toEqual([
      { text: 'a ', mark: false },
      { text: 'b', mark: true },
    ]);
  });

  it('drops <img onerror> and any other tag, keeping surrounding text', () => {
    expect(headlineText('x<img src=x onerror="alert(1)">y<a href="javascript:alert(1)">z</a>')).toBe('xyz');
    expect(headlineText('<iframe src="//evil"></iframe>ok')).toBe('ok');
  });

  it('does not treat <mark> with attributes as a mark', () => {
    const segs = parseHeadline('<mark onclick="alert(1)">x</mark>');
    expect(segs.every((s) => !s.text.includes('onclick'))).toBe(true);
    expect(headlineText('<mark class="x">x</mark>')).toBe('x');
  });

  it('flattens nesting and ignores stray closers', () => {
    expect(parseHeadline('<mark>a<mark>b</mark>c</mark>d')).toEqual([
      { text: 'ab', mark: true },
      { text: 'cd', mark: false },
    ]);
    expect(parseHeadline('</mark>x')).toEqual([{ text: 'x', mark: false }]);
  });

  it('escaped markup in the document stays visible as text, never as markup', () => {
    expect(headlineText('&lt;img src=x onerror=alert(1)&gt;')).toBe('<img src=x onerror=alert(1)>');
  });

  it('handles empty input', () => {
    expect(parseHeadline('')).toEqual([]);
    expect(parseHeadline(null)).toEqual([]);
  });

  it('decodeEntities leaves unknown entities alone', () => {
    expect(decodeEntities('&bogus; &#0;')).toBe('&bogus; &#0;');
  });
});

describe('highlightTerms (client-side highlight of names)', () => {
  it('marks query words case-insensitively', () => {
    expect(highlightTerms('Northwind Global Macro Fund', 'northwind macro')).toEqual([
      { text: 'Northwind', mark: true },
      { text: ' Global ', mark: false },
      { text: 'Macro', mark: true },
      { text: ' Fund', mark: false },
    ]);
  });

  it('ignores 1-character words and regex metacharacters', () => {
    expect(highlightTerms('a.b (c)', 'a ( .')).toEqual([{ text: 'a.b (c)', mark: false }]);
  });
});

describe('snippetSegments (SourceResponse.snippet_highlight)', () => {
  const S = 'MTD % (net) +2.84%';

  it('marks the [start, end) range of the contract example', () => {
    expect(snippetSegments(S, { start: 12, end: 18, text: '+2.84%' })).toEqual([
      { text: 'MTD % (net) ', mark: false },
      { text: '+2.84%', mark: true },
    ]);
  });

  it('handles a range at the start, in the middle and covering everything', () => {
    expect(snippetSegments('2.84% net', { start: 0, end: 5, text: '2.84%' })).toEqual([
      { text: '2.84%', mark: true },
      { text: ' net', mark: false },
    ]);
    expect(snippetSegments('Jan  2.84%   Feb', { start: 5, end: 10, text: '2.84%' })).toEqual([
      { text: 'Jan  ', mark: false },
      { text: '2.84%', mark: true },
      { text: '   Feb', mark: false },
    ]);
    expect(snippetSegments('1.92%', { start: 0, end: 5, text: '1.92%' })).toEqual([{ text: '1.92%', mark: true }]);
  });

  it('uses UTF-16 offsets (JS string indices), so non-BMP text before the value is fine', () => {
    const s = '📈 NAV 1,225.49';
    const start = s.indexOf('1,225.49');
    expect(start).toBe(7); // the emoji is 2 code units
    expect(snippetSegments(s, { start, end: start + 8, text: '1,225.49' })[1]).toEqual({ text: '1,225.49', mark: true });
  });

  it('is the plain snippet when the highlight is null or there is no snippet', () => {
    expect(snippetSegments(S, null)).toEqual([{ text: S, mark: false }]);
    expect(snippetSegments(S, undefined)).toEqual([{ text: S, mark: false }]);
    expect(snippetSegments(null, { start: 0, end: 1, text: 'M' })).toEqual([]);
    expect(snippetSegments('', null)).toEqual([]);
  });

  it('falls back to the plain snippet for out-of-range or malformed offsets', () => {
    const bad = [
      { start: 12, end: 99, text: '+2.84%' }, // past the end
      { start: -1, end: 5, text: 'MTD %' }, // negative
      { start: 18, end: 20, text: '' }, // starts at the end
      { start: 6, end: 6, text: '' }, // empty
      { start: 10, end: 4, text: 'x' }, // reversed
      { start: 1.5, end: 4, text: 'TD' }, // not integers
      { start: NaN, end: 4, text: 'MTD' },
      { start: 0, end: Infinity, text: S },
    ];
    for (const h of bad) expect(snippetSegments(S, h)).toEqual([{ text: S, mark: false }]);
  });

  it('falls back when the range does not slice to `text` (never marks a guessed position)', () => {
    expect(snippetSegments(S, { start: 0, end: 3, text: '+2.84%' })).toEqual([{ text: S, mark: false }]);
  });
});

describe('SearchResultsComponent rendering of headline_html', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  async function render(r: SearchResponse) {
    const fixture = TestBed.createComponent(SearchResultsComponent);
    fixture.componentRef.setInput('response', r);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders <mark> elements for the contract example', async () => {
    const el = await render(CONTRACT_SEARCH);
    const hits = el.querySelectorAll('[data-testid=chunk-hit]');
    expect(hits.length).toBe(2);
    expect(Array.from(hits[0].querySelectorAll('mark')).map((m) => m.textContent)).toEqual(['Meridian', 'duration']);
    expect(hits[0].querySelector('[data-testid=chunk-source]')!.textContent!.trim()).toBe('meridian_rv_tearsheet_2026-01.pdf · p.1 ↗');
    expect(hits[0].querySelector('a')!.getAttribute('href')).toContain('drive.google.com');
  });

  it('never creates <script>, <img> or attributes from hostile headline_html', async () => {
    const hostile = {
      ...CONTRACT_SEARCH,
      groups: {
        ...CONTRACT_SEARCH.groups,
        chunks: [
          {
            ...CONTRACT_SEARCH.groups.chunks[0],
            headline_html:
              '<script>window.__pwned=1</script>safe <mark>duration</mark> <img src=x onerror="window.__pwned=2"> <mark onmouseover="x">y</mark> &lt;b&gt;',
          },
        ],
      },
    };
    const el = await render(hostile);
    const hit = el.querySelector('[data-testid=chunk-hit]')!;
    expect(hit.querySelector('script')).toBeNull();
    expect(hit.querySelector('img')).toBeNull();
    expect(hit.querySelector('b')).toBeNull();
    const marks = Array.from(hit.querySelectorAll('mark'));
    expect(marks.map((m) => m.textContent)).toEqual(['duration']);
    expect(marks.every((m) => m.attributes.length === 0 || [...m.attributes].every((a) => a.name.startsWith('_ng')))).toBe(true);
    expect(hit.querySelector('.snippet')!.textContent).toContain('<b>'); // escaped text shown literally
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it('links passages without a Drive link to the document on the Data page', async () => {
    const r = {
      ...CONTRACT_SEARCH,
      groups: { ...CONTRACT_SEARCH.groups, chunks: [{ ...CONTRACT_SEARCH.groups.chunks[0], web_view_link: null, page: null }] },
    };
    const el = await render(r);
    const a = el.querySelector('[data-testid=chunk-source]') as HTMLAnchorElement;
    expect(a.textContent!.trim()).toBe('meridian_rv_tearsheet_2026-01.pdf · rows');
    expect(a.getAttribute('href')).toBe('/data?document_id=9&document_name=meridian_rv_tearsheet_2026-01.pdf');
  });

  it('groups results by type and links funds to the filtered Data page', async () => {
    const el = await render(CONTRACT_SEARCH);
    expect(Array.from(el.querySelectorAll('h6')).map((h) => h.textContent!.replace(/\s+/g, ' ').trim())).toEqual([
      'Funds & aliases 1',
      'Documents 2',
      'Passages 2',
    ]);
    const fund = el.querySelector('[data-testid=fund-hit]') as HTMLAnchorElement;
    expect(fund.getAttribute('href')).toBe('/data?fund_id=3');
    expect(fund.textContent).toContain('aka Meridian Rates RV Fund');
  });
});

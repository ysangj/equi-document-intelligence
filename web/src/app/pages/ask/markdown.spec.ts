import { Block, Inline, inlineText, parseInline, parseMarkdown, safeHref, splitUnverified, stripHtml } from './markdown';

const cites = (ids: number[]) => ({ cites: new Set(ids) });
const p = (blocks: Block[], k = 0): Inline[] => {
  const b = blocks[k];
  if (b.t !== 'p') throw new Error(`block ${k} is ${b.t}`);
  return b.c;
};
const kinds = (nodes: Inline[]) => nodes.map((n) => n.t);
const citeIds = (nodes: Inline[]): number[] =>
  nodes.flatMap((n) => (n.t === 'cite' ? [n.id] : n.t === 'strong' || n.t === 'em' || n.t === 'link' ? citeIds(n.c) : []));

describe('parseMarkdown: blocks', () => {
  it('parses paragraphs, headings, lists, code blocks, quotes, rules and tables', () => {
    const md = [
      '## Summary',
      'First line',
      'continues here.',
      '',
      '- one',
      '- two',
      '  - nested',
      '',
      '1. first',
      '2. second',
      '',
      '```sql',
      'SELECT 1 [1]',
      '```',
      '',
      '> quoted',
      '',
      '---',
      '',
      '| Fund | Return |',
      '|---|---:|',
      '| Kestrel | +2.84% |',
    ].join('\n');
    const blocks = parseMarkdown(md, { citationIds: [1] });
    expect(blocks.map((b) => b.t)).toEqual(['h', 'p', 'list', 'list', 'code', 'quote', 'hr', 'table']);
    expect(inlineText(p(blocks, 1))).toBe('First line continues here.');
    const ul = blocks[2] as Extract<Block, { t: 'list' }>;
    expect(ul.ordered).toBe(false);
    expect(ul.items.length).toBe(2);
    expect(ul.items[1].map((b) => b.t)).toEqual(['p', 'list']);
    const ol = blocks[3] as Extract<Block, { t: 'list' }>;
    expect(ol.ordered).toBe(true);
    expect(ol.items.length).toBe(2);
    const code = blocks[4] as Extract<Block, { t: 'code' }>;
    expect(code).toEqual({ t: 'code', lang: 'sql', v: 'SELECT 1 [1]' });
    const table = blocks[7] as Extract<Block, { t: 'table' }>;
    expect(table.align).toEqual([null, 'right']);
    expect(inlineText(table.rows[0][1])).toBe('+2.84%');
  });

  it('parses bold, italic, code spans, links and hard breaks', () => {
    const nodes = parseInline('**Kestrel** had *the* best `v_returns` [docs](https://example.com/a)  \nnext', cites([]));
    expect(kinds(nodes)).toEqual(['strong', 'text', 'em', 'text', 'code', 'text', 'link', 'br', 'text']);
    expect(nodes[6]).toMatchObject({ t: 'link', href: 'https://example.com/a' });
  });

  it('keeps snake_case identifiers and arithmetic asterisks as text', () => {
    const nodes = parseInline('Use v_returns and fund_name; 2 * 3 * 4 is not emphasis.');
    expect(kinds(nodes)).toEqual(['text']);
    expect(inlineText(nodes)).toBe('Use v_returns and fund_name; 2 * 3 * 4 is not emphasis.');
  });
});

describe('parseMarkdown: citation markers', () => {
  it('turns [n] into citation nodes next to punctuation and back to back', () => {
    const nodes = parseInline('at +2.84% [1]. Also (see [2]), return[1], and [1][2]; end[3]', cites([1, 2, 3]));
    expect(citeIds(nodes)).toEqual([1, 2, 1, 1, 2, 3]);
    expect(inlineText(nodes)).toBe('at +2.84% [1]. Also (see [2]), return[1], and [1][2]; end[3]');
    // punctuation stays outside the chip
    const after = nodes[nodes.findIndex((n) => n.t === 'cite') + 1];
    expect(after).toEqual({ t: 'text', v: '. Also (see ' });
  });

  it('replaces markers inside bold and list items', () => {
    const blocks = parseMarkdown('**+2.84% [1]**\n\n- Meridian cut duration [2].', { citationIds: [1, 2] });
    expect(citeIds(p(blocks))).toEqual([1]);
    const list = blocks[1] as Extract<Block, { t: 'list' }>;
    expect(citeIds(p(list.items[0]))).toEqual([2]);
  });

  it('never replaces markers inside code spans or code blocks', () => {
    const blocks = parseMarkdown('Run `rows[1]` here [1].\n\n```\nx = arr[1]\n```\n\n    indented[1]', { citationIds: [1] });
    const inl = p(blocks);
    expect(citeIds(inl)).toEqual([1]);
    expect(inl.find((n) => n.t === 'code')).toEqual({ t: 'code', v: 'rows[1]' });
    expect(blocks[1]).toEqual({ t: 'code', lang: null, v: 'x = arr[1]' });
    expect(blocks[2]).toEqual({ t: 'code', lang: null, v: 'indented[1]' });
  });

  it('leaves unknown ids and escaped markers as text', () => {
    const nodes = parseInline('see [9] and \\[1] and [1]', cites([1]));
    expect(citeIds(nodes)).toEqual([1]);
    expect(inlineText(nodes)).toBe('see [9] and [1] and [1]');
  });
});

describe('parseMarkdown: raw HTML and links are neutralized', () => {
  it('strips <script> with its content and any tag such as <img onerror>', () => {
    expect(stripHtml('a<script>alert(1)</script>b')).toBe('ab');
    expect(stripHtml('x <img src=x onerror="alert(1)"> y')).toBe('x  y');
    expect(stripHtml('<b>bold</b> <a href="javascript:alert(1)">link</a>')).toBe('bold link');
    expect(stripHtml('<!-- c --><iframe src="//evil"></iframe>ok')).toBe('ok');
    expect(stripHtml('<SCRIPT type="text/javascript">x()</SCRIPT>done')).toBe('done');
  });

  it('keeps comparison operators and HTML inside code spans', () => {
    expect(stripHtml('a < b and c > d')).toBe('a < b and c > d');
    const nodes = parseInline('Literal `<script>x</script>` stays code');
    expect(nodes.find((n) => n.t === 'code')).toEqual({ t: 'code', v: '<script>x</script>' });
  });

  it('drops javascript: and data: links but keeps their text', () => {
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('data:text/html,hi')).toBeNull();
    expect(safeHref('https://drive.google.com/x')).toBe('https://drive.google.com/x');
    const nodes = parseInline('[click](javascript:alert(1)) and [ok](mailto:a@b.c)');
    expect(nodes.some((n) => n.t === 'link' && n.href.startsWith('javascript'))).toBe(false);
    expect(inlineText(nodes)).toContain('click');
    expect(nodes.find((n) => n.t === 'link')).toMatchObject({ href: 'mailto:a@b.c' });
  });
});

describe('parseMarkdown: unverified numbers', () => {
  const md = '**Kestrel** led at **+2.84%** [1], 0.92 percentage points ahead of +1.92% [1]. `0.92` in code.';

  it('highlights exact substrings inside text, including inside bold, without changing the structure', () => {
    const plain = parseMarkdown(md, { citationIds: [1] });
    const marked = parseMarkdown(md, { citationIds: [1], unverified: ['0.92', '+2.84%'] });
    const inl = p(marked);
    // same top-level shape: strong, text..., cites stay cites, code stays code
    expect(citeIds(inl)).toEqual(citeIds(p(plain)));
    expect(inl.filter((n) => n.t === 'strong').length).toBe(2);
    const strong2 = inl.filter((n) => n.t === 'strong')[1] as Extract<Inline, { t: 'strong' }>;
    expect(strong2.c).toEqual([{ t: 'unverified', v: '+2.84%' }]);
    expect(inl.filter((n) => n.t === 'unverified').map((n) => (n as { v: string }).v)).toEqual(['0.92']);
    expect(inl.find((n) => n.t === 'code')).toEqual({ t: 'code', v: '0.92' });
    expect(inlineText(inl)).toBe(inlineText(p(plain)));
  });

  it('does not match inside a larger number', () => {
    expect(splitUnverified('10.92 and 0.925 and 0.92.', ['0.92']).filter((n) => n.t === 'unverified').length).toBe(1);
    expect(splitUnverified('1,250.5 vs 250', ['250']).filter((n) => n.t === 'unverified').length).toBe(1);
  });

  it('highlights every occurrence, preferring the longest needle', () => {
    const nodes = splitUnverified('0.5% then 0.5 then 0.5%', ['0.5', '0.5%']);
    expect(nodes.filter((n) => n.t === 'unverified').map((n) => (n as { v: string }).v)).toEqual(['0.5%', '0.5', '0.5%']);
  });

  it('highlights in list items and table cells', () => {
    const blocks = parseMarkdown('- moved NAV by roughly 0.5% [1]\n\n| a |\n|---|\n| 0.5% |', { citationIds: [1], unverified: ['0.5%'] });
    const list = blocks[0] as Extract<Block, { t: 'list' }>;
    expect(kinds(p(list.items[0]))).toEqual(['text', 'unverified', 'text', 'cite']);
    const table = blocks[1] as Extract<Block, { t: 'table' }>;
    expect(table.rows[0][0]).toEqual([{ t: 'unverified', v: '0.5%' }]);
  });
});

// @ts-expect-error node built-in; the spec tsconfig has no @types/node
import { readFileSync } from 'node:fs';

/**
 * BUG-Q7-01: jsdom does not evaluate container queries, so this guards the CSS text.
 * A segmented strip in a filter bar must wrap (not clip or hide) when main is narrow.
 */
describe('global .seg narrow-container rule (BUG-Q7-01)', () => {
  const css: string = readFileSync('src/styles.scss', 'utf8');

  function narrowBlock(): string {
    const start = css.indexOf('@container main (max-width: 568px)');
    expect(start).toBeGreaterThan(-1);
    let depth = 0;
    for (let i = css.indexOf('{', start); i < css.length; i++) {
      if (css[i] === '{') depth++;
      if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1);
    }
    throw new Error('unterminated block');
  }

  it('wraps the strip at full width with dividers that survive wrapping', () => {
    const block = narrowBlock().replace(/\s+/g, ' ');
    expect(block).toMatch(/\.filters > \.seg \{[^}]*display: flex;[^}]*flex-wrap: wrap;[^}]*width: 100%;[^}]*gap: 1px;/);
    expect(block).toMatch(/\.filters > \.seg button \{[^}]*flex: 1 1 auto;/);
    expect(block).toMatch(/\.filters > \.seg button \+ button \{[^}]*border-left: 0;/);
    expect(block).not.toMatch(/nowrap|overflow-x/);
  });

  it('is not overridden by a page-level scroll rule (Data view switch)', () => {
    const bar: string = readFileSync('src/app/pages/data/data-filter-bar.component.ts', 'utf8');
    expect(bar).not.toMatch(/\.seg\s*\{[^}]*overflow-x/);
  });
});

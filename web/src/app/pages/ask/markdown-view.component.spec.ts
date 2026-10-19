import { TestBed } from '@angular/core/testing';

import { MarkdownViewComponent } from './markdown-view.component';

describe('MarkdownViewComponent (safe answer rendering)', () => {
  async function render(markdown: string, citationIds: number[] = [], unverified: string[] = []) {
    const fixture = TestBed.createComponent(MarkdownViewComponent);
    fixture.componentRef.setInput('markdown', markdown);
    fixture.componentRef.setInput('citationIds', citationIds);
    fixture.componentRef.setInput('unverified', unverified);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  afterEach(() => delete (window as unknown as Record<string, unknown>)['__pwned']);

  it('renders markdown as elements: bold, lists, code and tables', async () => {
    const { el } = await render('**Kestrel** led.\n\n- one\n- two\n\n`v_returns`\n\n| a | b |\n|---|---|\n| 1 | 2 |');
    expect(el.querySelector('strong')!.textContent).toBe('Kestrel');
    expect(el.querySelectorAll('li').length).toBe(2);
    expect(el.querySelector('code')!.textContent).toBe('v_returns');
    expect(el.querySelectorAll('table td').length).toBe(2);
  });

  it('replaces [n] markers with clickable chips that emit the citation id', async () => {
    const { fixture, el } = await render('Best at +2.84% [1]. Kestrel said so [2], see `arr[1]`.', [1, 2]);
    const chips = Array.from(el.querySelectorAll<HTMLButtonElement>('button.cite'));
    expect(chips.map((c) => c.textContent!.trim())).toEqual(['1', '2']);
    expect(el.querySelector('code')!.textContent).toBe('arr[1]');
    expect(el.textContent).not.toContain('[1]. Kestrel');
    // no stray whitespace around chips and punctuation
    expect(el.querySelector('[data-testid=answer-md]')!.textContent!.trim()).toBe('Best at +2.84% 1. Kestrel said so 2, see arr[1].');
    const seen: number[] = [];
    fixture.componentInstance.cite.subscribe((id) => seen.push(id));
    chips[1].click();
    expect(seen).toEqual([2]);
  });

  it('neutralizes <script> and <img onerror> in the answer', async () => {
    const hostile = [
      'Hello <script>window.__pwned = 1</script> world',
      '',
      '<img src=x onerror="window.__pwned = 2"> and <iframe src="javascript:window.__pwned=3"></iframe>',
      '',
      '[x](javascript:window.__pwned=4) **<b onclick="window.__pwned=5">bold</b>**',
    ].join('\n');
    const { el } = await render(hostile);
    expect(el.querySelector('script, img, iframe, b')).toBeNull();
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('[onerror], [onclick]')).toBeNull();
    expect(el.textContent).not.toContain('__pwned');
    expect(el.textContent).toContain('Hello');
    expect(el.querySelector('strong')!.textContent).toBe('bold');
    expect((window as unknown as Record<string, unknown>)['__pwned']).toBeUndefined();
  });

  it('shows HTML inside code spans as literal text', async () => {
    const { el } = await render('Literal `<img src=x onerror=alert(1)>` here');
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('code')!.textContent).toBe('<img src=x onerror=alert(1)>');
  });

  it('only renders http(s)/mailto links, opening in a new tab', async () => {
    const { el } = await render('[Drive](https://drive.google.com/file/d/1)');
    const a = el.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('https://drive.google.com/file/d/1');
    expect(a.getAttribute('rel')).toBe('noopener noreferrer');
    expect(a.getAttribute('target')).toBe('_blank');
  });

  it('highlights unverified numbers with a tooltip and keeps the markdown intact', async () => {
    const { el } = await render('**Kestrel** at **+2.84%** [1], 0.92 points ahead of 10.92 [1].', [1], ['0.92']);
    const marks = Array.from(el.querySelectorAll('[data-testid=unverified]'));
    expect(marks.map((m) => m.textContent)).toEqual(['0.92']);
    expect(marks[0].getAttribute('title')).toBe('Not found in any tool result');
    expect(el.querySelectorAll('strong').length).toBe(2);
    expect(el.querySelectorAll('button.cite').length).toBe(2);
    expect(el.textContent).toContain('10.92');
  });
});

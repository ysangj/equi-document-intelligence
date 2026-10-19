import { TestBed } from '@angular/core/testing';

import { CONTRACT_ASK } from '../../testing/fixtures';
import { AskStepsComponent, formatDuration, stepsSummary } from './ask-steps.component';

describe('AskStepsComponent ("How I got this")', () => {
  async function render() {
    const fixture = TestBed.createComponent(AskStepsComponent);
    fixture.componentRef.setInput('steps', CONTRACT_ASK.steps);
    fixture.componentRef.setInput('durationMs', CONTRACT_ASK.duration_ms);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('is a collapsed panel with a summary of the calls', async () => {
    const { el } = await render();
    const details = el.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(details.querySelector('summary')!.textContent).toBe('How I got this · 3 tool calls · 1 rejected by SQL guard · 6.4s');
  });

  it('open state is controlled by the caller: [open] expands it, a user toggle emits openChange', async () => {
    const { fixture, el } = await render();
    const emitted: boolean[] = [];
    fixture.componentInstance.openChange.subscribe((v) => emitted.push(v));
    fixture.componentRef.setInput('open', true);
    await fixture.whenStable();
    const details = el.querySelector('details')!;
    expect(details.open).toBe(true);
    // the toggle that follows a programmatic open emits nothing
    details.dispatchEvent(new Event('toggle'));
    expect(emitted).toEqual([]);
    details.open = false;
    details.dispatchEvent(new Event('toggle'));
    expect(emitted).toEqual([false]);
  });

  it('lists steps in order with tool, input, result summary, duration and citation ids', async () => {
    const { el } = await render();
    const steps = Array.from(el.querySelectorAll('[data-testid=step]'));
    expect(steps.map((s) => s.getAttribute('data-step'))).toEqual(['1', '2', '3']);
    expect(steps[1].querySelector('.tool')!.textContent).toBe('query_sql');
    expect(steps[1].querySelector('[data-testid=step-sql]')!.textContent).toContain('FROM v_returns WHERE period_year = 2026');
    expect(steps[1].querySelector('.summary')!.textContent).toBe('7 rows');
    expect(steps[1].querySelector('.dur')!.textContent).toBe('21 ms');
    expect(Array.from(steps[1].querySelectorAll('.cite')).map((c) => c.textContent)).toEqual(['1']);
    const search = steps[2].querySelector('[data-testid=step-search]')!;
    expect(search.querySelector('.q')!.textContent).toBe('“Kestrel January performance commentary”');
    expect(Array.from(search.querySelectorAll('.filter')).map((f) => f.textContent)).toEqual(['k=8', 'fund=Kestrel Quant Equity Fund LP']);
  });

  it('styles the guard-rejected step apart and shows the guard message', async () => {
    const { el } = await render();
    const [guard, ok] = Array.from(el.querySelectorAll('[data-testid=step]'));
    expect(guard.classList).toContain('guard');
    expect(ok.classList).not.toContain('guard');
    expect(guard.querySelector('[data-testid=guard-badge]')!.textContent).toBe('Rejected by SQL guard');
    expect(guard.querySelector('[data-testid=step-error]')!.textContent).toContain('Only these views may be queried');
    expect(guard.querySelector('[data-testid=step-sql]')!.textContent).toContain('FROM returns r JOIN funds f');
    expect(ok.querySelector('[data-testid=step-error]')).toBeNull();
  });

  it('marks execution errors that are not guard errors', async () => {
    const fixture = TestBed.createComponent(AskStepsComponent);
    fixture.componentRef.setInput('steps', [{ ...CONTRACT_ASK.steps[1], error: 'canceling statement due to statement timeout', result_summary: 'Query failed' }]);
    await fixture.whenStable();
    const step = (fixture.nativeElement as HTMLElement).querySelector('[data-testid=step]')!;
    expect(step.classList).toContain('failed');
    expect(step.classList).not.toContain('guard');
    expect(step.querySelector('[data-testid=step-error]')!.textContent).toContain('statement timeout');
  });

  it('emits the citation id from a step chip', async () => {
    const { fixture, el } = await render();
    const seen: number[] = [];
    fixture.componentInstance.cite.subscribe((id) => seen.push(id));
    el.querySelectorAll<HTMLButtonElement>('[data-testid=step] .cite')[1].click();
    expect(seen).toEqual([2]);
  });

  it('formats durations and summaries', () => {
    expect(formatDuration(142)).toBe('142 ms');
    expect(formatDuration(6400)).toBe('6.4s');
    expect(stepsSummary([CONTRACT_ASK.steps[2]], null)).toBe('1 tool call');
  });
});

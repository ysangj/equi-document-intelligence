import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ConfigService } from '../../core/config.service';
import { NotifyService } from '../../core/notify.service';
import { AskPanelService } from '../../shell/ask-panel.service';
import { CONTRACT_ASK, DEMO_CONFIG, DRIVE_CONFIG } from '../../testing/fixtures';
import { AskPanelComponent, EXAMPLE_QUESTIONS } from './ask-panel.component';
import { AskSessionService } from './ask-session.service';
import { installMemoryStorage } from '../../testing/storage';

installMemoryStorage();

describe('AskPanelComponent (FR-11)', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])] });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(ConfigService).config.set(DRIVE_CONFIG);
    vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
  });

  async function render() {
    const fixture = TestBed.createComponent(AskPanelComponent);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, input: el.querySelector<HTMLTextAreaElement>('[data-testid=ask-input]')!, submit: el.querySelector<HTMLButtonElement>('[data-testid=ask-submit]')! };
  }

  async function type(fixture: { whenStable(): Promise<unknown> }, input: HTMLTextAreaElement, text: string) {
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
  }

  it('shows the three example questions; a chip asks it', async () => {
    const { fixture, el } = await render();
    const chips = Array.from(el.querySelectorAll<HTMLButtonElement>('[data-testid=example]'));
    expect(chips.map((c) => c.textContent!.trim())).toEqual([...EXAMPLE_QUESTIONS]);
    chips[2].click();
    await fixture.whenStable();
    const req = http.expectOne('/api/ask');
    expect(req.request.body).toEqual({ question: 'Which fund had the worst March and what did the manager say about it?', history: [] });
  });

  it('shows a spinner and disables submit while in flight, then renders the answer', async () => {
    const { fixture, el, input, submit } = await render();
    await type(fixture, input, 'Which fund had the best January return?');
    expect(submit.disabled).toBe(false);
    submit.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=ask-pending] mat-spinner')).not.toBeNull();
    expect(submit.disabled).toBe(true);
    expect(el.querySelector('[data-testid=question]')!.textContent).toBe('Which fund had the best January return?');
    expect(input.value).toBe('');

    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=ask-pending]')).toBeNull();
    expect(el.querySelectorAll('[data-testid=answer-md] button.cite').length).toBe(4);
    expect(el.querySelectorAll('app-citation-card').length).toBe(2);
    expect(el.querySelector('[data-testid=sql-table]')).not.toBeNull();
    expect(el.querySelector('[data-testid=passage-link]')!.textContent).toContain('kestrel_factsheet_2026-01.pdf · p.2');
    expect(el.querySelectorAll('[data-testid=step]').length).toBe(3);
    expect(el.querySelector('[data-testid=step].guard')).not.toBeNull();
    // unverified number highlighted + notice; flagged-data notice (§17)
    expect(Array.from(el.querySelectorAll('[data-testid=unverified]')).map((u) => u.textContent)).toEqual(['0.92']);
    expect(el.querySelector('[data-testid=unverified-notice]')!.textContent).toContain('1 number could not be verified');
    expect(el.querySelector('[data-testid=flagged-notice]')!.textContent).toContain('Includes flagged data');
  });

  it('a citation chip highlights its card', async () => {
    const { fixture, el, input } = await render();
    await type(fixture, input, 'q');
    el.querySelector<HTMLFormElement>('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await fixture.whenStable();
    const chip = Array.from(el.querySelectorAll<HTMLButtonElement>('[data-testid=answer-md] button.cite')).find((c) => c.textContent!.trim() === '2')!;
    chip.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-cite-card="2"]')!.classList).toContain('active');
    expect(el.querySelector('[data-cite-card="1"]')!.classList).not.toContain('active');
    expect(chip.classList).toContain('on');
  });

  it('Enter sends, Shift+Enter does not; follow-ups carry history; a session reset clears it', async () => {
    const { fixture, el, input } = await render();
    await type(fixture, input, 'first');
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true }));
    await fixture.whenStable();
    http.expectNone('/api/ask');
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await fixture.whenStable();
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await fixture.whenStable();

    await type(fixture, input, 'follow-up');
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await fixture.whenStable();
    const second = http.expectOne('/api/ask');
    expect(second.request.body.history.length).toBe(2);
    second.flush(CONTRACT_ASK);
    await fixture.whenStable();
    expect(el.querySelectorAll('section.turn').length).toBe(2);

    // "New conversation" lives in the dock's title bar (ask-dock spec); it calls reset()
    TestBed.inject(AskSessionService).reset();
    await fixture.whenStable();
    expect(el.querySelectorAll('section.turn').length).toBe(0);
    expect(el.querySelectorAll('[data-testid=example]').length).toBe(3);
    await type(fixture, input, 'fresh');
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await fixture.whenStable();
    expect(http.expectOne('/api/ask').request.body.history).toEqual([]);
  });

  it('keeps the draft and the expanded steps in the session, so a re-created panel shows them', async () => {
    const first = await render();
    first.el.querySelector<HTMLButtonElement>('[data-testid=example]')!.click();
    await first.fixture.whenStable();
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    await first.fixture.whenStable();
    const details = first.el.querySelector<HTMLDetailsElement>('[data-testid=steps]')!;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await type(first.fixture, first.input, 'half-typed follow-up');
    first.fixture.destroy();

    const again = await render();
    expect(again.input.value).toBe('half-typed follow-up');
    expect(again.el.querySelector<HTMLDetailsElement>('[data-testid=steps]')!.open).toBe(true);
    expect(again.el.querySelectorAll('section.turn').length).toBe(1);
    http.expectNone('/api/ask');
  });

  it('is disabled with the config reason when features.ask is off', async () => {
    TestBed.inject(ConfigService).config.set(DEMO_CONFIG);
    const { el, input, submit } = await render();
    expect(el.querySelector('[data-testid=ask-disabled]')!.textContent).toContain('Ask is disabled because ANTHROPIC_API_KEY is not set.');
    expect(input.disabled).toBe(true);
    expect(submit.disabled).toBe(true);
    expect(Array.from(el.querySelectorAll<HTMLButtonElement>('[data-testid=example]')).every((b) => b.disabled)).toBe(true);
  });

  it('a 503 ask_disabled disables the input and shows the reason', async () => {
    const { fixture, el, input } = await render();
    expect(el.querySelector('[data-testid=ask-disabled]')).toBeNull();
    el.querySelector<HTMLButtonElement>('[data-testid=example]')!.click();
    await fixture.whenStable();
    http.expectOne('/api/ask').flush(
      { error: { code: 'ask_disabled', message: 'Ask is disabled because ANTHROPIC_API_KEY is not set.', details: null } },
      { status: 503, statusText: 'Service Unavailable' },
    );
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=ask-disabled]')!.textContent).toContain('ANTHROPIC_API_KEY is not set');
    expect(el.querySelector('[data-testid=ask-error]')!.textContent).toContain('ANTHROPIC_API_KEY is not set');
    expect(input.disabled).toBe(true);
  });

  it('shows C2 errors with details and a Retry; a missing endpoint degrades gracefully', async () => {
    const { fixture, el } = await render();
    el.querySelector<HTMLButtonElement>('[data-testid=example]')!.click();
    await fixture.whenStable();
    http.expectOne('/api/ask').flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
    await fixture.whenStable();
    const err = el.querySelector('[data-testid=ask-error]')!;
    expect(err.textContent).toContain('Ask is not available on the api yet');
    err.querySelector<HTMLButtonElement>('button')!.click();
    await fixture.whenStable();
    http.expectOne('/api/ask').flush(
      { error: { code: 'validation_error', message: 'Invalid request parameters.', details: [{ loc: 'body.question', message: 'question must not be blank' }] } },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=ask-error]')!.textContent).toContain('body.question: question must not be blank');
  });

  it('a SQL row with an id loads its source through the panel service (shown by the shell)', async () => {
    const { fixture, el } = await render();
    el.querySelector<HTMLButtonElement>('[data-testid=example]')!.click();
    await fixture.whenStable();
    const sql = CONTRACT_ASK.citations[0];
    const withId = { ...CONTRACT_ASK, citations: [{ ...sql, columns: [...(sql as { columns: string[] }).columns, 'return_id'], rows: (sql as { rows: unknown[][] }).rows.map((r, k) => [...r, 101 + k]) }, CONTRACT_ASK.citations[1]] };
    http.expectOne('/api/ask').flush(withId);
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid=row-source]')!.click();
    await fixture.whenStable();
    expect(http.expectOne('/api/sources/returns/101').request.method).toBe('GET');
    const sources = TestBed.inject(AskPanelService).sources;
    expect(sources.open()).toBe(true);
    expect(sources.selected()).toEqual({ table: 'returns', id: 101, field: null });
    // the source panel is not rendered inside the Ask panel
    expect(el.querySelector('app-source-panel')).toBeNull();
  });
});

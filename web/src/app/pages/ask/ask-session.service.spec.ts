import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { AskRequest } from '../../api/models';
import { CONTRACT_ASK } from '../../testing/fixtures';
import { AskSessionService, AskTurn, buildHistory } from './ask-session.service';

const done = (id: number, q: string, a: string): AskTurn => ({ id, question: q, status: 'done', response: { ...CONTRACT_ASK, answer: a }, error: null });

describe('buildHistory', () => {
  it('sends earlier answered turns as user/assistant pairs, oldest first, the answer unchanged', () => {
    const turns: AskTurn[] = [
      done(1, 'List all Alder funds', 'There are two Alder funds [1].'),
      { id: 2, question: 'broken', status: 'error', response: null, error: null },
      done(3, 'And their January returns?', '**+1.10%** and +0.48% [1].'),
    ];
    expect(buildHistory(turns)).toEqual([
      { role: 'user', content: 'List all Alder funds' },
      { role: 'assistant', content: 'There are two Alder funds [1].' },
      { role: 'user', content: 'And their January returns?' },
      { role: 'assistant', content: '**+1.10%** and +0.48% [1].' },
    ]);
  });

  it('caps history at 20 items (the most recent), starting on a user turn', () => {
    const turns = Array.from({ length: 15 }, (_, k) => done(k + 1, `q${k + 1}`, `a${k + 1}`));
    const h = buildHistory(turns);
    expect(h.length).toBe(20);
    expect(h[0]).toEqual({ role: 'user', content: 'q6' });
    expect(h.at(-1)).toEqual({ role: 'assistant', content: 'a15' });
    // an odd cap never starts on an assistant turn
    const odd = buildHistory(turns, 5);
    expect(odd.map((x) => x.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
  });

  it('clips content to the 8000-char contract limit', () => {
    const h = buildHistory([done(1, 'q', 'x'.repeat(9000))]);
    expect(h[1].content.length).toBe(8000);
  });
});

describe('AskSessionService', () => {
  let http: HttpTestingController;
  let session: AskSessionService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    session = TestBed.inject(AskSessionService);
  });

  afterEach(() => vi.useRealTimers());

  it('posts the trimmed question with the history of earlier turns', () => {
    expect(session.ask('  Which fund had the best January return?  ')).toBe(true);
    const first = http.expectOne('/api/ask');
    expect(first.request.method).toBe('POST');
    expect(first.request.body).toEqual<AskRequest>({ question: 'Which fund had the best January return?', history: [] });
    first.flush(CONTRACT_ASK);

    session.ask('What did the manager say?');
    const second = http.expectOne('/api/ask');
    expect(second.request.body).toEqual<AskRequest>({
      question: 'What did the manager say?',
      history: [
        { role: 'user', content: 'Which fund had the best January return?' },
        { role: 'assistant', content: CONTRACT_ASK.answer },
      ],
    });
  });

  it('sends one request at a time and ignores blank questions', () => {
    expect(session.ask('   ')).toBe(false);
    http.expectNone('/api/ask');
    session.ask('one');
    expect(session.pending()).toBe(true);
    expect(session.ask('two')).toBe(false);
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    expect(session.pending()).toBe(false);
    expect(session.turns().map((t) => t.status)).toEqual(['done']);
  });

  it('a 503 ask_disabled disables Ask with the server reason', () => {
    session.ask('q');
    http.expectOne('/api/ask').flush(
      { error: { code: 'ask_disabled', message: 'Ask is disabled because ANTHROPIC_API_KEY is not set.', details: null } },
      { status: 503, statusText: 'Service Unavailable' },
    );
    expect(session.serverDisabled()).toBe('Ask is disabled because ANTHROPIC_API_KEY is not set.');
    expect(session.turns()[0].error!.code).toBe('ask_disabled');
    expect(session.ask('again')).toBe(false);
  });

  it('a 404 from the api (route not built yet, C2 envelope) says Ask is not available', () => {
    session.ask('q');
    http.expectOne('/api/ask').flush({ error: { code: 'not_found', message: 'Not found.', details: null } }, { status: 404, statusText: 'Not Found' });
    expect(session.turns()[0].error!.message).toBe('Ask is not available on the api yet (POST /api/ask is not implemented).');
    expect(session.serverDisabled()).toBeNull();
  });

  it('gives up after the 60 s client timeout', () => {
    vi.useFakeTimers();
    session.ask('slow');
    const req = http.expectOne('/api/ask');
    vi.advanceTimersByTime(59_999);
    expect(session.pending()).toBe(true);
    vi.advanceTimersByTime(1);
    expect(req.cancelled).toBe(true);
    const t = session.turns()[0];
    expect(t.status).toBe('error');
    expect(t.error!.code).toBe('timeout');
  });

  it('retry re-sends a failed question; reset clears the conversation and cancels the request', () => {
    session.ask('q1');
    http.expectOne('/api/ask').flush({ error: { code: 'upstream_error', message: 'The Anthropic API failed.', details: null } }, { status: 502, statusText: 'Bad Gateway' });
    const failed = session.turns()[0];
    expect(failed.error!.message).toBe('The Anthropic API failed.');
    expect(session.retry(failed.id)).toBe(true);
    const again = http.expectOne('/api/ask');
    expect(again.request.body.question).toBe('q1');
    session.reset();
    expect(again.cancelled).toBe(true);
    expect(session.turns()).toEqual([]);
  });

  it('keeps the draft and expanded steps for the panel; reset clears both', () => {
    session.draft.set('half a question');
    session.ask('q');
    http.expectOne('/api/ask').flush(CONTRACT_ASK);
    const id = session.turns()[0].id;
    expect(session.isStepsOpen(id)).toBe(false);
    session.setStepsOpen(id, true);
    expect(session.isStepsOpen(id)).toBe(true);
    const same = session.openSteps();
    session.setStepsOpen(id, true);
    expect(session.openSteps()).toBe(same);
    session.reset();
    expect(session.draft()).toBe('');
    expect(session.isStepsOpen(id)).toBe(false);
  });
});

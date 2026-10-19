import { Injectable, computed, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';

import { ApiError, isMissingEndpoint, toApiError } from '../../api/api-error';
import { ApiService } from '../../api/api.service';
import {
  ASK_MAX_HISTORY,
  ASK_MAX_HISTORY_CONTENT,
  ASK_MAX_QUESTION_LENGTH,
  AskHistoryItem,
  AskResponse,
} from '../../api/models';

export interface AskTurn {
  id: number;
  question: string;
  status: 'pending' | 'done' | 'error';
  response: AskResponse | null;
  error: ApiError | null;
}

/**
 * The `history` for the next question (contract): earlier answered turns as
 * user/assistant pairs, oldest first, the assistant content being the earlier
 * `answer` unchanged. Failed or pending turns are left out. At most 20 items (the
 * most recent ones, so the list always starts on a user turn) and 8000 chars each.
 */
export function buildHistory(turns: readonly AskTurn[], max: number = ASK_MAX_HISTORY): AskHistoryItem[] {
  const items: AskHistoryItem[] = [];
  for (const t of turns) {
    if (t.status !== 'done' || !t.response) continue;
    items.push({ role: 'user', content: clip(t.question) });
    items.push({ role: 'assistant', content: clip(t.response.answer) });
  }
  let out = items.slice(-max);
  if (out.length && out[0].role === 'assistant') out = out.slice(1);
  return out.filter((h) => h.content.length > 0);
}

const clip = (s: string) => s.slice(0, ASK_MAX_HISTORY_CONTENT);

/**
 * The Ask conversation (FR-11). The server is stateless, so the conversation lives
 * here, client-side, for the app session. Everything the Ask panel shows that must
 * survive navigation is kept here, not in a component: the turns (answers,
 * citations), the request in flight (it keeps running while the main area changes
 * route), the unsent draft and which "How I got this" panels are expanded.
 * One request at a time; "New conversation" clears it all and cancels a request in flight.
 */
@Injectable({ providedIn: 'root' })
export class AskSessionService {
  private readonly api = inject(ApiService);

  readonly turns = signal<AskTurn[]>([]);
  readonly pending = computed(() => this.turns().some((t) => t.status === 'pending'));
  /** set from a 503 `ask_disabled` (the server's reason) */
  readonly serverDisabled = signal<string | null>(null);
  /** the composer text, so it survives the panel closing or the page changing */
  readonly draft = signal('');
  /** ids of turns whose "How I got this" panel is expanded */
  readonly openSteps = signal<ReadonlySet<number>>(new Set());

  private seq = 0;
  private inFlight: Subscription | null = null;

  /** Send a question. Returns false when nothing was sent (blank, too long, busy or disabled). */
  ask(raw: string): boolean {
    const question = raw.trim();
    if (!question || question.length > ASK_MAX_QUESTION_LENGTH || this.pending() || this.serverDisabled()) return false;
    const history = buildHistory(this.turns());
    const id = ++this.seq;
    this.turns.update((ts) => [...ts, { id, question, status: 'pending', response: null, error: null }]);
    this.inFlight = this.api.ask({ question, history }).subscribe({
      next: (response) => this.settle(id, { status: 'done', response }),
      error: (e) => {
        const error = toApiError(e);
        if (isMissingEndpoint(error)) error.message = 'Ask is not available on the api yet (POST /api/ask is not implemented).';
        if (error.code === 'ask_disabled') this.serverDisabled.set(error.message);
        this.settle(id, { status: 'error', error });
      },
    });
    return true;
  }

  /** Re-ask a failed question (it is removed and sent again at the end). */
  retry(id: number): boolean {
    const t = this.turns().find((x) => x.id === id);
    if (!t || t.status !== 'error' || this.pending()) return false;
    this.turns.update((ts) => ts.filter((x) => x.id !== id));
    return this.ask(t.question);
  }

  reset(): void {
    this.inFlight?.unsubscribe();
    this.inFlight = null;
    this.turns.set([]);
    this.draft.set('');
    this.openSteps.set(new Set());
  }

  isStepsOpen(id: number): boolean {
    return this.openSteps().has(id);
  }

  setStepsOpen(id: number, open: boolean): void {
    if (this.openSteps().has(id) === open) return;
    this.openSteps.update((s) => {
      const next = new Set(s);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  private settle(id: number, patch: Partial<AskTurn>): void {
    this.inFlight = null;
    this.turns.update((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }
}

/**
 * Dev-only simulated worker for the mock api. It holds one state per document and
 * advances on every `/api/progress` poll (`tick`): the oldest `processing` document
 * finishes, and queued `pending` documents are promoted so two are in flight, like the
 * real worker at default concurrency. Reprocess re-queues a document at the end.
 *
 * The scanned Northwind PDF fails its first run (Anthropic 429 after 3 attempts) so the
 * Ingestion page has a real failure to show; a Reprocess then succeeds.
 */
import { DocumentItem, DocumentStatus } from '../api/models';
import { MOCK_DOCUMENTS, MockDocument } from './mock-data';

export const CONCURRENCY = 2;
export const FAILING_DOCUMENT_ID = 8;
export const FAILURE_MESSAGE = 'Anthropic API 429 rate_limit_error (gave up after 3 attempts)';

export interface DocState {
  doc: MockDocument;
  status: DocumentStatus;
  attempts: number;
  last_error: string | null;
  processed_at: string | null;
  created_at: string;
  /** value rows / chunks exist (a successful extraction happened at least once) */
  has_data: boolean;
  /** number of completed runs (the failing doc fails on run 1 only) */
  runs: number;
  tokens: { input: number; output: number; latency: number } | null;
}

function tokensFor(d: MockDocument): { input: number; output: number; latency: number } {
  const base = d.mime_type === 'application/pdf' ? 5200 : 1700;
  return {
    input: base + ((d.document_id * 937) % 4100),
    output: 420 + ((d.document_id * 211) % 880),
    latency: 6000 + ((d.document_id * 1733) % 11000),
  };
}

export class MockWorker {
  readonly states = new Map<number, DocState>();
  private queue: number[] = [];
  /**
   * false until a folder is listed (Drive mode, first run): no documents exist yet.
   * The first reconcile after `POST /api/drive/watch` calls `discover()`.
   */
  discovered: boolean;

  constructor(
    initialProcessed: number,
    private readonly iso: (offsetSeconds: number) => string,
    discovered = true,
  ) {
    this.discovered = discovered;
    const n = MOCK_DOCUMENTS.length;
    MOCK_DOCUMENTS.forEach((doc, i) => {
      this.states.set(doc.document_id, {
        doc,
        status: 'pending',
        attempts: 0,
        last_error: null,
        processed_at: null,
        created_at: iso(-(n - i) * 3 - 300),
        has_data: false,
        runs: 0,
        tokens: null,
      });
      this.queue.push(doc.document_id);
    });
    const k = Math.max(0, Math.min(n, initialProcessed));
    for (let i = 0; i < k; i++) this.finish(MOCK_DOCUMENTS[i].document_id, -(k - i) * 9);
    this.queue = this.queue.slice(k);
    this.promote();
  }

  get all(): DocState[] {
    return this.discovered ? [...this.states.values()] : [];
  }

  /** The folder was listed: every document becomes visible as pending and starts processing. */
  discover(): void {
    if (this.discovered) return;
    this.discovered = true;
    this.promote();
  }

  /** Documents whose values and chunks are visible (latest extraction succeeded). */
  visibleIds(): Set<number> {
    return new Set(this.all.filter((s) => s.has_data).map((s) => s.doc.document_id));
  }

  /** done + needs_review */
  processedCount(): number {
    return this.all.filter((s) => s.status === 'done' || s.status === 'needs_review').length;
  }

  inFlight(): number {
    return this.all.filter((s) => s.status === 'pending' || s.status === 'processing').length;
  }

  tick(): void {
    if (!this.discovered) return;
    const next = this.queue.find((id) => this.states.get(id)!.status === 'processing');
    if (next !== undefined) {
      this.finish(next, 0);
      this.queue = this.queue.filter((id) => id !== next);
    }
    this.promote();
  }

  /** Reprocess: back to pending with attempts reset; the caller checks busy/unsupported. */
  requeue(id: number): void {
    const s = this.states.get(id)!;
    s.status = 'pending';
    s.attempts = 0;
    s.last_error = null;
    this.queue = [...this.queue.filter((x) => x !== id), id];
    // Not promoted here: the 202 reports `pending`, the next poll shows `processing`.
  }

  private promote(): void {
    let busy = this.queue.filter((id) => this.states.get(id)!.status === 'processing').length;
    for (const id of this.queue) {
      if (busy >= CONCURRENCY) break;
      const s = this.states.get(id)!;
      if (s.status === 'pending') {
        s.status = 'processing';
        s.attempts += 1;
        busy++;
      }
    }
  }

  private finish(id: number, offsetSeconds: number): void {
    const s = this.states.get(id)!;
    s.runs += 1;
    if (id === FAILING_DOCUMENT_ID && s.runs === 1) {
      s.status = 'failed';
      s.attempts = 3;
      s.last_error = FAILURE_MESSAGE;
      return;
    }
    s.status = s.doc.final_status;
    s.attempts = Math.max(1, s.attempts);
    s.last_error = null;
    s.processed_at = this.iso(offsetSeconds);
    s.has_data = true;
    s.tokens = tokensFor(s.doc);
  }
}

export function documentItem(
  s: DocState,
  link: string | null,
  demo: boolean,
  counts: { value_count: number; review_count: number },
): DocumentItem {
  const t = s.tokens;
  const classified = s.has_data ? s.doc.document_type : null;
  return {
    document_id: s.doc.document_id,
    document_name: s.doc.document_name,
    document_type: classified,
    document_type_confidence: classified ? 0.9 + ((s.doc.document_id * 7) % 9) / 100 : null,
    mime_type: s.doc.mime_type,
    status: s.status,
    version: 1,
    source: demo ? 'local' : 'drive',
    drive_file_id: demo ? s.doc.document_name : `1mock${s.doc.document_id}File`,
    web_view_link: link,
    search_indexed: s.has_data,
    attempts: s.attempts,
    last_error: s.last_error,
    drive_modified_at: demo ? null : s.created_at,
    processed_at: s.processed_at,
    created_at: s.created_at,
    input_tokens: t?.input ?? null,
    output_tokens: t?.output ?? null,
    total_tokens: t ? t.input + t.output : null,
    extraction_model: t ? 'claude-opus-5-5' : null,
    extraction_source: t ? (demo ? 'fixture' : 'claude') : null,
    latency_ms: t?.latency ?? null,
    value_count: s.has_data ? counts.value_count : 0,
    review_count: s.has_data ? counts.review_count : 0,
    can_reprocess: s.status !== 'pending' && s.status !== 'processing' && s.status !== 'unsupported',
  };
}

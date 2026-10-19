import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatDialog } from '@angular/material/dialog';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { Observable, defer, firstValueFrom, tap } from 'rxjs';

import { ApiError, toApiError } from '../../api/api-error';
import { ApiService } from '../../api/api.service';
import {
  DEFAULT_PAGE_SIZE,
  Page,
  REVIEW_REASONS,
  REVIEW_REASON_LABELS,
  AffectedRow,
  ReviewActionRequest,
  ReviewCounts,
  ReviewItem,
  ReviewKind,
  ReviewListResponse,
  ReviewQuery,
  ReviewReason,
} from '../../api/models';
import { toApiPage } from '../../api/query-params';
import { ConfigService } from '../../core/config.service';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../core/confirm-dialog.component';
import { EntityKind, EntityNamesService } from '../../core/entity-names.service';
import { NotifyService } from '../../core/notify.service';
import { ProgressService } from '../../core/progress.service';
import { PagedResource } from '../../shared/paged-resource';
import { SourceLoader, SourceRef } from '../../shared/source-loader';
import { SourcePanelComponent } from '../data/source-panel.component';
import { TableStateComponent } from '../data/table-state.component';
import { EntityOption, ReviewItemComponent } from './review-item.component';
import {
  CandidateOutcome,
  CanonicalSwap,
  assertAllowed,
  canonicalSwap,
  conflictOutcome,
  outcomeSummary,
  reasonClass,
  renamedEntities,
  snapshotRef,
  snapshotValue,
} from './review-format';

/** The last conflict decision, from the POST response's `affected` (shown until dismissed). */
export interface DecisionOutcome {
  itemId: string;
  title: string;
  action: 'choose' | 'edit';
  rows: CandidateOutcome[];
}

const KIND_TABS: { id: ReviewKind | null; label: string }[] = [
  { id: null, label: 'All' },
  { id: 'conflict', label: 'Conflicts' },
  { id: 'alias', label: 'Aliases' },
  { id: 'value', label: 'Values' },
];

/** The confirmation shown before "Use as main name" (`make_canonical`). */
export function makeCanonicalDialog(sw: CanonicalSwap): ConfirmDialogData {
  return {
    title: `Use “${sw.alias}” as the main name?`,
    body: [
      `“${sw.alias}” becomes the ${sw.entity}'s display name everywhere: Data, Search and Ask.`,
      `“${sw.current}” is kept as a confirmed alias, so documents that use it still link to this ${sw.entity}.`,
    ],
    confirm: 'Use as main name',
  };
}

/** Total open items from `counts` (it ignores filters). */
export function openTotal(c: ReviewCounts | null | undefined): number | null {
  if (!c) return null;
  return Object.values(c.by_kind).reduce((a, b) => a + (b ?? 0), 0);
}

/**
 * `/review` (FR-13). Lists `/api/review` with reason chips and explanations, filters
 * by kind and reason, and posts decisions to `/api/review/{id}`. After a decision the
 * item leaves the list, the header badge takes `remaining_open`, and the list reloads
 * (dependent checks may change other items). Values open the FR-9 source panel.
 */
@Component({
  selector: 'app-review-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReviewItemComponent, SourcePanelComponent, TableStateComponent, MatPaginatorModule],
  template: `
    <p class="banner">Values here are stored but flagged. Decisions are logged and persist across reprocessing.</p>
    @if (disabledReason(); as reason) {
      <p class="off">{{ reason }}</p>
    }
    <div class="layout" [class.with-panel]="sources.open()">
      <div class="card main">
        <div class="filters">
          <div class="seg" role="tablist" aria-label="Kind">
            @for (t of kindTabs; track t.label) {
              <button type="button" role="tab" [class.on]="kind() === t.id" [attr.aria-selected]="kind() === t.id" (click)="setKind(t.id)" [attr.data-testid]="'kind-' + (t.id ?? 'all')">
                {{ t.label }}<span class="n">{{ kindCount(t.id) }}</span>
              </button>
            }
          </div>
          <div class="reasons" role="group" aria-label="Filter by reason">
            @for (r of reasonOptions(); track r.code) {
              <button
                type="button"
                class="chip"
                [class.on]="reasons().includes(r.code)"
                [class.cf]="r.cls === 'cf'"
                [attr.aria-pressed]="reasons().includes(r.code)"
                (click)="toggleReason(r.code)"
                [attr.data-testid]="'reason-' + r.code"
              >
                {{ r.label }} <b>{{ r.count }}</b>
              </button>
            }
          </div>
          <span class="spacer"></span>
          @if (resource.busy()) {
            <span class="muted small" aria-live="polite">Updating…</span>
          }
        </div>

        @if (outcome(); as o) {
          <div class="outcome" role="status" data-testid="decision-outcome">
            <div class="outcome-head">
              <b>{{ o.action === 'choose' ? 'Value chosen' : 'Value edited' }}</b>
              <span class="muted"> · {{ o.title }}</span>
              <span class="spacer"></span>
              <button type="button" class="btn link small" (click)="outcome.set(null)" aria-label="Dismiss">Dismiss</button>
            </div>
            <ul>
              @for (r of o.rows; track r.snapshot.id) {
                <li [attr.data-outcome]="r.outcome" [attr.data-candidate]="r.snapshot.id">
                  @if (r.outcome === 'removed') {
                    <span class="v gone">{{ val(r) }}</span>
                  } @else {
                    <button type="button" class="link-val v" (click)="openSource(ref(r))">{{ val(r) }}</button>
                  }
                  <span class="doc">{{ r.snapshot.document_name }}</span>
                  <span class="oc" [class]="'oc ' + r.outcome" data-testid="outcome-label">{{ r.label }}</span>
                </li>
              }
            </ul>
          </div>
        }

        @if (items().length) {
          <div class="list" data-testid="review-list">
            @for (item of items(); track item.id) {
              <app-review-item
                [item]="item"
                [busy]="pending().has(item.id)"
                [error]="errors().get(item.id) ?? null"
                [selected]="sources.selected()"
                [entities]="entitiesFor(item)"
                (act)="act(item, $event)"
                (source)="openSource($event)"
              />
            }
          </div>
          @if (total() > pageSize()) {
            <mat-paginator
              [length]="total()"
              [pageIndex]="pageIndex()"
              [pageSize]="pageSize()"
              [pageSizeOptions]="[25, 50, 100]"
              (page)="onPage($event)"
              aria-label="Review pages"
            />
          }
        } @else if (!resource.loading() && !resource.error() && !filtered()) {
          <div class="state" data-testid="review-empty">
            <h3>Nothing to review</h3>
            <p>
              Every extracted value passed its checks.
              @if (processing()) {
                New items may appear while documents are processing.
              }
            </p>
          </div>
        } @else {
          <app-table-state
            noun="review items"
            [loading]="resource.loading()"
            [error]="resource.error()"
            [filtered]="filtered()"
            [processing]="processing()"
            (retry)="resource.load()"
            (clearFilters)="clearFilters()"
          />
        }
      </div>
      <app-source-panel [source]="sources.source()" [loading]="sources.loading()" [error]="sources.error()" (closed)="sources.close()" />
    </div>
  `,
  styles: `
    .banner {
      font-size: 12px;
      color: var(--muted);
      margin: 0 0 12px;
    }
    .off {
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 8px;
      padding: 8px 12px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 16px;
      align-items: start;
    }
    .main {
      min-width: 0;
      overflow: hidden;
    }
    .seg .n {
      margin-left: 6px;
      font-size: 11.5px;
      opacity: 0.7;
    }
    .reasons {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .reasons .chip {
      cursor: pointer;
      color: var(--warn);
      background: var(--warn-soft);
      border-color: transparent;
    }
    .reasons .chip.cf {
      color: var(--bad);
      background: var(--bad-soft);
    }
    .reasons .chip.on {
      border-color: currentColor;
      box-shadow: inset 0 0 0 1px currentColor;
    }
    .small {
      font-size: 12px;
    }
    .outcome {
      margin: 10px 14px 4px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--head-bg);
      font-size: 13px;
    }
    .outcome-head {
      display: flex;
      align-items: baseline;
      gap: 4px;
      flex-wrap: wrap;
    }
    .outcome ul {
      list-style: none;
      margin: 6px 0 0;
      padding: 0;
      display: grid;
      gap: 3px;
    }
    .outcome li {
      display: flex;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .outcome .v {
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      min-width: 64px;
    }
    .outcome .gone {
      color: var(--muted);
      text-decoration: line-through;
    }
    .outcome .doc {
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .link-val {
      border: 0;
      background: none;
      font: inherit;
      color: var(--accent);
      cursor: pointer;
      padding: 0;
    }
    .oc {
      font-size: 11.5px;
      font-weight: 600;
      border-radius: 5px;
      padding: 0 6px;
    }
    .oc.chosen,
    .oc.kept {
      color: var(--ok);
      background: var(--ok-soft);
    }
    .oc.removed {
      color: var(--bad);
      background: var(--bad-soft);
    }
    .oc.unchanged {
      color: var(--muted);
    }
    @container (max-width: 852px) {
      .layout {
        grid-template-columns: minmax(0, 1fr);
      }
    }
  `,
})
export class ReviewPageComponent {
  private readonly api = inject(ApiService);
  private readonly notify = inject(NotifyService);
  private readonly progress = inject(ProgressService);
  private readonly config = inject(ConfigService);
  private readonly dialog = inject(MatDialog);
  private readonly names = inject(EntityNamesService);

  readonly kindTabs = KIND_TABS;
  readonly kind = signal<ReviewKind | null>(null);
  readonly reasons = signal<ReviewReason[]>([]);
  readonly pageIndex = signal(0);
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);

  readonly sources = new SourceLoader(inject(ApiService), inject(DestroyRef), (e) => this.notify.error(e, 'Could not load the source'));

  readonly query = computed<ReviewQuery>(() => ({
    kind: this.kind() ? [this.kind()!] : [],
    reason: this.reasons(),
    page: toApiPage(this.pageIndex()),
    page_size: this.pageSize(),
  }));

  private generation = 0;
  private completed = 0;
  readonly resource = new PagedResource<ReviewItem>(
    () =>
      defer(() => {
        const gen = ++this.generation;
        return (this.api.review(this.query()) as Observable<Page<ReviewItem>>).pipe(tap(() => (this.completed = gen)));
      }),
    inject(DestroyRef),
    (e) => this.notify.error(e, 'Could not load the review queue'),
  );

  readonly counts = computed(() => (this.resource.page() as ReviewListResponse | null)?.counts ?? null);
  readonly pending = signal<ReadonlySet<string>>(new Set());
  readonly errors = signal<ReadonlyMap<string, ApiError>>(new Map());
  readonly outcome = signal<DecisionOutcome | null>(null);
  /** resolved item id -> list generation after which the server list is authoritative */
  private readonly resolved = signal<ReadonlyMap<string, number>>(new Map());

  readonly items = computed(() => {
    const gone = this.resolved();
    return (this.resource.page()?.items ?? []).filter((i) => !gone.has(i.id));
  });
  readonly total = computed(() => this.resource.page()?.total ?? 0);
  readonly filtered = computed(() => this.kind() !== null || this.reasons().length > 0);
  readonly processing = computed(() => this.progress.progress()?.active === true);
  readonly disabledReason = computed(() => {
    const f = this.config.feature('review');
    return f.enabled ? null : f.reason;
  });

  /** Reasons that have open items, plus any selected ones (so a filter can be cleared). */
  readonly reasonOptions = computed(() => {
    const by = this.counts()?.by_reason ?? {};
    const sel = this.reasons();
    return REVIEW_REASONS.filter((r) => (by[r] ?? 0) > 0 || sel.includes(r)).map((code) => ({
      code,
      label: REVIEW_REASON_LABELS[code],
      count: by[code] ?? 0,
      cls: reasonClass(code),
    }));
  });

  private readonly funds = signal<EntityOption[]>([]);
  private readonly accounts = signal<EntityOption[]>([]);

  constructor() {
    const destroyRef = inject(DestroyRef);
    effect(() => {
      this.query();
      untracked(() => this.resource.load());
    });
    // New documents can add items; decisions elsewhere can remove them.
    this.progress.processedChanged$.pipe(takeUntilDestroyed(destroyRef)).subscribe(() => {
      this.resource.load();
      this.sources.refresh();
    });
    effect(() => {
      const c = this.counts();
      untracked(() => {
        const n = openTotal(c);
        if (n !== null) this.progress.patchReviewOpen(n); // keep the header badge in sync
        const r = this.resolved();
        const keep = new Map([...r].filter(([, gen]) => this.completed <= gen));
        if (keep.size !== r.size) this.resolved.set(keep);
      });
    });
    this.loadEntities();
  }

  kindCount(k: ReviewKind | null): number | string {
    const c = this.counts();
    if (!c) return '';
    return k === null ? (openTotal(c) ?? 0) : (c.by_kind[k] ?? 0);
  }

  setKind(k: ReviewKind | null): void {
    this.kind.set(k);
    this.pageIndex.set(0);
  }

  toggleReason(r: ReviewReason): void {
    const cur = this.reasons();
    this.reasons.set(cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]);
    this.pageIndex.set(0);
  }

  clearFilters(): void {
    this.kind.set(null);
    this.reasons.set([]);
    this.pageIndex.set(0);
  }

  onPage(e: PageEvent): void {
    if (e.pageSize !== this.pageSize()) {
      this.pageSize.set(e.pageSize);
      this.pageIndex.set(0);
    } else this.pageIndex.set(e.pageIndex);
  }

  openSource(ref: SourceRef): void {
    this.sources.show(ref);
  }

  entitiesFor(item: ReviewItem): EntityOption[] {
    if (item.kind !== 'alias') return [];
    return item.alias?.entity === 'account' ? this.accounts() : this.funds();
  }

  async act(item: ReviewItem, body: ReviewActionRequest): Promise<void> {
    try {
      assertAllowed(item, body);
    } catch (e) {
      this.setError(item.id, { status: 0, code: 'invalid_action', message: (e as Error).message, details: null, envelope: false });
      return;
    }
    if (this.pending().has(item.id)) return;
    if (body.action === 'make_canonical' && !(await this.confirmCanonical(item))) return;
    if (this.pending().has(item.id)) return;
    this.pending.set(new Set([...this.pending(), item.id]));
    this.setError(item.id, null);
    this.api.reviewAction(item.id, body).subscribe({
      next: (res) => {
        this.clearPending(item.id);
        this.progress.patchReviewOpen(res.remaining_open);
        const outcome = this.conflictDecision(item, body, res.affected);
        this.outcome.set(outcome);
        const summary = outcome ? outcomeSummary(outcome.rows) : '';
        if (body.action === 'make_canonical') {
          this.afterRename(item, res.affected);
          if (res.resolved) this.resolved.set(new Map([...this.resolved(), [item.id, this.generation]]));
        } else if (res.resolved) {
          this.resolved.set(new Map([...this.resolved(), [item.id, this.generation]]));
          this.notify.info(`Resolved: ${item.title}${summary ? ` (${summary})` : ''}`);
        } else {
          this.notify.info(`Saved. ${item.title} still needs review.`);
        }
        this.resource.load();
        this.sources.refresh();
        if (body.action === 'reject_alias') this.loadEntities();
      },
      error: (e) => {
        this.clearPending(item.id);
        const err = toApiError(e);
        this.setError(item.id, err);
        this.notify.error(err, 'Review action failed');
      },
    });
  }

  private async confirmCanonical(item: ReviewItem): Promise<boolean> {
    const sw = canonicalSwap(item);
    if (!sw) return false;
    const ref = this.dialog.open(ConfirmDialogComponent, { data: makeCanonicalDialog(sw), autoFocus: 'dialog' });
    return (await firstValueFrom(ref.afterClosed())) === true;
  }

  /**
   * After `make_canonical`: toast, and every cached fund / account name is stale. The
   * review list and badge reload in `act`; the "existing fund" picker reloads here, and
   * EntityNamesService tells Data filters and Search to fetch the names again.
   */
  private afterRename(item: ReviewItem, affected: AffectedRow[] | null | undefined): void {
    const sw = canonicalSwap(item);
    if (!sw) return;
    this.notify.info(`Renamed to '${sw.alias}'`);
    const renamed = renamedEntities(affected);
    const kinds = new Set<EntityKind>(renamed.map((r): EntityKind => (r.table === 'accounts' ? 'account' : 'fund')));
    if (!kinds.size) kinds.add(sw.entity); // the item says which entity even if affected omits it
    this.loadEntities();
    for (const k of kinds) this.names.invalidate(k);
  }

  /** Per-candidate result of `choose` / `edit` on a conflict, read from `affected`. */
  private conflictDecision(item: ReviewItem, body: ReviewActionRequest, affected: AffectedRow[] | null | undefined): DecisionOutcome | null {
    if (item.kind !== 'conflict' || (body.action !== 'choose' && body.action !== 'edit')) return null;
    const chosen = body.chosen_id ?? null;
    return { itemId: item.id, title: item.title, action: body.action, rows: conflictOutcome(item, chosen, affected ?? []) };
  }

  val(r: CandidateOutcome): string {
    return snapshotValue(r.snapshot);
  }

  ref(r: CandidateOutcome): SourceRef {
    return snapshotRef(r.snapshot);
  }

  private clearPending(id: string): void {
    const s = new Set(this.pending());
    s.delete(id);
    this.pending.set(s);
  }

  private setError(id: string, e: ApiError | null): void {
    const m = new Map(this.errors());
    if (e) m.set(id, e);
    else m.delete(id);
    this.errors.set(m);
  }

  /** Funds / accounts for "Different fund → existing fund"; optional. */
  private loadEntities(): void {
    this.api.funds().subscribe({
      next: (r) => this.funds.set(r.items.map((f) => ({ id: f.fund_id, name: f.fund_name }))),
      error: () => this.funds.set([]),
    });
    this.api.accounts().subscribe({
      next: (r) => this.accounts.set(r.items.map((a) => ({ id: a.account_id, name: a.account_name }))),
      error: () => this.accounts.set([]),
    });
  }
}

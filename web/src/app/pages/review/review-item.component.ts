import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { ApiError } from '../../api/api-error';
import { ReviewActionName, ReviewActionRequest, ReviewItem, ValueSnapshot } from '../../api/models';
import { SourceRef, sameRef } from '../../shared/source-loader';
import { formatConfidence, formatPeriod, kindedSignClass } from '../../shared/format';
import { ReviewEditFormComponent } from './review-edit-form.component';
import {
  CandidateGroup,
  acceptBody,
  allowedActions,
  chooseBody,
  choosePreviewText,
  conflictTolerance,
  groupCandidates,
  sourcesLabel,
  confirmAliasBody,
  canonicalSwap,
  explainItem,
  makeCanonicalBody,
  makeCanonicalTitle,
  pctOfFraction,
  reasonClass,
  rejectAliasBody,
  rollforwardRows,
  statementPeriodFields,
  snapshotRef,
  snapshotValue,
} from './review-format';

/** An existing fund / account the alias can be moved to on "Different fund". */
export interface EntityOption {
  id: number;
  name: string;
}

const KIND_LABEL: Record<ReviewItem['kind'], string> = { value: 'value', conflict: 'conflict', alias: 'alias' };

/**
 * One review item (mockup `.rev-item`): reason chips, title, the human explanation of
 * each reason, the values involved (each opens the source panel), and exactly the
 * actions `allowed_actions` permits for its kind. Emits action bodies; the page posts
 * them and passes back `busy` / `error`.
 */
@Component({
  selector: 'app-review-item',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReviewEditFormComponent],
  template: `
    <article class="rev-item" [attr.data-id]="item().id" [attr.data-kind]="item().kind" [class.busy]="busy()">
      <div class="chips">
        @for (r of explanations(); track r.code) {
          <span class="st" [class]="'st ' + chipClass(r.code)" data-testid="reason-chip">{{ r.label }}</span>
        }
      </div>

      <div class="main">
        <div class="title">
          <b data-testid="item-title">{{ item().title }}</b>
          <span class="kind muted">{{ kindLabel() }}</span>
        </div>
        <ul class="why" data-testid="item-why">
          @for (r of explanations(); track r.code) {
            <li [attr.data-reason]="r.code">{{ r.text }}</li>
          }
        </ul>

        @switch (item().kind) {
          @case ('value') {
            @if (item().current; as c) {
              <div class="cur">
                <span class="muted small">{{ item().target_table === 'statements' ? 'Stated closing balance' : 'Extracted value' }}</span>
                <button type="button" class="val" [class]="'val ' + sign(c)" [class.sel]="isSel(c)" (click)="openSnap(c)" data-testid="current-value">
                  {{ value(c) }}
                </button>
                <span class="muted small">{{ docLine(c) }}</span>
              </div>
            }
            @if (periodFields().length) {
              <div class="cur period" data-testid="statement-period">
                <span class="muted small">Statement period</span>
                @for (p of periodFields(); track p.field; let last = $last) {
                  <button type="button" class="link-val" [class.sel]="isSel(p)" (click)="openSnap(p)" [attr.data-field]="p.field" [attr.title]="p.field === 'period_start' ? 'Period start' : 'Period end'">
                    {{ value(p) }}
                  </button>
                  @if (!last) {
                    <span class="muted">–</span>
                  }
                }
              </div>
            }
            @if (item().detail.ytd_mismatch; as y) {
              <table class="mini" data-testid="ytd-table">
                <thead>
                  <tr><th>Month</th><th class="num">Return</th></tr>
                </thead>
                <tbody>
                  @for (m of y.months; track m.period_end) {
                    <tr>
                      <td>{{ period(m.period_end) }}</td>
                      <td class="num">
                        @if (m.return_id !== null) {
                          <button type="button" class="link-val" (click)="openRef({ table: 'returns', id: m.return_id, field: null })">{{ pct(m.value) }}</button>
                        } @else {
                          {{ pct(m.value) }}
                        }
                      </td>
                    </tr>
                  }
                  <tr class="sum"><td>Compounded YTD</td><td class="num">{{ pct(y.computed) }}</td></tr>
                  <tr class="sum bad"><td>Stated YTD</td><td class="num">{{ pct(y.stated) }}</td></tr>
                </tbody>
              </table>
            }
            @if (item().detail.rollforward_mismatch; as rf) {
              <table class="mini" data-testid="rollforward-table">
                <tbody>
                  @for (row of rfRows(); track row.label) {
                    <tr [class]="row.emphasis ?? ''">
                      <td class="op">{{ row.op }}</td>
                      <td>{{ row.label }}</td>
                      <td class="num">
                        @if (fieldSnap(row.field); as f) {
                          <button type="button" class="link-val" [class.sel]="isSel(f)" (click)="openSnap(f)">{{ row.value }}</button>
                        } @else {
                          {{ row.value }}
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          }
          @case ('conflict') {
            @if (groups().length > 1) {
              <div class="groups" data-testid="candidate-groups">
                @for (g of groups(); track $index; let last = $last) {
                  <span class="grp" [class.multi]="g.members.length > 1" data-testid="candidate-group">
                    <b>{{ g.label }}</b> ({{ sources(g.members.length) }})
                  </span>
                  @if (!last) {
                    <span class="muted">vs</span>
                  }
                }
              </div>
              @if (hasAgreement()) {
                <p class="muted small hint" data-testid="choose-hint">Choosing a value keeps the sources that agree with it; only sources with a different value are removed.</p>
              }
            }
            <div class="cands" data-testid="candidates">
              @for (c of item().candidates ?? []; track c.id) {
                <div class="card cand" [attr.data-candidate]="c.id">
                  <div class="cand-top">
                    <button type="button" class="val" [class]="'val ' + sign(c)" [class.sel]="isSel(c)" (click)="openSnap(c)">{{ value(c) }}</button>
                    @if (agreeCount(c); as n) {
                      <span class="agree" data-testid="agrees" [title]="'Reported by ' + (n + 1) + ' sources'">agrees with {{ n }} other{{ n === 1 ? '' : 's' }}</span>
                    }
                  </div>
                  <div class="doc-line">
                    <span class="doc">{{ c.document_name }}</span>
                    @if (c.provenance.page !== null) {
                      <span class="muted"> · p.{{ c.provenance.page }}</span>
                    }
                  </div>
                  @if (c.provenance.source_snippet) {
                    <div class="snip">{{ c.provenance.source_snippet }}</div>
                  }
                  <div class="muted small">confidence {{ conf(c.provenance.confidence) }}</div>
                  <div class="cand-actions">
                    <button type="button" class="btn link" (click)="openSnap(c)">Source</button>
                    @if (c.web_view_link) {
                      <a [href]="c.web_view_link" target="_blank" rel="noopener noreferrer">Open in Drive ↗</a>
                    }
                    @if (canDo('choose')) {
                      <button
                        type="button"
                        class="btn"
                        [class.primary]="$first"
                        [disabled]="busy()"
                        [attr.title]="chooseTitle(c.id)"
                        (click)="emit(chooseBodyFor(c.id))"
                        data-testid="choose"
                      >
                        Use {{ value(c) }}
                      </button>
                    }
                  </div>
                </div>
              }
            </div>
          }
          @case ('alias') {
            @if (item().alias; as a) {
              <div class="alias">
                <span class="q">“{{ a.alias }}”</span>
                <span class="arrow">→</span>
                <b>{{ a.suggested_name }}</b>
                @if (a.match_score !== null) {
                  <span class="score" [title]="'Fuzzy match score ' + a.match_score">{{ a.match_score.toFixed(1) }}</span>
                }
              </div>
            }
          }
        }

        @if (editing()) {
          <app-review-edit-form [item]="item()" [busy]="busy()" (save)="emit($event)" (cancel)="editing.set(false)" />
        }

        @if (rejecting()) {
          <div class="reassign" data-testid="reassign">
            <label>
              <input type="radio" name="reassign-{{ item().id }}" [checked]="reassignTo() === null" (change)="reassignTo.set(null)" />
              New {{ entityNoun() }} named “{{ item().alias?.alias }}”
            </label>
            @if (otherEntities().length) {
              <label>
                <input type="radio" name="reassign-{{ item().id }}" [checked]="reassignTo() !== null" (change)="reassignTo.set(otherEntities()[0].id)" />
                Existing {{ entityNoun() }}
                <select [disabled]="reassignTo() === null" (change)="reassignTo.set(+$any($event.target).value)" data-testid="reassign-select">
                  @for (e of otherEntities(); track e.id) {
                    <option [value]="e.id" [selected]="e.id === reassignTo()">{{ e.name }}</option>
                  }
                </select>
              </label>
            }
            <div class="btns">
              <button type="button" class="btn primary" [disabled]="busy()" (click)="emit(rejectBodyFor())" data-testid="reject-confirm">
                Move alias
              </button>
              <button type="button" class="btn" (click)="rejecting.set(false)">Cancel</button>
            </div>
          </div>
        }

        @if (showNote()) {
          <input class="note" placeholder="Note for the review log (optional)" maxlength="2000" [value]="note()" (input)="note.set($any($event.target).value)" data-testid="note" />
        }

        @if (error(); as e) {
          <div class="err" role="alert" data-testid="item-error">
            {{ e.message }}
            @if (e.details?.length) {
              <ul>
                @for (d of e.details; track $index) {
                  <li>{{ d.loc ? d.loc + ': ' : '' }}{{ d.message }}</li>
                }
              </ul>
            }
          </div>
        }
      </div>

      <div class="actions" data-testid="actions">
        @if (canDo('accept')) {
          <button type="button" class="btn" [disabled]="busy()" (click)="emit(acceptBodyNow())" data-testid="accept" title="Keep the value as extracted">Accept</button>
        }
        @if (canDo('edit')) {
          <button type="button" class="btn" [class.on]="editing()" [disabled]="busy()" (click)="toggleEdit()" data-testid="edit">Edit</button>
        }
        @if (canDo('confirm_alias')) {
          <button type="button" class="btn primary" [disabled]="busy()" (click)="emit(confirmBodyNow())" data-testid="confirm-alias">
            Same {{ entityNoun() }}
          </button>
        }
        @if (canDo('reject_alias')) {
          <button type="button" class="btn" [class.on]="rejecting()" [disabled]="busy()" (click)="toggleReject()" data-testid="reject-alias">
            Different {{ entityNoun() }}
          </button>
        }
        @if (canDo('make_canonical') && swap(); as sw) {
          <button
            type="button"
            class="btn"
            [disabled]="busy()"
            [attr.title]="canonicalTitle()"
            [attr.aria-label]="'Use ' + sw.alias + ' as main name'"
            (click)="emit(canonicalBody())"
            data-testid="make-canonical"
          >
            Use as main name
          </button>
        }
        @if (!showNote() && actions().length) {
          <button type="button" class="btn link small" (click)="showNote.set(true)">Add note</button>
        }
        @if (busy()) {
          <span class="muted small" aria-live="polite">Saving…</span>
        }
      </div>
    </article>
  `,
  styles: `
    /* the card is its own container: it stacks by its own width (narrower when Ask is docked) */
    :host {
      display: block;
      container-type: inline-size;
    }
    .rev-item {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    :host(:last-child) .rev-item {
      border-bottom: 0;
    }
    .rev-item.busy {
      opacity: 0.7;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .title {
      display: flex;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .kind {
      font-size: 11.5px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .why {
      margin: 4px 0 0;
      padding: 0;
      list-style: none;
      color: var(--muted);
      font-size: 12.5px;
    }
    .why li + li {
      margin-top: 2px;
    }
    .small {
      font-size: 12px;
    }
    .cur {
      display: flex;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    .val {
      border: 1px solid transparent;
      background: none;
      font: inherit;
      font-weight: 700;
      font-size: 16px;
      font-variant-numeric: tabular-nums;
      padding: 0 4px;
      border-radius: 6px;
      cursor: pointer;
      color: var(--ink);
    }
    .val.pos {
      color: var(--ok);
    }
    .val.neg {
      color: var(--bad);
    }
    .val:hover,
    .link-val:hover {
      text-decoration: underline;
      text-decoration-color: #cdd8fb;
    }
    .val.sel,
    .link-val.sel {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .link-val {
      border: 1px solid transparent;
      border-radius: 4px;
      background: none;
      font: inherit;
      color: var(--accent);
      cursor: pointer;
      padding: 0 3px;
      font-variant-numeric: tabular-nums;
    }
    table.mini {
      margin-top: 8px;
      border-collapse: collapse;
      font-size: 12.5px;
      min-width: 260px;
    }
    table.mini th,
    table.mini td {
      padding: 3px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
    }
    table.mini th {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      font-weight: 600;
    }
    table.mini .num {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    table.mini .op {
      color: var(--muted);
      width: 14px;
    }
    tr.sum td,
    tr.expected td {
      font-weight: 600;
    }
    tr.bad td,
    tr.stated td,
    tr.diff td {
      color: var(--bad);
    }
    tr.stated td {
      font-weight: 600;
    }
    .cands {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }
    .cand {
      padding: 10px 12px;
    }
    .cand-top {
      display: flex;
      gap: 8px;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .groups {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: baseline;
      margin-top: 8px;
      font-size: 13px;
    }
    .grp {
      font-variant-numeric: tabular-nums;
      padding: 1px 8px;
      border-radius: 999px;
      background: var(--head-bg);
      border: 1px solid var(--line);
    }
    .grp.multi {
      background: var(--ok-soft);
      border-color: transparent;
      color: var(--ok);
    }
    .hint {
      margin: 4px 0 0;
    }
    .agree {
      font-size: 11.5px;
      font-weight: 600;
      color: var(--ok);
      background: var(--ok-soft);
      border-radius: 5px;
      padding: 0 6px;
    }
    .cand .val {
      font-size: 20px;
      padding: 0;
    }
    .doc-line {
      font-size: 12.5px;
      margin-top: 2px;
    }
    .cand-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 8px;
      font-size: 12.5px;
    }
    .alias {
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: baseline;
    }
    .alias .q {
      font-weight: 600;
    }
    .alias .arrow {
      color: var(--muted);
    }
    .score {
      font-size: 12px;
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 5px;
      padding: 0 6px;
      font-weight: 700;
    }
    .reassign {
      margin-top: 10px;
      padding: 10px 12px;
      background: var(--head-bg);
      border: 1px solid var(--line);
      border-radius: 8px;
      display: grid;
      gap: 6px;
      font-size: 13px;
    }
    .reassign select {
      font: inherit;
      font-size: 13px;
      margin-left: 6px;
      max-width: 100%;
    }
    .btns {
      display: flex;
      gap: 6px;
    }
    .note {
      margin-top: 8px;
      width: 100%;
      font: inherit;
      font-size: 12.5px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
    }
    .err {
      margin-top: 8px;
      color: var(--bad);
      background: var(--bad-soft);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 12.5px;
    }
    .err ul {
      margin: 4px 0 0;
      padding-left: 18px;
    }
    .actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
      max-width: 260px;
    }
    .btn.on {
      border-color: var(--accent);
      color: var(--accent);
    }
    @container (max-width: 760px) {
      .rev-item {
        grid-template-columns: minmax(0, 1fr);
        gap: 8px;
      }
      .actions {
        justify-content: flex-start;
        max-width: none;
      }
    }
  `,
})
export class ReviewItemComponent {
  readonly item = input.required<ReviewItem>();
  readonly busy = input(false);
  readonly error = input<ApiError | null>(null);
  readonly selected = input<SourceRef | null>(null);
  /** funds or accounts, for moving a rejected alias to an existing one */
  readonly entities = input<EntityOption[]>([]);
  readonly act = output<ReviewActionRequest>();
  readonly source = output<SourceRef>();

  readonly editing = signal(false);
  readonly rejecting = signal(false);
  readonly reassignTo = signal<number | null>(null);
  readonly showNote = signal(false);
  readonly note = signal('');

  readonly actions = computed(() => allowedActions(this.item()));
  readonly explanations = computed(() => explainItem(this.item()));
  readonly rfRows = computed(() => {
    const d = this.item().detail.rollforward_mismatch;
    return d ? rollforwardRows(d) : [];
  });
  readonly periodFields = computed(() => statementPeriodFields(this.item()));
  /** conflict candidates grouped by agreeing value (detail.conflict.tolerance) */
  readonly groups = computed<CandidateGroup[]>(() =>
    this.item().kind === 'conflict' ? groupCandidates(this.item().candidates ?? [], conflictTolerance(this.item())) : [],
  );
  readonly hasAgreement = computed(() => this.groups().some((g) => g.members.length > 1));
  readonly kindLabel = computed(() => KIND_LABEL[this.item().kind]);
  readonly entityNoun = computed(() => (this.item().alias?.entity === 'account' ? 'account' : 'fund'));
  /** alias text and the canonical name it would replace ("Use as main name") */
  readonly swap = computed(() => canonicalSwap(this.item()));
  readonly canonicalTitle = computed(() => {
    const sw = this.swap();
    return sw ? makeCanonicalTitle(sw) : null;
  });
  readonly otherEntities = computed(() => this.entities().filter((e) => e.id !== this.item().alias?.suggested_id));

  canDo(a: ReviewActionName): boolean {
    return this.actions().includes(a);
  }

  emit(body: ReviewActionRequest): void {
    this.act.emit(body);
  }

  acceptBodyNow(): ReviewActionRequest {
    return acceptBody(this.note());
  }

  confirmBodyNow(): ReviewActionRequest {
    return confirmAliasBody(this.note());
  }

  /** The page asks for confirmation before posting it. */
  canonicalBody(): ReviewActionRequest {
    return makeCanonicalBody();
  }

  chooseBodyFor(id: number): ReviewActionRequest {
    return chooseBody(id, this.note());
  }

  chooseTitle(id: number): string {
    return choosePreviewText(this.item(), id);
  }

  /** How many other candidates report the same value (0 when it stands alone). */
  agreeCount(c: ValueSnapshot): number {
    const g = this.groups().find((x) => x.members.some((m) => m.id === c.id));
    return g ? g.members.length - 1 : 0;
  }

  sources(n: number): string {
    return sourcesLabel(n);
  }

  rejectBodyFor(): ReviewActionRequest {
    return rejectAliasBody(this.reassignTo(), this.note());
  }

  toggleEdit(): void {
    this.editing.set(!this.editing());
    this.rejecting.set(false);
  }

  toggleReject(): void {
    this.rejecting.set(!this.rejecting());
    this.editing.set(false);
    this.reassignTo.set(null);
  }

  fieldSnap(field: string | null): ValueSnapshot | null {
    if (!field) return null;
    return this.item().fields?.find((f) => f.field === field) ?? (this.item().current?.field === field ? this.item().current : null);
  }

  openSnap(s: ValueSnapshot): void {
    this.source.emit(snapshotRef(s));
  }

  openRef(r: SourceRef): void {
    this.source.emit(r);
  }

  isSel(s: ValueSnapshot): boolean {
    return sameRef(this.selected(), snapshotRef(s));
  }

  value(s: ValueSnapshot): string {
    return snapshotValue(s);
  }

  sign(s: ValueSnapshot): string {
    return kindedSignClass(s);
  }

  pct(f: number): string {
    return pctOfFraction(f);
  }

  period(d: string): string {
    return formatPeriod(d);
  }

  conf(c: number | null): string {
    return formatConfidence(c);
  }

  chipClass = reasonClass;

  docLine(s: ValueSnapshot): string {
    return s.provenance.page !== null ? `${s.document_name} · p.${s.provenance.page}` : s.document_name;
  }
}

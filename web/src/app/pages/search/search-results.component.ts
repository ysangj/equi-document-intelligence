import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ChunkHit, DOCUMENT_TYPE_LABELS, DocumentType, HitAlias, SearchResponse } from '../../api/models';
import { DocStatusChipComponent } from '../../shared/doc-status-chip.component';
import { Segment, highlightTerms, parseHeadline } from '../../shared/headline';
import { HighlightComponent } from '../../shared/highlight.component';

interface Group {
  key: 'funds' | 'accounts' | 'documents' | 'chunks';
  label: string;
  count: number;
}

/** Data-page link params for a document (C4 `document_id`; the name labels the filter chip). */
export function documentLinkParams(documentId: number, documentName: string) {
  return { document_id: documentId, document_name: documentName };
}

/** "meridian_rv_tearsheet_2026-01.pdf · p.1" (no page for CSV row groups). */
export function passageSourceLabel(c: Pick<ChunkHit, 'document_name' | 'page'>): string {
  return c.page === null ? `${c.document_name} · rows` : `${c.document_name} · p.${c.page}`;
}

/**
 * A hit's `aliases` (the server already leaves out the canonical name and sorts them),
 * with the matched alias moved first. `matched_alias` is always one of `aliases` per
 * the contract; if it ever is not, it is still shown (without an "unconfirmed" badge,
 * since its status is unknown) so the reason for the match stays visible.
 */
export function orderHitAliases(aliases: readonly HitAlias[] | null | undefined, matched: string | null): HitAlias[] {
  const list = [...(aliases ?? [])];
  if (!matched) return list;
  const i = list.findIndex((a) => a.alias === matched);
  if (i < 0) return [{ alias: matched, confirmed: true }, ...list];
  return [list[i], ...list.slice(0, i), ...list.slice(i + 1)];
}

/**
 * FR-10 results grouped by type: funds (and aliases), accounts, documents, passages.
 * Passages render `headline_html` through `parseHeadline`: only `<mark>` survives,
 * everything else is plain text. Fund / account / document hits link to the Data page
 * filtered by that id; a passage links to its document (Drive when `web_view_link`
 * is set, otherwise the document's values on the Data page).
 */
@Component({
  selector: 'app-search-results',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, HighlightComponent, DocStatusChipComponent],
  template: `
    @if (response(); as r) {
      @for (g of groups(); track g.key) {
        @if (g.count) {
          <section class="group" [attr.data-testid]="'group-' + g.key">
            <h6>{{ g.label }} <span class="n">{{ g.count }}</span></h6>
            @switch (g.key) {
              @case ('funds') {
                @for (f of r.groups.funds.slice(0, max()); track f.fund_id) {
                  <a class="hit" [routerLink]="['/data']" [queryParams]="{ fund_id: f.fund_id }" (click)="picked.emit()" data-testid="fund-hit">
                    <span class="name"><app-highlight [segments]="hl(f.fund_name)" /></span>
                    @if (f.manager) {
                      <small> · {{ f.manager }}</small>
                    }
                    @if (aliasList(f.aliases, f.matched_alias); as aliases) {
                      @if (aliases.length) {
                        <div class="sub" data-testid="fund-aliases">
                          @for (al of aliases; track al.alias) {
                            <span class="alias" [class.unconfirmed]="!al.confirmed" [class.matched]="al.alias === f.matched_alias" [attr.title]="al.confirmed ? 'Confirmed alias' : 'Alias awaiting confirmation in Review'">
                              aka <app-highlight [segments]="hl(al.alias)" />@if (!al.confirmed) {<em>&nbsp;(unconfirmed)</em>}
                            </span>
                          }
                        </div>
                      }
                    }
                  </a>
                }
              }
              @case ('accounts') {
                @for (a of r.groups.accounts.slice(0, max()); track a.account_id) {
                  <a class="hit" [routerLink]="['/data']" [queryParams]="{ view: 'statements', account_id: a.account_id }" (click)="picked.emit()" data-testid="account-hit">
                    <span class="name"><app-highlight [segments]="hl(a.account_name)" /></span>
                    <small>
                      @if (a.account_number_masked) { · {{ a.account_number_masked }} }
                      @if (a.custodian) { · {{ a.custodian }} }
                    </small>
                    @if (aliasList(a.aliases, a.matched_alias); as aliases) {
                      @if (aliases.length) {
                        <div class="sub" data-testid="account-aliases">
                          @for (al of aliases; track al.alias) {
                            <span class="alias" [class.unconfirmed]="!al.confirmed" [class.matched]="al.alias === a.matched_alias" [attr.title]="al.confirmed ? 'Confirmed alias' : 'Alias awaiting confirmation in Review'">
                              aka <app-highlight [segments]="hl(al.alias)" />@if (!al.confirmed) {<em>&nbsp;(unconfirmed)</em>}
                            </span>
                          }
                        </div>
                      }
                    }
                  </a>
                }
              }
              @case ('documents') {
                @for (d of r.groups.documents.slice(0, max()); track d.document_id) {
                  <div class="hit row" data-testid="document-hit">
                    <a [routerLink]="['/data']" [queryParams]="docParams(d.document_id, d.document_name)" (click)="picked.emit()">
                      <app-highlight [segments]="hl(d.document_name)" />
                    </a>
                    <small> · {{ typeLabel(d.document_type) }}</small>
                    @if (!compact()) {
                      <app-doc-status-chip [status]="d.status" />
                    }
                    @if (d.web_view_link) {
                      <a class="ext" [href]="d.web_view_link" target="_blank" rel="noopener noreferrer">Drive ↗</a>
                    }
                  </div>
                }
              }
              @case ('chunks') {
                @for (c of r.groups.chunks.slice(0, max()); track c.chunk_id) {
                  <div class="hit passage" data-testid="chunk-hit">
                    <div class="snippet">“<app-highlight [segments]="headline(c.headline_html)" />”</div>
                    @if (c.web_view_link) {
                      <a class="src" [href]="c.web_view_link" target="_blank" rel="noopener noreferrer" data-testid="chunk-source">
                        {{ sourceLabel(c) }} ↗
                      </a>
                    } @else {
                      <a
                        class="src"
                        [routerLink]="['/data']"
                        [queryParams]="docParams(c.document_id, c.document_name)"
                        (click)="picked.emit()"
                        title="Show the values extracted from this document"
                        data-testid="chunk-source"
                        >{{ sourceLabel(c) }}</a
                      >
                    }
                  </div>
                }
              }
            }
          </section>
        }
      }
    }
  `,
  styles: `
    :host {
      display: block;
    }
    h6 {
      margin: 10px 14px 4px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 600;
    }
    h6 .n {
      font-weight: 400;
    }
    .hit {
      display: block;
      padding: 6px 14px;
      color: var(--ink);
      text-decoration: none;
    }
    a.hit:hover,
    .hit.row:hover,
    .hit.passage:hover {
      background: var(--row-hover);
      text-decoration: none;
    }
    .hit.row a {
      color: var(--ink);
    }
    .name {
      font-weight: 600;
    }
    small {
      color: var(--muted);
    }
    .sub {
      margin-top: 2px;
      font-size: 12.5px;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 4px 12px;
    }
    .alias.matched {
      color: var(--ink);
    }
    .alias em {
      font-style: normal;
      color: var(--warn);
    }
    .ext {
      margin-left: 8px;
      font-size: 12.5px;
    }
    app-doc-status-chip {
      margin-left: 8px;
    }
    .snippet {
      word-break: break-word;
    }
    .src {
      display: inline-block;
      margin-top: 2px;
      font-size: 12.5px;
    }
    :host(.compact) .hit {
      padding: 5px 14px;
    }
    :host(.compact) .snippet {
      font-size: 13px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  `,
  host: { '[class.compact]': 'compact()' },
})
export class SearchResultsComponent {
  readonly response = input<SearchResponse | null>(null);
  /** hits shown per group */
  readonly max = input(20);
  readonly compact = input(false);
  /** a result link was followed (the dropdown closes) */
  readonly picked = output<void>();

  readonly groups = computed<Group[]>(() => {
    const g = this.response()?.groups;
    if (!g) return [];
    return [
      { key: 'funds', label: 'Funds & aliases', count: g.funds.length },
      { key: 'accounts', label: 'Accounts', count: g.accounts.length },
      { key: 'documents', label: 'Documents', count: g.documents.length },
      { key: 'chunks', label: 'Passages', count: g.chunks.length },
    ];
  });

  private readonly headlineCache = new Map<string, Segment[]>();

  hl(text: string): Segment[] {
    return highlightTerms(text, this.response()?.query);
  }

  headline(html: string): Segment[] {
    let s = this.headlineCache.get(html);
    if (!s) {
      s = parseHeadline(html);
      if (this.headlineCache.size > 200) this.headlineCache.clear();
      this.headlineCache.set(html, s);
    }
    return s;
  }

  aliasList(aliases: HitAlias[], matched: string | null): HitAlias[] {
    return orderHitAliases(aliases, matched);
  }

  docParams(id: number, name: string) {
    return documentLinkParams(id, name);
  }

  sourceLabel(c: ChunkHit): string {
    return passageSourceLabel(c);
  }

  typeLabel(t: DocumentType | null): string {
    return t ? DOCUMENT_TYPE_LABELS[t] : 'unclassified';
  }
}

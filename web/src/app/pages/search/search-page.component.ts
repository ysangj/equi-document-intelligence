import { ChangeDetectionStrategy, Component, computed, effect, inject, input, untracked } from '@angular/core';
import { Router } from '@angular/router';

import { ConfigService } from '../../core/config.service';
import { SearchService, normalizeQuery } from '../../core/search.service';
import { SearchResultsComponent } from './search-results.component';

/** §12 target for search latency. */
export const SEARCH_TARGET_MS = 300;

/**
 * `/search?q=` (FR-10). The header box is the single search input; this page shows
 * the full grouped results for the query in the URL.
 * - URL -> search: `?q=` (Enter in the header, a shared link, Back) runs at once.
 * - search -> URL: as-you-type results replace `?q=` (replaceUrl), so the address bar
 *   always matches what is on screen without flooding history.
 */
@Component({
  selector: 'app-search-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SearchResultsComponent],
  template: `
    <div class="card box">
      <div class="head">
        <h2>Search</h2>
        @if (state().status === 'ok' && state().response; as r) {
          <span class="meta" data-testid="search-meta">
            {{ r.total }} result{{ r.total === 1 ? '' : 's' }} ·
            <span [class.slow]="r.took_ms > target" [attr.title]="'Server time; target is under ' + target + ' ms'"
              >{{ r.took_ms }} ms</span
            >
            @if (state().roundTripMs !== null) {
              <span class="muted"> ({{ state().roundTripMs }} ms round trip)</span>
            }
          </span>
        } @else if (state().status === 'loading') {
          <span class="meta muted" aria-live="polite">Searching…</span>
        }
      </div>

      @if (disabledReason(); as reason) {
        <p class="off">{{ reason }}</p>
      }

      @switch (state().status) {
        @case ('idle') {
          <div class="state" data-testid="search-idle">
            <h3>Search funds, accounts, documents and text</h3>
            <p>Type in the search box at the top. Results appear as you type; press Enter to open them here.</p>
            <p class="muted small">Tips: <code>"rate sensitivity"</code> for a phrase, <code>-estimate</code> to exclude a word, <code>duration or convexity</code> for either.</p>
          </div>
        }
        @case ('short') {
          <div class="state" data-testid="search-short">Type at least {{ minLength() }} characters to search.</div>
        }
        @case ('error') {
          <div class="state error" role="alert" data-testid="search-error">
            <h3>Search failed</h3>
            <p>{{ state().error?.message }}</p>
            <button type="button" class="btn" (click)="retry()">Retry</button>
          </div>
        }
        @default {
          @if (state().response; as r) {
            @if (r.total === 0 && state().status === 'ok') {
              <div class="state" data-testid="search-empty">
                <h3>No results for “{{ r.query }}”</h3>
                <p>Search matches fund and account names, aliases, file names and document text.</p>
              </div>
            } @else {
              <app-search-results [response]="r" [class.stale]="state().status === 'loading'" />
            }
          }
        }
      }
    </div>
  `,
  styles: `
    .box {
      padding: 6px 0 14px;
      max-width: 920px;
    }
    .head {
      display: flex;
      align-items: baseline;
      gap: 12px;
      flex-wrap: wrap;
      padding: 10px 14px 0;
    }
    h2 {
      margin: 0;
      font-size: 18px;
    }
    .meta {
      font-size: 12.5px;
      color: var(--muted);
    }
    .slow {
      color: var(--warn);
      font-weight: 600;
    }
    .off {
      margin: 10px 14px 0;
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 8px;
      padding: 8px 12px;
    }
    .small {
      font-size: 12.5px;
    }
    code {
      font-family: var(--mono);
      font-size: 12px;
      background: var(--head-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 0 4px;
    }
    .stale {
      opacity: 0.6;
    }
  `,
})
export class SearchPageComponent {
  private readonly search = inject(SearchService);
  private readonly router = inject(Router);
  private readonly config = inject(ConfigService);

  /** `?q=` (router input binding) */
  readonly q = input<string | undefined>(undefined);

  readonly state = this.search.state;
  readonly minLength = this.search.minLength;
  readonly target = SEARCH_TARGET_MS;
  readonly disabledReason = computed(() => {
    const f = this.config.feature('search');
    return f.enabled ? null : f.reason;
  });

  constructor() {
    // URL -> search, only for changes from outside (a link, Back, a reload): when the
    // URL already matches the box or the results, the header or the effect below caused it.
    effect(() => {
      const fromUrl = normalizeQuery(this.q());
      untracked(() => {
        if (fromUrl !== this.search.settledQuery() && fromUrl !== normalizeQuery(this.search.text())) {
          this.search.submit(fromUrl);
        }
      });
    });
    // search -> URL, only once the results belong to what is in the box. Before that
    // (a deep link's `?q=` was just submitted and has not settled yet) the stale
    // settled query must not overwrite the URL: that used to clear `?q=` on a fresh
    // load of /search?q=Northwind.
    effect(() => {
      const settled = this.search.settledQuery();
      untracked(() => {
        if (settled !== normalizeQuery(this.search.text())) return;
        if (settled === normalizeQuery(this.q())) return;
        this.router.navigate([], { queryParams: { q: settled || null }, replaceUrl: true });
      });
    });
  }

  retry(): void {
    this.search.submit(this.search.text());
  }
}

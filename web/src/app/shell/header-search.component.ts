import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';

import { SearchService, normalizeQuery } from '../core/search.service';
import { SearchResultsComponent } from '../pages/search/search-results.component';

/** hits per group in the dropdown */
export const DROPDOWN_MAX = 3;

/**
 * The single search box (FR-10, mockup header). Typing searches as you type (250 ms
 * debounce, in SearchService) and shows the top hits in a dropdown; Enter opens
 * `/search?q=`. On the Search page itself the dropdown is not shown, because the page
 * is the full result list for the same query.
 */
@Component({
  selector: 'app-header-search',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SearchResultsComponent, RouterLink],
  template: `
    <form class="search" role="search" (submit)="submit($event)" (focusout)="onFocusOut($event)">
      <input
        name="q"
        type="search"
        autocomplete="off"
        [value]="search.text()"
        (input)="onInput($event)"
        (focus)="open.set(true)"
        (keydown.escape)="close()"
        placeholder="Search funds, accounts, documents, text…"
        aria-label="Search"
        maxlength="200"
        data-testid="header-search"
      />
      @if (showDropdown()) {
        <div class="dd" data-testid="search-dropdown" (mousedown)="$event.preventDefault()">
          @switch (state().status) {
            @case ('short') {
              <div class="msg">Type at least {{ search.minLength() }} characters.</div>
            }
            @case ('error') {
              <div class="msg bad">{{ state().error?.message }}</div>
            }
            @default {
              @if (state().response; as r) {
                @if (r.total === 0 && state().status === 'ok') {
                  <div class="msg">No results for “{{ r.query }}”.</div>
                } @else {
                  <app-search-results [response]="r" [max]="max" [compact]="true" (picked)="close()" />
                  <a class="all" [routerLink]="['/search']" [queryParams]="{ q: r.query }" (click)="close()">
                    See all {{ r.total }} results · {{ r.took_ms }} ms
                  </a>
                }
              } @else {
                <div class="msg">Searching…</div>
              }
            }
          }
        </div>
      }
    </form>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      max-width: 520px;
      min-width: 0;
    }
    .search {
      position: relative;
      margin: 0;
    }
    .search::before {
      content: '\\2315';
      position: absolute;
      left: 11px;
      top: 6px;
      color: var(--muted);
      font-size: 16px;
    }
    input {
      width: 100%;
      padding: 8px 12px 8px 32px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--bg);
      font: inherit;
      color: var(--ink);
    }
    input:focus {
      outline: 2px solid var(--accent-soft);
      border-color: #cdd8fb;
      background: var(--panel);
    }
    .dd {
      position: absolute;
      top: 40px;
      left: 0;
      right: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(20, 30, 50, 0.12);
      padding: 4px 0 6px;
      max-height: min(70vh, 560px);
      overflow: auto;
      z-index: 20;
    }
    .msg {
      padding: 10px 14px;
      color: var(--muted);
    }
    .msg.bad {
      color: var(--bad);
    }
    .all {
      display: block;
      padding: 8px 14px 4px;
      border-top: 1px solid var(--line);
      margin-top: 6px;
      font-size: 12.5px;
    }
  `,
})
export class HeaderSearchComponent {
  readonly search = inject(SearchService);
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);

  readonly max = DROPDOWN_MAX;
  readonly state = this.search.state;
  readonly open = signal(false);

  private readonly url = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map((e) => e.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );
  readonly onSearchPage = computed(() => this.url().startsWith('/search'));

  readonly showDropdown = computed(
    () => this.open() && !this.onSearchPage() && normalizeQuery(this.search.text()).length > 0 && this.state().status !== 'idle',
  );

  onInput(e: Event): void {
    this.open.set(true);
    this.search.type((e.target as HTMLInputElement).value);
  }

  /** Enter: run now and open the full results, `/search?q=`. */
  submit(e: Event): void {
    e.preventDefault();
    const q = normalizeQuery(this.search.text());
    this.search.submit(q);
    this.close();
    this.router.navigate(['/search'], { queryParams: q ? { q } : {} });
  }

  close(): void {
    this.open.set(false);
  }

  onFocusOut(e: FocusEvent): void {
    const next = e.relatedTarget as Node | null;
    if (!next || !(this.host.nativeElement as HTMLElement).contains(next)) this.close();
  }
}

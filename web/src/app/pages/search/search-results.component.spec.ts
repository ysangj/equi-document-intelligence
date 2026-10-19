import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { AccountHit, FundHit, SearchResponse } from '../../api/models';
import { CONTRACT_SEARCH } from '../../testing/fixtures';
import { SearchPageComponent } from './search-page.component';
import { SearchResultsComponent, orderHitAliases } from './search-results.component';

const NORTHWIND: FundHit = {
  fund_id: 2,
  fund_name: 'Northwind Global Macro Fund',
  manager: 'Northwind Capital Management',
  matched_alias: null,
  aliases: [{ alias: 'Northwind Macro Fund Ltd', confirmed: false }],
  score: 0.71,
};

const ACME: AccountHit = {
  account_id: 1,
  account_name: 'Acme Family Office LLC',
  account_number_masked: 'XXXX-4471',
  custodian: 'Larkspur Trust Company',
  matched_alias: 'Acme FO',
  aliases: [
    { alias: 'ACME Family Office', confirmed: true },
    { alias: 'Acme FO', confirmed: true },
  ],
  score: 0.5,
};

function response(funds: FundHit[], accounts: AccountHit[] = []): SearchResponse {
  return {
    query: 'northwind acme',
    min_query_length: 2,
    groups: { funds, accounts, documents: [], chunks: [] },
    total: funds.length + accounts.length,
    took_ms: 4,
  };
}

describe('orderHitAliases', () => {
  it('keeps the server order and moves the matched alias first', () => {
    expect(orderHitAliases(ACME.aliases, 'Acme FO').map((a) => a.alias)).toEqual(['Acme FO', 'ACME Family Office']);
    expect(orderHitAliases(ACME.aliases, null).map((a) => a.alias)).toEqual(['ACME Family Office', 'Acme FO']);
  });

  it('still shows a matched alias missing from aliases, and copes with no list', () => {
    expect(orderHitAliases([], 'X')).toEqual([{ alias: 'X', confirmed: true }]);
    expect(orderHitAliases(undefined, null)).toEqual([]);
  });
});

describe('SearchResultsComponent aliases (BUG-Q2-03: from the hit, not /api/funds)', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  async function render(r: SearchResponse) {
    const fixture = TestBed.createComponent(SearchResultsComponent);
    fixture.componentRef.setInput('response', r);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }
  const squash = (el: Element | null) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? null;

  it('lists a fund hit’s aliases with the unconfirmed state', async () => {
    const el = await render(response([NORTHWIND]));
    const aliases = el.querySelector('[data-testid=fund-hit] [data-testid=fund-aliases]');
    expect(squash(aliases)).toBe('aka Northwind Macro Fund Ltd (unconfirmed)');
    expect(aliases!.querySelector('.alias')!.classList).toContain('unconfirmed');
    expect(aliases!.querySelector('.alias')!.getAttribute('title')).toContain('awaiting confirmation');
  });

  it('shows account aliases too, matched alias first', async () => {
    const el = await render(response([], [ACME]));
    const hit = el.querySelector('[data-testid=account-hit]')!;
    const items = [...hit.querySelectorAll('[data-testid=account-aliases] .alias')];
    expect(items.map(squash)).toEqual(['aka Acme FO', 'aka ACME Family Office']);
    expect(items[0].classList).toContain('matched');
    expect(items.some((i) => i.classList.contains('unconfirmed'))).toBe(false);
  });

  it('shows no alias line when a hit has none', async () => {
    const el = await render(response([{ ...NORTHWIND, aliases: [] }], [{ ...ACME, aliases: [], matched_alias: null }]));
    expect(el.querySelector('[data-testid=fund-aliases]')).toBeNull();
    expect(el.querySelector('[data-testid=account-aliases]')).toBeNull();
  });

  it('renders the contract example’s aliases', async () => {
    const el = await render(CONTRACT_SEARCH);
    expect(squash(el.querySelector('[data-testid=fund-aliases]'))).toBe('aka Meridian Rates RV Fund (unconfirmed)');
  });
});

@Component({ template: '' })
class Blank {}

describe('SearchPageComponent requests', () => {
  it('searches with /api/search only and never calls /api/funds', async () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([{ path: 'search', component: Blank }])],
    });
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(SearchPageComponent);
    fixture.componentRef.setInput('q', 'Northwind');
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 5));
    http.expectOne((r) => r.url === '/api/search' && r.params.get('q') === 'Northwind').flush(response([NORTHWIND]));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid=fund-aliases]')!.textContent).toContain('Northwind Macro Fund Ltd');
    expect(http.match((r) => r.url.startsWith('/api/funds'))).toEqual([]);
    http.verify();
  });
});

describe('SearchPageComponent deep link', () => {
  it('a fresh load of /search?q=Northwind keeps ?q= and shows the results', async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([{ path: 'search', component: SearchPageComponent }], withComponentInputBinding()),
      ],
    });
    const http = TestBed.inject(HttpTestingController);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/search?q=Northwind');
    await new Promise((r) => setTimeout(r, 5));
    expect(TestBed.inject(Router).url).toBe('/search?q=Northwind');
    http.expectOne((r) => r.url === '/api/search' && r.params.get('q') === 'Northwind').flush(response([NORTHWIND]));
    harness.detectChanges();
    await harness.fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/search?q=Northwind');
    expect(harness.routeNativeElement!.querySelector('[data-testid=fund-hit]')).not.toBeNull();
    http.verify();
  });
});

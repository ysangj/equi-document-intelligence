import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ApiService, reviewIdPath } from './api.service';

describe('ApiService', () => {
  let api: ApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('GET /api/config and /api/progress', () => {
    api.config().subscribe();
    api.progress().subscribe();
    http.expectOne('/api/config').flush({});
    http.expectOne('/api/progress').flush({});
  });

  it('builds /api/returns with filters, sort and 1-based page', () => {
    api
      .returns({
        fund_id: [1, 3],
        return_type: ['MTD'],
        net_gross: ['net'],
        date_from: '2026-01-01',
        date_to: '2026-01-31',
        document_type: [],
        status: ['ok', 'conflict'],
        page: 2,
        page_size: 50,
        sort: '-value_pct',
      })
      .subscribe();
    const req = http.expectOne((r) => r.url === '/api/returns');
    expect(req.request.method).toBe('GET');
    expect(req.request.urlWithParams).toBe(
      '/api/returns?fund_id=1&fund_id=3&return_type=MTD&net_gross=net&date_from=2026-01-01&date_to=2026-01-31&status=ok&status=conflict&page=2&page_size=50&sort=-value_pct',
    );
    req.flush({ items: [], total: 0, page: 2, page_size: 50, sort: '-value_pct' });
  });

  it('omits the sort param when null so the endpoint default applies', () => {
    api.statements({ account_id: [2], page: 1, page_size: 50, sort: null }).subscribe();
    const req = http.expectOne((r) => r.url === '/api/statements');
    expect(req.request.params.has('sort')).toBe(false);
    expect(req.request.urlWithParams).toBe('/api/statements?account_id=2&page=1&page_size=50');
    req.flush({ items: [], total: 0, page: 1, page_size: 50, sort: '-period_end,account_name' });
  });

  it('builds /api/fund-facts', () => {
    api.fundFacts({ fund_id: [4], metric: ['aum'], page: 1, page_size: 25 }).subscribe();
    http.expectOne('/api/fund-facts?fund_id=4&metric=aum&page=1&page_size=25').flush({});
  });

  it('builds /api/statements/{id}/holdings with paging', () => {
    api.holdings(12, { page: 3, page_size: 10, sort: 'line_no' }).subscribe();
    http.expectOne('/api/statements/12/holdings?page=3&page_size=10&sort=line_no').flush({});
  });

  it('GET /api/funds and /api/accounts', () => {
    api.funds().subscribe();
    api.accounts().subscribe();
    http.expectOne('/api/funds').flush({ items: [], total: 0 });
    http.expectOne('/api/accounts').flush({ items: [], total: 0 });
  });

  it('builds /api/sources/{table}/{id} with ?field= only when given', () => {
    api.source('returns', 101).subscribe();
    http.expectOne('/api/sources/returns/101').flush({});
    api.source('statements', 12, 'closing_balance').subscribe();
    http.expectOne('/api/sources/statements/12?field=closing_balance').flush({});
    api.source('statement_holdings', 41, 'weight').subscribe();
    http.expectOne('/api/sources/statement_holdings/41?field=weight').flush({});
  });

  it('POST /api/sync', () => {
    api.sync().subscribe();
    const req = http.expectOne('/api/sync');
    expect(req.request.method).toBe('POST');
    req.flush({});
  });

  it('csvUrl keeps filters + sort, drops paging and adds format=csv (C5)', () => {
    const url = api.csvUrl('returns', {
      fund_id: [1, 2],
      status: ['conflict'],
      page: 3,
      page_size: 50,
      sort: '-value_pct',
    });
    expect(url).toBe('/api/returns?fund_id=1&fund_id=2&status=conflict&sort=-value_pct&format=csv');
    expect(api.csvUrl('statements', { account_id: [], page: 1, page_size: 50, sort: null })).toBe(
      '/api/statements?format=csv',
    );
    expect(api.holdingsCsvUrl(12, { page: 2, page_size: 10 })).toBe('/api/statements/12/holdings?format=csv');
  });
});

describe('ApiService U2 endpoints', () => {
  let api: ApiService;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('documents / reprocess / reconcile', () => {
    api.documents({ status: ['failed', 'pending'], q: 'north', page: 2, page_size: 25, sort: '-processed_at' }).subscribe();
    http.expectOne('/api/documents?status=failed&status=pending&q=north&page=2&page_size=25&sort=-processed_at').flush({});
    api.reprocess(8).subscribe();
    const r = http.expectOne('/api/documents/8/reprocess');
    expect(r.request.method).toBe('POST');
    r.flush({});
    api.reconcile().subscribe();
    expect(http.expectOne('/api/sync/reconcile').request.method).toBe('POST');
  });

  it('search encodes q and limit', () => {
    api.search('"rate sensitivity" -estimate', 8).subscribe();
    const req = http.expectOne((r) => r.url === '/api/search');
    expect(req.request.params.get('q')).toBe('"rate sensitivity" -estimate');
    expect(req.request.params.get('limit')).toBe('8');
  });

  it('review list and actions keep the colon in the id', () => {
    api.review({ kind: ['value', 'alias'], reason: ['ytd_mismatch'], page: 1, page_size: 50 }).subscribe();
    http.expectOne('/api/review?kind=value&kind=alias&reason=ytd_mismatch&page=1&page_size=50').flush({});
    api.reviewAction('returns:214', { action: 'choose', chosen_id: 214 }).subscribe();
    const req = http.expectOne('/api/review/returns:214');
    expect(req.request.body).toEqual({ action: 'choose', chosen_id: 214 });
    expect(reviewIdPath('fund_aliases:9')).toBe('fund_aliases:9');
    expect(reviewIdPath('a b:1/2')).toBe('a%20b:1%2F2');
  });
});

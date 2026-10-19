import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ReturnItem, ReturnsPage } from '../../api/models';
import { CsvExportService } from '../../core/csv-export.service';
import { MOCK_RETURNS } from '../../mock/mock-data';
import { DataFilters, EMPTY_FILTERS, SourceRef } from './data-filters';
import { ReturnsTableComponent } from './returns-table.component';

function page(items: ReturnItem[], total = items.length, sort = '-period_end,fund_name'): ReturnsPage {
  return { items, total, page: 1, page_size: 50, sort };
}

describe('ReturnsTableComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  async function setup(filters: DataFilters = EMPTY_FILTERS) {
    const fixture = TestBed.createComponent(ReturnsTableComponent);
    fixture.componentRef.setInput('filters', filters);
    await fixture.whenStable();
    return fixture;
  }

  it('loads page 1 with the default sort and renders formatted rows', async () => {
    const fixture = await setup();
    const req = http.expectOne((r) => r.url === '/api/returns');
    expect(req.request.urlWithParams).toBe('/api/returns?page=1&page_size=50');
    req.flush(page(MOCK_RETURNS.slice(0, 3), 36));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const rows = el.querySelectorAll('tbody tr');
    expect(rows.length).toBe(3);
    expect(rows[0].textContent).toContain('Kestrel Quant Equity Fund LP');
    expect(rows[0].textContent).toContain('Jan 2026');
    const value = el.querySelector('[data-testid=return-value]')!;
    expect(value.textContent!.trim()).toBe('+2.84%');
    expect(value.classList).toContain('pos');
  });

  it('sends filters as repeated keys and returns to page 1 when they change', async () => {
    const fixture = await setup({ ...EMPTY_FILTERS, fundIds: [1, 2], statuses: ['conflict'] });
    http
      .expectOne('/api/returns?fund_id=1&fund_id=2&status=conflict&page=1&page_size=50')
      .flush(page(MOCK_RETURNS.slice(7, 9)));
    await fixture.whenStable();

    fixture.componentInstance.onPage({ pageIndex: 1, pageSize: 50, length: 100 });
    await fixture.whenStable();
    http.expectOne((r) => r.url === '/api/returns' && r.params.get('page') === '2').flush(page([], 100));
    await fixture.whenStable();

    fixture.componentRef.setInput('filters', { ...EMPTY_FILTERS, returnTypes: ['YTD'] });
    await fixture.whenStable();
    http.expectOne('/api/returns?return_type=YTD&page=1&page_size=50').flush(page([]));
  });

  it('maps MatSort to the contract sort param', async () => {
    const fixture = await setup();
    http.expectOne((r) => r.url === '/api/returns').flush(page(MOCK_RETURNS.slice(0, 2)));
    await fixture.whenStable();
    fixture.componentInstance.onSort({ active: 'value_pct', direction: 'desc' });
    await fixture.whenStable();
    http.expectOne('/api/returns?page=1&page_size=50&sort=-value_pct').flush(page(MOCK_RETURNS.slice(0, 2)));
    fixture.componentInstance.sortBy('period_end'); // "As of" header
    await fixture.whenStable();
    http.expectOne('/api/returns?page=1&page_size=50&sort=-period_end').flush(page([]));
  });

  it('emits a source ref when a row is clicked', async () => {
    const fixture = await setup();
    http.expectOne((r) => r.url === '/api/returns').flush(page(MOCK_RETURNS.slice(0, 1)));
    await fixture.whenStable();
    const refs: SourceRef[] = [];
    fixture.componentInstance.source.subscribe((r) => refs.push(r));
    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('tbody tr')!.click();
    expect(refs).toEqual([{ table: 'returns', id: 101, field: null }]);
  });

  it('exports CSV with the current filters and sort', async () => {
    const csv = TestBed.inject(CsvExportService);
    const assigned: string[] = [];
    csv.navigator = { assign: (u) => assigned.push(u) };
    const fixture = await setup({ ...EMPTY_FILTERS, statuses: ['needs_review'] });
    http.expectOne((r) => r.url === '/api/returns').flush(page(MOCK_RETURNS.slice(0, 1)));
    fixture.componentInstance.onSort({ active: 'fund_name', direction: 'asc' });
    await fixture.whenStable();
    http.expectOne((r) => r.url === '/api/returns').flush(page(MOCK_RETURNS.slice(0, 1)));
    fixture.componentInstance.exportCsv();
    expect(assigned).toEqual(['/api/returns?status=needs_review&sort=fund_name&format=csv']);
  });

  it('shows "No documents processed yet" with an Ingestion link when empty', async () => {
    const fixture = await setup();
    http.expectOne((r) => r.url === '/api/returns').flush(page([]));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid=table-empty]')!.textContent).toContain('No documents processed yet');
    expect(el.querySelector('a[href="/ingestion"]')).not.toBeNull();
  });

  it('shows the error envelope message with Retry', async () => {
    const fixture = await setup();
    http
      .expectOne((r) => r.url === '/api/returns')
      .flush(
        { error: { code: 'db_unavailable', message: 'The database is not reachable.', details: null } },
        { status: 503, statusText: 'Service Unavailable' },
      );
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid=table-error]')!.textContent).toContain('The database is not reachable.');
    el.querySelector<HTMLButtonElement>('[data-testid=table-error] button')!.click();
    http.expectOne((r) => r.url === '/api/returns').flush(page(MOCK_RETURNS.slice(0, 1)));
  });
});

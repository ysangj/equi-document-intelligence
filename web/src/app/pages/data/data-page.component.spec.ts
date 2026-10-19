import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { FundItem, FundsList } from '../../api/models';
import { EntityNamesService } from '../../core/entity-names.service';
import { NO_MATERIAL_ANIMATIONS } from '../../testing/dialog';
import { DataPageComponent } from './data-page.component';

const fund = (name: string): FundItem => ({
  fund_id: 2,
  fund_name: name,
  manager: 'Northwind Capital Management',
  strategy: null,
  currency: 'USD',
  aliases: [],
  has_unconfirmed_alias: false,
  return_count: 19,
  document_count: 4,
  latest_period_end: '2026-03-31',
});

describe('DataPageComponent: fund / account names', () => {
  it('reloads the filter names when Review renames a fund ("Use as main name")', async () => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS] });
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(DataPageComponent);
    await fixture.whenStable();
    const flushFunds = (name: string) => {
      const list: FundsList = { items: [fund(name)], total: 1 } as FundsList;
      http.expectOne('/api/funds').flush(list);
      http.match('/api/accounts').forEach((r) => r.flush({ items: [], total: 0 }));
    };
    flushFunds('Northwind Macro Fund Ltd');
    expect(fixture.componentInstance.funds().map((f) => f.fund_name)).toEqual(['Northwind Macro Fund Ltd']);

    TestBed.inject(EntityNamesService).invalidate('fund');
    flushFunds('Northwind Global Macro Fund');
    expect(fixture.componentInstance.funds().map((f) => f.fund_name)).toEqual(['Northwind Global Macro Fund']);
  });
});

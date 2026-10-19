import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { CONTRACT_SEARCH } from '../testing/fixtures';
import { EntityKind, EntityNamesService } from './entity-names.service';
import { SearchService } from './search.service';

describe('EntityNamesService', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
  });

  const tick = () => new Promise((r) => setTimeout(r, 0));

  it('bumps the version, emits the entity kind and re-runs the current search', async () => {
    const search = TestBed.inject(SearchService);
    const names = TestBed.inject(EntityNamesService);
    const seen: EntityKind[] = [];
    names.changed$.subscribe((k) => seen.push(k));

    search.submit('northwind');
    await tick();
    http.expectOne((r) => r.url === '/api/search').flush(CONTRACT_SEARCH);
    expect(search.state().status).toBe('ok');

    names.invalidate('fund');
    await tick();
    const again = http.expectOne((r) => r.url === '/api/search');
    expect(again.request.params.get('q')).toBe('northwind');
    expect(seen).toEqual(['fund']);
    expect(names.version()).toBe(1);
  });

  it('does not search when there are no results to refresh', async () => {
    TestBed.inject(EntityNamesService).invalidate('account');
    await tick();
    http.expectNone((r) => r.url === '/api/search');
  });
});

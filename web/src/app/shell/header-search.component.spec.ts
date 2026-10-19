import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { SearchService } from '../core/search.service';
import { CONTRACT_SEARCH } from '../testing/fixtures';
import { HeaderSearchComponent } from './header-search.component';

@Component({ template: '' })
class Blank {}

describe('HeaderSearchComponent', () => {
  let http: HttpTestingController;
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'search', component: Blank },
          { path: 'data', component: Blank },
        ]),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
  });

  async function render() {
    const fixture = TestBed.createComponent(HeaderSearchComponent);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    return { fixture, el, input: el.querySelector('[data-testid=header-search]') as HTMLInputElement };
  }

  it('Enter goes to /search?q= (trimmed) and searches without waiting for the debounce', async () => {
    await router.navigateByUrl('/data');
    const { fixture, el, input } = await render();
    input.value = '  duration ';
    input.dispatchEvent(new Event('input'));
    el.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await fixture.whenStable();
    expect(router.url).toBe('/search?q=duration');
    await new Promise((r) => setTimeout(r, 5));
    http.expectOne((r) => r.url === '/api/search' && r.params.get('q') === 'duration').flush(CONTRACT_SEARCH);
  });

  it('shows grouped top hits in a dropdown while typing (off the Search page)', async () => {
    await router.navigateByUrl('/data');
    const { fixture, el, input } = await render();
    input.dispatchEvent(new Event('focus'));
    input.value = 'meridian duration';
    input.dispatchEvent(new Event('input'));
    await new Promise((r) => setTimeout(r, 300));
    http.expectOne((r) => r.url === '/api/search').flush(CONTRACT_SEARCH);
    await fixture.whenStable();
    const dd = el.querySelector('[data-testid=search-dropdown]')!;
    expect(dd.querySelectorAll('[data-testid=chunk-hit]').length).toBe(2);
    expect(dd.querySelectorAll('mark').length).toBeGreaterThan(0);
    expect(dd.textContent).toContain('See all 5 results · 38 ms');
  });

  it('mirrors the shared query text (e.g. set from /search?q=)', async () => {
    const { fixture, input } = await render();
    TestBed.inject(SearchService).submit('rate risk');
    await fixture.whenStable();
    expect(input.value).toBe('rate risk');
    await new Promise((r) => setTimeout(r, 5));
    http.expectOne((r) => r.url === '/api/search').flush(CONTRACT_SEARCH);
  });
});

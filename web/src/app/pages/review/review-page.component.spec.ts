import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ReviewListResponse } from '../../api/models';
import { EntityKind, EntityNamesService } from '../../core/entity-names.service';
import { NotifyService } from '../../core/notify.service';
import { ProgressService } from '../../core/progress.service';
import { NO_MATERIAL_ANIMATIONS, clickDialog, dialogText, flushAsync } from '../../testing/dialog';
import {
  ACCOUNT_ALIAS_CANONICAL,
  CHOOSE_KEEPS_AGREEING,
  CONTRACT_REVIEW_LIST,
  MAKE_CANONICAL_RESPONSE,
  NORTHWIND_ALIAS_CANONICAL,
  NORTHWIND_JAN_3,
  PROGRESS,
} from '../../testing/fixtures';
import { ReviewPageComponent, makeCanonicalDialog, openTotal } from './review-page.component';

describe('ReviewPageComponent (FR-13)', () => {
  let http: HttpTestingController;
  let progress: ProgressService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS] });
    http = TestBed.inject(HttpTestingController);
    progress = TestBed.inject(ProgressService);
    progress.progress.set({ ...PROGRESS, review_open: 9 });
    vi.spyOn(TestBed.inject(NotifyService), 'info').mockImplementation(() => undefined);
    vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
  });

  const list = (over: Partial<ReviewListResponse> = {}): ReviewListResponse => ({ ...CONTRACT_REVIEW_LIST, page_size: 50, ...over });

  async function render() {
    const fixture = TestBed.createComponent(ReviewPageComponent);
    await fixture.whenStable();
    http.match((r) => r.url === '/api/funds' || r.url === '/api/accounts').forEach((r) => r.flush({ items: [], total: 0 }));
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('lists items, shows kind/reason filters with counts and syncs the badge from counts', async () => {
    const { fixture, el } = await render();
    const req = http.expectOne((r) => r.url === '/api/review');
    expect(req.request.urlWithParams).toBe('/api/review?page=1&page_size=50');
    req.flush(list());
    await fixture.whenStable();
    expect(el.querySelectorAll('app-review-item').length).toBe(3);
    expect(el.querySelector('[data-testid=kind-all]')!.textContent).toContain('6');
    expect(el.querySelector('[data-testid=kind-conflict]')!.textContent).toContain('1');
    expect(el.querySelector('[data-testid=reason-ytd_mismatch]')!.textContent).toContain('YTD mismatch');
    expect(progress.progress()!.review_open).toBe(6); // badge follows counts (6), not the stale 9
  });

  it('filters by kind and reason with repeated keys', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/review').flush(list());
    await fixture.whenStable();
    (el.querySelector('[data-testid=kind-alias]') as HTMLButtonElement).click();
    await fixture.whenStable();
    http.expectOne('/api/review?kind=alias&page=1&page_size=50').flush(list({ items: [CONTRACT_REVIEW_LIST.items[1]], total: 1 }));
    await fixture.whenStable();
    (el.querySelector('[data-testid=reason-alias_match]') as HTMLButtonElement).click();
    await fixture.whenStable();
    (el.querySelector('[data-testid=reason-conflict]') as HTMLButtonElement).click();
    await fixture.whenStable();
    const reqs = http.match((r) => r.url === '/api/review');
    expect(reqs.at(-1)!.request.urlWithParams).toBe('/api/review?kind=alias&reason=alias_match&reason=conflict&page=1&page_size=50');
  });

  it('posts the action to /api/review/{id}, removes the resolved item and updates the badge', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/review').flush(list());
    await fixture.whenStable();
    const conflict = el.querySelector('app-review-item article[data-id="returns:214"]')!;
    (conflict.querySelector('[data-testid=choose]') as HTMLButtonElement).click();
    await fixture.whenStable();
    const post = http.expectOne('/api/review/returns:214');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual({ action: 'choose', chosen_id: 214 });
    post.flush({ id: 'returns:214', action: 'choose', resolved: true, review_action_ids: [31], affected: [], remaining_open: 5 });
    await fixture.whenStable();
    expect(el.querySelector('article[data-id="returns:214"]')).toBeNull();
    expect(progress.progress()!.review_open).toBe(5);
    // the list reloads (dependent checks may change other items)
    const reload = http.expectOne((r) => r.url === '/api/review');
    reload.flush(
      list({
        items: CONTRACT_REVIEW_LIST.items.slice(1),
        total: 5,
        counts: { by_kind: { value: 3, conflict: 0, alias: 2 }, by_reason: { alias_match: 2, ytd_mismatch: 1, rollforward_mismatch: 1, low_confidence: 1 } },
      }),
    );
    await fixture.whenStable();
    expect(el.querySelectorAll('app-review-item').length).toBe(2);
    expect(progress.progress()!.review_open).toBe(5);
  });

  it('choose: a kept agreeing row shows as "kept (agrees with chosen value)", only the differing one as removed', async () => {
    const info = vi.spyOn(TestBed.inject(NotifyService), 'info');
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/review').flush(list({ items: [NORTHWIND_JAN_3, ...CONTRACT_REVIEW_LIST.items.slice(1)] }));
    await fixture.whenStable();
    (el.querySelector('article[data-id="returns:214"] [data-candidate="214"] [data-testid=choose]') as HTMLButtonElement).click();
    await fixture.whenStable();
    const post = http.expectOne('/api/review/returns:214');
    expect(post.request.body).toEqual({ action: 'choose', chosen_id: 214 });
    post.flush(CHOOSE_KEEPS_AGREEING);
    await fixture.whenStable();

    const panel = el.querySelector('[data-testid=decision-outcome]')!;
    const row = (id: number) => panel.querySelector(`li[data-candidate="${id}"]`)!;
    expect(row(214).getAttribute('data-outcome')).toBe('chosen');
    expect(row(219).getAttribute('data-outcome')).toBe('kept');
    expect(row(219).textContent).toContain('kept (agrees with chosen value)');
    expect(row(219).textContent).toContain('northwind_monthly_feb26.pdf');
    expect(row(219).textContent).not.toContain('removed');
    expect(row(231).getAttribute('data-outcome')).toBe('removed');
    expect(panel.querySelectorAll('[data-outcome=removed]').length).toBe(1);
    expect(info).toHaveBeenCalledWith(
      'Resolved: Northwind Global Macro Fund · Jan 2026 · MTD net (kept 1 agreeing source, removed 1 with a different value)',
    );
    expect(progress.progress()!.review_open).toBe(5);

    // a kept row still opens its source; the panel can be dismissed
    (row(219).querySelector('button') as HTMLButtonElement).click();
    await fixture.whenStable();
    http.expectOne('/api/sources/returns/219');
    (panel.querySelector('button[aria-label=Dismiss]') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=decision-outcome]')).toBeNull();
  });

  it('shows the C2 error on the item and keeps it in the list', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/review').flush(list());
    await fixture.whenStable();
    const ytd = el.querySelector('article[data-id="returns:188"]')!;
    (ytd.querySelector('[data-testid=accept]') as HTMLButtonElement).click();
    await fixture.whenStable();
    http
      .expectOne('/api/review/returns:188')
      .flush(
        { error: { code: 'not_found', message: 'Review item returns:188 not found or already resolved.', details: null } },
        { status: 404, statusText: 'Not Found' },
      );
    await fixture.whenStable();
    expect(el.querySelector('article[data-id="returns:188"] [data-testid=item-error]')!.textContent).toContain('already resolved');
  });

  it('a value click opens the source panel via /api/sources', async () => {
    const { fixture, el } = await render();
    http.expectOne((r) => r.url === '/api/review').flush(list());
    await fixture.whenStable();
    (el.querySelector('article[data-id="returns:188"] [data-testid=current-value]') as HTMLButtonElement).click();
    await fixture.whenStable();
    http.expectOne('/api/sources/returns/188');
  });

  describe('"Use as main name" (make_canonical, BUG-Q3-01)', () => {
    const others = CONTRACT_REVIEW_LIST.items.filter((i) => i.kind !== 'alias');

    async function renderWith(alias = NORTHWIND_ALIAS_CANONICAL) {
      const r = await render();
      http.expectOne((x) => x.url === '/api/review').flush(list({ items: [alias, ...others] }));
      await r.fixture.whenStable();
      const btn = r.el.querySelector(`article[data-id="${alias.id}"] [data-testid=make-canonical]`) as HTMLButtonElement;
      return { ...r, btn };
    }

    afterEach(() => document.querySelectorAll('.cdk-overlay-container').forEach((n) => (n.innerHTML = '')));

    it('asks for confirmation first; Cancel sends nothing', async () => {
      const { fixture, btn } = await renderWith();
      btn.click();
      await flushAsync();
      const text = dialogText()!;
      expect(text).toContain('Use “Northwind Global Macro Fund” as the main name?');
      expect(text).toContain('“Northwind Macro Fund Ltd” is kept as a confirmed alias');
      clickDialog('confirm-cancel');
      await flushAsync();
      await fixture.whenStable();
      http.expectNone((r) => r.url.startsWith('/api/review/'));
      expect(dialogText()).toBeNull();
    });

    it('on confirm posts exactly {"action":"make_canonical"}, toasts, reloads the list, badge and names', async () => {
      const info = vi.spyOn(TestBed.inject(NotifyService), 'info');
      const names = TestBed.inject(EntityNamesService);
      const changed: EntityKind[] = [];
      names.changed$.subscribe((k) => changed.push(k));
      const { fixture, el, btn } = await renderWith();
      btn.click();
      await flushAsync();
      clickDialog('confirm-ok');
      await flushAsync();
      const post = http.expectOne('/api/review/fund_aliases:10');
      expect(post.request.method).toBe('POST');
      expect(JSON.stringify(post.request.body)).toBe('{"action":"make_canonical"}');
      post.flush(MAKE_CANONICAL_RESPONSE);
      await fixture.whenStable();

      expect(info).toHaveBeenCalledWith("Renamed to 'Northwind Global Macro Fund'");
      expect(progress.progress()!.review_open).toBe(5);
      expect(el.querySelector('article[data-id="fund_aliases:10"]')).toBeNull();
      // review list reloads; funds/accounts (the "existing fund" picker) reload; Data/Search are told
      http.expectOne((r) => r.url === '/api/review').flush(list({ items: others, total: 2 }));
      expect(http.match((r) => r.url === '/api/funds').length).toBe(1);
      expect(changed).toEqual(['fund']);
      expect(names.version()).toBe(1);
      expect(el.querySelector('[data-testid=decision-outcome]')).toBeNull();
    });

    it('account alias: invalidates account names', async () => {
      const info = vi.spyOn(TestBed.inject(NotifyService), 'info');
      const changed: EntityKind[] = [];
      TestBed.inject(EntityNamesService).changed$.subscribe((k) => changed.push(k));
      const { fixture, btn } = await renderWith(ACCOUNT_ALIAS_CANONICAL);
      btn.click();
      await flushAsync();
      expect(dialogText()).toContain("becomes the account's display name everywhere");
      clickDialog('confirm-ok');
      await flushAsync();
      const post = http.expectOne('/api/review/account_aliases:3');
      expect(post.request.body).toEqual({ action: 'make_canonical' });
      post.flush({ ...MAKE_CANONICAL_RESPONSE, id: 'account_aliases:3', affected: [{ table: 'accounts', id: 1, change: 'renamed', status: null }] });
      await fixture.whenStable();
      expect(info).toHaveBeenCalledWith("Renamed to 'Acme Family Office'");
      expect(changed).toEqual(['account']);
      expect(http.match((r) => r.url === '/api/accounts').length).toBe(1);
    });

    it('a failed rename shows the C2 error on the item and changes no names', async () => {
      const changed: EntityKind[] = [];
      TestBed.inject(EntityNamesService).changed$.subscribe((k) => changed.push(k));
      const { fixture, el, btn } = await renderWith();
      btn.click();
      await flushAsync();
      clickDialog('confirm-ok');
      await flushAsync();
      http
        .expectOne('/api/review/fund_aliases:10')
        .flush(
          { error: { code: 'invalid_action', message: "Action 'make_canonical' is not allowed for this item.", details: null } },
          { status: 422, statusText: 'Unprocessable Entity' },
        );
      await fixture.whenStable();
      expect(el.querySelector('article[data-id="fund_aliases:10"] [data-testid=item-error]')!.textContent).toContain('make_canonical');
      expect(changed).toEqual([]);
    });

    it('makeCanonicalDialog wording', () => {
      const d = makeCanonicalDialog({ entity: 'fund', alias: 'A', current: 'B' });
      expect(d.confirm).toBe('Use as main name');
      expect(d.body.join(' ')).toContain("“A” becomes the fund's display name everywhere");
    });
  });

  it('openTotal sums by_kind', () => {
    expect(openTotal(CONTRACT_REVIEW_LIST.counts)).toBe(6);
    expect(openTotal(null)).toBeNull();
  });
});

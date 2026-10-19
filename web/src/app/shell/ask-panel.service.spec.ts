import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { AskSessionService } from '../pages/ask/ask-session.service';
import { fakeViewport } from '../testing/viewport';
import { routes } from '../app.routes';
import {
  ASK_PANEL_DEFAULT_WIDTH,
  ASK_PANEL_MIN_WIDTH,
  ASK_PANEL_STORAGE_KEY,
  AskPanelService,
  MAIN_MIN_WIDTH,
  clampWidth,
  maxPanelWidth,
  readPrefs,
  sectionOf,
} from './ask-panel.service';
import { installMemoryStorage } from '../testing/storage';
import { ViewportService } from '../core/viewport.service';

installMemoryStorage();

@Component({ template: 'page' })
class StubPage {}

const stubRoutes = [
  { path: '', pathMatch: 'full' as const, redirectTo: 'data' },
  { path: 'data', component: StubPage },
  { path: 'ingestion', component: StubPage },
  { path: 'review', component: StubPage },
  { path: 'search', component: StubPage },
  // the real /ask entry (a redirect function), not a copy
  routes.find((r) => r.path === 'ask')!,
];

const stored = () => JSON.parse(localStorage.getItem(ASK_PANEL_STORAGE_KEY) ?? 'null');

describe('Ask panel helpers', () => {
  it('the max depends on the window: everything but the main area minimum (360 px), no fixed cap', () => {
    expect(MAIN_MIN_WIDTH).toBe(360);
    expect(maxPanelWidth(1440)).toBe(1080);
    expect(maxPanelWidth(1100)).toBe(740);
    expect(maxPanelWidth(1024)).toBe(664);
    expect(maxPanelWidth(2560)).toBe(2200);
    // never below the panel's own min, even on a window too small to dock comfortably
    expect(maxPanelWidth(500)).toBe(ASK_PANEL_MIN_WIDTH);
  });

  it('clampWidth keeps the panel between its min and the window-based max', () => {
    expect(clampWidth(100, 1600)).toBe(ASK_PANEL_MIN_WIDTH);
    expect(clampWidth(5000, 1600)).toBe(1600 - MAIN_MIN_WIDTH);
    // well past the old 760 px cap
    expect(clampWidth(1000, 1440)).toBe(1000);
    expect(clampWidth(1000, 1100)).toBe(1100 - MAIN_MIN_WIDTH);
    expect(clampWidth(500, 1100)).toBe(500);
    expect(clampWidth(500, 600)).toBe(ASK_PANEL_MIN_WIDTH);
    expect(clampWidth(Number.NaN, 1440)).toBe(ASK_PANEL_DEFAULT_WIDTH);
    expect(clampWidth(Number.POSITIVE_INFINITY, 1440)).toBe(1080);
    expect(clampWidth(612.6, 1440)).toBe(613);
    // whatever the window, the main area keeps its minimum
    for (const vw of [1024, 1100, 1280, 1440, 1920]) {
      for (const w of [0, 320, 440, 760, 1080, 5000]) expect(vw - clampWidth(w, vw)).toBeGreaterThanOrEqual(MAIN_MIN_WIDTH);
    }
  });

  it('readPrefs falls back to the defaults on missing, malformed or throwing storage', () => {
    localStorage.clear();
    expect(readPrefs()).toEqual({ open: false, pinned: true, width: ASK_PANEL_DEFAULT_WIDTH });
    localStorage.setItem(ASK_PANEL_STORAGE_KEY, '{not json');
    expect(readPrefs().pinned).toBe(true);
    localStorage.setItem(ASK_PANEL_STORAGE_KEY, JSON.stringify({ open: 'yes', pinned: false, width: 'wide' }));
    expect(readPrefs()).toEqual({ open: false, pinned: false, width: ASK_PANEL_DEFAULT_WIDTH });
    const spy = vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readPrefs().open).toBe(false);
    spy.mockRestore();
  });

  it('sectionOf ignores query strings and nested paths', () => {
    expect(sectionOf('/data?view=facts&document_id=3')).toBe('data');
    expect(sectionOf('/connect/folder')).toBe('connect');
    expect(sectionOf('/')).toBe('');
  });
});

describe('AskPanelService', () => {
  let viewport: ReturnType<typeof fakeViewport>;

  function setup(opts: { wide?: boolean; prefs?: object } = {}) {
    localStorage.clear();
    if (opts.prefs) localStorage.setItem(ASK_PANEL_STORAGE_KEY, JSON.stringify(opts.prefs));
    viewport = fakeViewport(opts.wide ?? true);
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter(stubRoutes), viewport.provider] });
    return { panel: TestBed.inject(AskPanelService), router: TestBed.inject(Router) };
  }

  it('starts closed and pinned by default; toggle opens and closes and is remembered', () => {
    const { panel } = setup();
    expect(panel.open()).toBe(false);
    expect(panel.pinned()).toBe(true);
    expect(panel.mode()).toBe('docked');
    expect(panel.width()).toBe(ASK_PANEL_DEFAULT_WIDTH);
    panel.toggle();
    expect(panel.open()).toBe(true);
    expect(stored()).toEqual({ open: true, pinned: true, width: ASK_PANEL_DEFAULT_WIDTH });
    panel.toggle();
    expect(panel.open()).toBe(false);
    expect(stored().open).toBe(false);
  });

  it('restores open, pinned and width from localStorage (a reload)', () => {
    const { panel } = setup({ prefs: { open: true, pinned: false, width: 380 } });
    expect(panel.open()).toBe(true);
    expect(panel.pinned()).toBe(false);
    expect(panel.width()).toBe(380);
  });

  it('pin and width changes are persisted; a live drag is only stored on commit', () => {
    const { panel } = setup();
    panel.setPinned(false);
    expect(stored().pinned).toBe(false);
    panel.previewWidth(500);
    expect(panel.width()).toBe(500);
    expect(stored().width).toBe(ASK_PANEL_DEFAULT_WIDTH);
    panel.commitWidth();
    expect(stored().width).toBe(500);
    panel.commitWidth(99999);
    expect(panel.width()).toBe(panel.maxWidth());
    panel.resetWidth();
    expect(stored().width).toBe(ASK_PANEL_DEFAULT_WIDTH);
  });

  it('resize: a wide width is re-clamped as the window narrows and given back when it widens; storage keeps the choice', () => {
    const { panel } = setup({ prefs: { open: true } });
    const vp = TestBed.inject(ViewportService);
    vp.width.set(1440);
    panel.commitWidth(99999);
    expect(panel.width()).toBe(1080);
    expect(panel.maxWidth()).toBe(1080);
    expect(stored().width).toBe(1080);
    vp.width.set(1100);
    expect(panel.width()).toBe(740);
    expect(panel.maxWidth()).toBe(740);
    expect(1100 - panel.width()!).toBe(MAIN_MIN_WIDTH);
    expect(stored().width).toBe(1080);
    vp.width.set(1440);
    expect(panel.width()).toBe(1080);
    // a drag on the narrower window stores what it shows
    vp.width.set(1100);
    panel.previewWidth(5000);
    panel.commitWidth();
    expect(stored().width).toBe(740);
  });

  it('reload on a narrower window: a stored wide width is clamped, not applied', () => {
    localStorage.clear();
    localStorage.setItem(ASK_PANEL_STORAGE_KEY, JSON.stringify({ open: true, pinned: true, width: 1080 }));
    viewport = fakeViewport(true);
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter(stubRoutes), viewport.provider] });
    TestBed.inject(ViewportService).width.set(1100);
    const panel = TestBed.inject(AskPanelService);
    expect(panel.width()).toBe(740);
    expect(panel.open()).toBe(true);
  });

  it('pinned: stays open while the main area moves between Data, Ingestion, Review and Search', async () => {
    const { panel, router } = setup();
    await router.navigateByUrl('/data');
    panel.show();
    for (const url of ['/ingestion', '/review', '/search?q=duration', '/data?view=statements']) {
      await router.navigateByUrl(url);
      expect(panel.open()).toBe(true);
    }
  });

  it('unpinned: collapses when the main area moves to another page, not on query changes', async () => {
    const { panel, router } = setup({ prefs: { pinned: false } });
    await router.navigateByUrl('/data');
    panel.show();
    await router.navigateByUrl('/data?view=facts');
    expect(panel.open()).toBe(true);
    await router.navigateByUrl('/review');
    expect(panel.open()).toBe(false);
    expect(stored().open).toBe(false);
  });

  it('restored open + pinned on reload, then unpinned: leaving the page collapses it', async () => {
    const { panel, router } = setup({ prefs: { open: true, pinned: true } });
    await router.navigateByUrl('/data');
    expect(panel.open()).toBe(true);
    panel.setPinned(false);
    await router.navigateByUrl('/data?view=facts');
    expect(panel.open()).toBe(true);
    await router.navigateByUrl('/ingestion');
    expect(panel.open()).toBe(false);
  });

  it('unpinned: a link followed from inside the panel does not collapse it', async () => {
    const { panel, router } = setup({ prefs: { pinned: false } });
    await router.navigateByUrl('/review');
    panel.show();
    panel.markPanelNavigation();
    await router.navigateByUrl('/data?document_id=9');
    expect(panel.open()).toBe(true);
    // ...and the page it went to is now "home": the next outside navigation collapses it
    await router.navigateByUrl('/ingestion');
    expect(panel.open()).toBe(false);
  });

  it('closing the panel also closes its source sheet', async () => {
    const { panel } = setup();
    panel.show();
    panel.sources.selected.set({ table: 'returns', id: 1, field: null });
    panel.close();
    expect(panel.sources.open()).toBe(false);
  });

  it('/ask deep link opens the panel over Data on a fresh load, with ?q= as the draft', async () => {
    const { panel, router } = setup({ prefs: { pinned: false } });
    await router.navigateByUrl('/ask?q=Which%20fund%20had%20the%20best%20January%20return%3F');
    expect(router.url).toBe('/data');
    expect(panel.open()).toBe(true);
    expect(TestBed.inject(AskSessionService).draft()).toBe('Which fund had the best January return?');
  });

  it('/ask later in the session keeps the last page in the main area', async () => {
    const { panel, router } = setup();
    await router.navigateByUrl('/review');
    await router.navigateByUrl('/ask');
    expect(router.url).toBe('/review');
    expect(panel.open()).toBe(true);
  });

  it('narrow window: an overlay drawer that starts closed, ignores pin and closes on navigation', async () => {
    const { panel, router } = setup({ wide: false, prefs: { open: true, pinned: true } });
    expect(panel.mode()).toBe('overlay');
    expect(panel.open()).toBe(false);
    expect(panel.pinned()).toBe(false);
    expect(panel.pinPreference()).toBe(true);
    expect(panel.width()).toBeNull();
    await router.navigateByUrl('/data');
    panel.show();
    // the drawer's open state is not remembered
    expect(stored().open).toBe(true);
    panel.markPanelNavigation();
    await router.navigateByUrl('/data?document_id=9');
    expect(panel.open()).toBe(false);
  });

  it('switching wide → narrow closes the docked panel into a drawer; back to wide restores it', async () => {
    const { panel } = setup();
    panel.show();
    viewport.set(false);
    TestBed.tick();
    expect(panel.mode()).toBe('overlay');
    expect(panel.open()).toBe(false);
    expect(stored().open).toBe(true);
    viewport.set(true);
    TestBed.tick();
    expect(panel.mode()).toBe('docked');
    expect(panel.open()).toBe(true);
    expect(panel.pinned()).toBe(true);
  });
});

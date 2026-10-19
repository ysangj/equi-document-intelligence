import { inject } from '@angular/core';
import { Router, Routes } from '@angular/router';

import { connectionGuard } from './core/connection.guard';
import { AskPanelService } from './shell/ask-panel.service';

/**
 * Tabs from ui-mockup.html plus Search and the OAuth redirect targets. The callback
 * redirects to `/connect/folder` on success and `/connect?error=<code>` on failure
 * (API Contract, T3). `connectionGuard` sends a first run in Drive mode to `/connect`
 * or `/connect/folder` (§4 steps 3-4), at most once per session.
 *
 * Ask is not a page: it is the docked panel beside the main area (shell/ask-dock).
 * `/ask` stays as a deep link that opens the panel (`?q=` prefills the question) and
 * shows the last page of this session in the main area, or Data on a fresh load.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'data' },
  {
    path: 'data',
    title: 'Data · FundDoc Intelligence',
    canActivate: [connectionGuard],
    loadComponent: () => import('./pages/data/data-page.component').then((m) => m.DataPageComponent),
  },
  {
    path: 'ask',
    redirectTo: ({ queryParams }) => {
      const q = queryParams['q'];
      return inject(Router).parseUrl(inject(AskPanelService).openFromDeepLink(typeof q === 'string' ? q : null));
    },
  },
  {
    path: 'ingestion',
    title: 'Ingestion · FundDoc Intelligence',
    canActivate: [connectionGuard],
    loadComponent: () => import('./pages/ingestion/ingestion-page.component').then((m) => m.IngestionPageComponent),
  },
  {
    path: 'review',
    title: 'Review · FundDoc Intelligence',
    canActivate: [connectionGuard],
    loadComponent: () => import('./pages/review/review-page.component').then((m) => m.ReviewPageComponent),
  },
  {
    path: 'search',
    title: 'Search · FundDoc Intelligence',
    canActivate: [connectionGuard],
    loadComponent: () => import('./pages/search/search-page.component').then((m) => m.SearchPageComponent),
  },
  {
    path: 'connect',
    title: 'Connect · FundDoc Intelligence',
    loadComponent: () => import('./pages/connect/connect-page.component').then((m) => m.ConnectPageComponent),
  },
  {
    path: 'connect/folder',
    title: 'Choose folder · FundDoc Intelligence',
    loadComponent: () => import('./pages/connect/folder-picker-page.component').then((m) => m.FolderPickerPageComponent),
  },
  { path: '**', redirectTo: 'data' },
];

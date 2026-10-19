import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, timeout } from 'rxjs';

import {
  AccountsList,
  ASK_TIMEOUT_MS,
  AskRequest,
  AskResponse,
  AuthStatusResponse,
  DisconnectResponse,
  DriveFoldersResponse,
  ConfigResponse,
  DocumentsPage,
  DocumentsQuery,
  FundFactsPage,
  FundFactsQuery,
  FundsList,
  HealthResponse,
  HoldingsPage,
  HoldingsQuery,
  ProgressResponse,
  ReprocessResponse,
  ReturnsPage,
  ReturnsQuery,
  ReviewActionRequest,
  ReviewActionResponse,
  ReviewListResponse,
  ReviewQuery,
  SearchResponse,
  SourceResponse,
  SourceTable,
  StatementsPage,
  StatementsQuery,
  SyncRequestResponse,
  WatchResponse,
} from './models';
import { QueryObject, buildParams, buildUrl } from './query-params';

/** Paginated list endpoints that support `format=csv` (C5). */
export type CsvEndpoint = 'returns' | 'fund-facts' | 'statements' | 'documents';

export const API_BASE = '/api';

/**
 * Typed client for the api. All URLs are relative (`/api/...`) and go through the
 * dev proxy (§5). Components never build URLs themselves.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>(`${API_BASE}/health`);
  }

  config(): Observable<ConfigResponse> {
    return this.http.get<ConfigResponse>(`${API_BASE}/config`);
  }

  authStatus(): Observable<AuthStatusResponse> {
    return this.http.get<AuthStatusResponse>(`${API_BASE}/auth/status`);
  }

  /** FR-2: the full-page navigation target for "Connect Google Drive" (never an XHR). */
  readonly loginUrl = `${API_BASE}/auth/google/login`;

  disconnect(): Observable<DisconnectResponse> {
    return this.http.post<DisconnectResponse>(`${API_BASE}/auth/disconnect`, null);
  }

  /** FR-3: direct child folders of `parent` ("root" = My Drive). */
  driveFolders(parent: string = 'root', pageToken: string | null = null): Observable<DriveFoldersResponse> {
    return this.http.get<DriveFoldersResponse>(`${API_BASE}/drive/folders`, {
      params: buildParams({ parent, page_token: pageToken }),
    });
  }

  /** FR-3: set the watched folder (non-recursive); the server requests a reconcile. */
  watch(folderId: string): Observable<WatchResponse> {
    return this.http.post<WatchResponse>(`${API_BASE}/drive/watch`, { folder_id: folderId });
  }

  /**
   * FR-11. Synchronous on the server (typically < 15 s); the contract asks the UI to
   * give up after 60 s. A timeout surfaces as an rxjs `TimeoutError`.
   */
  ask(body: AskRequest, timeoutMs: number = ASK_TIMEOUT_MS): Observable<AskResponse> {
    return this.http.post<AskResponse>(`${API_BASE}/ask`, body).pipe(timeout({ first: timeoutMs }));
  }

  progress(): Observable<ProgressResponse> {
    return this.http.get<ProgressResponse>(`${API_BASE}/progress`);
  }

  sync(): Observable<SyncRequestResponse> {
    return this.http.post<SyncRequestResponse>(`${API_BASE}/sync`, null);
  }

  /** "Full reconcile" (asynchronous; completion shows up in /api/progress). */
  reconcile(): Observable<SyncRequestResponse> {
    return this.http.post<SyncRequestResponse>(`${API_BASE}/sync/reconcile`, null);
  }

  documents(query: DocumentsQuery): Observable<DocumentsPage> {
    return this.http.get<DocumentsPage>(`${API_BASE}/documents`, { params: buildParams(query) });
  }

  reprocess(documentId: number): Observable<ReprocessResponse> {
    return this.http.post<ReprocessResponse>(`${API_BASE}/documents/${encodeURIComponent(documentId)}/reprocess`, null);
  }

  /** Keyword search (FR-10). Callers trim and enforce the minimum length. */
  search(q: string, limit?: number): Observable<SearchResponse> {
    return this.http.get<SearchResponse>(`${API_BASE}/search`, { params: buildParams({ q, limit: limit ?? null }) });
  }

  review(query: ReviewQuery): Observable<ReviewListResponse> {
    return this.http.get<ReviewListResponse>(`${API_BASE}/review`, { params: buildParams(query) });
  }

  /** `id` is the item id as-is (`returns:214`); the colon stays literal in the path. */
  reviewAction(id: string, body: ReviewActionRequest): Observable<ReviewActionResponse> {
    return this.http.post<ReviewActionResponse>(`${API_BASE}/review/${reviewIdPath(id)}`, body);
  }

  returns(query: ReturnsQuery): Observable<ReturnsPage> {
    return this.http.get<ReturnsPage>(`${API_BASE}/returns`, { params: buildParams(query) });
  }

  fundFacts(query: FundFactsQuery): Observable<FundFactsPage> {
    return this.http.get<FundFactsPage>(`${API_BASE}/fund-facts`, { params: buildParams(query) });
  }

  statements(query: StatementsQuery): Observable<StatementsPage> {
    return this.http.get<StatementsPage>(`${API_BASE}/statements`, { params: buildParams(query) });
  }

  holdings(statementId: number, query: HoldingsQuery = {}): Observable<HoldingsPage> {
    return this.http.get<HoldingsPage>(`${API_BASE}/statements/${encodeURIComponent(statementId)}/holdings`, {
      params: buildParams(query),
    });
  }

  funds(): Observable<FundsList> {
    return this.http.get<FundsList>(`${API_BASE}/funds`);
  }

  accounts(): Observable<AccountsList> {
    return this.http.get<AccountsList>(`${API_BASE}/accounts`);
  }

  /** Provenance for one value. `field` is required for statement / holding cells. */
  source(table: SourceTable, id: number, field?: string | null): Observable<SourceResponse> {
    return this.http.get<SourceResponse>(`${API_BASE}/sources/${table}/${encodeURIComponent(id)}`, {
      params: buildParams({ field: field ?? null }),
    });
  }

  /**
   * C5: the CSV of the current view is the same list URL with the same filters and
   * sort plus `format=csv`. `page` / `page_size` are dropped (the server ignores them
   * and exports every matching row).
   */
  csvUrl(endpoint: CsvEndpoint, query: QueryObject): string {
    const { page: _page, page_size: _size, ...rest } = query as Record<string, unknown>;
    return buildUrl(`${API_BASE}/${endpoint}`, { ...rest, format: 'csv' } as QueryObject);
  }

  /** Holdings CSV for one statement. */
  holdingsCsvUrl(statementId: number, query: HoldingsQuery = {}): string {
    const { page: _page, page_size: _size, ...rest } = query;
    return buildUrl(`${API_BASE}/statements/${encodeURIComponent(statementId)}/holdings`, {
      ...rest,
      format: 'csv',
    });
  }
}

/** `returns:214` -> `returns:214`, with each side percent-encoded but the colon kept. */
export function reviewIdPath(id: string): string {
  return id.split(':').map(encodeURIComponent).join(':');
}

import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { Router } from '@angular/router';
import { Observable, firstValueFrom, tap } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { API_BASE, ApiService } from '../api/api.service';
import { AuthStatusResponse, FolderRef, WatchResponse } from '../api/models';
import { BrowserLocation } from './browser-location';
import { ConfirmDialogComponent, ConfirmDialogData } from './confirm-dialog.component';
import { isMockEnabled } from './mock-flag';
import { NotifyService } from './notify.service';
import { ProgressService } from './progress.service';

/** Where the app should send the user, per the contract's `/api/auth/status` UI logic. */
export type ConnectTarget = '/connect' | '/connect/folder' | null;

export function connectTarget(s: AuthStatusResponse | null): ConnectTarget {
  if (!s || s.mode === 'demo') return null;
  if (!s.connected) return '/connect';
  if (!s.folder) return '/connect/folder';
  return null;
}

/** Dev-only: the mock api's stand-in for the Google consent round trip. */
export const MOCK_OAUTH_PATH = `${API_BASE}/__mock/oauth/callback`;

/**
 * Google Drive connection state (FR-2/FR-3): `/api/auth/status`, the login redirect,
 * disconnect and the watched folder. The header and Ingestion keep reading the
 * account and folder from `/api/progress` (C9); this service is the source for the
 * connect screens and the first-run redirect.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly location = inject(BrowserLocation);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(NotifyService);
  private readonly progress = inject(ProgressService);

  readonly status = signal<AuthStatusResponse | null>(null);
  readonly error = signal<ApiError | null>(null);
  readonly loading = signal(false);
  readonly disconnecting = signal(false);

  readonly connected = computed(() => this.status()?.connected === true);
  readonly folder = computed<FolderRef | null>(() => this.status()?.folder ?? null);

  /**
   * True once the app has shown (or decided not to show) the first-run connect prompt
   * in this session. The guard redirects at most once, so a user who is not connected
   * can still open the tabs afterwards (ingested data stays browsable, FR-2).
   */
  prompted = false;

  private inFlight: Promise<AuthStatusResponse | null> | null = null;

  /** Fetch `/api/auth/status`. Never throws: a failure is kept in `error()` and resolves null. */
  load(): Promise<AuthStatusResponse | null> {
    if (this.inFlight) return this.inFlight;
    this.loading.set(true);
    this.inFlight = firstValueFrom(this.api.authStatus())
      .then((s) => {
        this.status.set(s);
        this.error.set(null);
        return s;
      })
      .catch((e) => {
        this.error.set(toApiError(e));
        return null;
      })
      .finally(() => {
        this.loading.set(false);
        this.inFlight = null;
      });
    return this.inFlight;
  }

  /** The cached status, or a fresh load if none has been loaded yet. */
  ensureLoaded(): Promise<AuthStatusResponse | null> {
    const s = this.status();
    return s ? Promise.resolve(s) : this.load();
  }

  /**
   * "Connect Google Drive": a full-page navigation to `/api/auth/google/login`
   * through the dev proxy (a real 302 to Google, not an XHR). With the dev mock on,
   * the mock api simulates the consent round trip and the callback redirect instead.
   */
  login(): void {
    if (isMockEnabled()) {
      this.http.post<{ location: string }>(MOCK_OAUTH_PATH, null).subscribe({
        next: (r) => {
          void this.load();
          this.progress.refresh();
          void this.router.navigateByUrl(r.location);
        },
        error: (e) => this.notify.error(toApiError(e), 'Connect failed'),
      });
      return;
    }
    this.location.assign(this.api.loginUrl);
  }

  /** POST /api/drive/watch; on success the status and progress are refreshed. */
  watch(folderId: string): Observable<WatchResponse> {
    return this.api.watch(folderId).pipe(
      tap((r) => {
        const s = this.status();
        if (s) this.status.set({ ...s, folder: r.folder });
        this.progress.refresh();
      }),
    );
  }

  /** Confirm, then POST /api/auth/disconnect and return to `/connect`. Resolves true when disconnected. */
  async confirmDisconnect(): Promise<boolean> {
    const email = this.status()?.google_email ?? this.progress.progress()?.connection.google_email ?? null;
    const data: ConfirmDialogData = {
      title: 'Disconnect Google Drive?',
      body: [
        `The stored Google credentials${email ? ` for ${email}` : ''} are deleted and the folder watch stops.`,
        'Documents already ingested stay browsable. Connect again at any time.',
      ],
      confirm: 'Disconnect',
      danger: true,
    };
    const ok = await firstValueFrom(this.dialog.open(ConfirmDialogComponent, { data, autoFocus: 'dialog' }).afterClosed());
    if (!ok) return false;
    return this.disconnect();
  }

  /** POST /api/auth/disconnect (no confirm), then go to `/connect`. */
  async disconnect(): Promise<boolean> {
    if (this.disconnecting()) return false;
    this.disconnecting.set(true);
    try {
      await firstValueFrom(this.api.disconnect());
      const s = this.status();
      this.status.set(
        s
          ? { ...s, connected: false, google_email: null, scopes: [], connected_at: null, folder: null }
          : { mode: 'drive', connected: false, google_email: null, scopes: [], connected_at: null, folder: null },
      );
      this.prompted = true;
      this.notify.info('Disconnected from Google Drive.');
      this.progress.refresh();
      await this.router.navigateByUrl('/connect');
      return true;
    } catch (e) {
      this.notify.error(toApiError(e), 'Disconnect failed');
      return false;
    } finally {
      this.disconnecting.set(false);
    }
  }
}

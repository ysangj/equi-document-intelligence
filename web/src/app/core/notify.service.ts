import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Router } from '@angular/router';

import { ApiError, connectActionLabel, connectRouteFor } from '../api/api-error';

/**
 * Non-blocking messages (C2 errors). The same message is not repeated within a short
 * window, so a failing poll does not spam the user. Drive-state errors (409
 * `not_connected` / `no_folder`) get an action that opens `/connect` or `/connect/folder`.
 */
@Injectable({ providedIn: 'root' })
export class NotifyService {
  private readonly snack = inject(MatSnackBar);
  private readonly router = inject(Router);
  private readonly recent = new Map<string, number>();
  static readonly DEDUPE_MS = 30_000;

  error(err: ApiError | string, context?: string): void {
    const msg = typeof err === 'string' ? err : err.message;
    const text = context ? `${context}: ${msg}` : msg;
    if (this.isDuplicate(text)) return;
    const route = typeof err === 'string' ? null : connectRouteFor(err);
    if (route) {
      const ref = this.snack.open(text, connectActionLabel(route), { duration: 10000, panelClass: 'snack-error' });
      ref.onAction().subscribe(() => void this.router.navigateByUrl(route));
      return;
    }
    this.snack.open(text, 'Dismiss', { duration: 6000, panelClass: 'snack-error' });
  }

  info(text: string): void {
    this.snack.open(text, undefined, { duration: 3000 });
  }

  private isDuplicate(text: string): boolean {
    const now = Date.now();
    const last = this.recent.get(text);
    this.recent.set(text, now);
    return last !== undefined && now - last < NotifyService.DEDUPE_MS;
  }
}

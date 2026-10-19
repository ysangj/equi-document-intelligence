import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { toApiError } from '../api/api-error';
import { ApiService } from '../api/api.service';
import { SyncRequestResponse } from '../api/models';
import { NotifyService } from './notify.service';
import { ProgressService } from './progress.service';

/**
 * "Sync now" and "Full reconcile" (FR-12). Both are asynchronous on the server: the
 * POST only records a request (202) and the worker picks it up, so the UI shows a
 * "requested" toast and lets the next `/api/progress` poll show the outcome. One
 * instance is shared by the header and the Ingestion page, so a request in flight
 * disables the button in both places.
 */
@Injectable({ providedIn: 'root' })
export class SyncActionsService {
  private readonly api = inject(ApiService);
  private readonly notify = inject(NotifyService);
  private readonly progress = inject(ProgressService);

  readonly syncing = signal(false);
  readonly reconciling = signal(false);

  sync(): void {
    this.run(this.syncing, this.api.sync(), 'Sync', 'A sync is already queued.', 'Sync requested. Changes appear on the next refresh.');
  }

  reconcile(): void {
    this.run(
      this.reconciling,
      this.api.reconcile(),
      'Full reconcile',
      'A full reconcile is already queued.',
      'Full reconcile requested. The folder will be re-listed shortly.',
    );
  }

  private run(
    flag: WritableSignal<boolean>,
    req: Observable<SyncRequestResponse>,
    what: string,
    pendingText: string,
    okText: string,
  ): void {
    if (flag()) return;
    flag.set(true);
    req.subscribe({
      next: (r) => {
        flag.set(false);
        this.notify.info(r.already_pending ? pendingText : okText);
        this.progress.refresh();
      },
      error: (e) => {
        flag.set(false);
        this.notify.error(toApiError(e), `${what} failed`);
      },
    });
  }
}

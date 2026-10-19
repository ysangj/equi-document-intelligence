import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { Router, RouterLink } from '@angular/router';
import { Subject, catchError, firstValueFrom, map, of, switchMap } from 'rxjs';

import { ApiError, connectActionLabel, connectRouteFor, isMissingEndpoint, toApiError } from '../../api/api-error';
import { ApiService } from '../../api/api.service';
import { DriveFolder, DriveFolderParent, DriveFoldersResponse } from '../../api/models';
import { AuthService } from '../../core/auth.service';
import { ConfigService } from '../../core/config.service';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../core/confirm-dialog.component';
import { NotifyService } from '../../core/notify.service';
import { formatRelative } from '../../shared/format';

/** My Drive, as the contract represents root. Used for the first breadcrumb before the first response. */
export const ROOT_PARENT: DriveFolderParent = { folder_id: 'root', folder_name: 'My Drive', parent_id: null, is_root: true };

const NOT_AVAILABLE = 'Google Drive folder browsing is not available on the api yet.';

interface ListRequest {
  parent: string;
  pageToken: string | null;
  /** ancestors of `parent` (the breadcrumb trail before it) */
  base: DriveFolderParent[];
}

interface Picked {
  folder_id: string;
  folder_name: string;
}

type ListResult = { req: ListRequest; res: DriveFoldersResponse | null; error: ApiError | null };

/**
 * `/connect/folder` (FR-3): browse Drive folders from My Drive with breadcrumbs,
 * select one, confirm, then `POST /api/drive/watch` and go to Ingestion, where the
 * reconcile shows up. The watch is non-recursive and the page says so.
 */
@Component({
  selector: 'app-folder-picker-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <div class="card box">
      <div class="head">
        <h2>Choose a Drive folder</h2>
        @if (currentWatch(); as w) {
          <span class="muted small" data-testid="current-watch">Currently watching <b>{{ w }}</b></span>
        }
      </div>
      <p class="note" data-testid="non-recursive">
        The app reads <b>only files directly in this folder</b>. Files in its subfolders are not processed.
      </p>
      @if (!demo()) {
        <p class="note subtle" data-testid="my-drive-only">
          <b>My Drive only: shared folders and shared drives aren't listed.</b>
          To use files shared with you, move or copy them into a folder in My Drive.
        </p>
      }

      @if (demo()) {
        <p class="msg warn" data-testid="picker-demo">{{ demoReason() }}</p>
        <a routerLink="/data">Browse the data</a>
      } @else if (notConnected()) {
        <div class="state" data-testid="picker-not-connected">
          <h3>Google Drive is not connected</h3>
          <p>Connect your Google account first, then choose a folder.</p>
          <a class="btn primary" routerLink="/connect">Connect Google Drive</a>
        </div>
      } @else {
        <nav class="crumbs" aria-label="Folder path" data-testid="crumbs">
          @for (c of trail(); track c.folder_id; let i = $index, last = $last) {
            @if (last) {
              <b aria-current="page">{{ c.folder_name }}</b>
            } @else {
              <button type="button" class="btn link" (click)="goCrumb(i)">{{ c.folder_name }}</button>
              <span class="sep" aria-hidden="true">›</span>
            }
          }
          <span class="spacer"></span>
          @if (current(); as cur) {
            @if (!cur.is_root) {
              <button type="button" class="btn small" (click)="up()" data-testid="up">↑ Up</button>
            }
          }
        </nav>

        <div class="list" role="listbox" aria-label="Folders" data-testid="folder-list" [attr.aria-busy]="loading()">
          @if (loading() && !items().length) {
            <div class="state" aria-busy="true">Loading folders…</div>
          } @else if (error(); as e) {
            <div class="state error" role="alert" data-testid="picker-error">
              <h3>Couldn't load folders</h3>
              <p>{{ e.message }}</p>
              @if (connectRoute(e); as route) {
                <a class="btn primary" [routerLink]="route">{{ connectLabel(route) }}</a>
              }
              <button type="button" class="btn" (click)="retry()">Retry</button>
            </div>
          } @else if (!items().length) {
            <div class="state" data-testid="picker-empty">
              <h3>No subfolders here</h3>
              @if (current() && !current()!.is_root) {
                <p>You can watch <b>{{ current()!.folder_name }}</b> itself.</p>
                <button type="button" class="btn" (click)="selectCurrent()" data-testid="select-current">Select “{{ current()!.folder_name }}”</button>
              } @else {
                <p>My Drive has no folders. Create one in Google Drive (or move a shared folder's files into one), then reload.</p>
              }
            </div>
          } @else {
            @for (f of items(); track f.folder_id) {
              <div
                class="row"
                role="option"
                tabindex="0"
                [class.on]="selected()?.folder_id === f.folder_id"
                [attr.aria-selected]="selected()?.folder_id === f.folder_id"
                [attr.data-id]="f.folder_id"
                data-testid="folder-row"
                (click)="select(f)"
                (dblclick)="open(f)"
                (keydown.enter)="open(f)"
                (keydown.space)="select(f); $event.preventDefault()"
              >
                <span class="radio" aria-hidden="true"></span>
                <span class="icon" aria-hidden="true">📁</span>
                <span class="name">{{ f.folder_name }}</span>
                <span class="muted small when">{{ modified(f.modified_at) }}</span>
                <button type="button" class="btn small" (click)="open(f); $event.stopPropagation()" [attr.aria-label]="'Open ' + f.folder_name" data-testid="open">Open ›</button>
              </div>
            }
            @if (nextPageToken()) {
              <div class="more">
                <button type="button" class="btn" (click)="loadMore()" [disabled]="loading()" data-testid="load-more">
                  {{ loading() ? 'Loading…' : 'Load more folders' }}
                </button>
              </div>
            }
          }
        </div>

        <div class="footer">
          @if (selected(); as s) {
            <span data-testid="selection">Selected: <b>{{ s.folder_name }}</b> <span class="muted">· only files directly in this folder</span></span>
          } @else {
            <span class="muted">Select a folder, or open one to browse inside it.</span>
          }
          <span class="spacer"></span>
          @if (watchError(); as e) {
            <span class="err" role="alert" data-testid="watch-error">
              {{ e.message }}
              @if (connectRoute(e); as route) {
                <a [routerLink]="route">{{ connectLabel(route) }}</a>
              }
            </span>
          }
          <button type="button" class="btn primary" [disabled]="!selected() || watching()" (click)="confirmWatch()" data-testid="watch">
            {{ watching() ? 'Saving…' : 'Watch this folder' }}
          </button>
        </div>
      }
    </div>
  `,
  styles: `
    .box {
      padding: 20px 22px;
      max-width: 760px;
    }
    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 12px;
    }
    h2 {
      margin: 0;
      font-size: 18px;
    }
    .small {
      font-size: 12.5px;
    }
    .note {
      margin: 8px 0 14px;
      padding: 8px 12px;
      background: var(--accent-soft);
      color: var(--accent-ink);
      border-radius: 8px;
      font-size: 13px;
    }
    .note.subtle {
      margin-top: -6px;
      background: var(--bg);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .msg.warn {
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 8px;
      padding: 8px 12px;
    }
    .crumbs {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .crumbs .sep {
      color: var(--muted);
    }
    .btn.small {
      padding: 3px 9px;
      font-size: 12.5px;
    }
    .list {
      min-height: 120px;
      max-height: 420px;
      overflow-y: auto;
    }
    .row {
      display: grid;
      grid-template-columns: 16px 20px minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      padding: 8px 6px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }
    .row:hover {
      background: var(--row-hover);
    }
    .row.on {
      background: var(--accent-soft);
    }
    .row:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }
    .radio {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 1.5px solid var(--muted);
    }
    .row.on .radio {
      border-color: var(--accent);
      box-shadow: inset 0 0 0 3px var(--panel);
      background: var(--accent);
    }
    .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .more {
      padding: 10px;
      text-align: center;
    }
    .footer {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      padding-top: 14px;
    }
    .err {
      color: var(--bad);
      font-size: 12.5px;
    }
    a.btn {
      display: inline-block;
      text-decoration: none;
    }
    @container (max-width: 568px) {
      .when {
        display: none;
      }
      .row {
        grid-template-columns: 16px 20px minmax(0, 1fr) auto;
      }
    }
  `,
})
export class FolderPickerPageComponent {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly config = inject(ConfigService);
  private readonly dialog = inject(MatDialog);
  private readonly notify = inject(NotifyService);
  private readonly router = inject(Router);

  readonly trail = signal<DriveFolderParent[]>([ROOT_PARENT]);
  readonly items = signal<DriveFolder[]>([]);
  readonly nextPageToken = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly selected = signal<Picked | null>(null);
  readonly watching = signal(false);
  readonly watchError = signal<ApiError | null>(null);

  readonly current = computed(() => this.trail().at(-1) ?? null);
  /** set when the api answers 409 `demo_mode` (C7) although config/status did not say demo */
  private readonly demoFromApi = signal(false);
  readonly demo = computed(() => this.demoFromApi() || !this.config.feature('drive').enabled || this.auth.status()?.mode === 'demo');
  readonly demoReason = computed(() => this.config.feature('drive').reason ?? 'Demo mode: Google Drive is not used.');
  readonly notConnected = computed(() => {
    const s = this.auth.status();
    return !!s && s.mode === 'drive' && !s.connected;
  });
  readonly currentWatch = computed(() => {
    const f = this.auth.status()?.folder;
    return f?.source === 'drive' ? f.folder_name : null;
  });

  readonly connectRoute = connectRouteFor;
  readonly connectLabel = connectActionLabel;

  private readonly requests = new Subject<ListRequest>();
  private last: ListRequest = { parent: 'root', pageToken: null, base: [] };

  constructor() {
    this.auth.prompted = true;
    const sub = this.requests
      .pipe(
        switchMap((req) =>
          this.api.driveFolders(req.parent, req.pageToken).pipe(
            map((res): ListResult => ({ req, res, error: null })),
            catchError((e) => of<ListResult>({ req, res: null, error: toApiError(e) })),
          ),
        ),
      )
      .subscribe((r) => this.onResult(r));
    inject(DestroyRef).onDestroy(() => sub.unsubscribe());
    void this.init();
  }

  private async init(): Promise<void> {
    if (this.demo()) return;
    const s = await this.auth.load();
    if (this.demo() || (s && !s.connected)) return;
    const authErr = this.auth.error();
    if (!s && authErr && isMissingEndpoint(authErr)) {
      this.error.set({ ...authErr, message: NOT_AVAILABLE });
      return;
    }
    this.list({ parent: 'root', pageToken: null, base: [] });
  }

  private list(req: ListRequest): void {
    this.last = req;
    this.loading.set(true);
    this.error.set(null);
    if (!req.pageToken) {
      // Show the target in the breadcrumbs at once; the response replaces it with the real parent.
      this.items.set([]);
      this.nextPageToken.set(null);
    }
    this.requests.next(req);
  }

  private onResult({ req, res, error }: ListResult): void {
    this.loading.set(false);
    if (error?.code === 'demo_mode') {
      this.demoFromApi.set(true);
      this.error.set(null);
      return;
    }
    if (error || !res) {
      // the root listing can only 404 when the endpoint itself is missing
      this.error.set(error && req.parent === 'root' && isMissingEndpoint(error) ? { ...error, message: NOT_AVAILABLE } : error);
      return;
    }
    this.trail.set([...req.base, res.parent]);
    this.items.set(req.pageToken ? [...this.items(), ...res.items] : res.items);
    this.nextPageToken.set(res.next_page_token);
  }

  select(f: DriveFolder): void {
    this.selected.set({ folder_id: f.folder_id, folder_name: f.folder_name });
    this.watchError.set(null);
  }

  selectCurrent(): void {
    const c = this.current();
    if (c && !c.is_root) this.selected.set({ folder_id: c.folder_id, folder_name: c.folder_name });
  }

  open(f: DriveFolder): void {
    this.selected.set(null);
    const base = this.trail();
    this.trail.set([...base, { folder_id: f.folder_id, folder_name: f.folder_name, parent_id: base.at(-1)?.folder_id ?? null, is_root: false }]);
    this.list({ parent: f.folder_id, pageToken: null, base });
  }

  goCrumb(i: number): void {
    const t = this.trail();
    if (i < 0 || i >= t.length - 1) return;
    this.selected.set(null);
    this.trail.set(t.slice(0, i + 1));
    this.list({ parent: t[i].folder_id, pageToken: null, base: t.slice(0, i) });
  }

  /** Up: the previous breadcrumb, or the parent's id when the trail does not know it. */
  up(): void {
    const t = this.trail();
    if (t.length >= 2) {
      this.goCrumb(t.length - 2);
      return;
    }
    const pid = t.at(-1)?.parent_id;
    if (pid) {
      this.selected.set(null);
      this.list({ parent: pid, pageToken: null, base: [] });
    }
  }

  loadMore(): void {
    const token = this.nextPageToken();
    if (!token || this.loading()) return;
    this.list({ parent: this.current()?.folder_id ?? 'root', pageToken: token, base: this.trail().slice(0, -1) });
  }

  retry(): void {
    this.list(this.last);
  }

  modified(ts: string | null): string {
    return ts ? `modified ${formatRelative(ts, Date.now())}` : '';
  }

  async confirmWatch(): Promise<void> {
    const pick = this.selected();
    if (!pick || this.watching()) return;
    const old = this.currentWatch();
    const body = [
      `Only files directly in “${pick.folder_name}” are processed. Files in its subfolders are ignored.`,
      'A full reconcile starts now: every supported file is queued and processed in the background.',
    ];
    if (old && old !== pick.folder_name) {
      body.push(`Documents from “${old}” will be marked removed and leave every view.`);
    }
    const data: ConfirmDialogData = { title: `Watch “${pick.folder_name}”?`, body, confirm: 'Watch folder' };
    const ok = await firstValueFrom(this.dialog.open(ConfirmDialogComponent, { data, autoFocus: 'dialog' }).afterClosed());
    if (!ok) return;
    this.watching.set(true);
    this.watchError.set(null);
    this.auth.watch(pick.folder_id).subscribe({
      next: (r) => {
        this.watching.set(false);
        this.notify.info(`Watching “${r.folder.folder_name ?? pick.folder_name}”. A full reconcile was requested.`);
        void this.router.navigateByUrl('/ingestion');
      },
      error: (e) => {
        this.watching.set(false);
        const err = toApiError(e);
        if (err.code === 'not_found') err.message = 'That folder no longer exists in Drive. Pick another one.';
        else if (err.code === 'validation_error' && err.details?.length) err.message = err.details[0].message;
        this.watchError.set(err);
      },
    });
  }
}

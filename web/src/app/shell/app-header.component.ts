import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatMenuModule } from '@angular/material/menu';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { AuthService } from '../core/auth.service';
import { ConfigService } from '../core/config.service';
import { driveAccess } from '../core/drive-scope';
import { ProgressService } from '../core/progress.service';
import { askDisabledReason } from '../pages/ask/ask-disabled';
import { AskSessionService } from '../pages/ask/ask-session.service';
import { AskPanelService } from './ask-panel.service';
import { SyncActionsService } from '../core/sync-actions.service';
import { formatRelative } from '../shared/format';
import { HeaderSearchComponent } from './header-search.component';

/**
 * Header from ui-mockup.html: brand, search box, connection status (account, watched
 * folder, last sync, Sync now) and the tab nav with the Review badge.
 * Connection data comes from the single `/api/progress` poll (C9).
 */
@Component({
  selector: 'app-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive, MatMenuModule, HeaderSearchComponent],
  template: `
    <div class="top">
      <a class="brand" routerLink="/data">Fund<span>Doc</span> Intelligence</a>
      <app-header-search />
      <div class="conn" data-testid="conn">
        @if (progress(); as p) {
          @if (p.mode === 'demo') {
            <span><span class="dot demo"></span>Demo</span>
          } @else if (p.connection.connected) {
            <button type="button" class="acct" [matMenuTriggerFor]="acct" (menuOpened)="loadAccess()" aria-label="Google account menu" data-testid="account">
              <span class="dot"></span>{{ p.connection.google_email }} <span class="caret" aria-hidden="true">▾</span>
            </button>
            <mat-menu #acct="matMenu" xPosition="before">
              <div class="menu-head">
                Signed in as <b>{{ p.connection.google_email }}</b>
                @if (access(); as a) {
                  <div class="scope" [attr.title]="a.raw || null" data-testid="header-scope">{{ a.label }}</div>
                }
              </div>
              <a mat-menu-item routerLink="/connect/folder">{{ p.sync.folder_name ? 'Change folder' : 'Choose a folder' }}</a>
              <a mat-menu-item routerLink="/connect">Connection settings</a>
              <button mat-menu-item type="button" class="danger" (click)="disconnect()" data-testid="header-disconnect">Disconnect…</button>
            </mat-menu>
          } @else {
            <span><span class="dot off"></span><a routerLink="/connect" data-testid="header-connect">Not connected · Connect</a></span>
          }
          @if (p.sync.folder_name) {
            <span class="sep">·</span><span data-testid="header-folder" [attr.title]="p.mode === 'drive' ? 'Watched folder (only files directly in it)' : null"
              >Folder: {{ p.sync.folder_name }}</span
            >
          } @else if (p.mode === 'drive' && p.connection.connected) {
            <span class="sep">·</span><a routerLink="/connect/folder" data-testid="header-choose-folder">Choose a folder</a>
          }
          <span class="sep">·</span><span>synced {{ synced() }}</span>
          <button class="btn" type="button" (click)="syncNow()" [disabled]="syncing() || p.sync.sync_pending" data-testid="header-sync">
            {{ syncing() ? 'Requesting…' : p.sync.sync_pending ? 'Sync queued…' : 'Sync now' }}
          </button>
        } @else if (progressError()) {
          <span><span class="dot off"></span>api status unknown</span>
        }
      </div>
    </div>
    <nav aria-label="Main">
      <a routerLink="/data" routerLinkActive="on">Data</a>
      <a routerLink="/ingestion" routerLinkActive="on">Ingestion</a>
      <a routerLink="/review" routerLinkActive="on"
        >Review
        @if (reviewOpen() > 0) {
          <span class="badge" data-testid="review-badge">{{ reviewOpen() }}</span>
        }
      </a>
      <span class="nav-spacer"></span>
      <button
        type="button"
        class="ask-toggle"
        [class.open]="askPanel.open()"
        [class.disabled]="!!askReason()"
        [attr.aria-expanded]="askPanel.open()"
        aria-controls="ask-panel"
        [attr.title]="askTitle()"
        (click)="askPanel.toggle()"
        data-testid="ask-toggle"
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.3" />
          <rect x="9.5" y="3" width="4.5" height="10" [attr.fill]="askPanel.open() ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="1" />
        </svg>
        Ask
        @if (askPanel.pinned() && askPanel.open()) {
          <span class="pinned" data-testid="ask-pinned">pinned</span>
        }
      </button>
    </nav>
  `,
  styles: `
    :host {
      display: block;
    }
    .top {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 12px 24px;
    }
    .brand {
      font-weight: 700;
      font-size: 15px;
      white-space: nowrap;
      color: var(--ink);
      text-decoration: none;
    }
    .brand span {
      color: var(--accent);
    }
    .conn {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 12.5px;
      white-space: nowrap;
    }
    .conn .btn {
      margin-left: 6px;
    }
    .acct {
      border: 0;
      background: none;
      font: inherit;
      color: inherit;
      cursor: pointer;
      padding: 2px 4px;
      border-radius: 6px;
    }
    .acct:hover {
      background: var(--bg);
      color: var(--ink);
    }
    .caret {
      font-size: 10px;
    }
    .menu-head {
      padding: 8px 16px 6px;
      font-size: 12.5px;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
    }
    .menu-head .scope {
      margin-top: 2px;
      font-size: 12px;
    }
    .danger {
      color: var(--bad);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      display: inline-block;
      margin-right: 6px;
    }
    .dot.demo {
      background: var(--warn);
    }
    .dot.off {
      background: var(--muted);
    }
    nav {
      display: flex;
      gap: 4px;
      padding: 0 24px;
      overflow-x: auto;
      align-items: stretch;
    }
    nav a {
      padding: 10px 14px;
      color: var(--muted);
      border-bottom: 2px solid transparent;
      text-decoration: none;
      white-space: nowrap;
    }
    nav a.on {
      color: var(--ink);
      border-bottom-color: var(--accent);
      font-weight: 600;
    }
    .nav-spacer {
      flex: 1;
    }
    .ask-toggle {
      align-self: center;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 4px 0;
      padding: 5px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      font: inherit;
      color: var(--ink);
      cursor: pointer;
      white-space: nowrap;
    }
    .ask-toggle:hover {
      border-color: #cdd8fb;
    }
    .ask-toggle.open {
      background: var(--accent-soft);
      border-color: #cdd8fb;
      color: var(--accent-ink);
      font-weight: 600;
    }
    .ask-toggle.disabled {
      color: var(--muted);
    }
    .ask-toggle .pinned {
      font-size: 11px;
      font-weight: 500;
      color: var(--accent);
    }
    .badge {
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 10px;
      padding: 0 7px;
      font-size: 11.5px;
      margin-left: 6px;
    }
    @media (max-width: 900px) {
      .conn {
        display: none;
      }
    }
    @media (max-width: 600px) {
      .top {
        padding: 10px 16px;
        gap: 10px;
      }
      nav {
        padding: 0 8px;
      }
      nav a {
        padding: 10px 10px;
      }
      .brand {
        font-size: 14px;
      }
    }
  `,
})
export class AppHeaderComponent {
  private readonly sync = inject(SyncActionsService);
  private readonly config = inject(ConfigService);
  private readonly progressService = inject(ProgressService);
  private readonly auth = inject(AuthService);

  readonly progress = this.progressService.progress;
  readonly progressError = this.progressService.error;
  readonly syncing = this.sync.syncing;

  /** ticks every second so "synced 12s ago" stays current */
  private readonly clock = signal(Date.now());

  readonly reviewOpen = computed(() => this.progress()?.review_open ?? 0);
  readonly askPanel = inject(AskPanelService);
  private readonly askSession = inject(AskSessionService);
  /** Ask unavailable (config or a 503): the toggle is dimmed but still opens the panel to show why */
  readonly askReason = computed(() => askDisabledReason(this.config, this.askSession));
  readonly askTitle = computed(() => {
    const reason = this.askReason();
    const action = this.askPanel.open() ? 'Close the Ask panel' : 'Open the Ask panel';
    return reason ? `${reason} ${action} to see why.` : action;
  });
  /** Drive scope label for the account menu (scopes come from `/api/auth/status`, not `/api/progress`). */
  readonly access = computed(() => {
    const s = this.auth.status();
    return s?.connected ? driveAccess(s.scopes) : null;
  });

  readonly synced = computed(() => {
    const p = this.progress();
    const received = this.progressService.receivedAt();
    if (!p) return '';
    // C6: relative times come from generated_at and last_success_at; advance by local elapsed time.
    const serverNow = Date.parse(p.generated_at) + Math.max(0, this.clock() - (received ?? this.clock()));
    return formatRelative(p.sync.last_success_at, serverNow);
  });

  constructor() {
    const id = setInterval(() => this.clock.set(Date.now()), 1000);
    inject(DestroyRef).onDestroy(() => clearInterval(id));
  }

  syncNow(): void {
    this.sync.sync();
  }

  /** The menu needs the granted scopes; load the status once if no screen has yet. */
  loadAccess(): void {
    if (!this.auth.status()) void this.auth.ensureLoaded();
  }

  disconnect(): void {
    void this.auth.confirmDisconnect();
  }
}

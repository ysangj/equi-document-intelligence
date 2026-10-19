import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { isMissingEndpoint } from '../../api/api-error';
import { AuthService } from '../../core/auth.service';
import { ConfigService } from '../../core/config.service';
import { DRIVE_READONLY_SCOPE, driveAccess } from '../../core/drive-scope';
import { formatRelative } from '../../shared/format';
import { connectErrorMessage } from './connect-errors';

/**
 * `/connect` (FR-2, §4 step 3): connection status from `/api/auth/status`, the
 * "Connect Google Drive" full-page redirect, a message per `?error=` code, and the
 * demo-mode explanation (`features.drive.reason`) with Connect hidden.
 */
@Component({
  selector: 'app-connect-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <div class="card box">
      <h2>Google Drive</h2>
      @if (errorMessage(); as m) {
        <p class="msg bad" role="alert" data-testid="connect-error">{{ m }}</p>
      }

      @if (demo()) {
        <div data-testid="connect-demo">
          <p class="msg warn">{{ demoReason() }}</p>
          <p class="muted">No Google connection is needed in demo mode. Browsing, Search and Review work on the sample documents.</p>
          <a class="btn primary" routerLink="/data">Browse the data</a>
        </div>
      } @else if (auth.loading() && !status()) {
        <p class="muted" aria-busy="true">Checking the connection…</p>
      } @else if (auth.error(); as e) {
        <div data-testid="connect-unavailable">
          @if (missing(e)) {
            <p class="msg warn">Google Drive connection is not available on the api yet.</p>
            <p class="muted">The api does not serve <code>/api/auth/status</code>. Data already ingested is still browsable.</p>
          } @else {
            <p class="msg bad" role="alert">Couldn't check the connection: {{ e.message }}</p>
          }
          <div class="row">
            <button type="button" class="btn" (click)="reload()">Retry</button>
            <a routerLink="/data">Back to Data</a>
          </div>
        </div>
      } @else if (status(); as s) {
        @if (s.connected) {
          <div data-testid="connect-connected">
            <p class="state-line"><span class="dot"></span>Connected as <b>{{ s.google_email }}</b>
              @if (s.connected_at) {
                <span class="muted"> · connected {{ since(s.connected_at) }}</span>
              }
            </p>
            <dl>
              <dt>Access</dt>
              <dd data-testid="connect-scope">
                @let a = access(s.scopes);
                <span [attr.title]="a.raw || null">{{ a.label }}</span>
                @if (s.scopes.length) {
                  <details class="scopes" data-testid="connect-scope-details">
                    <summary>Granted scopes</summary>
                    <ul>
                      @for (sc of s.scopes; track sc) {
                        <li><code>{{ sc }}</code></li>
                      }
                    </ul>
                    <p class="muted small">The identity scopes are used only to read your account email.</p>
                  </details>
                }
              </dd>
              <dt>Watched folder</dt>
              <dd data-testid="connect-folder">
                @if (s.folder?.folder_name; as name) {
                  <b>{{ name }}</b> <span class="muted">· only files directly in this folder</span>
                } @else {
                  <span class="muted">No folder chosen yet</span>
                }
              </dd>
            </dl>
            <div class="row">
              <a class="btn primary" routerLink="/connect/folder" data-testid="choose-folder">{{ s.folder ? 'Change folder' : 'Choose a folder' }}</a>
              <button type="button" class="btn" (click)="login()" data-testid="reconnect">Reconnect</button>
              <span class="spacer"></span>
              <button type="button" class="btn danger" (click)="disconnect()" [disabled]="auth.disconnecting()" data-testid="disconnect">
                {{ auth.disconnecting() ? 'Disconnecting…' : 'Disconnect' }}
              </button>
            </div>
          </div>
        } @else {
          <div data-testid="connect-disconnected">
            <p class="state-line"><span class="dot off"></span>Not connected</p>
            <p class="muted">
              Sign in with Google to let the app read one Drive folder. The app asks for
              <span [attr.title]="readOnlyScope">read-only access to Google Drive</span>, and only files directly in
              the folder you choose are processed.
            </p>
            <div class="row">
              <button type="button" class="btn primary" (click)="login()" data-testid="connect-btn">Connect Google Drive</button>
              <a routerLink="/data">Browse existing data</a>
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: `
    .box {
      padding: 24px;
      max-width: 640px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .msg {
      border-radius: 8px;
      padding: 8px 12px;
      margin: 0 0 12px;
    }
    .msg.bad {
      background: var(--bad-soft);
      color: var(--bad);
    }
    .msg.warn {
      background: var(--warn-soft);
      color: var(--warn);
    }
    .state-line {
      margin: 0 0 12px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      display: inline-block;
      margin-right: 8px;
    }
    .dot.off {
      background: var(--muted);
    }
    dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 6px 16px;
      margin: 0 0 16px;
    }
    dt {
      color: var(--muted);
      font-size: 12.5px;
    }
    dd {
      margin: 0;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    a.btn {
      display: inline-block;
      text-decoration: none;
    }
    .btn.danger {
      color: var(--bad);
    }
    .scopes {
      margin-top: 4px;
      font-size: 12.5px;
    }
    .scopes summary {
      cursor: pointer;
      color: var(--muted);
    }
    .scopes ul {
      margin: 6px 0 4px;
      padding-left: 18px;
    }
    .scopes code {
      font-size: 12px;
      word-break: break-all;
    }
    .small {
      font-size: 12px;
      margin: 0;
    }
  `,
})
export class ConnectPageComponent {
  readonly auth = inject(AuthService);
  private readonly config = inject(ConfigService);

  /** `?error=` from the OAuth callback */
  readonly error = input<string | undefined>(undefined);

  readonly status = this.auth.status;
  readonly missing = isMissingEndpoint;
  readonly errorMessage = computed(() => connectErrorMessage(this.error()));
  readonly demo = computed(() => !this.config.feature('drive').enabled || this.status()?.mode === 'demo');
  readonly demoReason = computed(
    () => this.config.feature('drive').reason ?? 'Demo mode: documents are read from ./sample_docs instead of Google Drive.',
  );

  constructor() {
    // Visiting /connect counts as the first-run prompt (the guard does not redirect again).
    this.auth.prompted = true;
    if (!this.demo()) void this.auth.load();
  }

  reload(): void {
    void this.auth.load();
  }

  login(): void {
    this.auth.login();
  }

  disconnect(): void {
    void this.auth.confirmDisconnect();
  }

  since(ts: string): string {
    return formatRelative(ts, Date.now());
  }

  readonly access = driveAccess;
  readonly readOnlyScope = DRIVE_READONLY_SCOPE;
}

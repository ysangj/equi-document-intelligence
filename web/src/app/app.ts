import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, afterNextRender, inject, viewChild } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { ConfigService } from './core/config.service';
import { isMockEnabled } from './core/mock-flag';
import { ProgressService } from './core/progress.service';
import { AppHeaderComponent } from './shell/app-header.component';
import { AskDockComponent } from './shell/ask-dock.component';
import { AskSourceSheetComponent } from './shell/ask-source-sheet.component';
import { DemoBannerComponent } from './shell/demo-banner.component';
import { ProgressIndicatorComponent } from './shell/progress-indicator.component';

/**
 * App shell from ui-mockup.html: sticky header (top bar, tabs, progress), then the
 * main area (the routed page) with the Ask panel docked on its right (U9). The main
 * area is a CSS container, so pages lay out by the width they actually get.
 * `--header-h` (the header's live height) positions the sticky Ask panel and the
 * sticky source panels under the header.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, AppHeaderComponent, DemoBannerComponent, ProgressIndicatorComponent, AskDockComponent, AskSourceSheetComponent],
  template: `
    <header #header>
      <app-header />
      <app-demo-banner [config]="config.config()" />
      <app-progress-indicator [progress]="progress.progress()" [error]="progress.error()" />
      @if (config.error(); as e) {
        <div class="cfg-err" role="alert">Could not load app configuration: {{ e.message }}</div>
      }
      @if (mock) {
        <div class="mock" data-testid="mock-flag">Mock api (dev only): data is simulated in the browser.</div>
      }
    </header>
    <div class="shell">
      <main>
        <div class="page">
          <router-outlet />
        </div>
      </main>
      <app-ask-dock />
    </div>
    <app-ask-source-sheet />
  `,
  styles: `
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .shell {
      display: flex;
      align-items: flex-start;
    }
    main {
      flex: 1 1 0;
      min-width: 0;
      padding: 20px 24px 40px;
      container-type: inline-size;
      container-name: main;
    }
    .page {
      max-width: 1440px;
    }
    .cfg-err {
      padding: 6px 24px;
      background: var(--bad-soft);
      color: var(--bad);
      font-size: 12.5px;
    }
    .mock {
      padding: 3px 24px;
      background: #0f1729;
      color: #dbe4ff;
      font-size: 11.5px;
      font-family: var(--mono);
    }
    @media (max-width: 600px) {
      header {
        position: static;
      }
      main {
        padding: 16px 16px 32px;
      }
    }
  `,
})
export class App {
  readonly config = inject(ConfigService);
  readonly progress = inject(ProgressService);
  readonly mock = isMockEnabled();

  private readonly header = viewChild.required<ElementRef<HTMLElement>>('header');

  constructor() {
    this.progress.start();
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const el = this.header().nativeElement;
      const publish = () => document.documentElement.style.setProperty('--header-h', `${el.offsetHeight}px`);
      publish();
      if (typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver(publish);
      ro.observe(el);
      destroyRef.onDestroy(() => ro.disconnect());
    });
  }
}

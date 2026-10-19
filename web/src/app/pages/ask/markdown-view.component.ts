import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { Block, parseMarkdown } from './markdown';

/**
 * Renders an Ask answer from `parseMarkdown` with Angular templates only: every piece
 * of answer text goes through interpolation, so raw HTML in the answer can never
 * become an element. `[n]` markers render as citation chips; unverified numbers get
 * a warning underline and a tooltip.
 */
@Component({
  selector: 'app-markdown-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgTemplateOutlet],
  template: `
    <div class="md" data-testid="answer-md">
      <ng-container *ngTemplateOutlet="blocksT; context: { $implicit: blocks() }" />
    </div>

    <ng-template #blocksT let-blocks>
      @for (b of blocks; track $index) {
        @switch (b.t) {
          @case ('p') {
            <p><ng-container *ngTemplateOutlet="inlT; context: { $implicit: b.c }" /></p>
          }
          @case ('h') {
            <div class="h" [class]="'h h' + b.level" role="heading" [attr.aria-level]="b.level + 2">
              <ng-container *ngTemplateOutlet="inlT; context: { $implicit: b.c }" />
            </div>
          }
          @case ('list') {
            @if (b.ordered) {
              <ol [attr.start]="b.start === 1 ? null : b.start">
                @for (it of b.items; track $index) {
                  <li><ng-container *ngTemplateOutlet="itemT; context: { $implicit: it }" /></li>
                }
              </ol>
            } @else {
              <ul>
                @for (it of b.items; track $index) {
                  <li><ng-container *ngTemplateOutlet="itemT; context: { $implicit: it }" /></li>
                }
              </ul>
            }
          }
          @case ('code') {
            <pre><code>{{ b.v }}</code></pre>
          }
          @case ('quote') {
            <blockquote><ng-container *ngTemplateOutlet="blocksT; context: { $implicit: b.c }" /></blockquote>
          }
          @case ('hr') {
            <hr />
          }
          @case ('table') {
            <div class="tbl">
              <table class="mini">
                <thead>
                  <tr>
                    @for (h of b.head; track $index; let k = $index) {
                      <th [style.text-align]="b.align[k]"><ng-container *ngTemplateOutlet="inlT; context: { $implicit: h }" /></th>
                    }
                  </tr>
                </thead>
                <tbody>
                  @for (r of b.rows; track $index) {
                    <tr>
                      @for (c of r; track $index; let k = $index) {
                        <td [style.text-align]="b.align[k]"><ng-container *ngTemplateOutlet="inlT; context: { $implicit: c }" /></td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        }
      }
    </ng-template>

    <!-- a tight list item (one paragraph) renders inline, like GFM -->
    <ng-template #itemT let-it>
      @if (it.length === 1 && it[0].t === 'p') {
        <ng-container *ngTemplateOutlet="inlT; context: { $implicit: it[0].c }" />
      } @else {
        <ng-container *ngTemplateOutlet="blocksT; context: { $implicit: it }" />
      }
    </ng-template>

    <!-- kept on few lines on purpose: whitespace next to interpolated text would show up as stray spaces -->
    <ng-template #inlT let-nodes
      >@for (n of nodes; track $index) {@switch (n.t) {@case ('text') {<ng-container>{{ n.v }}</ng-container>}
      @case ('strong') {<strong><ng-container *ngTemplateOutlet="inlT; context: { $implicit: n.c }" /></strong>}
      @case ('em') {<em><ng-container *ngTemplateOutlet="inlT; context: { $implicit: n.c }" /></em>}
      @case ('code') {<code>{{ n.v }}</code>}
      @case ('link') {<a [href]="n.href" target="_blank" rel="noopener noreferrer"><ng-container *ngTemplateOutlet="inlT; context: { $implicit: n.c }" /></a>}
      @case ('cite') {<button type="button" class="cite" [class.on]="activeCitation() === n.id" [attr.data-cite]="n.id" [attr.aria-label]="'Show citation ' + n.id" (click)="cite.emit(n.id)">{{ n.id }}</button>}
      @case ('unverified') {<span class="unverified" tabindex="0" [attr.title]="unverifiedTitle" [attr.aria-label]="n.v + ' (' + unverifiedTitle + ')'" data-testid="unverified">{{ n.v }}</span>}
      @case ('br') {<br />}}}</ng-template
    >
  `,
  styles: `
    :host {
      display: block;
    }
    .md {
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .md p {
      margin: 0 0 10px;
    }
    .md .h {
      font-weight: 700;
      margin: 14px 0 6px;
    }
    .md .h1,
    .md .h2 {
      font-size: 16px;
    }
    .md .h3,
    .md .h4,
    .md .h5,
    .md .h6 {
      font-size: 14px;
    }
    .md ul,
    .md ol {
      margin: 0 0 10px;
      padding-left: 22px;
    }
    .md li {
      margin: 2px 0;
    }
    .md code {
      font-family: var(--mono);
      font-size: 12.5px;
      background: var(--head-bg);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 0 4px;
    }
    .md pre {
      font-family: var(--mono);
      font-size: 12px;
      background: #0f1729;
      color: #dbe4ff;
      border-radius: 8px;
      padding: 10px;
      overflow: auto;
      margin: 6px 0 10px;
    }
    .md pre code {
      background: none;
      border: 0;
      padding: 0;
      color: inherit;
    }
    .md blockquote {
      margin: 0 0 10px;
      padding: 2px 12px;
      border-left: 3px solid var(--line);
      color: var(--muted);
    }
    .md hr {
      border: 0;
      border-top: 1px solid var(--line);
      margin: 12px 0;
    }
    .tbl {
      overflow-x: auto;
      margin: 0 0 10px;
    }
    table.mini {
      border-collapse: collapse;
    }
    table.mini th,
    table.mini td {
      padding: 5px 8px;
      font-size: 12.5px;
      border-bottom: 1px solid var(--line);
      text-align: left;
    }
    table.mini th {
      color: var(--muted);
      font-weight: 600;
      background: var(--head-bg);
    }
    .cite {
      display: inline-block;
      border: 0;
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 5px;
      padding: 0 6px;
      font: inherit;
      font-size: 11.5px;
      font-weight: 700;
      line-height: 1.5;
      vertical-align: 1px;
      margin: 0 1px 0 2px;
      cursor: pointer;
    }
    .cite:hover,
    .cite.on {
      background: var(--accent);
      color: #fff;
    }
    .cite:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .unverified {
      background: var(--warn-soft);
      color: var(--warn);
      text-decoration: underline dotted var(--warn);
      text-underline-offset: 3px;
      border-radius: 3px;
      padding: 0 1px;
      cursor: help;
    }
  `,
})
export class MarkdownViewComponent {
  readonly markdown = input('');
  readonly citationIds = input<readonly number[]>([]);
  readonly unverified = input<readonly string[]>([]);
  readonly activeCitation = input<number | null>(null);
  readonly cite = output<number>();

  readonly unverifiedTitle = 'Not found in any tool result';

  readonly blocks = computed<Block[]>(() =>
    parseMarkdown(this.markdown(), { citationIds: this.citationIds(), unverified: this.unverified() }),
  );
}

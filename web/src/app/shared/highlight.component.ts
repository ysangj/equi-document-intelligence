import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Segment } from './headline';

/**
 * Renders highlight segments: marked parts in `<mark>`, the rest as text. Pure
 * interpolation, so nothing in a segment can become markup.
 */
@Component({
  selector: 'app-highlight',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@for (s of segments(); track $index) {@if (s.mark) {<mark>{{ s.text }}</mark>} @else {<ng-container>{{ s.text }}</ng-container>}}`,
  styles: `
    mark {
      background: var(--mark);
      color: inherit;
      padding: 0 1px;
      border-radius: 2px;
    }
  `,
})
export class HighlightComponent {
  readonly segments = input<Segment[]>([]);
}

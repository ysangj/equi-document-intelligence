import { Injectable, inject, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';

import { SearchService } from './search.service';

export type EntityKind = 'fund' | 'account';

/**
 * Fund / account display names changed on the server (Review "Use as main name",
 * `make_canonical`). Pages that hold `/api/funds` or `/api/accounts` lists (Data
 * filters, Review's "existing fund" picker) reload them on `changed$`; `version` lets
 * a page that loads names lazily tell it is stale. The shared search results are
 * re-run so Search shows the new name. Ask answers already on screen are a record of
 * what was answered and are not rewritten; the next question gets the new name.
 */
@Injectable({ providedIn: 'root' })
export class EntityNamesService {
  private readonly search = inject(SearchService);
  private readonly changes = new Subject<EntityKind>();

  /** bumped on every invalidation */
  readonly version = signal(0);
  readonly changed$: Observable<EntityKind> = this.changes.asObservable();

  invalidate(kind: EntityKind): void {
    this.version.update((v) => v + 1);
    this.changes.next(kind);
    this.search.refresh();
  }
}

import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { ApiError, toApiError } from '../api/api-error';
import { ApiService } from '../api/api.service';
import { ConfigResponse, Feature, Features } from '../api/models';

/**
 * `/api/config`, loaded once at startup (FR-14). Exposes the demo flag and feature
 * flags to the rest of the app. If the api is unreachable the app still boots: the
 * flags are unknown (`config()` is null) and `error()` explains why.
 */
@Injectable({ providedIn: 'root' })
export class ConfigService {
  private readonly api = inject(ApiService);

  readonly config = signal<ConfigResponse | null>(null);
  readonly error = signal<ApiError | null>(null);
  readonly loaded = signal(false);

  readonly demoMode = computed(() => this.config()?.demo_mode === true);

  async load(): Promise<void> {
    try {
      this.config.set(await firstValueFrom(this.api.config()));
      this.error.set(null);
    } catch (e) {
      this.error.set(toApiError(e));
    } finally {
      this.loaded.set(true);
    }
  }

  /** A feature flag. Unknown (config not loaded) counts as enabled with no reason. */
  feature(name: keyof Features): Feature {
    return this.config()?.features[name] ?? { enabled: true, reason: null };
  }
}

import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService, connectTarget } from './auth.service';
import { ConfigService } from './config.service';

/**
 * §4 steps 3-4: on the first run in Drive mode (not connected, or connected with no
 * folder) the app opens `/connect` or `/connect/folder`. A returning run (connected
 * with a folder) is not prompted, and neither is demo mode. The redirect happens at
 * most once per app session, and never blocks the app when the status is unavailable.
 */
export const connectionGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const config = inject(ConfigService);
  const router = inject(Router);
  if (auth.prompted) return true;
  const cfg = config.config();
  if (cfg && (cfg.demo_mode || !cfg.features.drive.enabled)) {
    auth.prompted = true;
    return true;
  }
  const status = await auth.ensureLoaded();
  auth.prompted = true;
  const target = connectTarget(status);
  return target ? router.parseUrl(target) : true;
};

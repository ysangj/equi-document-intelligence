import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { routes } from './app.routes';
import { ConfigService } from './core/config.service';
import { mockApiInterceptor } from './mock/mock-api.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    // The mock interceptor is a pass-through unless the dev mock flag is on (core/mock-flag.ts).
    provideHttpClient(withInterceptors([mockApiInterceptor])),
    // FR-14: load /api/config before the first render. Never blocks boot on failure.
    provideAppInitializer(() => inject(ConfigService).load()),
  ],
};

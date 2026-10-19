/**
 * Default environment: talks to the real api through the dev proxy.
 * The mock layer is OFF unless `mock` is true (see `environment.mock.ts`, used by
 * `npm run start:mock`) or the page is opened with `?mock=1` in a non-production build.
 */
export const environment = {
  production: false,
  mock: false,
};

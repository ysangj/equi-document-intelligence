# web: Angular UI

Angular 21 (LTS) + Angular Material, Vitest unit tests (jsdom, headless).

| Command | What it does |
|---|---|
| `npm ci` | Install from `package-lock.json` (start.sh does this when the lock changes) |
| `npm run start` | Dev server on **:4200**, proxy `/api` -> `http://localhost:8000` (`proxy.conf.json`). Used by `start.sh`. Talks to the real api. |
| `npm run start:4201` | Same, on :4201 |
| `npm run start:mock` | Dev server on :4201 with the **mock api** always on (no backend needed) |
| `npm run build` | Production build to `web/dist/` (mock flag is ignored in production builds) |
| `npm test` | Unit tests, headless, single run |

## Mock api (dev only, OFF by default)

`src/app/mock/` answers `/api/...` in the browser from fixture data based on
`sample_docs/MANIFEST.md` and the API Contract examples. It turns on when:

- the app is served with `npm run start:mock` (`environment.mock.ts`), or
- a dev build is opened with `?mock=1` (remembered for the tab; `?mock=0` turns it off).

Mock-only knobs: `?demo=0` reports drive mode (no demo banner), `?empty=1` starts with
no processed documents. The simulated worker finishes one document per `/api/progress`
poll, so tables fill in progressively.

## Layout

- `src/app/api/`: `models.ts` (every contract type), `api.service.ts`, query/param helpers, error parsing (C2)
- `src/app/core/`: config (FR-14), progress polling (C9), notifications, CSV export (C5), mock flag
- `src/app/shell/`: header, demo banner, progress indicator
- `src/app/pages/data/`: Data page, tables, filters, provenance side panel
- `src/app/pages/placeholders/`: Ask / Ingestion / Review / Search / Connect placeholders (U2-U4)

# PRD — Fund Document Intelligence (Take-Home Option A)

## 1. Overview

A locally-run web application that connects to a user's Google Drive via OAuth, watches a designated folder for financial documents (fund factsheets, account statements, performance reports — PDF, HTML email, CSV), extracts structured data with Claude, stores it in a local Postgres database (with pgvector), and lets the user browse, search, and ask questions across everything ingested — both structured ("Which fund had the best January return?") and qualitative ("What did Manager X say about rate risk?").

**Core promise:** connect Drive, point at a folder of messy documents from different managers, and immediately see a normalized table of performance and statement data. New files dropped in the folder are processed automatically. No per-format parser configuration.

## 2. Deployment Model & Principles

| Principle | Meaning |
|---|---|
| **Local-only** | Everything runs on the user's machine via `./start.sh`: Postgres in Docker, `api` and `worker` in one shared Python virtual environment (`.venv`), `web` via the Angular dev server. No hosted backend. |
| **Stateless processes** | `api` and `worker` hold no in-memory state between requests/iterations. All durable state (tokens, sync cursor, documents, extractions, chunks, embeddings) lives in the local Postgres. Any process can restart at any time without data loss or duplicate processing. Correctness never depends on startup order. |
| **Secrets never in git** | `.env` holds all secrets and is always git-ignored. Only `.env.example` (no real values) is committed. |
| **Bring your own keys** | The user supplies their own Anthropic API key and their own Google Cloud OAuth client. No secrets ship with the repo. Embeddings run locally — no second API key. |
| **LLM for ambiguity, code for truth** | Claude handles layout/terminology variance, document classification, and answer synthesis. Deterministic code handles normalization, validation, conflict detection, search ranking, and query safety. |
| **Every value and answer is traceable** | Each extracted value links to source document, page, and verbatim snippet. Each answer cites the SQL + rows or document passages it used. |

## 3. Scope

### In scope
- Google Drive OAuth (single user, read-only scope)
- Folder selection; non-recursive watch of that folder
- Catch-up reconcile on boot + incremental polling sync
- Concurrent extraction from PDF, HTML, CSV via Anthropic API
- Document classification: factsheet, account statement, performance report, email, other
- Normalization, validation, fund-name alias resolution, conflict flagging
- Local Postgres 16 + pgvector, migrations via Alembic
- Hybrid document retrieval: Postgres full-text search + pgvector (local embeddings)
- **Search:** instant keyword search (no LLM)
- **Ask:** Claude tool-use loop over SQL and document retrieval, with citations
- Angular UI: connect, folder picker, ingestion status, data tables, search, ask, review queue
- Demo mode (local folder + fixture extractions, no keys required for browsing)
- Unit and integration tests for deterministic components

### Out of scope
- Multi-user accounts / app-level authentication
- Hosted deployment, CI/CD, Kubernetes
- Drive push notifications (webhooks)
- Recursive subfolder scanning
- Google OAuth app verification (app stays in Testing mode)
- Write access to Drive
- Currency conversion across funds

## 4. User Flow

1. User clones repo, copies `.env.example` → `.env`, fills in Anthropic key and Google OAuth client credentials (or runs `scripts/gen_keys.py` for local secrets). When building with agents, the agent asks for the keys and writes `.env` itself (FR-0).
2. User runs `./start.sh` (macOS: double-click `start.command`). Script creates or activates `.venv`, installs requirements if needed, starts Postgres, runs migrations, starts `api`, `worker` and `web`, waits for health, opens `http://localhost:4200`.
3. **First run:** user clicks **Connect Google Drive**, completes Google consent, selects a folder.
4. **Returning run:** stored token is reused; worker immediately runs catch-up reconcile.
5. Worker processes files concurrently; the Data table fills in progressively with a progress indicator ("14 / 20 processed").
6. User browses **Funds & Returns** and **Statements** tables, filters, clicks any value to see its source.
7. User types in **Search** for instant keyword matches across funds, documents, and text.
8. User uses **Ask** for questions: structured, qualitative, or both. Answer shows citations (SQL + rows, and/or document + page passages).
9. User drops a new file in the Drive folder; within ~1 sync interval it's processed.
10. Low-confidence or conflicting values appear in **Review** for confirmation.

## 5. Architecture

```
┌──────────┐     ┌──────────┐     ┌─────────────────────┐     ┌──────────┐
│   web    │────►│   api    │────►│ postgres + pgvector │◄────│  worker  │
│ Angular  │     │ FastAPI  │     └─────────────────────┘     │  Python  │
└──────────┘     └────┬─────┘                                 └────┬─────┘
                      │           ┌─────────────────────┐          │
                      └──────────►│    Anthropic API    │◄─────────┤
                                  └─────────────────────┘          │
                                  ┌─────────────────────┐          │
                                  │  Google Drive API   │◄─────────┘
                                  └─────────────────────┘
```

- **web** calls **api**; **api** reads Postgres and calls Claude for Ask, and calls Google for the OAuth token exchange and Drive folder listing (FR-3).
- **worker** polls Drive, sends each file to Claude for extraction, and writes results, chunks, and embeddings to Postgres.
- Everything except the two external APIs runs locally: Postgres in Docker; `api` and `worker` as host processes from the shared `.venv`; `web` via the Angular dev server.

### Runtime processes

| Process | Runs in | Command (started by `start.sh`) | Port |
|---|---|---|---|
| `postgres` | Docker (`pgvector/pgvector:pg16`, via `docker-compose.yml`) | `docker compose up -d postgres` | 5432 |
| `api` | Shared `.venv` | `alembic upgrade head && uvicorn app.main:app --port 8000` | 8000 |
| `worker` | Shared `.venv` (same as `api`) | `python -m app.worker` | — |
| `web` | Node (Angular dev server) with proxy `/api` → `localhost:8000` | `npm run start` | 4200 |

- One database. pgvector is an extension inside Postgres, not a separate store. Relational tables, full-text indexes, and vector indexes coexist and can be joined.
- `api` and `worker` share one codebase, one `requirements.txt`, and one virtual environment at `./.venv`; they differ only by entrypoint.
- `worker` waits for migrations (polls `alembic_version`) before starting.
- Embedding model weights are downloaded on first run into a local cache directory (`./.cache/fastembed`, git-ignored) and reused afterwards.
- Prerequisites on the host: Python 3.12, Node.js LTS, Docker.

### Tech stack
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, `anthropic`, `google-auth-oauthlib`, `google-api-python-client`, `cryptography` (Fernet), `sqlglot`, `rapidfuzz`, `pgvector` (SQLAlchemy type), `fastembed` (ONNX, `BAAI/bge-small-en-v1.5`, 384-dim), PyMuPDF (snippet verification only), pytest
- **Frontend:** Angular (latest LTS), TypeScript, Angular Material, HttpClient
- **DB:** Postgres 16 + pgvector (Docker container, named volume `pgdata`)

## 6. Functional Requirements

### FR-0 Secrets & Repository Hygiene
- **Agent-assisted key setup.** When agents begin implementation, the first step is secrets setup: the agent asks the user to paste each required value (`ANTHROPIC_API_KEY`, then `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) directly into the prompt. The agent writes them into `.env` (creating it from `.env.example` if missing), generates `FERNET_KEY` and `SESSION_SECRET` itself, and confirms by key name only. It never echoes the values back, prints `.env`, or writes secrets to any other file, log, test, or commit.
- **`.env` is never in git.** `.gitignore` must contain `.env`, `.env.*`, and `!.env.example`. Before writing `.env`, the agent verifies `git check-ignore .env` succeeds; if `.env` is already tracked, it is removed from the index (`git rm --cached .env`) and the user is told.
- Also git-ignored: `.venv/`, `.cache/`, `node_modules/`, `web/dist/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.run/`.
- `.env.example` contains every variable from §11 with empty or placeholder values only.

### FR-1 Configuration & Startup
- `.env.example` documents all variables (see §11).
- `scripts/gen_keys.py` generates `FERNET_KEY` and `SESSION_SECRET`.
- `start.sh` / `start.command`:
  1. Verifies Docker is running, Python 3.12 and Node are available, and `.env` exists; prints missing variables.
  2. **Virtual environment:** if `./.venv` does not exist, creates it (`python3.12 -m venv .venv`) and installs `requirements.txt`. If it exists, activates it and re-installs only when `requirements.txt` has changed (hash stored in `.venv/.requirements.sha256`).
  3. Installs UI dependencies with `npm ci` if `web/node_modules` is missing or `package-lock.json` changed.
  4. `docker compose up -d postgres` and waits until Postgres accepts connections.
  5. Starts `api` (runs migrations first) and `worker` from the same activated `.venv`, and `web`, as background processes; writes PIDs to `.run/` and logs to `.run/*.log` (git-ignored).
  6. Waits for `GET /api/health` to return OK.
  7. Opens the browser to `http://localhost:4200`.
- `stop.sh` stops `api`, `worker` and `web` using the PIDs in `.run/`, then runs `docker compose stop postgres` (data persists in the volume).
- Services fail fast with a clear message if required variables are missing, **except** when demo mode applies (FR-14).

### FR-2 Google OAuth
- `GET /api/auth/google/login` builds the authorization URL with scope `drive.readonly`, `access_type=offline`, `prompt=consent`; stores `state` in a signed session cookie; redirects to Google.
- `GET /api/auth/google/callback` validates `state`, exchanges code for tokens, Fernet-encrypts the refresh token, persists it in `oauth_credentials`, redirects to the UI folder picker.
- `GET /api/auth/status` returns connected / not connected and Google account email.
- `POST /api/auth/disconnect` deletes stored credentials and sync state.
- Tokens are never held in process memory beyond a single request/iteration.

### FR-3 Folder Selection
- `GET /api/drive/folders?parent=` lists folders for browsing.
- `POST /api/drive/watch` stores the selected `folder_id` and triggers a reconcile.
- Watch is **non-recursive**: only files directly in the selected folder.

### FR-4 Sync (worker)
- **On every worker boot, and whenever a watched folder is set:** catch-up reconcile.
  1. List all files in the watched folder (`files.list`, paginated).
  2. Compare against `documents` by `(drive_file_id, md5_checksum)`.
  3. Enqueue new or changed files as `pending`; mark files no longer present as `removed`.
  4. Record a fresh `startPageToken`.
- **Then, every `SYNC_INTERVAL_SECONDS` (default 30):** incremental sync via `changes.list` from stored page token, filtered to the watched folder. Same upsert rules.
- Page token and document upserts committed in one transaction.
- If no credentials exist, worker idles and re-checks each interval.
- Supported MIME types: `application/pdf`, `text/html`, `message/rfc822` (HTML body), `text/csv`, Google Sheets exported as CSV. Others recorded as `unsupported`.
- `POST /api/sync` triggers an immediate incremental sync; `POST /api/sync/reconcile` forces a full reconcile.

### FR-5 Extraction (worker)
- Worker runs `WORKER_CONCURRENCY` (default 4) extraction tasks in parallel.
- Each task claims one job with `SELECT … FOR UPDATE SKIP LOCKED` (safe for restarts and multiple workers).
- File bytes are downloaded per job and not persisted; extracted text is persisted for snippet verification, search, and display.
- PDFs sent to Claude as native document blocks; HTML and CSV sent as text.
- **Single Claude call per document** returns structured output conforming to the canonical schema (§9):
  - `document_type` classification
  - Type-specific structured fields (fund performance and/or account statement)
  - Per-page transcribed text (used for chunking when the PDF has no text layer)
  - Every field includes `page`, verbatim `source_snippet`, and `confidence` (0–1)
- Stored per extraction: raw JSON, model, prompt version, input/output tokens, latency.
- Retry on Anthropic 429/5xx and Drive transient errors with exponential backoff; max `MAX_ATTEMPTS` (default 3), then `failed` with `last_error`.
- Behind an interface `extract(content: bytes, mime: str) -> Extraction` for mocking.

### FR-6 Normalization & Validation
- **Normalize:** percentages (`1.2%`, `0.012`, `120bps`, `(0.8)` → −0.8%), dates (`Jan-26`, `01/31/2026`, `January 2026` → period end date), currency codes, amounts (`1.2mm`, `$1,200,000`).
- **Fund resolution:** extracted fund name → canonical `funds` row via `fund_aliases`. Exact alias match → link. Fuzzy match (rapidfuzz ≥ threshold) → link + flag for confirmation. No match → new fund.
- **Account resolution (statements):** same approach for `accounts` via account name / masked number.
- **Validation rules** (failures → `needs_review`, never silently dropped):
  - Monthly return within ±50%
  - Monthly returns for a year compound to stated YTD within tolerance (e.g. 5 bps)
  - Statement roll-forward: opening balance + contributions − withdrawals + P&L ≈ closing balance (when all present)
  - `as_of_date` not in the future
  - `source_snippet` appears in the document's text (skipped for image-only pages)
  - Field confidence below `REVIEW_CONFIDENCE_THRESHOLD`
- **Conflicts:** if a value for the same natural key already exists from another document and differs beyond tolerance, both are retained and flagged `conflict`. No overwrite.

### FR-7 Chunking & Embedding (worker)
- After extraction, document text is split into chunks by page, then by paragraph, targeting ~500 tokens with ~50 token overlap. CSVs are chunked by row groups with header repeated.
- Each chunk stores: `document_id`, `page`, `text`, `tsv` (generated `tsvector`), `embedding` (384-dim, `fastembed` / `bge-small-en-v1.5`, computed locally).
- Re-embedding is triggered only when a document version changes.
- Chunk/embedding failure does not fail structured extraction; document is marked `done` with `search_indexed=false` and retried.

### FR-8 Storage
- Schema managed by Alembic; first migration runs `CREATE EXTENSION IF NOT EXISTS vector;`.
- Indexes: GIN on `document_chunks.tsv`, HNSW (`vector_cosine_ops`) on `document_chunks.embedding`, GIN on `documents.name` trigram, B-tree on natural keys.
- Read-only Postgres role `app_readonly` with `SELECT` on query views only (§8).

### FR-9 Data Browsing (UI)
- **Funds & Returns** table: fund, manager, strategy, period, return type, net/gross, value, as-of date, source document.
- **Statements** table: account, custodian/manager, as-of date, opening balance, contributions, withdrawals, P&L, closing balance / NAV, currency, source document.
- Filters: fund / account (multi-select), date range, document type, status (ok / needs_review / conflict).
- Sort by any column. Export current view to CSV.
- Clicking a value opens a side panel: document name, Drive link, page, snippet, confidence, extraction model.
- Progress indicator while documents are processing; rows appear as documents complete.

### FR-10 Search (keyword, no LLM)
- Single search box, results as you type (debounced 250ms).
- `GET /api/search?q=` searches, in parallel:
  - Funds and aliases (trigram similarity)
  - Accounts
  - Document names
  - Document chunks (Postgres FTS, `websearch_to_tsquery`, ranked by `ts_rank_cd`)
- Results grouped by type; chunk results show highlighted snippet (`ts_headline`) with document + page link.
- Deterministic, instant, zero API cost.

### FR-11 Ask (Claude tool-use)
- `POST /api/ask { question, history? }` runs a Claude tool-use loop (max `ASK_MAX_STEPS`, default 6) with two tools:

| Tool | Input | Behavior |
|---|---|---|
| `query_sql` | `sql: string` | Parsed with sqlglot; must be single `SELECT`; only allowed views referenced; `LIMIT` enforced (default 200); executed as `app_readonly` with 5s statement timeout. Returns rows or a guard error. |
| `search_documents` | `query: string, k?: int, document_type?: string, fund?: string` | Hybrid retrieval: FTS top-N + vector top-N over `document_chunks`, merged with Reciprocal Rank Fusion (k=60), optional filters, returns top-k passages with `chunk_id`, document, page. |

- System prompt provides documented view schemas and instructs Claude to:
  - Use `query_sql` for numbers, rankings, comparisons, aggregations
  - Use `search_documents` for commentary, explanations, terms, qualitative content
  - Use both when needed (e.g. "Which fund had the worst March and what did the manager say about it?")
  - Never state a number that didn't come from a tool result
- Response: `answer` (markdown), `citations[]` (each either `{type: sql, sql, rows}` or `{type: passage, chunk_id, document_id, page, text}`), `steps[]` (tool calls for transparency).
- **Citation check:** numbers in the answer are checked against values in returned SQL rows / passages; unmatched numbers are flagged in the UI.
- UI renders the answer with clickable citations and an expandable "How I got this" panel showing tool calls.
- Disabled with explanatory message when no `ANTHROPIC_API_KEY`.

### FR-12 Ingestion Status (UI)
- Lists documents: filename, detected type, status, attempts, last error, processed time, token usage, search-indexed flag.
- Header: connected account, watched folder, last successful sync, counts by status, overall progress.
- Auto-refresh every 3s while any document is `pending`/`processing`, otherwise every 15s.
- Actions: **Reprocess** (single document), **Sync now**, **Full reconcile**.

### FR-13 Review Queue (UI)
- Lists items flagged `needs_review` or `conflict`, with reason (low confidence, YTD mismatch, roll-forward mismatch, alias match, conflict).
- Actions: accept value, edit value, choose between conflicting values, confirm/reject fund or account alias.
- Decisions stored in `review_actions`; they persist across reprocessing.

### FR-14 Demo Mode
- Triggered when `GOOGLE_CLIENT_ID` is unset or `DEMO_MODE=true`.
- Worker watches the local `./sample_docs` folder instead of Drive; same reconcile + polling semantics.
- If `ANTHROPIC_API_KEY` is also unset, worker loads committed fixture extractions from `./fixtures/`. Browsing, Search, and Review fully work (embeddings are local). Ask is disabled with an explanatory message.
- UI shows a "Demo mode" banner.

## 7. Data Model

| Table | Key columns |
|---|---|
| `oauth_credentials` | `id`, `google_email`, `refresh_token_encrypted`, `scopes`, `created_at` |
| `sync_state` | `id`, `folder_id`, `folder_name`, `page_token`, `last_reconciled_at`, `last_synced_at`, `last_error` |
| `documents` | `id`, `drive_file_id`, `name`, `mime_type`, `md5_checksum`, `version`, `document_type`, `status` (pending / processing / done / failed / needs_review / unsupported / removed), `search_indexed`, `attempts`, `last_error`, `text_content`, `drive_modified_at`, `processed_at` |
| `extractions` | `id`, `document_id`, `model`, `prompt_version`, `raw_json`, `input_tokens`, `output_tokens`, `latency_ms`, `created_at` |
| `funds` | `id`, `canonical_name`, `manager`, `strategy`, `currency` |
| `fund_aliases` | `id`, `fund_id`, `alias`, `confirmed` |
| `returns` | `id`, `fund_id`, `document_id`, `period_end`, `return_type` (MTD / QTD / YTD / ITD / annualized), `net_gross`, `value`, `page`, `source_snippet`, `confidence`, `status` |
| `fund_facts` | `id`, `fund_id`, `document_id`, `name` (aum, sharpe, max_drawdown, vol, …), `value`, `unit`, `as_of_date`, `page`, `source_snippet`, `confidence`, `status` |
| `accounts` | `id`, `canonical_name`, `account_number_masked`, `custodian`, `currency` |
| `account_aliases` | `id`, `account_id`, `alias`, `confirmed` |
| `statements` | `id`, `account_id`, `fund_id` (nullable), `document_id`, `period_start`, `period_end`, `opening_balance`, `contributions`, `withdrawals`, `pnl`, `fees`, `closing_balance`, `currency`, `provenance` (JSON: per-field page/snippet/confidence), `status` |
| `statement_holdings` | `id`, `statement_id`, `name`, `identifier`, `quantity`, `market_value`, `weight`, `page`, `source_snippet`, `confidence` (optional; populated when present) |
| `document_chunks` | `id`, `document_id`, `page`, `chunk_index`, `text`, `tsv tsvector` (generated), `embedding vector(384)` |
| `review_actions` | `id`, `target_table`, `target_id`, `action`, `old_value`, `new_value`, `created_at` |

**Query views (only objects exposed to `query_sql`):**
- `v_returns` — returns + fund + manager + strategy + document name/type
- `v_fund_facts` — fund facts + fund + document
- `v_statements` — statements + account + optional fund + document
- `v_holdings` — holdings + statement + account
- `v_funds`, `v_accounts`, `v_documents`

Views exclude `removed` documents by default and expose `status` so Claude can filter or mention flagged data.

## 8. Retrieval Design

- **Why hybrid:** FTS is precise for exact terms (fund names, "NAV", months, identifiers); vectors capture concepts ("rate risk" ↔ "duration positioning", "Fed outlook").
- **Algorithm:**
  1. FTS: top 20 chunks by `ts_rank_cd` for `websearch_to_tsquery(query)`.
  2. Vector: top 20 chunks by cosine distance to `embed(query)`.
  3. Merge with RRF: `score = Σ 1 / (60 + rank_i)`.
  4. Apply optional filters (document type, fund via join), return top k (default 8).
- **Embedding model:** `BAAI/bge-small-en-v1.5` via `fastembed` (ONNX, CPU, no PyTorch), 384-dim, cosine. Same model used for chunks and queries; model name stored with chunks so a model change triggers re-embedding.
- Search (FR-10) uses FTS only; Ask (FR-11) uses hybrid.

## 9. Canonical Extraction Schema

```python
class SourcedValue(BaseModel):
    value: str | float | None
    page: int | None
    source_snippet: str | None
    confidence: float

class ReturnEntry(BaseModel):
    period: str
    value: SourcedValue
    return_type: Literal["MTD", "QTD", "YTD", "ITD", "annualized", "unknown"]
    net_gross: Literal["net", "gross", "unknown"]

class FundExtraction(BaseModel):
    fund_name: SourcedValue
    manager: SourcedValue | None
    strategy: SourcedValue | None
    currency: SourcedValue | None
    as_of_date: SourcedValue | None
    aum: SourcedValue | None
    returns: list[ReturnEntry]
    other_metrics: dict[str, SourcedValue]

class Holding(BaseModel):
    name: SourcedValue
    identifier: SourcedValue | None
    quantity: SourcedValue | None
    market_value: SourcedValue | None
    weight: SourcedValue | None

class StatementExtraction(BaseModel):
    account_name: SourcedValue
    account_number_masked: SourcedValue | None
    custodian_or_manager: SourcedValue | None
    fund_name: SourcedValue | None
    currency: SourcedValue | None
    period_start: SourcedValue | None
    period_end: SourcedValue
    opening_balance: SourcedValue | None
    contributions: SourcedValue | None
    withdrawals: SourcedValue | None
    pnl: SourcedValue | None
    fees: SourcedValue | None
    closing_balance: SourcedValue | None
    holdings: list[Holding]

class PageText(BaseModel):
    page: int
    text: str

class Extraction(BaseModel):
    document_type: Literal["factsheet", "account_statement", "performance_report", "email", "other"]
    document_type_confidence: float
    funds: list[FundExtraction]            # factsheets, reports, emails
    statements: list[StatementExtraction]  # account statements
    pages: list[PageText]                  # transcription for chunking
    extra: dict
```

- A document may populate both `funds` and `statements` (e.g. a statement that also reports fund performance).
- `PROMPT_VERSION` constant; changes bump the version and allow reprocessing.

## 10. API Summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + DB check |
| GET | `/api/config` | Demo mode flag, enabled features |
| GET | `/api/auth/google/login` | Start OAuth |
| GET | `/api/auth/google/callback` | Complete OAuth |
| GET | `/api/auth/status` | Connection status |
| POST | `/api/auth/disconnect` | Remove credentials |
| GET | `/api/drive/folders` | Browse folders |
| POST | `/api/drive/watch` | Set watched folder (triggers reconcile) |
| POST | `/api/sync` | Immediate incremental sync |
| POST | `/api/sync/reconcile` | Full reconcile |
| GET | `/api/progress` | Counts by status for progress bar |
| GET | `/api/documents` | Ingestion status list |
| POST | `/api/documents/{id}/reprocess` | Re-queue |
| GET | `/api/returns` | Filtered returns |
| GET | `/api/fund-facts` | Filtered fund facts |
| GET | `/api/statements` | Filtered statements |
| GET | `/api/statements/{id}/holdings` | Holdings for a statement |
| GET | `/api/funds` | Funds + aliases |
| GET | `/api/accounts` | Accounts + aliases |
| GET | `/api/sources/{table}/{id}` | Provenance for a value |
| GET | `/api/search?q=` | Keyword search (FR-10) |
| POST | `/api/ask` | Claude tool-use answer with citations (FR-11) |
| GET | `/api/review` | Review queue |
| POST | `/api/review/{id}` | Apply review action |

## 11. Configuration (`.env`)

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (unless fixture demo) | User's own key |
| `CLAUDE_MODEL` | No | Default: a mid-tier Claude model |
| `GOOGLE_CLIENT_ID` | Yes (unless demo mode) | User's own Cloud project |
| `GOOGLE_CLIENT_SECRET` | Yes (unless demo mode) | |
| `GOOGLE_REDIRECT_URI` | Yes (unless demo mode) | `http://localhost:8000/api/auth/google/callback`; must match Cloud console exactly |
| `OAUTHLIB_INSECURE_TRANSPORT` | Dev | `1` for localhost HTTP |
| `DATABASE_URL` | Yes | Default `postgresql+psycopg://app:app@localhost:5432/docintel` |
| `DATABASE_READONLY_URL` | Yes | `app_readonly` role |
| `FERNET_KEY` | Yes | Encrypts refresh token |
| `SESSION_SECRET` | Yes | Signs OAuth state cookie |
| `SYNC_INTERVAL_SECONDS` | No | Default 30 |
| `WORKER_CONCURRENCY` | No | Default 4 |
| `MAX_ATTEMPTS` | No | Default 3 |
| `REVIEW_CONFIDENCE_THRESHOLD` | No | Default 0.7 |
| `EMBEDDING_MODEL` | No | Default `BAAI/bge-small-en-v1.5` |
| `ASK_MAX_STEPS` | No | Default 6 |
| `DEMO_MODE` | No | Force demo mode |

## 12. Non-Functional Requirements

- **Security:** secrets only in `.env`, which is always git-ignored and never committed (FR-0); refresh token encrypted at rest; `drive.readonly` scope only; `query_sql` executes under read-only role with view allowlist and timeout; document contents and API keys never logged.
- **Reliability:** idempotent ingestion keyed on `(drive_file_id, md5_checksum)`; reconcile on every boot; page token and upserts committed atomically; worker survives individual job failures; restart of any container causes no duplicates or lost files.
- **Observability:** structured JSON logs (job id, document id, status, latency, tokens); per-document token usage visible in UI; Ask responses expose tool-call steps.
- **Performance targets (local):**
  - New file detected ≤ 1 sync interval
  - Single factsheet extracted in < 30s
  - 20 documents fully processed in < 3 min at default concurrency
  - Search results < 300ms
  - Ask answer < 15s typical
  - Table page loads < 1s for ≤ 10k rows

## 13. Sample Data

`sample_docs/` contains ~20 synthetic documents for ~6 fictional funds and ~3 accounts:
- 4–5 distinct factsheet PDF layouts with varied terminology ("Net Return", "Perf. (net)", "MTD %") and manager commentary paragraphs
- 3 account statement PDFs (different custodians/admins, one with holdings table)
- 1 image-only / scanned-style PDF
- 2 HTML email performance updates with qualitative commentary
- 2 CSV administrator exports
- Deliberate messiness: same fund under two names, one conflicting January figure, a YTD that doesn't reconcile, a statement whose roll-forward doesn't tie, a missing month, commentary that uses synonyms (e.g. "duration" vs "rate sensitivity") to demonstrate hybrid retrieval

`fixtures/` contains committed extraction outputs for every sample doc (demo mode).

## 14. Testing

- **Unit:** percentage/date/amount normalization; YTD compounding validator; statement roll-forward validator; fuzzy alias resolution; snippet verification; SQL guard (rejects DDL/DML, multiple statements, non-allowlisted objects; enforces LIMIT); RRF merge; chunker.
- **Integration:** pipeline end-to-end with mocked `extract()` against fixtures → asserts DB rows, conflict flags, review items, chunks + embeddings present.
- **Idempotency:** process the same document twice → no duplicate rows; changed checksum → new version.
- **Reconcile:** files added/removed while worker is down are picked up/marked on boot.
- **Ask:** mocked Claude responses exercising each tool and the guard-error path.
- **All Python tests run inside the shared virtual environment:** `source .venv/bin/activate && pytest` (or `.venv/bin/pytest`). `start.sh` creates the environment if it doesn't exist; `scripts/test.sh` does the same and runs the suite. Tests that need a database use the Docker Postgres with a separate test database.
- UI tests: `npm test` in `web/`.

## 15. Deliverables & Acceptance Criteria

### Deliverables
1. GitHub repo: code, `docker-compose.yml` (Postgres only), `requirements.txt`, `start.sh`, `start.command`, `stop.sh`, `scripts/test.sh`, `.env.example`, `.gitignore`, `sample_docs/`, `fixtures/`. `.env` is not in the repo.
2. README: quick start, Google Cloud setup (step-by-step), demo mode, architecture, design decisions, trade-offs, production next steps
3. 5-minute video walkthrough

### Acceptance criteria
- [ ] `./start.sh` on a clean clone (no `.venv`) creates the virtual environment, installs requirements, brings up Postgres, `api`, `worker` and `web`, and opens the browser
- [ ] Re-running `./start.sh` reuses the existing `.venv` without reinstalling
- [ ] `.env` is git-ignored (`git check-ignore .env` succeeds) and does not appear in git history
- [ ] Python tests pass when run from `.venv`
- [ ] Demo mode works with no keys (browse, filter, search, review)
- [ ] OAuth connect → folder pick → files ingested with no manual upload
- [ ] Data table fills progressively; 20 sample docs processed in < 3 min
- [ ] Files added while the app was stopped are processed on next start
- [ ] New file in Drive folder processed automatically within ~1 sync interval
- [ ] Unchanged re-add creates no duplicates; edited file creates a new version
- [ ] Account statements are classified correctly and appear in the Statements table
- [ ] Every value links to document, page, and snippet
- [ ] Alias match, conflict, YTD mismatch, and roll-forward mismatch from sample data appear in Review
- [ ] Search returns instant keyword results with highlighted snippets
- [ ] Ask: "Which fund had the best January return?" → correct answer via `query_sql`, SQL + rows cited
- [ ] Ask: "What did managers say about rate risk?" → answer via `search_documents`, passages cited, includes a synonym match
- [ ] Ask: mixed question uses both tools
- [ ] Non-SELECT SQL generated by Claude is rejected by the guard
- [ ] Restarting `worker` mid-processing causes no lost or duplicate documents

### Video outline (5 min)
1. Architecture & principles (45s)
2. `./start.sh`, connect Drive, pick folder, progressive ingestion (60s)
3. Funds & Statements tables, filters, click-through provenance (45s)
4. Search (15s)
5. Ask: structured, qualitative, and mixed question with citations and tool steps (60s)
6. Drop new file live → appears processed (20s)
7. Review queue: conflict + alias + roll-forward (25s)
8. Trade-offs & production next steps (30s)

## 16. Trade-offs & Future Work

| Decision | Rationale | Production alternative |
|---|---|---|
| Polling `changes.list` + boot reconcile | No public HTTPS endpoint needed locally; reconcile guarantees catch-up | Drive push notifications via webhook |
| Non-blocking startup (UI up immediately, progressive fill) | First run needs OAuth via UI; blocking would delay UI by minutes | Same |
| Single user, no app auth | Take-home scope | App auth + per-user credentials and tenancy |
| Claude native PDF input, one call per document | Removes per-format parsing stack; classification + extraction + transcription together | Page-level batching and cost controls for large docs |
| Postgres + pgvector in one database | One service; joins between chunks and structured data | Same, or managed vector store at scale |
| Local embeddings (fastembed) | No second API key; offline; fast on CPU | Hosted embeddings (e.g. Voyage) for higher quality |
| Hybrid FTS + vector with RRF | Exact terms and concepts both covered | Add reranker |
| Keyword Search separate from Ask | Instant and free for lookups; LLM only when reasoning is needed | Same |
| Postgres-backed job queue | No extra infra | Pub/Sub / Cloud Tasks |
| Non-recursive folder watch | Matches "designated folder"; simpler | Recursive with include/exclude rules |
| Fixtures for demo mode | Zero-setup evaluation | — |
| Local Postgres | Matches brief | Cloud SQL / BigQuery on GCP |
| `api`/`worker` in a shared `.venv`, Postgres in Docker | Fast local iteration and debugging; one environment for running and testing | Containerize `api`/`worker` for deployment |

## 17. Open Questions

- Net vs gross when unlabeled: store as `unknown` and flag, or infer from context? *(Default: `unknown` + review.)*
- Statement holdings depth: full holdings tables can be long; cap extracted holdings per statement? *(Default: extract all, paginate in UI.)*
- Should Ask include `needs_review` / `conflict` data by default? *(Default: yes, and Claude must mention the flag in the answer.)*

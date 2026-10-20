# Fund Document Intelligence

A local web app that connects to your Google Drive, watches one folder of fund documents (factsheets, account statements, performance reports, PDFs, HTML emails, CSVs), extracts structured data with Claude, stores it in Postgres + pgvector, and lets you browse, search and ask questions across everything, with every number traceable to its document, page and snippet.

- **Data**: normalized Funds & Returns, Fund facts and Statements tables. Click any value to see where it came from.
- **Search**: instant keyword search across funds, accounts, documents and text (no LLM).
- **Ask**: questions in plain English ("Which fund had the best January return?", "What did managers say about rate risk?"). Answers cite the SQL rows or document passages they used.
- **Review**: low-confidence values, conflicts, YTD and roll-forward mismatches, and fund-name matches, for you to confirm.
- New files dropped into the Drive folder are picked up automatically. No per-format parser configuration.

---

## Quick start (demo mode, no keys needed)

Requires **Docker Desktop**, **Python 3.12** and **Node.js LTS**.

```bash
git clone https://github.com/ysangj/equi-document-intelligence.git && cd equi-document-intelligence
cp .env.example .env      # leave every key empty
./start.sh                # macOS: or double-click start.command
```
Or run `./scripts/init_env.sh --demo` instead of the `cp`: it creates `.env` for demo mode and generates the local secrets.

The browser opens at **http://localhost:4200**. With no Google client ID set, the app runs in **demo mode**: it watches the local `./sample_docs` folder (21 synthetic documents) instead of Drive. With no Anthropic key it loads the committed Claude extractions in `./fixtures/`, so Data, Search and Review work fully offline. Ask is disabled until you add an Anthropic key.

Stop everything with `./stop.sh`. Your data persists in the Docker volume.

---

## Full setup (your Google Drive + Claude)

> 📄 **Prefer screenshots?** [`docs/setup-guide.pdf`](docs/setup-guide.pdf) walks through the Google Cloud and Anthropic console steps below with annotated screenshots (10 pages), plus the `init_env.sh` → `start.sh` → Connect flow and troubleshooting.

### 1. Prerequisites
| Tool | Version | Check |
|---|---|---|
| Docker Desktop | running | `docker info` |
| Python | 3.12 | `python3.12 --version` |
| Node.js | LTS | `node --version` |
| Anthropic API key | with credits | [console.anthropic.com](https://console.anthropic.com) (now opens the Claude Console at platform.claude.com) → API keys |
| Google account | any | used to own the Cloud project and the Drive folder |

> The Anthropic API is billed separately from a Claude.ai subscription. The key's account needs a credit balance (Console → **Settings → Billing**). Otherwise every call fails with "credit balance is too low".

### 2. Create the Google Cloud OAuth client
You create your own OAuth client; no Google credentials ship with this repo. The app stays in **Testing** mode, so no Google verification is needed.

> 📄 **Step-by-step with screenshots:** see [Part 1 of the setup guide (PDF)](docs/setup-guide.pdf) for an annotated screenshot of every step below.

1. **Create a project.** Go to [console.cloud.google.com](https://console.cloud.google.com), open the project picker, choose **New project** and name it (e.g. `doc-intelligence`).
2. **Enable the Drive API.** Go to **APIs & Services → Library**, search **Google Drive API**, then **Enable**.
   *Easy to miss: without it, sign-in succeeds but listing folders fails.*
3. **Configure Google Auth Platform** (left menu **Google Auth Platform**, formerly "OAuth consent screen"):
   - **Branding**: app name (e.g. *Fund Document Intelligence*), your support email and developer contact email.
   - **Audience**: user type **External**, publishing status **Testing**. Under **Test users**, click **Add users** and add the Google account whose Drive you'll connect. *Without this, Google rejects the sign-in with `access_denied`.*
   - **Data Access** (optional in Testing mode): add the scope `https://www.googleapis.com/auth/drive.readonly`. The app also requests `openid` and `userinfo.email`, only to show which account is connected.
4. **Create the OAuth client.** Go to **Google Auth Platform → Clients → Create client**:
   - Application type: **Web application**
   - Authorized redirect URIs: add **exactly** `http://localhost:8000/api/auth/google/callback`
     (no trailing slash, `localhost` not `127.0.0.1`; it must equal `GOOGLE_REDIRECT_URI` in `.env`)
   - **Create**. The dialog that follows shows the **Client ID** and **Client secret** and has a **Download JSON** button. **Download the JSON now** (or copy both values), because it's the only time Google offers it: an existing client's page no longer shows or downloads the secret. If you lose it, open the client and use **Add secret** to make a new one.

### 3. Fill in `.env`
**Recommended: run the setup script.** It prepares `.env` for you, and it never prints a secret value.
```bash
./scripts/init_env.sh
```
Have two things ready: your **Anthropic API key**, and the **client JSON** you downloaded in step 2.4 (from the dialog shown right after **Create**, usually named `client_secret_<id>.apps.googleusercontent.com.json`). If you only copied the ID and secret, choose "type the client ID and secret" instead. The script then:

1. **Checks git safety.** It confirms `.env` and its backups are git-ignored and that `.env` isn't tracked. If either check fails, it stops.
2. **Creates or updates `.env`.** A missing `.env` is created from `.env.example` (so is an empty one, or one with only comments). An existing one is updated in place, and a backup is saved to `.env.bak.<timestamp>` first, but only if something actually changes.
3. **Step 1/4, Anthropic key.** Paste the key; typing is hidden. A new key is checked with one call to Anthropic (`valid` / `rejected` / `could not check`). A valid key still needs credits. Press Enter to keep a key that's already set.
4. **Step 2/4, Google client.** Pick one:
   1. Path to the client JSON (drag the file into the terminal, or use Tab to complete the path)
   2. Type the client ID and secret (the secret is hidden)
   3. Skip, which gives demo mode with no Google sign-in
   4. Keep the current client (the default when one is set)

   From the JSON it reads `client_id` and `client_secret`, and checks that `redirect_uris` contains exactly `http://localhost:8000/api/auth/google/callback`. It warns, and shows the console fix, if the redirect URI is wrong or the client isn't a **Web application** client.
5. **Step 3/4, local secrets.** It generates `FERNET_KEY` and `SESSION_SECRET` if they're empty. An existing `FERNET_KEY` is **kept**, because changing it disconnects Google Drive (see below).
6. **Step 4/4, Postgres port.** If the configured Postgres port (`POSTGRES_PORT`, default 5432) is taken by another program, it offers a free port (e.g. 55432) and updates `POSTGRES_PORT` and both database URLs to match.
7. **Saves and summarizes.** It writes `.env` with owner-only permissions (`600`) and prints a table of key names and their status (`set`, `kept`, `generated`, `empty (demo)` or `missing`), never the values. It also shows the mode it detected (Drive, demo with fixtures, or demo with Claude) and the next step: `./start.sh`.

**Non-interactive** (for scripted demos or CI). Secrets are passed as **file paths**, never as values on the command line, so they don't show up in `ps` or shell history:
```bash
# Drive mode: key saved in a file, plus Google's downloaded client JSON
./scripts/init_env.sh --yes \
    --anthropic-key-file ~/secrets/anthropic_key.txt \
    --google-client-json ~/Downloads/client_secret_<id>.apps.googleusercontent.com.json

# Demo mode: no keys at all
./scripts/init_env.sh --demo --yes
```
| Option | Effect |
|---|---|
| `--anthropic-key-file PATH` | Read the Anthropic key from a file (or set `ANTHROPIC_API_KEY` in the environment) |
| `--google-client-json PATH` | Read the client ID and secret from Google's downloaded JSON |
| `--demo` | Skip Google (demo mode). On an existing `.env` it clears `GOOGLE_CLIENT_ID`/`SECRET`; the backup keeps the old values |
| `--yes` | Accept every default without asking; implied when stdin isn't a terminal |
| `--no-validate` | Don't call Anthropic to check the key |
| `--rotate-keys` | Generate a new `FERNET_KEY` and `SESSION_SECRET`. **This disconnects Google Drive**; you'll need to connect again. |
| `--reset` | Start fresh: replace `.env` from `.env.example` and ask for every key again (see below) |
| `--help` | Show all options |

**Re-running it is safe.** Pressing Enter at every prompt changes nothing and makes no backup. Only the values you enter are replaced. Avoid `--demo` and `--rotate-keys` on a working setup unless you mean to clear Google or reconnect Drive. The previous values are kept in `.env.bak.<timestamp>`, which is git-ignored and readable only by you.

**Start fresh.** `./scripts/init_env.sh --reset` replaces `.env` with a fresh copy of `.env.example` and asks for the Anthropic key and the Google client again. There's no "keep current" option, and pressing Enter leaves a value empty. It asks you to confirm first (`--yes` skips this) and backs up the old file to `.env.bak.<timestamp>`. It **keeps `FERNET_KEY` by default** (it asks: *Keep your existing FERNET_KEY so your Google Drive connection keeps working? [Y/n]*), so Drive stays connected. Answer `n`, or pass `--rotate-keys`, to get new keys; then you'll click **Connect Google Drive** again, and your data is kept. A non-default `POSTGRES_PORT` and the database URLs are carried over too. With `--reset --yes`, values come only from `--anthropic-key-file`, `ANTHROPIC_API_KEY` and `--google-client-json`, and anything not given is left empty (demo mode).

**Or edit `.env` by hand:**
```bash
cp .env.example .env            # if you haven't already
python3.12 scripts/gen_keys.py  # prints FERNET_KEY and SESSION_SECRET
```
Edit `.env` and set:
| Variable | Where the value comes from | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com) → **API keys** (the account needs credits) | Extraction (worker) and Ask (api) |
| `GOOGLE_CLIENT_ID` | Google Cloud → **Google Auth Platform → Clients** → your Web application client (step 2.4) | Starting the OAuth sign-in |
| `GOOGLE_CLIENT_SECRET` | The same client, shown when you create it (download or copy it then) | Exchanging Google's code for tokens |
| `FERNET_KEY` | Generated by `gen_keys.py` (random 32-byte key) | Encrypting the Google refresh token stored in Postgres |
| `SESSION_SECRET` | Generated by `gen_keys.py` (random) | Signing the short-lived OAuth `state` cookie |

These are already filled in by `.env.example` and normally stay as they are: `GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback` (must match the redirect URI on your OAuth client) and `OAUTHLIB_INSECURE_TRANSPORT=1` (allows OAuth over `http://localhost`). Leave `DEMO_MODE` empty.

**What goes in `.env` vs. the database.** `.env` holds only what the app needs to *start* OAuth. What OAuth *returns* (your Google refresh token, account email and granted scopes) is saved when you click **Connect Google Drive**, Fernet-encrypted, in the Postgres table `oauth_credentials`. The app never writes to `.env`.

> **Keep `FERNET_KEY` stable.** The stored refresh token is encrypted with it. If you change or lose it, the app can't decrypt the token and you'll need to connect Google Drive again.

`.env` is git-ignored. Never commit it, and never paste it anywhere (only `.env.example` is committed).

> **Port 5432 already in use?** Set `POSTGRES_PORT=55432` (or any free port) and change the port in both `DATABASE_URL` and `DATABASE_READONLY_URL` to match. `start.sh` tells you when the port is taken.

### 4. Prepare the Drive folder
In Google Drive, create a folder in **My Drive** (e.g. `Fund Docs`) and upload your documents **directly into it**. To try it with the sample set, upload the 21 files from `sample_docs/` (`MANIFEST.md` isn't needed).

- Only files directly in the folder are watched; subfolders are ignored.
- Supported: PDF (including scanned/image-only), HTML, `.eml` (HTML body), CSV, and Google Sheets (exported as CSV). Other types are listed as `unsupported`.
- Only **My Drive** folders can be picked. To use files shared with you, copy them into a My Drive folder.

### 5. Run and connect
```bash
./start.sh
```
1. The app opens on **Connect**. Click **Connect Google Drive** and sign in with your test-user account.
2. Google warns that the app is unverified (expected in Testing mode). Click **Continue**.
3. **Tick the box for "See and download all your Google Drive files"**. If it's left unticked, the app returns `insufficient_scope` and you have to connect again.
4. Pick your folder and confirm. You land on **Ingestion**, where documents go `pending → processing → done` and the Data tables fill in as each one finishes.

Sign-in is stored (refresh token encrypted at rest), so later runs of `./start.sh` reconnect automatically and catch up on anything that changed while the app was stopped.

---

## What `start.sh` does
1. Checks that Docker is running, that `python3.12` and `node` exist, and that `.env` exists. It lists missing variables by name only.
2. Creates `./.venv` and installs `requirements.txt` on first run. Later runs reinstall only when `requirements.txt` changes (hash in `.venv/.requirements.sha256`).
3. Runs `npm ci` in `web/` only when `node_modules` is missing or `package-lock.json` changed.
4. `docker compose up -d postgres` (`pgvector/pgvector:pg16`, volume `pgdata`) and waits for it.
5. Starts **api** (`alembic upgrade head && uvicorn app.main:app --port 8000`), **worker** (`python -m app.worker`) and **web** (`npm run start`, port 4200, proxying `/api` to :8000) in the background. PIDs go in `.run/*.pid` and logs in `.run/*.log`.
6. Waits for `GET /api/health`, then opens http://localhost:4200. Set `NO_BROWSER=1` to skip opening the browser.

`./stop.sh` stops web, worker and api, then `docker compose stop postgres`.

The first time the worker indexes documents it downloads the embedding model (`BAAI/bge-small-en-v1.5`, about 65 MB) into `./.cache/fastembed`. That needs a one-time internet connection.

---

## Demo mode
| `.env` | Source watched | Extraction | Ask |
|---|---|---|---|
| no `GOOGLE_CLIENT_ID` (or `DEMO_MODE=true`), no `ANTHROPIC_API_KEY` | `./sample_docs` | committed fixtures in `./fixtures/` (no API calls) | disabled, with an explanation |
| no `GOOGLE_CLIENT_ID` (or `DEMO_MODE=true`), with `ANTHROPIC_API_KEY` | `./sample_docs` | **real Claude** (about 21 calls on first run) | enabled |
| `GOOGLE_CLIENT_ID` set, `DEMO_MODE` empty | your Drive folder | real Claude | enabled |

A banner shows when demo mode is on. Demo mode uses the same reconcile and polling as Drive: add, edit or delete files in `sample_docs/` and they're picked up.

The sample set contains deliberate edge cases (see `sample_docs/MANIFEST.md`): the same fund under two names, a conflicting January figure, a YTD that doesn't compound, a statement roll-forward that doesn't tie, a missing month only found in a multi-fund report, returns in %, bps, parentheses and decimals, a scanned PDF, and commentary that says "duration" where you'd search "rate risk".

---

## Using the app
| Tab / panel | What it does |
|---|---|
| **Data** | Funds & Returns, Fund facts and Statements (expand a row for holdings). Filter by fund/account, date range, document type and status (ok / needs_review / conflict). Sort any column. **Export CSV** of the current view. Click a value to open the source panel: document, Drive link, page, snippet with the value highlighted, confidence, model. |
| **Ingestion** | Every document with type, status, attempts, last error, token usage and search-indexed flag. Actions: **Reprocess**, **Sync now**, **Full reconcile**. |
| **Review** | Flagged items with the reason (low confidence, YTD mismatch, roll-forward mismatch, conflict, fund/account name match, …). Actions: accept, edit, choose between conflicting values (rows that agree with your choice are kept), confirm/reject a name match, or **Use as main name**. Decisions survive reprocessing. |
| **Search** (header box) | Results as you type, grouped into funds/aliases, accounts, documents and passages, with highlighted snippets. Pure Postgres, no LLM. |
| **Ask** (right-hand panel) | Opens from the **Ask** button. Pin it to keep the conversation open while you move between tabs. Answers have numbered citation chips (SQL + rows, or document + page passages), a **How I got this** panel with every tool call, and an underline on any number not found in a tool result. |

---

## Architecture

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

| Process | Runs in | Role |
|---|---|---|
| `postgres` | Docker, `pgvector/pgvector:pg16`, port 5432 | All durable state: tokens, sync cursor, documents, extractions, values, chunks, embeddings, review decisions |
| `api` | `.venv`, port 8000 | REST API for the UI, OAuth, Drive folder listing, Search, Ask (Claude tool-use loop) |
| `worker` | `.venv` (same codebase as api) | Drive or local-folder sync, job queue, Claude extraction, normalization and validation, chunking and local embeddings |
| `web` | Angular dev server, port 4200 | UI; proxies `/api` to :8000 |

The api and worker hold **no state in memory** between requests or iterations. Either can be restarted at any time without losing or duplicating work.

### Pipeline
1. **Sync** (worker). On every boot, and when a folder is picked, a **full reconcile** lists the folder and compares it with the database by `(file id, md5)`. Then every `SYNC_INTERVAL_SECONDS` (30), an **incremental sync** reads Drive `changes.list` from the stored page token. New or changed files become `pending`, and files that disappeared become `removed`. The page token and document updates are committed in one transaction.
2. **Queue.** `WORKER_CONCURRENCY` (4) tasks claim jobs with `SELECT … FOR UPDATE SKIP LOCKED`. Retries use exponential backoff, up to `MAX_ATTEMPTS`, then the document is marked `failed` with the error. A job left `processing` by a dead worker is reclaimed after 60 s.
3. **Extract.** One Claude call per document, with PDFs sent as native document blocks and HTML/CSV as text. Claude returns schema-validated structured output: document type, fund and/or statement fields (each with page, verbatim snippet and confidence), and per-page text, which covers scanned PDFs.
4. **Normalize and validate** (deterministic code). Converts %, bps, `(0.8)`, decimals, dates and amounts like `12.40mm`. Resolves fund and account names (exact match, fuzzy match → review, or new). Checks:
   - monthly return within ±50%
   - monthly returns compound to the stated YTD within 5 bps
   - statement roll-forward: opening + contributions − withdrawals + P&L − fees ≈ closing
   - no future dates
   - the snippet exists in the document text and contains the value
   - confidence threshold

   A different value for the same fund and period from another document marks **both** rows `conflict`; nothing is overwritten.
5. **Index for retrieval.** The document's text is split into chunks, stored with a full-text vector, and embedded locally. See the next section.

### How RAG works: indexing on the worker side
Right after a document is extracted, the worker splits its text into chunks, embeds each one locally, and stores them in Postgres. At question time, Ask searches these chunks.

**1. Chunking: by page, then paragraph**
- **One page at a time.** A chunk never spans two pages, so every cited passage has an exact page number.
- **Paragraphs packed up to about 500 tokens**, which fits the embedding model's 512-token limit.
- **About 50 tokens of overlap** between neighbouring chunks, so a sentence on a boundary isn't cut in half.
- **CSVs are chunked by groups of rows, with the header repeated in every chunk**, so values stay next to their column names.
- Scanned PDFs use the page text Claude transcribed during extraction.

*The 21 sample documents produce 24 chunks; most factsheet pages fit in a single chunk.*

**2. Embedding: local, no API key**
| What | Detail |
|---|---|
| Model | `BAAI/bge-small-en-v1.5` via **fastembed** (ONNX, runs on CPU) |
| Vector | 384 dimensions, cosine similarity |
| Stored in | `document_chunks.embedding` (pgvector, HNSW index), next to a full-text `tsvector` column |
| Cost | Free and offline once the ~65 MB model is cached in `./.cache/fastembed` |

Chunks are keyword-searchable as soon as the extraction commits. Embeddings are added right after, and if embedding fails it's retried every 10 s. Search indexing never blocks or fails extraction.

**3. Re-indexing: only when something changed**
- **New file version** (different checksum): new chunks and embeddings. The old version is hidden from search along with its values.
- **Reprocess with identical text:** existing chunks and embeddings are kept, with no re-embedding.
- **`EMBEDDING_MODEL` changed:** chunks from the old model are re-embedded automatically, without re-extracting anything.
- **File removed:** its chunks drop out of search.

**At question time:** Ask embeds the search text with the same model, takes the top 20 keyword matches and the top 20 nearest vectors from Postgres, merges them (Reciprocal Rank Fusion) and sends the best 8 passages to Claude. Claude only ever sees passage text, never vectors.

### Search vs Ask
- **Search** (`GET /api/search`): Postgres full-text search (`websearch_to_tsquery`, `ts_rank_cd`, `ts_headline`) on chunks, plus trigram and prefix matching on fund, account and document names. Instant and free.
- **Ask** (`POST /api/ask`): a Claude tool-use loop (up to 6 steps) with two tools:
  - `query_sql`: parsed with sqlglot. It must be a single `SELECT` over 7 documented views (`v_returns`, `v_fund_facts`, `v_statements`, `v_holdings`, `v_funds`, `v_accounts`, `v_documents`), using only allowlisted functions. `LIMIT` is capped at 200. It runs as the read-only `app_readonly` role with a 5 s timeout.
  - `search_documents`: hybrid retrieval over the chunk index described above, combining full-text and vector search, merged with RRF.

  Every number in the answer is checked against the returned rows and passages.

### Code map
| Path | Contents |
|---|---|
| `app/main.py`, `app/api/` | FastAPI app and routers (data, ingestion, sources, search, review, auth, drive, ask), error envelope |
| `app/schemas/` | Pydantic request/response models (the API contract) |
| `app/worker.py`, `app/ingest/` | Worker loop, sync, queue, pipeline, post-processing, validation, alias resolution, conflicts, canonical names |
| `app/sources/` | `LocalFolderSource` (demo) and `DriveSource` (`files.list` / `changes.list`, Sheets → CSV) |
| `app/google/` | OAuth (PKCE, signed state cookie), encrypted credential storage, Drive client |
| `app/extraction/` | Canonical extraction schema (§9), Claude extractor, prompt (`PROMPT_VERSION`), fixture extractor |
| `app/normalize/` | Percent, date and amount parsers; value-in-snippet locator |
| `app/search/` | Chunking, embeddings, indexing |
| `app/ask/` | SQL guard, hybrid retrieval, tool-use loop, prompt, citation checks |
| `migrations/` | Alembic: pgvector extension, schema, views, indexes, read-only role |
| `web/` | Angular 21 + Material UI (`web/README.md` covers the dev mock) |
| `fixtures/` | Committed Claude extractions for every sample doc (demo mode) |
| `scripts/` | `gen_keys.py`, `test.sh`, `gen_fixtures.py`, shared shell helpers |
| `tests/` | Python unit and integration tests |

---

## Design decisions
- **LLM for ambiguity, code for truth.** Claude only reads documents (layout, terminology, classification, page text) and writes answers. Normalization, validation, conflict detection, name resolution, search ranking and SQL safety are deterministic code, and Claude's extracted values are stored exactly as written, so every step can be re-run and tested.
- **One Claude call per document, with structured output.** Classification, extraction and transcription come back together as schema-validated JSON. PDFs go to Claude natively, so there's no per-format parsing stack and scanned PDFs work through vision.
- **Every value is traceable.** Each value stores its document, page, verbatim snippet and confidence. The snippet is checked against the document text and must contain the value; failures go to Review. Every Ask answer cites the exact SQL and rows, or the passages, it used.
- **Flag, never overwrite.** Validation failures and cross-document disagreements are kept and flagged for Review rather than dropped or silently resolved. Review decisions are stored by natural key, so they're re-applied when a document is reprocessed.
- **Deterministic canonical names.** A fund's display name is the name used in the most documents (ties go to factsheets, then the longest name), so it doesn't depend on processing order. **Use as main name** in Review overrides it, and the override sticks.
- **One Postgres for everything.** Relational tables, full-text indexes, vector (HNSW) indexes and the job queue live in one database, so chunks can be joined to structured data, there's one service to run, and every process is stateless.
- **Defense in depth for Ask SQL.** The sqlglot guard (single SELECT, view and function allowlists, LIMIT) runs first, and the query then executes as a role that can only read the seven views, in a read-only transaction with a timeout.
- **Hybrid retrieval with query expansion.** Full-text search catches exact terms (fund names, "NAV", months), and vectors catch concepts. The small local embedding model ranks literal wording above synonyms, so the Ask prompt also has Claude search related terms (e.g. rate risk → duration, rate sensitivity, interest-rate exposure).
- **Local-first security.** Secrets live only in git-ignored `.env`. The Drive scope is `drive.readonly`, the refresh token is Fernet-encrypted at rest, and OAuth codes, tokens, state, document text and Ask text are never logged.

## Trade-offs
| Decision | Why | Production alternative |
|---|---|---|
| Polling `changes.list` every 30 s + reconcile on boot | No public HTTPS endpoint needed locally; reconcile guarantees catch-up | Drive push notifications (webhooks) |
| Non-blocking startup, progressive fill | First run needs OAuth in the UI | Same |
| Single user, no app login | Take-home scope | App auth, per-user credentials, tenancy |
| One Claude call per document | Simple and accurate for factsheet-sized docs | Page batching and cost controls for very large documents |
| Postgres-backed job queue | No extra infrastructure | Pub/Sub, Cloud Tasks, SQS |
| Local embeddings (fastembed, bge-small) | No second API key, works offline, fast on CPU | Hosted embeddings (e.g. Voyage) + a reranker |
| Non-recursive folder watch, My Drive only | Matches "one designated folder"; simpler | Recursive watch with include/exclude rules; shared drives |
| Committed fixtures for demo mode | Zero-setup evaluation | — |
| api/worker run from a shared `.venv`, Postgres in Docker | Fast local iteration, one environment for running and testing | Containerize api/worker |

## Known limitations
- **Mac sleep pauses sync.** The worker can't poll Drive while the machine sleeps; it catches up on wake. Keep the Mac awake for live demos (`caffeinate -i`).
- **My Drive folders only.** "Shared with me" and shared drives aren't listed in the picker.
- **Renaming after confirmation.** Once a name match is confirmed it leaves Review, so **Use as main name** isn't available for it any more. There's no separate rename screen.
- **Removed conflict partners.** If one side of a conflict is removed, the other stays flagged as a normal Review item for you to accept.
- **Cross-turn numbers in Ask.** A number repeated from an earlier answer in the same conversation can show as unverified, because only the current answer's citations are checked.
- **Unlabeled net/gross** returns are stored as `unknown` and sent to Review.
- **No currency conversion** across funds.

---

## Testing
```bash
scripts/test.sh -q              # Python: creates .venv if needed, starts Postgres, uses a separate test DB
cd web && npm test              # UI: Vitest, headless
```
- **Python** (about 1,400 tests): normalizers, YTD and roll-forward validators, fuzzy aliases and canonical names, snippet verification, chunker, RRF, the SQL guard (writes, multiple statements, base tables, system catalogs and server-info functions rejected; LIMIT enforced), the pipeline end to end on fixtures, idempotency and versions, reconcile after downtime, worker crash recovery, the API contract, and Ask with mocked Claude including the guard-error path. Google and Anthropic are mocked; no network is used.
- **UI** (about 380 tests): API params, formatting, safe rendering of highlights and markdown, polling cadence, review actions, connect flow, and the Ask panel (citations, steps, unverified numbers, persistence across navigation).
- Set `TEST_DATABASE_NAME=<name>` to run test suites in parallel without clashing.
- `scripts/gen_fixtures.py` regenerates `fixtures/` with real Claude (`--force`, `--only <file>`). Bump `PROMPT_VERSION` in `app/extraction/prompt.py` when the prompt changes. A full run is about 155k input and 39k output tokens.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `/connect?error=access_denied` | Your account isn't a **Test user** (Google Auth Platform → Audience), or you cancelled the consent screen |
| `/connect?error=insufficient_scope` | You didn't tick the Drive checkbox on Google's consent screen. Connect again and tick it |
| Google says `redirect_uri_mismatch` | The client's Authorized redirect URI must be exactly `http://localhost:8000/api/auth/google/callback` |
| Connected, but folder list errors | Enable the **Google Drive API** for the project |
| Documents `failed` with "credit balance is too low" | Add credits to the Anthropic account behind `ANTHROPIC_API_KEY` |
| `start.sh`: port 5432 in use | Set `POSTGRES_PORT` and update both database URLs (see setup step 3) |
| "Google Drive access was revoked or has expired" | Reconnect from the header menu; the watched folder is kept |
| Demo docs all `failed` with "no fixture for …" | A file in `sample_docs/` has no committed fixture; add an Anthropic key or regenerate fixtures |
| Logs | `.run/api.log`, `.run/worker.log`, `.run/web.log` (structured JSON; no secrets or document text) |

## Production next steps
- **Deploy:** containerize api and worker, use managed Postgres (Cloud SQL) with pgvector, and put secrets in Secret Manager / KMS instead of `.env`.
- **Real-time sync:** Drive push notifications instead of polling. Support shared drives and recursive watch with include/exclude rules.
- **Users and access:** app authentication, per-user OAuth credentials and tenant isolation. Publish and verify the Google OAuth app.
- **Scale ingestion:** a managed queue (Pub/Sub / Cloud Tasks), per-tenant rate limits, cost budgets, and page-level batching for large documents.
- **Retrieval quality:** hosted embeddings and a reranker, plus an evaluation set for extraction accuracy and answer grounding, tracked per `PROMPT_VERSION`.
- **Observability:** metrics and traces (latency, token spend, failure rates), alerting on sync errors, and an audit log of review decisions.
- **Product:** fund rename and merge screens, currency normalization, a document viewer with highlighted snippets, and saved questions.

---

## Configuration reference (`.env`)
| Variable | Required | Default / notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes, except for fixture demo | Your key (needs credits) |
| `CLAUDE_MODEL` | No | `claude-opus-5-5` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Yes, except in demo mode | From your OAuth client |
| `GOOGLE_REDIRECT_URI` | No | `http://localhost:8000/api/auth/google/callback`; must match the Cloud console exactly |
| `OAUTHLIB_INSECURE_TRANSPORT` | Dev | `1` (localhost over HTTP) |
| `POSTGRES_PORT` | No | `5432` |
| `DATABASE_URL` | No | `postgresql+psycopg://app:app@localhost:5432/docintel` |
| `DATABASE_READONLY_URL` | No | `postgresql+psycopg://app_readonly:app_readonly@localhost:5432/docintel` (the role's password is set from this URL at migration time) |
| `FERNET_KEY`, `SESSION_SECRET` | Yes, except in demo mode | `python3.12 scripts/gen_keys.py` |
| `SYNC_INTERVAL_SECONDS` | No | `30` |
| `WORKER_CONCURRENCY` | No | `4` |
| `MAX_ATTEMPTS` | No | `3` |
| `REVIEW_CONFIDENCE_THRESHOLD` | No | `0.7` |
| `EMBEDDING_MODEL` | No | `BAAI/bge-small-en-v1.5` |
| `ASK_MAX_STEPS` | No | `6` |
| `DEMO_MODE` | No | `true` forces demo mode |

An empty value counts as unset. A variable set in your shell overrides `.env`, even if it's empty: `DEMO_MODE=true ANTHROPIC_API_KEY= ./start.sh` runs keyless demo mode without editing `.env`.

/**
 * TypeScript mirror of the API contract (docs/progress.md -> "API Contract"; the
 * Pydantic models in app/schemas/ are the executable form). Every response type the
 * UI consumes lives here so a contract change is a one-file change.
 *
 * C1: snake_case, integer ids, `YYYY-MM-DD` dates, ISO-8601 UTC timestamps, numbers
 * are numbers, and every documented field is always present (null when empty).
 */

// ---- scalars / enums (app/schemas/common.py) ---------------------------------------

/** `YYYY-MM-DD` */
export type IsoDate = string;
/** ISO 8601 UTC with `Z`, e.g. `2026-10-16T18:44:31Z` */
export type Timestamp = string;

export type DocumentStatus = 'pending' | 'processing' | 'done' | 'failed' | 'needs_review' | 'unsupported';
export type DocumentType = 'factsheet' | 'account_statement' | 'performance_report' | 'email' | 'other';
export type DocumentSource = 'drive' | 'local';
export type ValueStatus = 'ok' | 'needs_review' | 'conflict';
export type ReturnType = 'MTD' | 'QTD' | 'YTD' | 'ITD' | 'annualized' | 'unknown';
export type NetGross = 'net' | 'gross' | 'unknown';
export type ReviewReason =
  | 'low_confidence'
  | 'ytd_mismatch'
  | 'rollforward_mismatch'
  | 'alias_match'
  | 'conflict'
  | 'snippet_not_found'
  | 'future_date'
  | 'out_of_range'
  | 'net_gross_unknown'
  | 'return_type_unknown'
  | 'unparseable_value';
export type AliasMatchMethod = 'exact' | 'fuzzy' | 'new' | 'manual';
export type ExtractionSource = 'claude' | 'fixture';
/** Every ExtractionSource, for runtime checks (pinned against the contract in api/contract.spec.ts). */
export const EXTRACTION_SOURCES = ['claude', 'fixture'] as const satisfies readonly ExtractionSource[];
export type Mode = 'demo' | 'drive';
export type ValueKind = 'percent' | 'money' | 'number' | 'text' | 'date';

export const DOCUMENT_TYPES: readonly DocumentType[] = [
  'factsheet',
  'account_statement',
  'performance_report',
  'email',
  'other',
];
export const VALUE_STATUSES: readonly ValueStatus[] = ['ok', 'needs_review', 'conflict'];
export const RETURN_TYPES: readonly ReturnType[] = ['MTD', 'QTD', 'YTD', 'ITD', 'annualized', 'unknown'];
export const NET_GROSS: readonly NetGross[] = ['net', 'gross', 'unknown'];

/** Mirrors `REVIEW_REASON_LABELS` in app/schemas/common.py. */
export const REVIEW_REASON_LABELS: Record<ReviewReason, string> = {
  low_confidence: 'Low confidence',
  ytd_mismatch: 'YTD mismatch',
  rollforward_mismatch: 'Roll-forward mismatch',
  alias_match: 'Alias match',
  conflict: 'Conflict',
  snippet_not_found: 'Snippet not found',
  future_date: 'Future date',
  out_of_range: 'Out of range',
  net_gross_unknown: 'Net/gross unknown',
  return_type_unknown: 'Return type unknown',
  unparseable_value: 'Unparseable value',
};

export const DOCUMENT_TYPE_LABELS: Record<DocumentType, string> = {
  factsheet: 'Factsheet',
  account_statement: 'Account statement',
  performance_report: 'Performance report',
  email: 'Email',
  other: 'Other',
};

export const VALUE_STATUS_LABELS: Record<ValueStatus, string> = {
  ok: 'ok',
  needs_review: 'needs review',
  conflict: 'conflict',
};

// ---- errors (C2) ------------------------------------------------------------------

export type ErrorCode =
  | 'validation_error'
  | 'invalid_action'
  | 'not_found'
  | 'method_not_allowed'
  | 'not_connected'
  | 'no_folder'
  | 'demo_mode'
  | 'document_busy'
  | 'unsupported_document'
  | 'drive_error'
  | 'upstream_error'
  | 'ask_disabled'
  | 'db_unavailable'
  | 'internal_error';

export interface ErrorDetail {
  loc: string | null;
  message: string;
}

export interface ErrorInfo {
  code: ErrorCode;
  message: string;
  details: ErrorDetail[] | null;
}

export interface ErrorResponse {
  error: ErrorInfo;
}

// ---- envelopes (C3) ----------------------------------------------------------------

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  sort: string;
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export const DEFAULT_PAGE_SIZE = 50;
export const MAX_PAGE_SIZE = 500;

// ---- health / config ---------------------------------------------------------------

export interface HealthResponse {
  status: 'ok' | 'error';
  db: 'ok' | 'error';
  alembic_revision: string | null;
}

export interface Feature {
  enabled: boolean;
  reason: string | null;
}

export interface Features {
  browse: Feature;
  search: Feature;
  review: Feature;
  ask: Feature;
  drive: Feature;
}

export interface ConfigResponse {
  demo_mode: boolean;
  demo_reason: 'DEMO_MODE' | 'GOOGLE_CLIENT_ID_UNSET' | null;
  /** `"fixture"` only in demo mode with no ANTHROPIC_API_KEY (same spelling as ExtractionSource since T6) */
  extraction_source: ExtractionSource;
  claude_model: string;
  sync_interval_seconds: number;
  features: Features;
}

// ---- progress / sync / documents (app/schemas/ingestion.py) ------------------------

export interface StatusCounts {
  pending: number;
  processing: number;
  done: number;
  needs_review: number;
  failed: number;
  unsupported: number;
}

export interface TokenTotals {
  input_tokens: number;
  output_tokens: number;
}

export interface SyncInfo {
  source: DocumentSource;
  folder_id: string | null;
  folder_name: string | null;
  last_synced_at: Timestamp | null;
  last_reconciled_at: Timestamp | null;
  last_success_at: Timestamp | null;
  last_error: string | null;
  sync_requested_at: Timestamp | null;
  reconcile_requested_at: Timestamp | null;
  sync_pending: boolean;
  reconcile_pending: boolean;
}

export interface ConnectionInfo {
  connected: boolean;
  google_email: string | null;
}

export interface ProgressResponse {
  mode: Mode;
  counts: StatusCounts;
  total: number;
  /** done + needs_review: the "14 / 20 processed" numerator */
  processed: number;
  /** processed + failed + unsupported: bar width numerator */
  finished: number;
  in_flight: number;
  /** poll every 3 s while true, otherwise every 15 s (C9) */
  active: boolean;
  review_open: number;
  tokens: TokenTotals;
  sync: SyncInfo;
  connection: ConnectionInfo;
  generated_at: Timestamp;
}

export interface SyncRequestResponse {
  kind: 'sync' | 'reconcile';
  mode: Mode;
  requested_at: Timestamp;
  already_pending: boolean;
  last_success_at: Timestamp | null;
}

export interface DocumentItem {
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  document_type_confidence: number | null;
  mime_type: string;
  status: DocumentStatus;
  version: number;
  source: DocumentSource;
  drive_file_id: string;
  web_view_link: string | null;
  search_indexed: boolean;
  attempts: number;
  last_error: string | null;
  drive_modified_at: Timestamp | null;
  processed_at: Timestamp | null;
  created_at: Timestamp;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  extraction_model: string | null;
  extraction_source: ExtractionSource | null;
  latency_ms: number | null;
  value_count: number;
  review_count: number;
  can_reprocess: boolean;
}

// ---- auth (app/schemas/auth.py), used by /connect -----------------------------------

export interface FolderRef {
  source: DocumentSource;
  folder_id: string | null;
  folder_name: string | null;
}

export interface AuthStatusResponse {
  mode: Mode;
  connected: boolean;
  google_email: string | null;
  scopes: string[];
  connected_at: Timestamp | null;
  folder: FolderRef | null;
}

// ---- data browsing (app/schemas/data.py) -------------------------------------------

export interface ReturnItem {
  return_id: number;
  fund_id: number;
  fund_name: string;
  manager: string | null;
  strategy: string | null;
  fund_currency: string | null;
  period_end: IsoDate;
  period_year: number;
  period_month: number;
  period_label: string | null;
  return_type: ReturnType;
  net_gross: NetGross;
  /** fraction; for CSV / exact math only. Display `value_pct`. */
  value: number | null;
  value_pct: number | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  confidence: number | null;
  page: number | null;
  source_snippet: string | null;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
}

export interface FundFactItem {
  fact_id: number;
  fund_id: number;
  fund_name: string;
  manager: string | null;
  strategy: string | null;
  metric: string;
  value: number | null;
  /** value * 100 when unit == "fraction", else null */
  value_pct: number | null;
  value_text: string | null;
  /** "fraction" | ISO currency (e.g. "USD") | "ratio" | "years" | null */
  unit: string | null;
  as_of_date: IsoDate | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  confidence: number | null;
  page: number | null;
  source_snippet: string | null;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
}

export type StatementField =
  | 'account_name'
  | 'account_number_masked'
  | 'custodian'
  | 'fund_name'
  | 'currency'
  | 'period_start'
  | 'period_end'
  | 'opening_balance'
  | 'contributions'
  | 'withdrawals'
  | 'pnl'
  | 'fees'
  | 'closing_balance';

export interface StatementItem {
  statement_id: number;
  account_id: number;
  account_name: string;
  account_number_masked: string | null;
  custodian: string | null;
  fund_id: number | null;
  fund_name: string | null;
  manager: string | null;
  period_start: IsoDate | null;
  period_end: IsoDate;
  opening_balance: number | null;
  contributions: number | null;
  withdrawals: number | null;
  pnl: number | null;
  fees: number | null;
  closing_balance: number | null;
  currency: string | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  /** cells to highlight */
  flagged_fields: StatementField[];
  holdings_count: number;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
}

export type HoldingField = 'holding_name' | 'identifier' | 'quantity' | 'market_value' | 'weight';

export interface HoldingItem {
  holding_id: number;
  statement_id: number;
  account_id: number;
  account_name: string;
  custodian: string | null;
  fund_id: number | null;
  fund_name: string | null;
  period_end: IsoDate;
  currency: string | null;
  line_no: number;
  holding_name: string;
  identifier: string | null;
  quantity: number | null;
  market_value: number | null;
  /** fraction */
  weight: number | null;
  /** display this */
  weight_pct: number | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  confidence: number | null;
  page: number | null;
  source_snippet: string | null;
  document_id: number;
  document_name: string;
}

export interface AliasItem {
  alias_id: number;
  alias: string;
  confirmed: boolean;
  match_method: AliasMatchMethod;
  match_score: number | null;
  source_document_id: number | null;
  source_document_name: string | null;
}

export interface FundItem {
  fund_id: number;
  fund_name: string;
  manager: string | null;
  strategy: string | null;
  currency: string | null;
  aliases: AliasItem[];
  has_unconfirmed_alias: boolean;
  return_count: number;
  document_count: number;
  latest_period_end: IsoDate | null;
}

export interface AccountItem {
  account_id: number;
  account_name: string;
  account_number_masked: string | null;
  custodian: string | null;
  currency: string | null;
  aliases: AliasItem[];
  has_unconfirmed_alias: boolean;
  statement_count: number;
  latest_period_end: IsoDate | null;
}

export type ReturnsPage = Page<ReturnItem>;
export type FundFactsPage = Page<FundFactItem>;
export type StatementsPage = Page<StatementItem>;
export type HoldingsPage = Page<HoldingItem>;
export type FundsList = ListResponse<FundItem>;
export type AccountsList = ListResponse<AccountItem>;
export type DocumentsPage = Page<DocumentItem>;

// ---- sources (side panel) ----------------------------------------------------------

export type SourceTable = 'returns' | 'fund_facts' | 'statements' | 'statement_holdings';

export interface Provenance {
  page: number | null;
  /** verbatim; render as text with white-space: pre-wrap */
  source_snippet: string | null;
  confidence: number | null;
  /** true: found in document text; false: not found; null: check skipped */
  snippet_verified: boolean | null;
}

/**
 * Where the value is written inside `provenance.source_snippet` (T6). `start`/`end`
 * are UTF-16 offsets (JS string indices), so `snippet.slice(start, end) === text`.
 * Rendered with `snippetSegments` (shared/headline.ts), which falls back to the plain
 * snippet when the range does not fit.
 */
export interface SnippetHighlight {
  start: number;
  end: number;
  text: string;
}

export interface DocumentRef {
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  mime_type: string;
  source: DocumentSource;
  /** Drive link; null in demo mode (local files) */
  web_view_link: string | null;
  version: number;
  processed_at: Timestamp | null;
}

export interface ExtractionRef {
  extraction_id: number | null;
  model: string | null;
  prompt_version: string | null;
  source: ExtractionSource | null;
  created_at: Timestamp | null;
}

export interface ReasonInfo {
  code: ReviewReason;
  label: string;
  message: string;
}

export interface ValueSnapshot {
  table: SourceTable;
  id: number;
  field: string;
  value: number | string | null;
  value_pct: number | null;
  value_kind: ValueKind;
  unit: string | null;
  currency: string | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  provenance: Provenance;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  web_view_link: string | null;
  extraction_model: string | null;
}

export interface ReviewHistoryEntry {
  review_action_id: number;
  action: ReviewActionName;
  old_value: unknown;
  new_value: unknown;
  note: string | null;
  created_at: Timestamp;
}

export interface SourceResponse {
  table: SourceTable;
  id: number;
  field: string;
  label: string;
  field_label: string;
  value: number | string | null;
  value_pct: number | null;
  value_kind: ValueKind;
  unit: string | null;
  currency: string | null;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  reasons: ReasonInfo[];
  provenance: Provenance;
  /** null for text/date values, no snippet, or no matching number in the snippet */
  snippet_highlight: SnippetHighlight | null;
  document: DocumentRef;
  extraction: ExtractionRef;
  conflicts: ValueSnapshot[];
  review_history: ReviewHistoryEntry[];
}

// ---- queries (request side) ---------------------------------------------------------

/** Shared by every paginated list (C3). `page` is 1-based. */
export interface ListQuery {
  page?: number;
  page_size?: number;
  /** comma-separated, leading `-` = descending */
  sort?: string | null;
}

/** C4 common value filters. Arrays become repeated keys. */
export interface ValueFilters {
  document_id?: number[];
  document_type?: DocumentType[];
  status?: ValueStatus[];
  review_reason?: ReviewReason[];
  date_from?: IsoDate | null;
  date_to?: IsoDate | null;
}

export interface ReturnsQuery extends ListQuery, ValueFilters {
  fund_id?: number[];
  return_type?: ReturnType[];
  net_gross?: NetGross[];
}

export interface FundFactsQuery extends ListQuery, ValueFilters {
  fund_id?: number[];
  metric?: string[];
}

export interface StatementsQuery extends ListQuery, ValueFilters {
  account_id?: number[];
  fund_id?: number[];
}

export interface HoldingsQuery extends ListQuery {
  status?: ValueStatus[];
}

/** Sortable columns per endpoint (C3); anything else is a 422. */
export const RETURNS_SORTABLE = [
  'fund_name', 'manager', 'strategy', 'fund_currency', 'period_end', 'return_type', 'net_gross',
  'value_pct', 'status', 'confidence', 'document_name', 'document_type',
] as const;
export const FUND_FACTS_SORTABLE = [
  'fund_name', 'manager', 'strategy', 'metric', 'value', 'unit', 'as_of_date', 'status',
  'confidence', 'document_name', 'document_type',
] as const;
export const STATEMENTS_SORTABLE = [
  'account_name', 'account_number_masked', 'custodian', 'fund_name', 'manager',
  'period_start', 'period_end', 'opening_balance', 'contributions', 'withdrawals',
  'pnl', 'fees', 'closing_balance', 'currency', 'status', 'holdings_count',
  'document_name', 'document_type',
] as const;
export const HOLDINGS_SORTABLE = [
  'line_no', 'holding_name', 'identifier', 'quantity', 'market_value', 'weight_pct', 'status', 'confidence',
] as const;

// ---- ingestion (U2, app/schemas/ingestion.py) ----------------------------------------

export const DOCUMENT_STATUSES: readonly DocumentStatus[] = [
  'pending', 'processing', 'done', 'needs_review', 'failed', 'unsupported',
];

export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  pending: 'pending',
  processing: 'processing',
  done: 'done',
  needs_review: 'needs review',
  failed: 'failed',
  unsupported: 'unsupported',
};

export interface DocumentsQuery extends ListQuery {
  status?: DocumentStatus[];
  document_type?: DocumentType[];
  /** case-insensitive substring of document_name, max 200 chars */
  q?: string | null;
  search_indexed?: boolean | null;
}

export const DOCUMENTS_SORTABLE = [
  'document_name', 'document_type', 'status', 'attempts', 'processed_at', 'created_at',
  'drive_modified_at', 'total_tokens', 'search_indexed', 'version',
] as const;

export interface ReprocessResponse {
  document_id: number;
  status: 'pending';
  attempts: number;
  queued_at: Timestamp;
}

// ---- search (U2, app/schemas/search.py) ---------------------------------------------

export const SEARCH_MIN_QUERY_LENGTH = 2;
export const SEARCH_MAX_QUERY_LENGTH = 200;

/** An alias on a search hit (app/schemas/search.py::HitAlias). */
export interface HitAlias {
  alias: string;
  /** false: a fuzzy suggestion still waiting in Review */
  confirmed: boolean;
}

export interface FundHit {
  fund_id: number;
  fund_name: string;
  manager: string | null;
  /** set when an alias matched better than the canonical name */
  matched_alias: string | null;
  /** every alias except the canonical name, sorted (T6) */
  aliases: HitAlias[];
  score: number;
}

export interface AccountHit {
  account_id: number;
  account_name: string;
  account_number_masked: string | null;
  custodian: string | null;
  matched_alias: string | null;
  /** every alias except the canonical name, sorted (T6) */
  aliases: HitAlias[];
  score: number;
}

export interface DocumentHit {
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  status: DocumentStatus;
  web_view_link: string | null;
  score: number;
}

export interface ChunkHit {
  chunk_id: number;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  /** null for CSV row groups */
  page: number | null;
  chunk_index: number;
  /** escaped text whose ONLY markup is <mark>...</mark>; see shared/headline.ts */
  headline_html: string;
  rank: number;
  web_view_link: string | null;
}

export interface SearchGroups {
  funds: FundHit[];
  accounts: AccountHit[];
  documents: DocumentHit[];
  chunks: ChunkHit[];
}

export interface SearchResponse {
  query: string;
  min_query_length: number;
  groups: SearchGroups;
  total: number;
  took_ms: number;
}

// ---- review (U2, app/schemas/review.py) ---------------------------------------------

export type ReviewKind = 'value' | 'conflict' | 'alias';
export type ReviewTargetTable = SourceTable | 'fund_aliases' | 'account_aliases';
export type ReviewActionName = 'accept' | 'edit' | 'choose' | 'confirm_alias' | 'reject_alias' | 'make_canonical';

export const REVIEW_KINDS: readonly ReviewKind[] = ['value', 'conflict', 'alias'];
export const REVIEW_REASONS: readonly ReviewReason[] = Object.keys(REVIEW_REASON_LABELS) as ReviewReason[];

/** Mirrors `ALLOWED_ACTIONS` in app/schemas/review.py. */
export const ALLOWED_ACTIONS: Record<ReviewKind, readonly ReviewActionName[]> = {
  value: ['accept', 'edit'],
  conflict: ['choose', 'edit'],
  alias: ['confirm_alias', 'reject_alias', 'make_canonical'],
};

export const REVIEW_SORTABLE = ['created_at', 'kind', 'document_name', 'target_table'] as const;

export interface LowConfidenceDetail { confidence: number; threshold: number; fields?: string[] | null }
export interface YtdMonth { period_end: IsoDate; value: number; return_id: number | null }
export interface YtdMismatchDetail {
  year: number;
  /** fraction */
  stated: number;
  /** fraction, compounded from `months` */
  computed: number;
  difference: number;
  tolerance: number;
  months: YtdMonth[];
}
export interface RollforwardMismatchDetail {
  opening_balance: number;
  contributions: number;
  withdrawals: number;
  pnl: number;
  fees: number;
  expected_closing: number;
  stated_closing: number;
  difference: number;
  tolerance: number;
  currency: string | null;
}
export interface ConflictDetail { conflicting_ids: number[]; tolerance: number }
export interface AliasMatchDetail { alias: string; match_score: number | null; fund_id?: number | null; account_id?: number | null }
export interface OutOfRangeDetail { value: number; min: number; max: number }
export interface FutureDateDetail { date: IsoDate; today: IsoDate }
/**
 * `value_missing: true` (Q2 fix, additive): the snippet IS in the document, but the value
 * is not written in it. Absent / false: the snippet itself was not found.
 */
export interface SnippetNotFoundDetail { snippet: string | null; fields?: string[] | null; value_missing?: boolean | null }
export interface UnknownLabelDetail { raw_label: string | null }
export interface UnparseableValueDetail { raw: string | null; field: string | null }

/** `review_detail`: one key per reason (REVIEW_DETAIL_MODELS). */
export interface ReviewDetail {
  low_confidence?: LowConfidenceDetail;
  ytd_mismatch?: YtdMismatchDetail;
  rollforward_mismatch?: RollforwardMismatchDetail;
  conflict?: ConflictDetail;
  alias_match?: AliasMatchDetail;
  out_of_range?: OutOfRangeDetail;
  future_date?: FutureDateDetail;
  snippet_not_found?: SnippetNotFoundDetail;
  net_gross_unknown?: UnknownLabelDetail;
  return_type_unknown?: UnknownLabelDetail;
  unparseable_value?: UnparseableValueDetail;
}

export interface ReviewSubject {
  fund_id: number | null;
  fund_name: string | null;
  account_id: number | null;
  account_name: string | null;
  period_end: IsoDate | null;
  period_label: string | null;
  return_type: ReturnType | null;
  net_gross: NetGross | null;
  metric: string | null;
  holding_name: string | null;
}

export interface AliasSuggestion {
  entity: 'fund' | 'account';
  alias_id: number;
  alias: string;
  match_method: AliasMatchMethod;
  match_score: number | null;
  suggested_id: number;
  suggested_name: string;
  source_document_id: number | null;
  source_document_name: string | null;
  affected_rows: number;
}

export interface ReviewItem {
  /** `<target_table>:<target_id>`, used as-is in POST /api/review/{id} */
  id: string;
  kind: ReviewKind;
  target_table: ReviewTargetTable;
  target_id: number;
  status: ValueStatus;
  review_reasons: ReviewReason[];
  reasons: ReasonInfo[];
  title: string;
  subject: ReviewSubject;
  detail: ReviewDetail;
  current: ValueSnapshot | null;
  fields: ValueSnapshot[] | null;
  candidates: ValueSnapshot[] | null;
  alias: AliasSuggestion | null;
  allowed_actions: ReviewActionName[];
  document_id: number | null;
  document_name: string | null;
  created_at: Timestamp;
}

export interface ReviewCounts {
  /** always all three kinds (0 when none), T6 */
  by_kind: Record<ReviewKind, number>;
  /** only reasons with at least one open item */
  by_reason: Partial<Record<ReviewReason, number>>;
}

export interface ReviewListResponse extends Page<ReviewItem> {
  counts: ReviewCounts;
}

export interface ReviewQuery extends ListQuery {
  kind?: ReviewKind[];
  reason?: ReviewReason[];
  document_id?: number[];
}

interface ActionBase { note?: string | null }
export interface AcceptAction extends ActionBase { action: 'accept' }
export interface EditAction extends ActionBase {
  action: 'edit';
  field?: string | null;
  value?: number | string | null;
  value_pct?: number | null;
  chosen_id?: number | null;
}
export interface ChooseAction extends ActionBase { action: 'choose'; chosen_id: number }
export interface ConfirmAliasAction extends ActionBase { action: 'confirm_alias' }
export interface RejectAliasAction extends ActionBase { action: 'reject_alias'; reassign_to_id?: number | null }
/**
 * BUG-Q3-01 (user decision, option 3): the alias text becomes the fund's / account's
 * canonical name and the old canonical name stays as a confirmed alias. The body is
 * exactly `{"action":"make_canonical"}`: no `note` or other fields.
 */
export interface MakeCanonicalAction { action: 'make_canonical' }
export type ReviewActionRequest = AcceptAction | EditAction | ChooseAction | ConfirmAliasAction | RejectAliasAction | MakeCanonicalAction;

export interface AffectedRow {
  table: ReviewTargetTable | 'funds' | 'accounts';
  id: number;
  /** `renamed`: a `funds` / `accounts` row whose canonical name changed (`make_canonical`) */
  change: 'status_changed' | 'value_edited' | 'deleted' | 'alias_confirmed' | 'alias_reassigned' | 'relinked' | 'created' | 'renamed';
  status: ValueStatus | null;
}

export interface ReviewActionResponse {
  id: string;
  action: ReviewActionName;
  resolved: boolean;
  review_action_ids: number[];
  affected: AffectedRow[];
  remaining_open: number;
}

// ---- auth / drive (U3, app/schemas/auth.py) ----------------------------------------

/** `?error=` codes on `/connect` set by the OAuth callback (T3 decision 6). */
export type OAuthErrorCode =
  | 'access_denied'
  | 'state_mismatch'
  | 'token_exchange_failed'
  | 'missing_refresh_token'
  | 'insufficient_scope'
  | 'demo_mode';

export interface DisconnectResponse {
  connected: false;
}

export interface DriveFolderParent {
  /** "root" for My Drive */
  folder_id: string;
  /** "My Drive" for root */
  folder_name: string;
  /** for an "Up" button; null at root */
  parent_id: string | null;
  is_root: boolean;
}

export interface DriveFolder {
  folder_id: string;
  folder_name: string;
  modified_at: Timestamp | null;
}

export interface DriveFoldersResponse {
  parent: DriveFolderParent;
  /** direct child folders only, sorted by name (case-insensitive) */
  items: DriveFolder[];
  next_page_token: string | null;
}

export interface WatchRequest {
  folder_id: string;
}

export interface WatchResponse {
  folder: FolderRef;
  reconcile_requested_at: Timestamp;
}

// ---- ask (U4, app/schemas/ask.py) ---------------------------------------------------

export const ASK_MAX_QUESTION_LENGTH = 2000;
export const ASK_MAX_HISTORY = 20;
export const ASK_MAX_HISTORY_CONTENT = 8000;
/** Client-side HTTP timeout for POST /api/ask (contract: 60 s). */
export const ASK_TIMEOUT_MS = 60_000;

export interface AskHistoryItem {
  role: 'user' | 'assistant';
  content: string;
}

export interface AskRequest {
  question: string;
  history: AskHistoryItem[];
}

export interface SqlCitation {
  id: number;
  type: 'sql';
  /** as executed, after the guard enforced LIMIT */
  sql: string;
  columns: string[];
  /** aligned with columns; dates YYYY-MM-DD, numerics as numbers */
  rows: unknown[][];
  row_count: number;
  /** the LIMIT (200) was reached */
  truncated: boolean;
  /** rows whose `status` is needs_review / conflict */
  flagged_row_count: number;
}

export interface PassageCitation {
  id: number;
  type: 'passage';
  chunk_id: number;
  document_id: number;
  document_name: string;
  document_type: DocumentType | null;
  page: number | null;
  text: string;
  web_view_link: string | null;
}

export type Citation = SqlCitation | PassageCitation;

export type AskTool = 'query_sql' | 'search_documents';

export interface AskStep {
  /** 1-based, in call order */
  index: number;
  tool: AskTool;
  /** the raw tool input Claude sent */
  input: Record<string, unknown>;
  result_summary: string;
  /** query_sql rejected by the SQL guard */
  is_guard_error: boolean;
  error: string | null;
  citation_ids: number[];
  duration_ms: number;
}

export interface AskUsage {
  input_tokens: number;
  output_tokens: number;
}

export interface AskResponse {
  /** markdown with [n] citation markers */
  answer: string;
  citations: Citation[];
  steps: AskStep[];
  /** exact substrings of `answer` that matched no tool result */
  unverified_numbers: string[];
  uses_flagged_data: boolean;
  stop_reason: 'answered' | 'max_steps';
  model: string;
  usage: AskUsage;
  duration_ms: number;
}

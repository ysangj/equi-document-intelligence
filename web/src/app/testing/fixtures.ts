/** Contract example payloads shared by specs (docs/progress.md -> API Contract). */
import {
  AskResponse,
  AuthStatusResponse,
  ConfigResponse,
  DriveFoldersResponse,
  DocumentsPage,
  ProgressResponse,
  ReviewActionResponse,
  ReviewItem,
  ReviewListResponse,
  SearchResponse,
} from '../api/models';

/** The /api/config contract example (demo, fixtures, Ask disabled). */
export const DEMO_CONFIG: ConfigResponse = {
  demo_mode: true,
  demo_reason: 'GOOGLE_CLIENT_ID_UNSET',
  extraction_source: 'fixture',
  claude_model: 'claude-opus-5-5',
  sync_interval_seconds: 30,
  features: {
    browse: { enabled: true, reason: null },
    search: { enabled: true, reason: null },
    review: { enabled: true, reason: null },
    ask: { enabled: false, reason: 'Ask is disabled because ANTHROPIC_API_KEY is not set.' },
    drive: { enabled: false, reason: 'Demo mode: documents are read from ./sample_docs instead of Google Drive.' },
  },
};

export const DRIVE_CONFIG: ConfigResponse = {
  ...DEMO_CONFIG,
  demo_mode: false,
  demo_reason: null,
  extraction_source: 'claude',
  features: {
    ...DEMO_CONFIG.features,
    ask: { enabled: true, reason: null },
    drive: { enabled: true, reason: null },
  },
};

/** The /api/progress contract example. */
export const PROGRESS: ProgressResponse = {
  mode: 'drive',
  counts: { pending: 0, processing: 2, done: 13, needs_review: 4, failed: 1, unsupported: 0 },
  total: 20,
  processed: 17,
  finished: 18,
  in_flight: 2,
  active: true,
  review_open: 6,
  tokens: { input_tokens: 168400, output_tokens: 21950 },
  sync: {
    source: 'drive',
    folder_id: '1Q8vXk2mFundDocs',
    folder_name: 'Fund Docs',
    last_synced_at: '2026-10-16T18:51:58Z',
    last_reconciled_at: '2026-10-16T18:41:40Z',
    last_success_at: '2026-10-16T18:51:58Z',
    last_error: null,
    sync_requested_at: null,
    reconcile_requested_at: null,
    sync_pending: false,
    reconcile_pending: false,
  },
  connection: { connected: true, google_email: 'sj.demo@gmail.com' },
  generated_at: '2026-10-16T18:52:10Z',
};

// ---- U2: contract examples (generated from docs/progress.md -> API Contract) ----------
export const CONTRACT_DOCUMENTS: DocumentsPage = {
  "items": [
    {
      "document_id": 3,
      "document_name": "kestrel_factsheet_2026-01.pdf",
      "document_type": "factsheet",
      "document_type_confidence": 0.98,
      "mime_type": "application/pdf",
      "status": "done",
      "version": 1,
      "source": "drive",
      "drive_file_id": "1kT7aKestrelJan26",
      "web_view_link": "https://drive.google.com/file/d/1kT7aKestrelJan26/view?usp=drivesdk",
      "search_indexed": true,
      "attempts": 1,
      "last_error": null,
      "drive_modified_at": "2026-10-15T09:20:03Z",
      "processed_at": "2026-10-16T18:44:31Z",
      "created_at": "2026-10-16T18:41:42Z",
      "input_tokens": 6420,
      "output_tokens": 812,
      "total_tokens": 7232,
      "extraction_model": "claude-opus-5-5",
      "extraction_source": "claude",
      "latency_ms": 14820,
      "value_count": 9,
      "review_count": 0,
      "can_reprocess": true
    },
    {
      "document_id": 8,
      "document_name": "northwind_dec25_scanned.pdf",
      "document_type": null,
      "document_type_confidence": null,
      "mime_type": "application/pdf",
      "status": "failed",
      "version": 1,
      "source": "drive",
      "drive_file_id": "1nW8Dec25Scan",
      "web_view_link": "https://drive.google.com/file/d/1nW8Dec25Scan/view?usp=drivesdk",
      "search_indexed": false,
      "attempts": 3,
      "last_error": "Anthropic API 429 rate_limit_error (gave up after 3 attempts)",
      "drive_modified_at": "2026-10-15T09:20:11Z",
      "processed_at": null,
      "created_at": "2026-10-16T18:41:42Z",
      "input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "extraction_model": null,
      "extraction_source": null,
      "latency_ms": null,
      "value_count": 0,
      "review_count": 0,
      "can_reprocess": true
    }
  ],
  "total": 20,
  "page": 1,
  "page_size": 50,
  "sort": "-created_at"
};
export const CONTRACT_SEARCH: SearchResponse = {
  "query": "meridian duration",
  "min_query_length": 2,
  "groups": {
    "funds": [
      {
        "fund_id": 3,
        "fund_name": "Meridian Rates Relative Value Fund",
        "manager": "Meridian Capital Advisors",
        "matched_alias": "Meridian Rates RV Fund",
        "aliases": [{"alias": "Meridian Rates RV Fund", "confirmed": false}],
        "score": 0.42
      }
    ],
    "accounts": [],
    "documents": [
      {
        "document_id": 9,
        "document_name": "meridian_rv_tearsheet_2026-01.pdf",
        "document_type": "factsheet",
        "status": "done",
        "web_view_link": "https://drive.google.com/file/d/1mRv01Tear/view?usp=drivesdk",
        "score": 0.36
      },
      {
        "document_id": 19,
        "document_name": "meridian_pm_note_feb2026.eml",
        "document_type": "email",
        "status": "done",
        "web_view_link": "https://drive.google.com/file/d/1mRvPmNote/view?usp=drivesdk",
        "score": 0.33
      }
    ],
    "chunks": [
      {
        "chunk_id": 88,
        "document_id": 9,
        "document_name": "meridian_rv_tearsheet_2026-01.pdf",
        "document_type": "factsheet",
        "page": 1,
        "chunk_index": 1,
        "headline_html": "<mark>Meridian</mark> Capital Advisors … sold off. We reduced <mark>duration</mark> from 3.4 to 2.1 years ahead of the March meeting and remain cautious on term premium.",
        "rank": 0.31,
        "web_view_link": "https://drive.google.com/file/d/1mRv01Tear/view?usp=drivesdk"
      },
      {
        "chunk_id": 131,
        "document_id": 19,
        "document_name": "meridian_pm_note_feb2026.eml",
        "document_type": "email",
        "page": 1,
        "chunk_index": 0,
        "headline_html": "We cut <mark>duration</mark> earlier in the year and have not added it back … Portfolio Manager, <mark>Meridian</mark> Rates Relative Value Fund",
        "rank": 0.22,
        "web_view_link": "https://drive.google.com/file/d/1mRvPmNote/view?usp=drivesdk"
      }
    ]
  },
  "total": 5,
  "took_ms": 38
};
export const CONTRACT_REVIEW_LIST: ReviewListResponse = {
  "items": [
    {
      "id": "returns:214",
      "kind": "conflict",
      "target_table": "returns",
      "target_id": 214,
      "status": "conflict",
      "review_reasons": [
        "conflict"
      ],
      "reasons": [
        {
          "code": "conflict",
          "label": "Conflict",
          "message": "2 documents report different values for Northwind Global Macro Fund · Jan 2026 · MTD net: +1.92% and +1.89%."
        }
      ],
      "title": "Northwind Global Macro Fund · Jan 2026 · MTD net",
      "subject": {
        "fund_id": 2,
        "fund_name": "Northwind Global Macro Fund",
        "account_id": null,
        "account_name": null,
        "period_end": "2026-01-31",
        "period_label": "Jan-26",
        "return_type": "MTD",
        "net_gross": "net",
        "metric": null,
        "holding_name": null
      },
      "detail": {
        "conflict": {
          "conflicting_ids": [
            231
          ],
          "tolerance": 0.0001
        }
      },
      "current": null,
      "fields": null,
      "candidates": [
        {
          "table": "returns",
          "id": 214,
          "field": "value",
          "value": 0.0192,
          "value_pct": 1.92,
          "value_kind": "percent",
          "unit": null,
          "currency": null,
          "status": "conflict",
          "review_reasons": [
            "conflict"
          ],
          "provenance": {
            "page": 1,
            "source_snippet": "Perf. (net) Jan-26 1.92%",
            "confidence": 0.93,
            "snippet_verified": true
          },
          "document_id": 7,
          "document_name": "northwind_monthly_jan26.pdf",
          "document_type": "factsheet",
          "web_view_link": "https://drive.google.com/file/d/1nW4Jan26Factsheet/view?usp=drivesdk",
          "extraction_model": "claude-opus-5-5"
        },
        {
          "table": "returns",
          "id": 231,
          "field": "value",
          "value": 0.0189,
          "value_pct": 1.89,
          "value_kind": "percent",
          "unit": null,
          "currency": null,
          "status": "conflict",
          "review_reasons": [
            "conflict"
          ],
          "provenance": {
            "page": 1,
            "source_snippet": "Jan 2026 +1.89%",
            "confidence": 0.9,
            "snippet_verified": true
          },
          "document_id": 18,
          "document_name": "northwind_flash_update_jan2026.eml",
          "document_type": "email",
          "web_view_link": "https://drive.google.com/file/d/1nWFlashJan/view?usp=drivesdk",
          "extraction_model": "claude-opus-5-5"
        }
      ],
      "alias": null,
      "allowed_actions": [
        "choose",
        "edit"
      ],
      "document_id": 7,
      "document_name": "northwind_monthly_jan26.pdf",
      "created_at": "2026-10-16T18:46:10Z"
    },
    {
      "id": "fund_aliases:9",
      "kind": "alias",
      "target_table": "fund_aliases",
      "target_id": 9,
      "status": "needs_review",
      "review_reasons": [
        "alias_match"
      ],
      "reasons": [
        {
          "code": "alias_match",
          "label": "Alias match",
          "message": "“Alder Asia Fund SPC – Class A” was matched to Alder Asia Fund (score 91.4)."
        }
      ],
      "title": "“Alder Asia Fund SPC – Class A” → Alder Asia Fund",
      "subject": {
        "fund_id": 4,
        "fund_name": "Alder Asia Fund",
        "account_id": null,
        "account_name": null,
        "period_end": null,
        "period_label": null,
        "return_type": null,
        "net_gross": null,
        "metric": null,
        "holding_name": null
      },
      "detail": {
        "alias_match": {
          "alias": "Alder Asia Fund SPC – Class A",
          "match_score": 91.4,
          "fund_id": 4
        }
      },
      "current": null,
      "fields": null,
      "candidates": null,
      "alias": {
        "entity": "fund",
        "alias_id": 9,
        "alias": "Alder Asia Fund SPC – Class A",
        "match_method": "fuzzy",
        "match_score": 91.4,
        "suggested_id": 4,
        "suggested_name": "Alder Asia Fund",
        "source_document_id": 11,
        "source_document_name": "alder_asia_fund_march2026.pdf",
        "affected_rows": 4
      },
      "allowed_actions": [
        "confirm_alias",
        "reject_alias"
      ],
      "document_id": 11,
      "document_name": "alder_asia_fund_march2026.pdf",
      "created_at": "2026-10-16T18:47:02Z"
    },
    {
      "id": "returns:188",
      "kind": "value",
      "target_table": "returns",
      "target_id": 188,
      "status": "needs_review",
      "review_reasons": [
        "ytd_mismatch"
      ],
      "reasons": [
        {
          "code": "ytd_mismatch",
          "label": "YTD mismatch",
          "message": "Monthly returns for 2026 compound to -0.15% but the stated YTD is +0.15%."
        }
      ],
      "title": "Meridian Rates Relative Value Fund · Feb 2026 · YTD net",
      "subject": {
        "fund_id": 3,
        "fund_name": "Meridian Rates Relative Value Fund",
        "account_id": null,
        "account_name": null,
        "period_end": "2026-02-28",
        "period_label": "Feb 2026",
        "return_type": "YTD",
        "net_gross": "net",
        "metric": null,
        "holding_name": null
      },
      "detail": {
        "ytd_mismatch": {
          "year": 2026,
          "stated": 0.0015,
          "computed": -0.0015076,
          "difference": 0.0030076,
          "tolerance": 0.0005,
          "months": [
            {
              "period_end": "2026-01-31",
              "value": -0.0036,
              "return_id": 175
            },
            {
              "period_end": "2026-02-28",
              "value": 0.0021,
              "return_id": 187
            }
          ]
        }
      },
      "current": {
        "table": "returns",
        "id": 188,
        "field": "value",
        "value": 0.0015,
        "value_pct": 0.15,
        "value_kind": "percent",
        "unit": null,
        "currency": null,
        "status": "needs_review",
        "review_reasons": [
          "ytd_mismatch"
        ],
        "provenance": {
          "page": 1,
          "source_snippet": "YTD 0.15%",
          "confidence": 0.92,
          "snippet_verified": true
        },
        "document_id": 13,
        "document_name": "meridian_rv_tearsheet_2026-02.pdf",
        "document_type": "factsheet",
        "web_view_link": "https://drive.google.com/file/d/1mRv02Tear/view?usp=drivesdk",
        "extraction_model": "claude-opus-5-5"
      },
      "fields": null,
      "candidates": null,
      "alias": null,
      "allowed_actions": [
        "accept",
        "edit"
      ],
      "document_id": 13,
      "document_name": "meridian_rv_tearsheet_2026-02.pdf",
      "created_at": "2026-10-16T18:46:55Z"
    }
  ],
  "total": 6,
  "page": 1,
  "page_size": 3,
  "sort": "-created_at",
  "counts": {
    "by_kind": {
      "value": 3,
      "conflict": 1,
      "alias": 2
    },
    "by_reason": {
      "conflict": 1,
      "alias_match": 2,
      "ytd_mismatch": 1,
      "rollforward_mismatch": 1,
      "low_confidence": 1
    }
  }
};
export const CONTRACT_REVIEW_STATEMENT: ReviewItem = {
  "id": "statements:12",
  "kind": "value",
  "target_table": "statements",
  "target_id": 12,
  "status": "needs_review",
  "review_reasons": [
    "rollforward_mismatch"
  ],
  "reasons": [
    {
      "code": "rollforward_mismatch",
      "label": "Roll-forward mismatch",
      "message": "Opening 12,400,000.00 + contributions 500,000.00 - withdrawals 0.00 + P&L 180,000.00 - fees 0.00 = 13,080,000.00, but the stated closing balance is 13,100,000.00 (difference 20,000.00 USD)."
    }
  ],
  "title": "Acme Family Office LLC · XXXX-4471 · Jan 2026",
  "subject": {
    "fund_id": null,
    "fund_name": null,
    "account_id": 1,
    "account_name": "Acme Family Office LLC",
    "period_end": "2026-01-31",
    "period_label": "January 2026",
    "return_type": null,
    "net_gross": null,
    "metric": null,
    "holding_name": null
  },
  "detail": {
    "rollforward_mismatch": {
      "opening_balance": 12400000.0,
      "contributions": 500000.0,
      "withdrawals": 0.0,
      "pnl": 180000.0,
      "fees": 0.0,
      "expected_closing": 13080000.0,
      "stated_closing": 13100000.0,
      "difference": 20000.0,
      "tolerance": 1.0,
      "currency": "USD"
    }
  },
  "current": {
    "table": "statements",
    "id": 12,
    "field": "closing_balance",
    "value": 13100000.0,
    "value_pct": null,
    "value_kind": "money",
    "unit": null,
    "currency": "USD",
    "status": "needs_review",
    "review_reasons": [
      "rollforward_mismatch"
    ],
    "provenance": {
      "page": 1,
      "source_snippet": "Closing balance 13,100,000.00",
      "confidence": 0.95,
      "snippet_verified": true
    },
    "document_id": 15,
    "document_name": "acme_family_office_statement_2026-01.pdf",
    "document_type": "account_statement",
    "web_view_link": null,
    "extraction_model": "claude-opus-5-5"
  },
  "fields": [
    {
      "table": "statements",
      "id": 12,
      "field": "opening_balance",
      "value": 12400000.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Opening balance 12,400,000.00",
        "confidence": 0.96,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {
      "table": "statements",
      "id": 12,
      "field": "contributions",
      "value": 500000.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Contributions 500,000.00",
        "confidence": 0.96,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {
      "table": "statements",
      "id": 12,
      "field": "withdrawals",
      "value": 0.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Withdrawals 0.00",
        "confidence": 0.95,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {
      "table": "statements",
      "id": 12,
      "field": "pnl",
      "value": 180000.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Investment gain / (loss) 180,000.00",
        "confidence": 0.94,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {
      "table": "statements",
      "id": 12,
      "field": "fees",
      "value": 0.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Fees 0.00",
        "confidence": 0.93,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {
      "table": "statements",
      "id": 12,
      "field": "closing_balance",
      "value": 13100000.0,
      "value_pct": null,
      "value_kind": "money",
      "unit": null,
      "currency": "USD",
      "status": "needs_review",
      "review_reasons": [
        "rollforward_mismatch"
      ],
      "provenance": {
        "page": 1,
        "source_snippet": "Closing balance 13,100,000.00",
        "confidence": 0.95,
        "snippet_verified": true
      },
      "document_id": 15,
      "document_name": "acme_family_office_statement_2026-01.pdf",
      "document_type": "account_statement",
      "web_view_link": null,
      "extraction_model": "claude-opus-5-5"
    },
    {"table": "statements", "id": 12, "field": "period_start", "value": "2026-01-01", "value_pct": null, "value_kind": "date", "unit": null, "currency": null, "status": "needs_review", "review_reasons": ["rollforward_mismatch"], "provenance": {"page": 1, "source_snippet": "Statement period: January 1 - 31, 2026", "confidence": 0.95, "snippet_verified": true}, "document_id": 15, "document_name": "acme_family_office_statement_2026-01.pdf", "document_type": "account_statement", "web_view_link": null, "extraction_model": "claude-opus-5-5"},
    {"table": "statements", "id": 12, "field": "period_end", "value": "2026-01-31", "value_pct": null, "value_kind": "date", "unit": null, "currency": null, "status": "needs_review", "review_reasons": ["rollforward_mismatch"], "provenance": {"page": 1, "source_snippet": "Statement period: January 1 - 31, 2026", "confidence": 0.95, "snippet_verified": true}, "document_id": 15, "document_name": "acme_family_office_statement_2026-01.pdf", "document_type": "account_statement", "web_view_link": null, "extraction_model": "claude-opus-5-5"}
  ],
  "candidates": null,
  "alias": null,
  "allowed_actions": [
    "accept",
    "edit"
  ],
  "document_id": 15,
  "document_name": "acme_family_office_statement_2026-01.pdf",
  "created_at": "2026-10-16T18:45:02Z"
};
export const CONTRACT_REVIEW_ACTION_RESPONSE: ReviewActionResponse = {
  "id": "returns:214",
  "action": "choose",
  "resolved": true,
  "review_action_ids": [
    31
  ],
  "affected": [
    {
      "table": "returns",
      "id": 214,
      "change": "status_changed",
      "status": "ok"
    },
    {
      "table": "returns",
      "id": 231,
      "change": "deleted",
      "status": null
    }
  ],
  "remaining_open": 5
};

// ---- U3 / U4 contract examples ------------------------------------------------------

export const AUTH_CONNECTED: AuthStatusResponse = {
  mode: 'drive',
  connected: true,
  google_email: 'sj.demo@gmail.com',
  // as the real api sends them (contract example): drive.readonly plus the two identity scopes, sorted
  scopes: ['https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/userinfo.email', 'openid'],
  connected_at: '2026-10-16T18:40:02Z',
  folder: { source: 'drive', folder_id: '1Q8vXk2mFundDocs', folder_name: 'Fund Docs' },
};

export const AUTH_DEMO: AuthStatusResponse = {
  mode: 'demo',
  connected: false,
  google_email: null,
  scopes: [],
  connected_at: null,
  folder: { source: 'local', folder_id: null, folder_name: 'sample_docs' },
};

export const AUTH_DISCONNECTED: AuthStatusResponse = {
  mode: 'drive',
  connected: false,
  google_email: null,
  scopes: [],
  connected_at: null,
  folder: null,
};

export const DRIVE_ROOT: DriveFoldersResponse = {
  parent: { folder_id: 'root', folder_name: 'My Drive', parent_id: null, is_root: true },
  items: [
    { folder_id: '1Q8vXk2mFundDocs', folder_name: 'Fund Docs', modified_at: '2026-10-15T09:12:44Z' },
    { folder_id: '1Zp3TaxArchive', folder_name: 'Tax archive', modified_at: '2026-04-02T17:01:10Z' },
  ],
  next_page_token: null,
};

export const DRIVE_FUND_DOCS_EMPTY: DriveFoldersResponse = {
  parent: { folder_id: '1Q8vXk2mFundDocs', folder_name: 'Fund Docs', parent_id: 'root', is_root: false },
  items: [],
  next_page_token: null,
};

/** The POST /api/ask contract example (guard error, SQL + passage, unverified "0.92", flagged). */
export const CONTRACT_ASK: AskResponse = {
  answer:
    "**Kestrel Quant Equity Fund LP** had the best January 2026 net return at **+2.84%** [1], a lead of 0.92 percentage points over Northwind Global Macro Fund at +1.92% [1]. Note that Northwind's January figure is flagged as a **conflict**: its flash email reported +1.89% [1].\n\nKestrel attributed the month to short-term reversal signals in US large caps, and reduced gross exposure from 3.1x to 2.6x as dispersion compressed in the final week [2].",
  citations: [
    {
      id: 1,
      type: 'sql',
      sql: "SELECT fund_name, value_pct, status, document_name FROM v_returns WHERE period_year = 2026 AND period_month = 1 AND return_type = 'MTD' AND net_gross = 'net' ORDER BY value_pct DESC LIMIT 200",
      columns: ['fund_name', 'value_pct', 'status', 'document_name'],
      rows: [
        ['Kestrel Quant Equity Fund LP', 2.84, 'ok', 'kestrel_factsheet_2026-01.pdf'],
        ['Northwind Global Macro Fund', 1.92, 'conflict', 'northwind_monthly_jan26.pdf'],
        ['Northwind Global Macro Fund', 1.89, 'conflict', 'northwind_flash_update_jan2026.eml'],
        ['Alder Asia Fund', 1.1, 'ok', 'alder_asia_fund_january2026.pdf'],
        ['Harbor Credit Opportunities Fund', 0.71, 'ok', 'harbor_admin_nav_export_2026-03.csv'],
        ['Alder Dubai Fund', 0.48, 'ok', 'alder_dubai_fund_january2026.pdf'],
        ['Meridian Rates Relative Value Fund', -0.36, 'ok', 'meridian_rv_tearsheet_2026-01.pdf'],
      ],
      row_count: 7,
      truncated: false,
      flagged_row_count: 2,
    },
    {
      id: 2,
      type: 'passage',
      chunk_id: 57,
      document_id: 3,
      document_name: 'kestrel_factsheet_2026-01.pdf',
      document_type: 'factsheet',
      page: 2,
      text: 'January gains came primarily from short-term reversal signals in US large caps. As dispersion compressed in the final week, we reduced gross exposure from 3.1x to 2.6x. Factor exposures remained tightly controlled; net beta to the S&P 500 averaged 0.02.',
      web_view_link: 'https://drive.google.com/file/d/1kT7aKestrelJan26/view?usp=drivesdk',
    },
  ],
  steps: [
    {
      index: 1,
      tool: 'query_sql',
      input: { sql: 'SELECT f.canonical_name, r.value FROM returns r JOIN funds f ON f.id = r.fund_id' },
      result_summary: 'Rejected by SQL guard',
      is_guard_error: true,
      error: 'Only these views may be queried: v_returns, v_fund_facts, v_statements, v_holdings, v_funds, v_accounts, v_documents (found: returns, funds).',
      citation_ids: [],
      duration_ms: 3,
    },
    {
      index: 2,
      tool: 'query_sql',
      input: { sql: "SELECT fund_name, value_pct, status, document_name FROM v_returns WHERE period_year = 2026 AND period_month = 1 AND return_type = 'MTD' AND net_gross = 'net' ORDER BY value_pct DESC" },
      result_summary: '7 rows',
      is_guard_error: false,
      error: null,
      citation_ids: [1],
      duration_ms: 21,
    },
    {
      index: 3,
      tool: 'search_documents',
      input: { query: 'Kestrel January performance commentary', k: 8, fund: 'Kestrel Quant Equity Fund LP' },
      result_summary: '8 passages (FTS + vector, RRF)',
      is_guard_error: false,
      error: null,
      citation_ids: [2],
      duration_ms: 142,
    },
  ],
  unverified_numbers: ['0.92'],
  uses_flagged_data: true,
  stop_reason: 'answered',
  model: 'claude-opus-5-5',
  usage: { input_tokens: 9120, output_tokens: 410 },
  duration_ms: 6400,
};

/**
 * Q2 fix: the real Northwind Jan conflict has three candidates. The Feb factsheet's grid
 * repeats Jan at +1.92%, agreeing with the Jan factsheet; the flash email says +1.89%.
 */
const NW_CONFLICT = CONTRACT_REVIEW_LIST.items[0];
export const NORTHWIND_JAN_3: ReviewItem = {
  ...NW_CONFLICT,
  detail: { conflict: { conflicting_ids: [219, 231], tolerance: 0.0001 } },
  candidates: [
    NW_CONFLICT.candidates![0],
    {
      ...NW_CONFLICT.candidates![0],
      id: 219,
      provenance: { page: 1, source_snippet: 'Perf. (net) Jan-26 1.92%', confidence: 0.94, snippet_verified: true },
      document_id: 6,
      document_name: 'northwind_monthly_feb26.pdf',
      web_view_link: null,
    },
    NW_CONFLICT.candidates![1],
  ],
};

/** `choose 214` on NORTHWIND_JAN_3: 219 is kept (agrees), only 231 is deleted. */
export const CHOOSE_KEEPS_AGREEING: ReviewActionResponse = {
  id: 'returns:214',
  action: 'choose',
  resolved: true,
  review_action_ids: [31, 32, 33],
  affected: [
    { table: 'returns', id: 214, change: 'status_changed', status: 'ok' },
    { table: 'returns', id: 219, change: 'status_changed', status: 'ok' },
    { table: 'returns', id: 231, change: 'deleted', status: null },
  ],
  remaining_open: 5,
};

/**
 * BUG-Q3-01 option 3: alias items that also allow `make_canonical` (the contract's
 * `fund_aliases:9` example plus the new action), the Q3a Northwind case, and an
 * account alias.
 */
const ALIAS_BASE = CONTRACT_REVIEW_LIST.items[1];
export const NORTHWIND_ALIAS_CANONICAL: ReviewItem = {
  ...ALIAS_BASE,
  id: 'fund_aliases:10',
  target_id: 10,
  title: '“Northwind Global Macro Fund” → Northwind Macro Fund Ltd',
  subject: { ...ALIAS_BASE.subject, fund_id: 2, fund_name: 'Northwind Macro Fund Ltd' },
  detail: { alias_match: { alias: 'Northwind Global Macro Fund', match_score: 100, fund_id: 2 } },
  alias: {
    ...ALIAS_BASE.alias!,
    alias_id: 10,
    alias: 'Northwind Global Macro Fund',
    match_score: 100,
    suggested_id: 2,
    suggested_name: 'Northwind Macro Fund Ltd',
  },
  allowed_actions: ['confirm_alias', 'reject_alias', 'make_canonical'],
};
export const ACCOUNT_ALIAS_CANONICAL: ReviewItem = {
  ...ALIAS_BASE,
  id: 'account_aliases:3',
  target_table: 'account_aliases',
  target_id: 3,
  title: '“Acme Family Office” → Acme Family Office LLC',
  subject: { ...ALIAS_BASE.subject, fund_id: null, fund_name: null, account_id: 1, account_name: 'Acme Family Office LLC' },
  detail: { alias_match: { alias: 'Acme Family Office', match_score: 93.1, account_id: 1 } },
  alias: {
    ...ALIAS_BASE.alias!,
    entity: 'account',
    alias_id: 3,
    alias: 'Acme Family Office',
    match_score: 93.1,
    suggested_id: 1,
    suggested_name: 'Acme Family Office LLC',
  },
  allowed_actions: ['confirm_alias', 'reject_alias', 'make_canonical'],
};
export const MAKE_CANONICAL_RESPONSE: ReviewActionResponse = {
  id: 'fund_aliases:10',
  action: 'make_canonical',
  resolved: true,
  review_action_ids: [40],
  affected: [{ table: 'funds', id: 2, change: 'renamed', status: null }],
  remaining_open: 5,
};

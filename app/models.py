"""SQLAlchemy models (PRD §7). The schema itself is owned by the Alembic migrations
in `migrations/versions/`; these models mirror it (tests/test_schema.py checks the two
agree) and are the autogenerate target.

Conventions
- Enumerations are TEXT columns with named CHECK constraints (easy to extend in a
  migration; plain strings in SQL for the Ask tool). Allowed values live in the
  tuples below and are the single source of truth for both models and migration.
- Value rows (returns, fund_facts, statements, statement_holdings) carry their review
  state inline: `status` (ok / needs_review / conflict) plus `review_reasons` (text[]
  drawn from REVIEW_REASONS) and `review_detail` (JSONB: expected vs actual,
  conflicting row ids, ...). Invariants enforced by CHECKs:
    status = 'ok'        <=> review_reasons is empty
    status = 'conflict'  <=> 'conflict' = ANY(review_reasons)
- Document versions: one row per (drive_file_id, md5_checksum). `version` counts up
  per drive_file_id and exactly one row per drive_file_id has `is_latest = true`.
  Query views only expose latest, non-removed documents.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---- allowed values ------------------------------------------------------------

DOCUMENT_STATUSES = ("pending", "processing", "done", "failed", "needs_review", "unsupported", "removed")
DOCUMENT_TYPES = ("factsheet", "account_statement", "performance_report", "email", "other")
DOCUMENT_SOURCES = ("drive", "local")
# Where documents.text_content came from: the file's own text, or the extractor's
# transcription (PDF without a text layer). Snippets are only verified against `file`.
TEXT_SOURCES = ("file", "transcription")
VALUE_STATUSES = ("ok", "needs_review", "conflict")
RETURN_TYPES = ("MTD", "QTD", "YTD", "ITD", "annualized", "unknown")
NET_GROSS = ("net", "gross", "unknown")
EXTRACTION_STATUSES = ("ok", "failed")
EXTRACTION_SOURCES = ("claude", "fixture")
ALIAS_MATCH_METHODS = ("exact", "fuzzy", "new", "manual")
# Review reasons (FR-6, FR-13, §17). The first eight are the FR-13/FR-6 list; the last
# three cover §17 (unlabeled net/gross or return type -> unknown + review) and values
# that could not be normalized.
REVIEW_REASONS = (
    "low_confidence",
    "ytd_mismatch",
    "rollforward_mismatch",
    "alias_match",
    "conflict",
    "snippet_not_found",
    "future_date",
    "out_of_range",
    "net_gross_unknown",
    "return_type_unknown",
    "unparseable_value",
)
REVIEW_TARGET_TABLES = (
    "returns",
    "fund_facts",
    "statements",
    "statement_holdings",
    "fund_aliases",
    "account_aliases",
)

EMBEDDING_DIM = 384
READONLY_ROLE = "app_readonly"
QUERY_VIEWS = ("v_returns", "v_fund_facts", "v_statements", "v_holdings", "v_funds", "v_accounts", "v_documents")


def sql_in(column: str, values: tuple[str, ...]) -> str:
    """`column IN ('a', 'b')` for CHECK constraints (values are module constants)."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def sql_text_array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(repr(v) for v in values) + "]::text[]"


def review_checks(table: str) -> tuple[CheckConstraint, ...]:
    """CHECKs shared by every value table (status/reason invariants)."""
    return (
        CheckConstraint(sql_in("status", VALUE_STATUSES), name=f"ck_{table}_status"),
        CheckConstraint(f"review_reasons <@ {sql_text_array(REVIEW_REASONS)}", name=f"ck_{table}_review_reasons"),
        CheckConstraint(
            "(status = 'ok') = (cardinality(review_reasons) = 0)", name=f"ck_{table}_status_reasons"
        ),
        CheckConstraint(
            "(status = 'conflict') = ('conflict' = ANY(review_reasons))", name=f"ck_{table}_conflict_reason"
        ),
    )


def confidence_check(table: str) -> CheckConstraint:
    return CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name=f"ck_{table}_confidence")


def page_check(table: str) -> CheckConstraint:
    return CheckConstraint("page IS NULL OR page >= 1", name=f"ck_{table}_page")


# ---- base / mixins ---------------------------------------------------------------


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at() -> Mapped[datetime]:
    # Maintained by the set_updated_at() trigger in the DB; onupdate covers ORM flushes.
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


# ---- auth / sync ------------------------------------------------------------------


class OAuthCredential(Base):
    """Single-user app: at most one row (id = 1)."""

    __tablename__ = "oauth_credentials"
    __table_args__ = (CheckConstraint("id = 1", name="ck_oauth_credentials_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    google_email: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet token
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class SyncState(Base):
    """Watched folder + Drive change cursor. Single row (id = 1). The api requests a
    sync/reconcile by setting *_requested_at; the worker clears it (stateless, FR-4)."""

    __tablename__ = "sync_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_sync_state_singleton"),
        CheckConstraint(sql_in("source", DOCUMENT_SOURCES), name="ck_sync_state_source"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'drive'"))
    folder_id: Mapped[str | None] = mapped_column(Text)
    folder_name: Mapped[str | None] = mapped_column(Text)
    page_token: Mapped[str | None] = mapped_column(Text)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


# ---- documents ----------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # FR-4/§12 idempotency: an unchanged re-add hits this key; a changed checksum
        # is a new row with version + 1.
        UniqueConstraint("drive_file_id", "md5_checksum", name="uq_documents_file_checksum"),
        UniqueConstraint("drive_file_id", "version", name="uq_documents_file_version"),
        Index("uq_documents_latest", "drive_file_id", unique=True, postgresql_where=text("is_latest")),
        CheckConstraint(sql_in("status", DOCUMENT_STATUSES), name="ck_documents_status"),
        CheckConstraint(
            f"document_type IS NULL OR {sql_in('document_type', DOCUMENT_TYPES)}", name="ck_documents_document_type"
        ),
        CheckConstraint(sql_in("source", DOCUMENT_SOURCES), name="ck_documents_source"),
        CheckConstraint(
            f"text_source IS NULL OR {sql_in('text_source', TEXT_SOURCES)}", name="ck_documents_text_source"
        ),
        CheckConstraint("version >= 1", name="ck_documents_version"),
        CheckConstraint("attempts >= 0", name="ck_documents_attempts"),
        CheckConstraint(
            "document_type_confidence IS NULL OR (document_type_confidence >= 0 AND document_type_confidence <= 1)",
            name="ck_documents_document_type_confidence",
        ),
        Index("ix_documents_status", "status"),
        Index(
            "ix_documents_claim",
            "next_attempt_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_documents_search_unindexed",
            "id",
            postgresql_where=text("status IN ('done', 'needs_review') AND NOT search_indexed"),
        ),
        Index("ix_documents_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )

    id: Mapped[int] = pk()
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'drive'"))
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Drive md5Checksum; for files without one (Google Sheets, local demo files) the
    # worker stores a synthetic checksum (md5 of bytes, or "drive-version:<n>").
    md5_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    web_view_link: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(Text)
    document_type_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    search_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_content: Mapped[str | None] = mapped_column(Text)
    text_source: Mapped[str | None] = mapped_column(Text)  # 'file' | 'transcription' (0003)
    drive_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    extractions: Mapped[list[Extraction]] = relationship(back_populates="document", passive_deletes=True)
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="document", passive_deletes=True)


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (
        CheckConstraint(sql_in("status", EXTRACTION_STATUSES), name="ck_extractions_status"),
        CheckConstraint(sql_in("source", EXTRACTION_SOURCES), name="ck_extractions_source"),
        Index("ix_extractions_document_id", "document_id", "created_at"),
    )

    id: Mapped[int] = pk()
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'claude'"))
    model: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    error: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()

    document: Mapped[Document] = relationship(back_populates="extractions")


# ---- funds / accounts --------------------------------------------------------------------


class Fund(Base):
    __tablename__ = "funds"
    __table_args__ = (
        Index("uq_funds_canonical_name_lower", func.lower(text("canonical_name")), unique=True),
        Index(
            "ix_funds_canonical_name_trgm",
            "canonical_name",
            postgresql_using="gin",
            postgresql_ops={"canonical_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = pk()
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    manager: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    aliases: Mapped[list[FundAlias]] = relationship(back_populates="fund", passive_deletes=True)


class FundAlias(Base):
    """Name variants seen in documents. An alias maps to exactly one fund
    (case-insensitive). `match_method = 'fuzzy' AND NOT confirmed` is a review item
    with reason alias_match (FR-6, FR-13)."""

    __tablename__ = "fund_aliases"
    __table_args__ = (
        Index("uq_fund_aliases_alias_lower", func.lower(text("alias")), unique=True),
        Index("ix_fund_aliases_fund_id", "fund_id"),
        Index("ix_fund_aliases_source_document_id", "source_document_id"),
        Index("ix_fund_aliases_alias_trgm", "alias", postgresql_using="gin", postgresql_ops={"alias": "gin_trgm_ops"}),
        CheckConstraint(sql_in("match_method", ALIAS_MATCH_METHODS), name="ck_fund_aliases_match_method"),
        CheckConstraint(
            "match_score IS NULL OR (match_score >= 0 AND match_score <= 100)", name="ck_fund_aliases_match_score"
        ),
    )

    id: Mapped[int] = pk()
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    match_method: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'exact'"))
    match_score: Mapped[float | None] = mapped_column(Float)  # rapidfuzz score 0-100
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    fund: Mapped[Fund] = relationship(back_populates="aliases")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        Index(
            "uq_accounts_name_number",
            func.lower(text("canonical_name")),
            func.coalesce(text("account_number_masked"), text("''")),
            unique=True,
        ),
        Index(
            "ix_accounts_canonical_name_trgm",
            "canonical_name",
            postgresql_using="gin",
            postgresql_ops={"canonical_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = pk()
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_number_masked: Mapped[str | None] = mapped_column(Text)
    custodian: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    aliases: Mapped[list[AccountAlias]] = relationship(back_populates="account", passive_deletes=True)


class AccountAlias(Base):
    __tablename__ = "account_aliases"
    __table_args__ = (
        Index("uq_account_aliases_alias_lower", func.lower(text("alias")), unique=True),
        Index("ix_account_aliases_account_id", "account_id"),
        Index("ix_account_aliases_source_document_id", "source_document_id"),
        Index(
            "ix_account_aliases_alias_trgm", "alias", postgresql_using="gin", postgresql_ops={"alias": "gin_trgm_ops"}
        ),
        CheckConstraint(sql_in("match_method", ALIAS_MATCH_METHODS), name="ck_account_aliases_match_method"),
        CheckConstraint(
            "match_score IS NULL OR (match_score >= 0 AND match_score <= 100)", name="ck_account_aliases_match_score"
        ),
    )

    id: Mapped[int] = pk()
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    match_method: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'exact'"))
    match_score: Mapped[float | None] = mapped_column(Float)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    account: Mapped[Account] = relationship(back_populates="aliases")


# ---- extracted values ---------------------------------------------------------------------


class Return(Base):
    """`value` is a decimal fraction: 0.012 = 1.2 %, -0.008 = -0.8 %."""

    __tablename__ = "returns"
    __table_args__ = (
        # One row per natural key per source document (reprocessing cannot duplicate).
        UniqueConstraint(
            "document_id", "fund_id", "period_end", "return_type", "net_gross", name="uq_returns_document_key"
        ),
        # Natural key for cross-document conflict detection: indexed, NOT unique (FR-6).
        Index("ix_returns_natural_key", "fund_id", "period_end", "return_type", "net_gross"),
        Index("ix_returns_extraction_id", "extraction_id"),
        Index("ix_returns_fund_alias_id", "fund_alias_id"),
        Index("ix_returns_flagged", "status", postgresql_where=text("status <> 'ok'")),
        CheckConstraint(sql_in("return_type", RETURN_TYPES), name="ck_returns_return_type"),
        CheckConstraint(sql_in("net_gross", NET_GROSS), name="ck_returns_net_gross"),
        confidence_check("returns"),
        page_check("returns"),
        *review_checks("returns"),
    )

    id: Mapped[int] = pk()
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    extraction_id: Mapped[int | None] = mapped_column(ForeignKey("extractions.id", ondelete="SET NULL"))
    fund_alias_id: Mapped[int | None] = mapped_column(ForeignKey("fund_aliases.id", ondelete="SET NULL"))
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_label: Mapped[str | None] = mapped_column(Text)  # period as written, e.g. "Jan-26"
    return_type: Mapped[str] = mapped_column(Text, nullable=False)
    net_gross: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    page: Mapped[int | None] = mapped_column(Integer)
    source_snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    # true = found in the document text, false = not found, null = check skipped (0003)
    snippet_verified: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    review_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class FundFact(Base):
    """Scalar fund metrics (aum, sharpe, max_drawdown, vol, ...). Numeric metrics use
    `value`; non-numeric ones use `value_text`."""

    __tablename__ = "fund_facts"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "fund_id", "name", "as_of_date",
            name="uq_fund_facts_document_key", postgresql_nulls_not_distinct=True,
        ),
        Index("ix_fund_facts_natural_key", "fund_id", "name", "as_of_date"),
        Index("ix_fund_facts_extraction_id", "extraction_id"),
        Index("ix_fund_facts_fund_alias_id", "fund_alias_id"),
        Index("ix_fund_facts_flagged", "status", postgresql_where=text("status <> 'ok'")),
        confidence_check("fund_facts"),
        page_check("fund_facts"),
        *review_checks("fund_facts"),
    )

    id: Mapped[int] = pk()
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="RESTRICT"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    extraction_id: Mapped[int | None] = mapped_column(ForeignKey("extractions.id", ondelete="SET NULL"))
    fund_alias_id: Mapped[int | None] = mapped_column(ForeignKey("fund_aliases.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    page: Mapped[int | None] = mapped_column(Integer)
    source_snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    # true = found in the document text, false = not found, null = check skipped (0003)
    snippet_verified: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    review_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "account_id", "fund_id", "period_end",
            name="uq_statements_document_key", postgresql_nulls_not_distinct=True,
        ),
        Index("ix_statements_natural_key", "account_id", "period_end", "fund_id"),
        Index("ix_statements_fund_id", "fund_id"),
        Index("ix_statements_extraction_id", "extraction_id"),
        Index("ix_statements_fund_alias_id", "fund_alias_id"),
        Index("ix_statements_account_alias_id", "account_alias_id"),
        Index("ix_statements_flagged", "status", postgresql_where=text("status <> 'ok'")),
        CheckConstraint("period_start IS NULL OR period_start <= period_end", name="ck_statements_period"),
        *review_checks("statements"),
    )

    id: Mapped[int] = pk()
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id", ondelete="RESTRICT"))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    extraction_id: Mapped[int | None] = mapped_column(ForeignKey("extractions.id", ondelete="SET NULL"))
    fund_alias_id: Mapped[int | None] = mapped_column(ForeignKey("fund_aliases.id", ondelete="SET NULL"))
    account_alias_id: Mapped[int | None] = mapped_column(ForeignKey("account_aliases.id", ondelete="SET NULL"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    contributions: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    withdrawals: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    fees: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    currency: Mapped[str | None] = mapped_column(Text)
    # Per-field provenance: {"opening_balance": {"page": 1, "source_snippet": "...", "confidence": 0.9}, ...}
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    review_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    holdings: Mapped[list[StatementHolding]] = relationship(back_populates="statement", passive_deletes=True)


class StatementHolding(Base):
    """`weight` is a decimal fraction of the statement (0.25 = 25 %). `line_no` is the
    0-based row order in the source table (stable pagination, uniqueness per statement)."""

    __tablename__ = "statement_holdings"
    __table_args__ = (
        UniqueConstraint("statement_id", "line_no", name="uq_statement_holdings_line_no"),
        Index("ix_statement_holdings_flagged", "status", postgresql_where=text("status <> 'ok'")),
        CheckConstraint("line_no >= 0", name="ck_statement_holdings_line_no"),
        confidence_check("statement_holdings"),
        page_check("statement_holdings"),
        *review_checks("statement_holdings"),
    )

    id: Mapped[int] = pk()
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    identifier: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    page: Mapped[int | None] = mapped_column(Integer)
    source_snippet: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    # true = found in the document text, false = not found, null = check skipped (0003)
    snippet_verified: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    review_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()

    statement: Mapped[Statement] = relationship(back_populates="holdings")


# ---- retrieval ------------------------------------------------------------------------------


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index"),
        page_check("document_chunks"),
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_document_chunks_embedding_model", "embedding_model"),
    )

    id: Mapped[int] = pk()
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)  # null for CSV row groups
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, text)", persisted=True), nullable=True
    )
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(Text)  # §8: model change -> re-embed
    created_at: Mapped[datetime] = created_at()

    document: Mapped[Document] = relationship(back_populates="chunks")


# ---- review ------------------------------------------------------------------------------------


class ReviewAction(Base):
    """Reviewer decisions (FR-13). Reprocessing recreates value rows with new ids, so
    each decision also records `document_id` + `natural_key` (e.g. {"fund_id": 1,
    "period_end": "2026-01-31", "return_type": "MTD", "net_gross": "net"} or
    {"alias": "Kestrel Fund"}); the pipeline re-applies decisions by that key."""

    __tablename__ = "review_actions"
    __table_args__ = (
        CheckConstraint(sql_in("target_table", REVIEW_TARGET_TABLES), name="ck_review_actions_target_table"),
        Index("ix_review_actions_target", "target_table", "target_id"),
        Index("ix_review_actions_document_id", "document_id"),
    )

    id: Mapped[int] = pk()
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    natural_key: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[Any | None] = mapped_column(JSONB)
    new_value: Mapped[Any | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


TABLES_WITH_UPDATED_AT = tuple(
    t.name for t in Base.metadata.sorted_tables if "updated_at" in t.c
)

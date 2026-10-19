"""Snippet-verification bookkeeping (T5, FR-6; T3 note "record a skipped check").

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16

- `documents.text_source`: where `text_content` came from. `file` = the file's own
  text (PDF text layer, HTML/.eml body, CSV); `transcription` = the extractor's
  per-page transcription, used when a PDF has no text layer (scanned). Snippets are
  only verified against `file` text, otherwise the check would be circular.
- `snippet_verified` on returns, fund_facts and statement_holdings: true = the
  snippet was found in the document text, false = not found (the row then also has
  the `snippet_not_found` reason), null = check skipped (image-only page, or no text).
  Statements keep this per field in `provenance[<field>].snippet_verified`.

No view changes: the query views and their columns are unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE documents ADD COLUMN text_source text;
ALTER TABLE documents ADD CONSTRAINT ck_documents_text_source
    CHECK (text_source IS NULL OR text_source IN ('file', 'transcription'));
ALTER TABLE returns ADD COLUMN snippet_verified boolean;
ALTER TABLE fund_facts ADD COLUMN snippet_verified boolean;
ALTER TABLE statement_holdings ADD COLUMN snippet_verified boolean;
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE statement_holdings DROP COLUMN IF EXISTS snippet_verified;
ALTER TABLE fund_facts DROP COLUMN IF EXISTS snippet_verified;
ALTER TABLE returns DROP COLUMN IF EXISTS snippet_verified;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_text_source;
ALTER TABLE documents DROP COLUMN IF EXISTS text_source;
""")

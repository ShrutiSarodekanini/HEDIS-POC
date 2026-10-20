"""Document intake additions: encrypted storage flag, parent file for unpacked bundles, scan result, OCR engine.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE source_files
    ADD COLUMN encrypted      boolean NOT NULL DEFAULT false,
    ADD COLUMN parent_file_id bigint REFERENCES source_files(id),
    ADD COLUMN scan_result    text CHECK (scan_result IN ('clean','infected','not_scanned','scan_failed')),
    ADD COLUMN detected_kind  text;

ALTER TABLE document_pages
    ADD COLUMN ocr_engine text;

ALTER TABLE documents
    ADD COLUMN conversion_note text;

CREATE INDEX source_files_status_idx ON source_files (processing_status, match_status);
""")


def downgrade() -> None:
    op.execute(r"""
DROP INDEX IF EXISTS source_files_status_idx;
ALTER TABLE documents DROP COLUMN IF EXISTS conversion_note;
ALTER TABLE document_pages DROP COLUMN IF EXISTS ocr_engine;
ALTER TABLE source_files
    DROP COLUMN IF EXISTS detected_kind,
    DROP COLUMN IF EXISTS scan_result,
    DROP COLUMN IF EXISTS parent_file_id,
    DROP COLUMN IF EXISTS encrypted;
""")

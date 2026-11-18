"""Add stored_file and page_image: uploaded originals and rendered page images kept in the database.

stored_file holds the bytes of each uploaded file as they are (not encrypted), keyed by their SHA-256
(source_files.stored_path is "db:<sha256>"). page_image holds the PNG of each converted page as it is
(document_pages.image_path is "db:<document_id>/<page_no>"). Nothing of either is written to disk.

Revision ID: 0018_document_bytes_in_db
Revises: 0017_evidence_library
Create Date: 2026-11-16
"""

from alembic import op

revision = "0018_document_bytes_in_db"
down_revision = "0017_evidence_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE stored_file (
               sha256 text PRIMARY KEY,
               content bytea NOT NULL,
               size_bytes bigint NOT NULL,
               created_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    op.execute(
        """CREATE TABLE page_image (
               document_reference_id text NOT NULL,
               page_no integer NOT NULL,
               content bytea NOT NULL,
               created_at timestamptz NOT NULL DEFAULT now(),
               PRIMARY KEY (document_reference_id, page_no)
           )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE page_image")
    op.execute("DROP TABLE stored_file")

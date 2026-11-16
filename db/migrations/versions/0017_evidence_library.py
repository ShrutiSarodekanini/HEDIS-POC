"""Add evidence_library: the stored evidence documents, kept in the database instead of a folder.

Each row is one document with its bytes and the Synthea patient it belongs to. scripts/db/load_seed.py fills it from
data/seed/stored_evidence; the documents step attaches the rows of a batch's patients through intake.

Revision ID: 0017_evidence_library
Revises: 0014_care_gap_closed_by_user
Create Date: 2026-11-13
"""

from alembic import op

revision = "0017_evidence_library"
down_revision = "0014_care_gap_closed_by_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE evidence_library (
               doc_id text PRIMARY KEY,
               synthea_patient_id text NOT NULL,
               file_name text NOT NULL,
               mime_type text,
               sha256 text NOT NULL,
               content bytea NOT NULL,
               created_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    op.execute("CREATE INDEX ix_evidence_library_patient ON evidence_library (synthea_patient_id)")


def downgrade() -> None:
    op.execute("DROP TABLE evidence_library")

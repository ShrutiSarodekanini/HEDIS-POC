"""Allow partially_completed status on source_batch table.

The ingestion pipeline uses 'partially_completed' when there are warnings or minor errors
but processing succeeded overall. The original constraint was too restrictive.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
ALTER TABLE source_batch DROP CONSTRAINT source_batch_status_check;
ALTER TABLE source_batch ADD CONSTRAINT source_batch_status_check 
    CHECK (status IN ('running', 'completed', 'partially_completed', 'failed'));
""")


def downgrade() -> None:
    op.execute(r"""
ALTER TABLE source_batch DROP CONSTRAINT source_batch_status_check;
ALTER TABLE source_batch ADD CONSTRAINT source_batch_status_check 
    CHECK (status IN ('running', 'completed', 'failed'));
""")

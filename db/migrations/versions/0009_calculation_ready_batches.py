"""Allow loaded batches to wait for an explicitly selected engine calculation.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE batch_run DROP CONSTRAINT batch_run_status_check")
    op.execute(
        """ALTER TABLE batch_run ADD CONSTRAINT batch_run_status_check
           CHECK (status IN ('running', 'ready', 'prioritized', 'failed'))"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE batch_run DROP CONSTRAINT batch_run_status_check")
    op.execute(
        """ALTER TABLE batch_run ADD CONSTRAINT batch_run_status_check
           CHECK (status IN ('running', 'prioritized', 'failed'))"""
    )

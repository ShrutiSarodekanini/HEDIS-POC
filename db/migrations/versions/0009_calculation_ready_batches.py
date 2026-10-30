"""Allow loaded batches to wait for an explicitly selected engine calculation.

Revision ID: 0011_calculation_ready_batches
Revises: 0010_local_auth
Create Date: 2026-10-27
"""

from alembic import op

revision = "0011_calculation_ready_batches"
down_revision = "0010_local_auth"
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

"""Add correlation IDs to extraction runs and attempts.

Revision ID: 0015_extraction_correlation_ids
Revises: 0014_care_gap_closed_by_user
Create Date: 2026-11-12
"""

from alembic import op

revision = "0015_extraction_correlation_ids"
down_revision = "0014_care_gap_closed_by_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE extraction_runs ADD COLUMN correlation_id text")
    op.execute("ALTER TABLE extraction_attempts ADD COLUMN correlation_id text")
    op.execute(
        """UPDATE extraction_attempts a
              SET correlation_id = r.correlation_id
             FROM extraction_runs r
            WHERE a.run_id = r.id
              AND a.correlation_id IS NULL"""
    )
    op.execute("CREATE INDEX ix_extraction_runs_correlation_id ON extraction_runs (correlation_id)")
    op.execute("CREATE INDEX ix_extraction_attempts_correlation_id ON extraction_attempts (correlation_id)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_extraction_attempts_correlation_id")
    op.execute("DROP INDEX ix_extraction_runs_correlation_id")
    op.execute("ALTER TABLE extraction_attempts DROP COLUMN correlation_id")
    op.execute("ALTER TABLE extraction_runs DROP COLUMN correlation_id")

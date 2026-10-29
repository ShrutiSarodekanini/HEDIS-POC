"""Require administrator approval for locally registered users.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user ADD COLUMN access_status text NOT NULL DEFAULT 'approved'")
    op.execute(
        """ALTER TABLE app_user ADD CONSTRAINT app_user_access_status_check
           CHECK (access_status IN ('pending', 'approved', 'rejected'))"""
    )
    op.execute("CREATE INDEX ix_app_user_access_status ON app_user (access_status, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_app_user_access_status")
    op.execute("ALTER TABLE app_user DROP CONSTRAINT app_user_access_status_check")
    op.execute("ALTER TABLE app_user DROP COLUMN access_status")

"""Add care_gap.closed_by with a foreign key to app_user.

Revision ID: 0014_care_gap_closed_by_user
Revises: 0013_reliable_messaging, 0013_fill_star_values_all_10
Create Date: 2026-11-05
"""

from alembic import op

revision = "0014_care_gap_closed_by_user"
down_revision = ("0013_reliable_messaging", "0013_fill_star_values_all_10")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE care_gap ADD COLUMN closed_by bigint")
    op.execute("ALTER TABLE care_gap ADD CONSTRAINT care_gap_closed_by_fk FOREIGN KEY (closed_by) REFERENCES app_user(user_id)")
    op.execute("CREATE INDEX ix_care_gap_closed_by ON care_gap (closed_by, closed_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_care_gap_closed_by")
    op.execute("ALTER TABLE care_gap DROP CONSTRAINT care_gap_closed_by_fk")
    op.execute("ALTER TABLE care_gap DROP COLUMN closed_by")

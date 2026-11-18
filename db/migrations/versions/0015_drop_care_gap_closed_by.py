"""Drop care_gap.closed_by: a gap closes only through an engine run, and team tables get no added columns.

0014 added closed_by to the team table care_gap for a manual "Close gap" button. That button is gone: a reviewer now
asks for an engine rerun with the confirmed supplemental data, and the engine closes the gap. Who asked is kept in the
gap.engine_rerun_requested and gap.closed_by_engine audit events. Values already in closed_by are dropped; the
gap.closed audit events written by the old button still name the user.

Revision ID: 0015_drop_care_gap_closed_by
Revises: 0014_care_gap_closed_by_user
Create Date: 2026-11-12
"""

from alembic import op

revision = "0015_drop_care_gap_closed_by"
down_revision = "0014_care_gap_closed_by_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_care_gap_closed_by")
    op.execute("ALTER TABLE care_gap DROP CONSTRAINT IF EXISTS care_gap_closed_by_fk")
    op.execute("ALTER TABLE care_gap DROP COLUMN IF EXISTS closed_by")


def downgrade() -> None:
    op.execute("ALTER TABLE care_gap ADD COLUMN closed_by bigint")
    op.execute("ALTER TABLE care_gap ADD CONSTRAINT care_gap_closed_by_fk FOREIGN KEY (closed_by) "
               "REFERENCES app_user(user_id)")
    op.execute("CREATE INDEX ix_care_gap_closed_by ON care_gap (closed_by, closed_at DESC)")

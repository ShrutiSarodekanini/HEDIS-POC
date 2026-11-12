"""User roles from UC-HC-02: analyst, abstractor, outreach and auditor alongside admin.

app_user is our table (0008_local_auth), not a team table. Existing "user" accounts become "analyst", which keeps
what they could do before except administrator actions. Each changed account gets a user.role_changed audit event.
The permission each role has is in services/integration/auth.py (PERMISSIONS).

Revision ID: 0016_user_roles
Revises: 0015_drop_care_gap_closed_by
Create Date: 2026-11-12
"""

from alembic import op

revision = "0016_user_roles"
down_revision = "0015_drop_care_gap_closed_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user DROP CONSTRAINT app_user_role_check")
    op.execute("""
        INSERT INTO audit_events (entity_type, entity_id, action, actor, payload)
        SELECT 'app_user', user_id::text, 'user.role_changed', 'migration 0016_user_roles',
               jsonb_build_object('user_id', user_id, 'role_before', 'user', 'role', 'analyst')
          FROM app_user WHERE role = 'user'
    """)
    op.execute("UPDATE app_user SET role = 'analyst', updated_at = now() WHERE role = 'user'")
    op.execute("""ALTER TABLE app_user ADD CONSTRAINT app_user_role_check
                  CHECK (role IN ('admin', 'analyst', 'abstractor', 'outreach', 'auditor'))""")


def downgrade() -> None:
    op.execute("ALTER TABLE app_user DROP CONSTRAINT app_user_role_check")
    op.execute("""
        INSERT INTO audit_events (entity_type, entity_id, action, actor, payload)
        SELECT 'app_user', user_id::text, 'user.role_changed', 'migration 0016_user_roles downgrade',
               jsonb_build_object('user_id', user_id, 'role_before', role, 'role', 'user')
          FROM app_user WHERE role <> 'admin'
    """)
    op.execute("UPDATE app_user SET role = 'user', updated_at = now() WHERE role <> 'admin'")
    op.execute("ALTER TABLE app_user ADD CONSTRAINT app_user_role_check CHECK (role IN ('admin', 'user'))")

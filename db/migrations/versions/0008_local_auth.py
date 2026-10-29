"""Local auth tables for integration login and role-based access.

Adds app-managed users, bearer sessions, and password-reset tokens.
Emails are lowercase-only. Sessions and reset tokens have server-side expiry.

Revision ID: 0010_local_auth
Revises: 0009
Create Date: 2026-10-27
"""

from alembic import op

revision = "0010_local_auth"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE app_user (
    user_id         bigserial PRIMARY KEY,
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('admin', 'user')),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (email = lower(email))
);

CREATE TABLE auth_session (
    session_id      uuid PRIMARY KEY,
    user_id         bigint NOT NULL REFERENCES app_user(user_id),
    token_hash      text NOT NULL UNIQUE,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz
);
CREATE INDEX ix_auth_session_user ON auth_session(user_id);
CREATE INDEX ix_auth_session_expiry ON auth_session(expires_at) WHERE revoked_at IS NULL;

CREATE TABLE password_reset_token (
    reset_id        uuid PRIMARY KEY,
    user_id         bigint NOT NULL REFERENCES app_user(user_id),
    token_hash      text NOT NULL UNIQUE,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    used_at         timestamptz
);
CREATE INDEX ix_password_reset_user ON password_reset_token(user_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE password_reset_token, auth_session, app_user")

"""Add transactional outbox and inbox tables for RabbitMQ delivery.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE message_outbox (
    outbox_id          bigserial PRIMARY KEY,
    message_id         uuid NOT NULL UNIQUE,
    source_message_id  text,
    queue_name         text NOT NULL,
    correlation_id     text,
    payload            jsonb NOT NULL,
    headers            jsonb NOT NULL DEFAULT '{}'::jsonb,
    status             text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'published', 'failed')),
    publish_attempts   integer NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    available_at       timestamptz NOT NULL DEFAULT now(),
    published_at       timestamptz,
    last_error_code    text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'published') = (published_at IS NOT NULL))
);

CREATE UNIQUE INDEX uq_message_outbox_source_queue
    ON message_outbox (source_message_id, queue_name)
    WHERE source_message_id IS NOT NULL;
CREATE INDEX ix_message_outbox_pending
    ON message_outbox (available_at, outbox_id)
    WHERE status = 'pending';

CREATE TABLE message_inbox (
    consumer_name      text NOT NULL,
    message_id         text NOT NULL,
    status             text NOT NULL CHECK (status IN ('processing', 'processed', 'failed')),
    delivery_attempts  integer NOT NULL DEFAULT 1 CHECK (delivery_attempts > 0),
    locked_until       timestamptz,
    error_code         text,
    received_at        timestamptz NOT NULL DEFAULT now(),
    processed_at       timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, message_id),
    CHECK (status <> 'processed' OR processed_at IS NOT NULL)
);

CREATE INDEX ix_message_inbox_status
    ON message_inbox (status, locked_until);
""")


def downgrade() -> None:
    op.execute(r"""
DROP TABLE message_inbox;
DROP TABLE message_outbox;
""")
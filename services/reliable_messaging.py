"""PostgreSQL primitives for reliable RabbitMQ delivery."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.document_intake import db
from services.rabbitmq_config import QUEUES


def message_payload(message: Any) -> dict[str, Any]:
    """Return a JSON-compatible message mapping without changing the caller's object."""
    if is_dataclass(message) and not isinstance(message, type):
        return asdict(message)
    if isinstance(message, dict):
        return dict(message)
    raise TypeError("RabbitMQ messages must be dataclass instances or dictionaries")


def enqueue_outbox(
    conn: psycopg.Connection,
    queue_name: str,
    message: Any,
    *,
    source_message_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """Store one outbound message in the caller's database transaction."""
    if queue_name not in QUEUES:
        raise ValueError(f"Unknown RabbitMQ queue: {queue_name}")
    payload = message_payload(message)
    outgoing_id = message_id or str(uuid4())
    correlation_id = str(payload.get("batch_id", "")) or None
    row = conn.execute(
        """INSERT INTO message_outbox
               (message_id, source_message_id, queue_name, correlation_id, payload, headers)
             VALUES (%s, %s, %s, %s, %s, %s)
             ON CONFLICT DO NOTHING
             RETURNING message_id::text""",
        (
            outgoing_id,
            source_message_id,
            queue_name,
            correlation_id,
            Jsonb(payload),
            Jsonb({"schema_version": 1, "retry_count": 0}),
        ),
    ).fetchone()
    if row:
        return row[0]
    if source_message_id is None:
        raise RuntimeError("Could not enqueue the outbound message")
    existing = conn.execute(
        """SELECT message_id::text FROM message_outbox
             WHERE source_message_id = %s AND queue_name = %s""",
        (source_message_id, queue_name),
    ).fetchone()
    if existing is None:
        raise RuntimeError("Could not find the existing outbound message")
    return existing[0]


def enqueue_outbox_session(
    session: Session,
    queue_name: str,
    message: Any,
    *,
    source_message_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """Store an outbound message in a caller-owned SQLAlchemy transaction."""
    if queue_name not in QUEUES:
        raise ValueError(f"Unknown RabbitMQ queue: {queue_name}")
    payload = message_payload(message)
    outgoing_id = message_id or str(uuid4())
    session.execute(
        text(
            """INSERT INTO message_outbox
                   (message_id, source_message_id, queue_name, correlation_id, payload, headers)
                 VALUES (CAST(:message_id AS uuid), :source_message_id, :queue_name, :correlation_id,
                         CAST(:payload AS jsonb), CAST(:headers AS jsonb))"""
        ),
        {
            "message_id": outgoing_id,
            "source_message_id": source_message_id,
            "queue_name": queue_name,
            "correlation_id": str(payload.get("batch_id", "")) or None,
            "payload": json.dumps(payload),
            "headers": json.dumps({"schema_version": 1, "retry_count": 0}),
        },
    )
    return outgoing_id


def claim_inbox(
    consumer_name: str,
    message_id: str,
    lease_seconds: int = 900,
) -> Literal["claimed", "processed", "busy"]:
    """Claim a delivery or report why it cannot currently be processed."""
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO message_inbox
                   (consumer_name, message_id, status, locked_until)
                 VALUES (%s, %s, 'processing', now() + make_interval(secs => %s))
                 ON CONFLICT (consumer_name, message_id) DO UPDATE
                     SET status = 'processing',
                         delivery_attempts = message_inbox.delivery_attempts + 1,
                         locked_until = EXCLUDED.locked_until,
                         error_code = NULL,
                         updated_at = now()
                   WHERE message_inbox.status = 'failed'
                      OR (message_inbox.status = 'processing' AND message_inbox.locked_until < now())
                 RETURNING status""",
            (consumer_name, message_id, lease_seconds),
        ).fetchone()
        if row is not None:
            return "claimed"
        status = conn.execute(
            """SELECT status FROM message_inbox
                 WHERE consumer_name = %s AND message_id = %s""",
            (consumer_name, message_id),
        ).fetchone()
        return "processed" if status and status[0] == "processed" else "busy"


def complete_inbox(conn: psycopg.Connection, consumer_name: str, message_id: str) -> None:
    """Mark a claimed delivery processed in the caller's transaction."""
    updated = conn.execute(
        """UPDATE message_inbox
              SET status = 'processed', processed_at = now(), locked_until = NULL,
                  error_code = NULL, updated_at = now()
            WHERE consumer_name = %s AND message_id = %s""",
        (consumer_name, message_id),
    ).rowcount
    if updated != 1:
        raise RuntimeError("The RabbitMQ inbox claim no longer exists")


def complete_inbox_separately(consumer_name: str, message_id: str) -> None:
    """Complete a delivery when its processor has no caller-owned transaction."""
    with db.connect() as conn:
        complete_inbox(conn, consumer_name, message_id)


def fail_inbox(consumer_name: str, message_id: str, error_code: str) -> None:
    """Release a failed delivery for retry without storing exception text."""
    with db.connect() as conn:
        conn.execute(
            """UPDATE message_inbox
                  SET status = 'failed', locked_until = NULL, error_code = %s, updated_at = now()
                WHERE consumer_name = %s AND message_id = %s AND status <> 'processed'""",
            (error_code[:120], consumer_name, message_id),
        )
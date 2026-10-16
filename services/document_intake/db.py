"""Database access for document intake."""

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb

from .config import SETTINGS


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """A connection that commits on success and rolls back on error."""
    if not SETTINGS.database_url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    with psycopg.connect(SETTINGS.database_url, connect_timeout=10) as conn:
        yield conn


def audit(conn: psycopg.Connection, entity_type: str, entity_id: str, action: str, payload: dict | None = None,
          actor: str | None = None) -> None:
    """Append an audit event. Audit rows can never be changed or deleted (database rule)."""
    conn.execute(
        "INSERT INTO audit_events (entity_type, entity_id, action, actor, payload) VALUES (%s, %s, %s, %s, %s)",
        (entity_type, str(entity_id), action, actor or SETTINGS.actor, Jsonb(payload) if payload is not None else None),
    )

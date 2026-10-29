"""Publish committed outbox rows to RabbitMQ with publisher confirms."""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from services.document_intake import db
from services.rabbitmq_messages import Publisher

log = logging.getLogger(__name__)
MAX_PUBLISH_ATTEMPTS = 10


def dispatch_once(batch_size: int = 100) -> int:
    """Publish one locked batch of pending outbox rows and return the success count."""
    published = 0
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT outbox_id, message_id::text, queue_name, payload, headers, publish_attempts
                 FROM message_outbox
                WHERE status = 'pending' AND available_at <= now()
                ORDER BY outbox_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s""",
            (batch_size,),
        ).fetchall()
        if not rows:
            return 0
        with Publisher() as publisher:
            for outbox_id, message_id, queue_name, payload, headers, attempts in rows:
                try:
                    publisher.publish(
                        queue_name,
                        _mapping(payload),
                        message_id=message_id,
                        headers=_mapping(headers),
                    )
                    conn.execute(
                        """UPDATE message_outbox
                              SET status = 'published', publish_attempts = publish_attempts + 1,
                                  published_at = now(), last_error_code = NULL, updated_at = now()
                            WHERE outbox_id = %s""",
                        (outbox_id,),
                    )
                    published += 1
                except Exception as exc:
                    next_attempt = attempts + 1
                    status = "failed" if next_attempt >= MAX_PUBLISH_ATTEMPTS else "pending"
                    delay_seconds = min(300, 2 ** min(next_attempt, 8))
                    conn.execute(
                        """UPDATE message_outbox
                              SET status = %s, publish_attempts = %s, last_error_code = %s,
                                  available_at = now() + make_interval(secs => %s), updated_at = now()
                            WHERE outbox_id = %s""",
                        (status, next_attempt, type(exc).__name__, delay_seconds, outbox_id),
                    )
                    log.error("Outbox publication failed for outbox_id=%s: %s", outbox_id, type(exc).__name__)
    return published


def run(poll_seconds: float = 1.0) -> None:
    """Continuously dispatch committed outbox rows."""
    log.info("Outbox dispatcher started")
    while True:
        try:
            count = dispatch_once()
        except Exception as exc:
            log.error("Outbox dispatch cycle failed: %s", type(exc).__name__)
            count = 0
        if count == 0:
            time.sleep(poll_seconds)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Outbox payload and headers must be JSON objects")
    return value


def main() -> None:
    """Run one dispatch cycle or the long-running dispatcher."""
    parser = argparse.ArgumentParser(description="Publish committed RabbitMQ outbox messages")
    parser.add_argument("--once", action="store_true", help="publish one available batch and exit")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.once:
        log.info("Published %d outbox messages", dispatch_once())
        return
    run(args.poll_seconds)


if __name__ == "__main__":
    main()
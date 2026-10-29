"""Inspect, replay, or purge pipeline dead-letter queues without printing payloads."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable
from typing import Any

import pika

from services.rabbitmq_config import EXCHANGE_NAME, QUEUES, RabbitMQConfig, initialize_queues

log = logging.getLogger(__name__)


def queue_names(selected: str) -> list[str]:
    """Resolve one pipeline queue or all pipeline queues."""
    if selected == "all":
        return list(QUEUES)
    if selected not in QUEUES:
        raise ValueError(f"Unknown pipeline queue: {selected}")
    return [selected]


def status(selected: str) -> dict[str, int]:
    """Return message counts without consuming or displaying message bodies."""
    initialize_queues()
    connection = RabbitMQConfig().get_connection()
    try:
        channel = connection.channel()
        return {
            queue_name: channel.queue_declare(queue=f"{queue_name}.dead", passive=True).method.message_count
            for queue_name in queue_names(selected)
        }
    finally:
        connection.close()


def replay(selected: str, limit: int) -> int:
    """Republish confirmed dead letters to their original queue and acknowledge the originals."""
    if limit < 1:
        raise ValueError("Replay limit must be greater than zero")
    initialize_queues()
    connection = RabbitMQConfig().get_connection()
    replayed = 0
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        for queue_name in queue_names(selected):
            while replayed < limit:
                method, properties, body = channel.basic_get(queue=f"{queue_name}.dead", auto_ack=False)
                if method is None:
                    break
                headers = dict(properties.headers or {})
                headers.pop("failed_queue", None)
                headers["retry_count"] = 0
                headers["replay_count"] = int(headers.get("replay_count", 0)) + 1
                try:
                    channel.basic_publish(
                        exchange=EXCHANGE_NAME,
                        routing_key=queue_name,
                        body=body,
                        mandatory=True,
                        properties=_replay_properties(properties, headers),
                    )
                except Exception:
                    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    raise
                channel.basic_ack(delivery_tag=method.delivery_tag)
                replayed += 1
            if replayed >= limit:
                break
    finally:
        connection.close()
    return replayed


def purge(selected: str, confirmed: bool) -> int:
    """Purge known test messages only after explicit operator confirmation."""
    if not confirmed:
        raise ValueError("Purge requires --confirm-smoke-test-only")
    initialize_queues()
    connection = RabbitMQConfig().get_connection()
    purged = 0
    try:
        channel = connection.channel()
        for queue_name in queue_names(selected):
            result = channel.queue_purge(queue=f"{queue_name}.dead")
            purged += result.method.message_count
    finally:
        connection.close()
    return purged


def _replay_properties(properties: pika.BasicProperties, headers: dict[str, Any]) -> pika.BasicProperties:
    return pika.BasicProperties(
        delivery_mode=pika.DeliveryMode.Persistent,
        content_type=properties.content_type or "application/json",
        message_id=properties.message_id,
        correlation_id=properties.correlation_id,
        timestamp=properties.timestamp,
        headers=headers,
    )


def _print_counts(counts: Iterable[tuple[str, int]]) -> None:
    for queue_name, count in counts:
        print(f"{queue_name}.dead: {count}")


def main() -> None:
    """Run the selected dead-letter queue operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "replay", "purge"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--queue", default="all", choices=["all", *QUEUES])
        if command == "replay":
            subparser.add_argument("--limit", type=int, default=100)
        if command == "purge":
            subparser.add_argument("--confirm-smoke-test-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "status":
        _print_counts(status(args.queue).items())
    elif args.command == "replay":
        print(f"Replayed {replay(args.queue, args.limit)} messages")
    else:
        print(f"Purged {purge(args.queue, args.confirm_smoke_test_only)} messages")


if __name__ == "__main__":
    main()
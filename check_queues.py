#!/usr/bin/env python
"""Print queue depth and consumer counts without reading message payloads."""

import sys

from services.rabbitmq_config import QUEUES, RabbitMQConfig


def main() -> int:
    """Report pipeline queue health using the configured RabbitMQ connection."""
    try:
        connection = RabbitMQConfig().get_connection()
        try:
            channel = connection.channel()
            totals = {"messages": 0, "consumers": 0}
            print("Pipeline Queue Status")
            print("=" * 66)
            for queue_name in QUEUES:
                result = channel.queue_declare(queue=queue_name, passive=True)
                messages = result.method.message_count
                consumers = result.method.consumer_count
                totals["messages"] += messages
                totals["consumers"] += consumers
                print(f"{queue_name:24} messages={messages:4} consumers={consumers:2}")
            print("=" * 66)
            print(f"Total messages={totals['messages']} consumers={totals['consumers']}")
            return 0
        finally:
            connection.close()
    except Exception as exc:
        print(f"Queue status unavailable ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

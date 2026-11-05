"""
RabbitMQ Message Definitions and Publisher/Worker Base Classes

Defines the data structures for messages flowing through the pipeline
and provides Publisher/Worker abstractions for easy queue interaction.
"""

import functools
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Optional
from uuid import uuid4

import pika
import pika.exceptions

from services.rabbitmq_config import DEAD_LETTER_EXCHANGE_NAME, EXCHANGE_NAME, RabbitMQConfig
from services.reliable_messaging import (
    claim_inbox,
    complete_inbox,
    complete_inbox_separately,
    enqueue_outbox,
    fail_inbox,
    message_payload,
)

logger = logging.getLogger(__name__)
MAX_RETRIES = 3
RECONNECT_FIRST_DELAY_S = 2
RECONNECT_MAX_DELAY_S = 30
_CONNECTION_ERRORS = (
    pika.exceptions.AMQPConnectionError,
    pika.exceptions.StreamLostError,
    pika.exceptions.ConnectionClosedByBroker,
    pika.exceptions.ChannelClosedByBroker,
)


# Message dataclasses for each pipeline stage

@dataclass
class ConversionMessage:
    """Message: Raw data ready to convert to FHIR."""
    batch_id: str
    source_system: str
    file_path: str
    file_type: str  # csv, json, fhir, etc.
    adapter_name: str | None = None
    job_id: str | None = None
    priority: int = 0


@dataclass
class LoadMessage:
    """Message: FHIR resources ready to load into database."""
    batch_id: str
    job_id: str
    resource_count: int
    started_at: str
    priority: int = 0


@dataclass
class QualityCheckMessage:
    """Message: Batch to run quality checks on."""
    batch_id: str
    job_id: str
    check_type: str  # comprehensive, basic, specific_fields
    priority: int = 0


@dataclass
class ValueSetResolveMessage:
    """Message: Prepare measure value sets before calculation."""
    batch_id: str
    job_id: str
    measure_set: str = "HEDIS"
    priority: int = 0


@dataclass
class MeasureRunMessage:
    """Message: Patient/member data to calculate measures for."""
    batch_id: str
    job_id: str
    measure_set: str  # HEDIS, CMS, etc.
    member_ids: Optional[list[str]] = None
    priority: int = 0


@dataclass
class GapIdentifyMessage:
    """Message: Materialized measure results are ready for gap identification."""
    batch_id: str
    job_id: str
    measure_count: int
    priority: int = 0


@dataclass
class GapPrioritizeMessage:
    """Message: Care gaps to prioritize."""
    batch_id: str
    job_id: str
    member_id: Optional[str] = None
    gap_type: Optional[str] = None  # medication, lab, visit, etc.
    priority: int = 0


@dataclass
class DocumentAttachMessage:
    """Message: Supporting evidence documents to attach."""
    batch_id: str
    job_id: str
    gap_id: Optional[str] = None
    member_id: Optional[str] = None
    priority: int = 0


@dataclass
class EvidenceExtractionMessage:
    """Message: Extract proposed evidence for one open gap."""
    batch_id: str
    job_id: str
    gap_id: str
    remaining_gap_ids: list[str] = field(default_factory=list)
    total_gaps: int = 0
    proposed_total: int = 0
    failed_gaps: int = 0
    priority: int = 0


@dataclass
class StarCalculationMessage:
    """Message: Calculate and persist the batch star-rating summary."""
    batch_id: str
    job_id: str
    priority: int = 0


class Publisher:
    """Publishes messages to RabbitMQ queues."""

    def __init__(self, config: Optional[RabbitMQConfig] = None):
        self.config = config or RabbitMQConfig()
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.adapters.blocking_connection.BlockingChannel] = None

    def __enter__(self):
        """Context manager entry: establish connection."""
        logger.debug(f"📤 Publisher: Establishing RabbitMQ connection to {self.config.host}:{self.config.port}")
        self.connection = self.config.get_connection()
        self.channel = self.connection.channel()
        self.channel.confirm_delivery()
        logger.debug("📤 Publisher: Connection established, channel created")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit: close connection."""
        if self.connection:
            logger.debug("📤 Publisher: Closing RabbitMQ connection")
            self.connection.close()
            logger.debug("📤 Publisher: Connection closed")

    def publish(
        self,
        queue_name: str,
        message: Any,
        *,
        message_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> str:
        """
        Publish a message to a queue.

        Args:
            queue_name: Name of the queue (e.g., "ingestion.convert")
            message: Dataclass instance or dict to serialize
        """
        if not self.channel:
            raise RuntimeError("Publisher not initialized. Use 'with' statement.")

        message_dict = message_payload(message)
        message_json = json.dumps(message_dict)
        outgoing_id = message_id or str(uuid4())

        logger.debug(f"📤 Publisher: Publishing to queue '{queue_name}'")
        logger.debug(f"   Exchange: {EXCHANGE_NAME}, Routing Key: {queue_name}")
        logger.debug(f"   Message size: {len(message_json)} bytes")
        self.channel.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=queue_name,
            body=message_json,
            mandatory=True,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
                message_id=outgoing_id,
                correlation_id=str(message_dict.get("batch_id", "")) or None,
                timestamp=int(time.time()),
                headers=headers or {"schema_version": 1, "retry_count": 0},
            ),
        )
        logger.info("Published message to queue '%s'", queue_name)
        return outgoing_id

    def publish_batch(self, queue_name: str, messages: list[Any]) -> None:
        """Publish multiple messages to a queue."""
        logger.info(f"📤 Publishing batch of {len(messages)} messages to '{queue_name}'")
        for i, message in enumerate(messages, 1):
            logger.debug(f"   Message {i}/{len(messages)}...")
            self.publish(queue_name, message)
        logger.info(f"✅ 📤 Published {len(messages)} messages to '{queue_name}'")


class Worker:
    """
    Base class for queue workers.

    Subclass this and override `process_message()` to create specific workers.
    """

    def __init__(
        self,
        queue_name: str,
        config: Optional[RabbitMQConfig] = None,
        prefetch_count: int = 1,
    ):
        self.queue_name = queue_name
        self.config = config or RabbitMQConfig()
        self.prefetch_count = prefetch_count
        if prefetch_count > 1:
            logger.warning("Worker on queue '%s' uses prefetch_count=%d; jobs may then run concurrently",
                           queue_name, prefetch_count)
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.adapters.blocking_connection.BlockingChannel] = None
        self._local = threading.local()
        self._job_thread: threading.Thread | None = None

    @property
    def current_message_id(self) -> str | None:
        """The message the calling thread is processing; each job runs on its own thread."""
        return getattr(self._local, "message_id", None)

    @current_message_id.setter
    def current_message_id(self, value: str | None) -> None:
        self._local.message_id = value

    def connect(self) -> None:
        """Establish connection to RabbitMQ."""
        logger.info(f"🔌 Worker: Connecting to RabbitMQ at {self.config.host}:{self.config.port}...")
        self.connection = self.config.get_connection()
        self.channel = self.connection.channel()
        self.channel.confirm_delivery()
        self.channel.basic_qos(prefetch_count=self.prefetch_count)
        logger.info(f"✅ 🔌 Worker connected to queue '{self.queue_name}'")
        logger.debug(f"   Prefetch count: {self.prefetch_count}")
        logger.debug("   Ready to receive messages...")

    def disconnect(self) -> None:
        """Close connection to RabbitMQ."""
        if self.connection and self.connection.is_open:
            logger.info(f"🔌 Worker: Closing connection to '{self.queue_name}'")
            self.connection.close()
            logger.info("✅ 🔌 Worker: Connection closed")

    def process_message(self, message: dict) -> bool:
        """
        Process a single message.

        Override this method in subclasses.

        Args:
            message: Deserialized JSON message dict

        Returns:
            True if processing succeeded (message acknowledged)
            False if processing failed (message rejected)
        """
        raise NotImplementedError("Subclasses must implement process_message()")

    def enqueue_next(self, conn: Any, queue_name: str, message: Any) -> str:
        """Atomically complete the current delivery and enqueue its next stage."""
        if self.current_message_id is None:
            raise RuntimeError("No RabbitMQ message is currently being processed")
        outgoing_id = enqueue_outbox(
            conn,
            queue_name,
            message,
            source_message_id=self.current_message_id,
        )
        complete_inbox(conn, self.queue_name, self.current_message_id)
        return outgoing_id

    def complete_current(self, conn: Any) -> None:
        """Atomically complete a terminal delivery in the caller's transaction."""
        if self.current_message_id is None:
            raise RuntimeError("No RabbitMQ message is currently being processed")
        complete_inbox(conn, self.queue_name, self.current_message_id)

    def _message_callback(
        self, ch: pika.adapters.blocking_connection.BlockingChannel, method, properties, body
    ) -> None:
        """Handle incoming message from queue."""
        message_id = properties.message_id or sha256(body).hexdigest()
        claim = claim_inbox(self.queue_name, message_id)
        if claim == "processed":
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info("Duplicate message acknowledged on queue '%s'", self.queue_name)
            return
        if claim == "busy":
            if self.connection is not None:
                self.connection.sleep(1)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            logger.info("Message has an active processing lease on queue '%s'", self.queue_name)
            return
        if self.connection is None:
            # No live connection (called directly): nothing needs heartbeats, so run the job here.
            self._run_job(ch, method.delivery_tag, properties, body, message_id)
            return
        # The job runs off the connection thread so start_consuming keeps sending heartbeats during long jobs.
        self._job_thread = threading.Thread(
            target=self._run_job,
            args=(ch, method.delivery_tag, properties, body, message_id),
            name=f"{self.queue_name}-job",
            daemon=False,
        )
        self._job_thread.start()

    def _run_job(self, ch, delivery_tag: int, properties, body: bytes, message_id: str) -> None:
        """Process one claimed delivery. Never touches the channel; channel actions go to the connection thread."""
        self.current_message_id = message_id
        try:
            message = json.loads(body)
            logger.info(f"\n📨 Received message from '{self.queue_name}':")
            logger.info(f"   Delivery Tag: {delivery_tag}")
            logger.debug(f"   Properties: content_type={properties.content_type}, delivery_mode={properties.delivery_mode}")

            success = self.process_message(message)

            if success:
                complete_inbox_separately(self.queue_name, message_id)
                self._on_connection_thread(message_id, self._ack, ch, delivery_tag)
            else:
                self._fail_inbox(message_id, "processor_returned_false")
                self._on_connection_thread(message_id, self._retry_or_dead_letter, ch, delivery_tag, properties, body)
        except Exception as exc:
            self._fail_inbox(message_id, type(exc).__name__)
            logger.error("Message processing raised %s on queue '%s'", type(exc).__name__, self.queue_name)
            self._on_connection_thread(message_id, self._retry_or_dead_letter, ch, delivery_tag, properties, body)
        finally:
            self.current_message_id = None

    def _fail_inbox(self, message_id: str, reason: str) -> None:
        """fail_inbox, logged instead of raised so the delivery is still retried rather than left unacked."""
        try:
            fail_inbox(self.queue_name, message_id, reason)
        except Exception as exc:
            logger.error("Could not record the failure of message %s on queue '%s': %s", message_id,
                         self.queue_name, type(exc).__name__)

    def _on_connection_thread(self, message_id: str, action: Callable[..., None], ch, *args: Any) -> None:
        """Run a channel action on the connection thread; pika channels are not thread-safe. Never raises."""
        call = functools.partial(self._channel_action, message_id, action, ch, *args)
        if self.connection is None:
            call()
            return
        try:
            self.connection.add_callback_threadsafe(call)
        except Exception as exc:
            logger.error("Connection closed before %s for message %s on queue '%s' (%s); the database side is "
                         "done and the redelivery is handled by the inbox", action.__name__, message_id,
                         self.queue_name, type(exc).__name__)

    def _channel_action(self, message_id: str, action: Callable[..., None], ch, *args: Any) -> None:
        if not getattr(ch, "is_open", True):
            logger.error("Channel closed before %s for message %s on queue '%s'; the database side is done and "
                         "the redelivery is handled by the inbox", action.__name__, message_id, self.queue_name)
            return
        try:
            action(ch, *args)
        except Exception as exc:
            logger.error("%s failed for message %s on queue '%s': %s", action.__name__, message_id, self.queue_name,
                         type(exc).__name__)

    def _ack(self, ch, delivery_tag: int) -> None:
        ch.basic_ack(delivery_tag=delivery_tag)
        logger.info(f"✅ 📨 Message processed successfully - acknowledged (tag={delivery_tag})")

    def _retry_or_dead_letter(self, ch, delivery_tag: int, properties, body: bytes) -> None:
        headers = dict(properties.headers or {})
        retry_count = int(headers.get("retry_count", 0))
        if retry_count >= MAX_RETRIES:
            self._dead_letter(ch, delivery_tag, properties, body)
            return
        headers["retry_count"] = retry_count + 1
        ch.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=self.queue_name,
            body=body,
            mandatory=True,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type=properties.content_type or "application/json",
                message_id=properties.message_id,
                correlation_id=properties.correlation_id,
                timestamp=properties.timestamp,
                headers=headers,
            ),
        )
        ch.basic_ack(delivery_tag=delivery_tag)
        logger.warning("Retrying message on queue '%s' (attempt %d/%d)", self.queue_name, retry_count + 1,
                       MAX_RETRIES)

    def _dead_letter(self, ch, delivery_tag: int, properties, body: bytes) -> None:
        ch.basic_publish(
            exchange=DEAD_LETTER_EXCHANGE_NAME,
            routing_key=self.queue_name,
            body=body,
            mandatory=True,
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type=properties.content_type or "application/json",
                message_id=properties.message_id,
                correlation_id=properties.correlation_id,
                timestamp=properties.timestamp,
                headers={**(properties.headers or {}), "failed_queue": self.queue_name},
            ),
        )
        ch.basic_ack(delivery_tag=delivery_tag)
        logger.error("Message moved to dead-letter queue for '%s'", self.queue_name)

    def run(self) -> None:
        """
        Start consuming messages from the queue.

        Blocks until Ctrl+C. A lost connection is re-established with backoff instead of ending the worker.
        """
        delay = RECONNECT_FIRST_DELAY_S
        try:
            while True:
                try:
                    self.connect()
                    self.channel.basic_consume(
                        queue=self.queue_name,
                        on_message_callback=self._message_callback,
                    )
                    delay = RECONNECT_FIRST_DELAY_S

                    logger.info(f"\n🚀 Worker started on queue '{self.queue_name}'")
                    logger.info("⏳ Waiting for messages...")
                    logger.info("(Press Ctrl+C to stop)\n")
                    self.channel.start_consuming()
                    return
                except _CONNECTION_ERRORS as exc:
                    logger.error("RabbitMQ connection lost on queue '%s' (%s); reconnecting in %ds",
                                 self.queue_name, type(exc).__name__, delay)
                    self._wait_for_job()
                    self._disconnect_quietly()
                    time.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY_S)
        except KeyboardInterrupt:
            logger.info("\n🛑 Worker interrupted by user")
            self._stop_after_current_job()
        except Exception as exc:
            logger.error("Worker stopped after %s", type(exc).__name__)
            raise
        finally:
            self._disconnect_quietly()

    def _wait_for_job(self) -> None:
        if self._job_thread is not None and self._job_thread.is_alive():
            logger.info("Worker on queue '%s' waiting for the current job to finish", self.queue_name)
            self._job_thread.join()

    def _stop_after_current_job(self) -> None:
        """Stop taking messages, let the running job finish, and send its ack if the connection is still up."""
        try:
            if self.channel is not None and self.channel.is_open:
                self.channel.stop_consuming()
        except Exception as exc:
            logger.error("Could not stop consuming on queue '%s': %s", self.queue_name, type(exc).__name__)
        self._wait_for_job()
        try:
            if self.connection is not None and self.connection.is_open:
                self.connection.process_data_events(time_limit=1)
        except Exception as exc:
            logger.error("Could not send the last ack on queue '%s': %s", self.queue_name, type(exc).__name__)

    def _disconnect_quietly(self) -> None:
        try:
            self.disconnect()
        except Exception as exc:
            logger.error("Closing the connection on queue '%s' failed: %s", self.queue_name, type(exc).__name__)

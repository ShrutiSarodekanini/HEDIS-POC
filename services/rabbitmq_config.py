"""
RabbitMQ Configuration and Queue Management

Handles connection parameters, queue declarations, and exchange setup.
Ensures all required queues and exchanges are declared before workers start.
"""

import logging
import os

import pika

logger = logging.getLogger(__name__)


class RabbitMQConfig:
    """RabbitMQ connection configuration from environment variables."""

    def __init__(self) -> None:
        self.host = os.getenv("RABBITMQ_HOST", "localhost")
        self.port = int(os.getenv("RABBITMQ_PORT", 5672))
        self.user = os.getenv("RABBITMQ_USER", "guest")
        self.password = os.getenv("RABBITMQ_PASSWORD", "guest")
        self.vhost = os.getenv("RABBITMQ_VHOST", "/")
        self.management_port = int(os.getenv("RABBITMQ_MANAGEMENT_PORT", 15672))

    def get_connection_parameters(self) -> pika.ConnectionParameters:
        """Get pika ConnectionParameters configured from environment."""
        credentials = pika.PlainCredentials(self.user, self.password)
        return pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            virtual_host=self.vhost,
            credentials=credentials,
            connection_attempts=3,
            retry_delay=2,
        )

    def get_connection(self) -> pika.BlockingConnection:
        """Establish and return a blocking connection to RabbitMQ."""
        logger.debug(
            "Establishing RabbitMQ connection host=%s port=%d vhost=%s",
            self.host,
            self.port,
            self.vhost,
        )
        conn = pika.BlockingConnection(self.get_connection_parameters())
        logger.debug("RabbitMQ connection established")
        return conn

    @property
    def management_url(self) -> str:
        """RabbitMQ Management UI URL."""
        return f"http://{self.host}:{self.management_port}"


# Queue definitions for the async pipeline
QUEUES = {
    "ingestion.convert": {
        "description": "Convert raw data to FHIR format",
        "durable": True,
    },
    "ingestion.load": {
        "description": "Load FHIR resources into database",
        "durable": True,
    },
    "quality.check": {
        "description": "Perform data quality checks",
        "durable": True,
    },
    "valuesets.resolve": {
        "description": "Validate and resolve measure value sets",
        "durable": True,
    },
    "measures.run": {
        "description": "Calculate HEDIS measures",
        "durable": True,
    },
    "gaps.identify": {
        "description": "Identify care gaps from measure results",
        "durable": True,
    },
    "gaps.prioritize": {
        "description": "Prioritize care gaps",
        "durable": True,
    },
    "documents.attach": {
        "description": "Attach supporting evidence documents",
        "durable": True,
    },
    "evidence.extract": {
        "description": "Extract proposed evidence for human review",
        "durable": True,
    },
    "stars.calculate": {
        "description": "Calculate batch star ratings",
        "durable": True,
    },
}

# Exchanges
EXCHANGE_NAME = "pipeline"
EXCHANGE_TYPE = "direct"
DEAD_LETTER_EXCHANGE_NAME = "pipeline.dead"


class QueueManager:
    """Manages RabbitMQ queue and exchange setup."""

    def __init__(self, config: RabbitMQConfig) -> None:
        self.config = config

    def declare_infrastructure(self) -> None:
        """
        Declare all exchanges and queues.
        Safe to call multiple times (idempotent).
        """
        logger.info("Initializing RabbitMQ infrastructure")
        connection = self.config.get_connection()
        try:
            channel = connection.channel()

            # Declare exchange
            logger.debug("Declaring RabbitMQ exchange name=%s type=%s", EXCHANGE_NAME, EXCHANGE_TYPE)
            channel.exchange_declare(
                exchange=EXCHANGE_NAME,
                exchange_type=EXCHANGE_TYPE,
                durable=True,
            )
            channel.exchange_declare(
                exchange=DEAD_LETTER_EXCHANGE_NAME,
                exchange_type=EXCHANGE_TYPE,
                durable=True,
            )
            logger.info("RabbitMQ exchange declared name=%s", EXCHANGE_NAME)

            # Declare all queues and bind to exchange
            logger.info("Declaring %d RabbitMQ queues", len(QUEUES))
            for queue_name, queue_config in QUEUES.items():
                logger.debug("Declaring RabbitMQ queue name=%s", queue_name)
                channel.queue_declare(
                    queue=queue_name,
                    durable=queue_config.get("durable", True),
                )
                channel.queue_bind(
                    exchange=EXCHANGE_NAME,
                    queue=queue_name,
                    routing_key=queue_name,
                )
                dead_queue_name = f"{queue_name}.dead"
                channel.queue_declare(queue=dead_queue_name, durable=True)
                channel.queue_bind(
                    exchange=DEAD_LETTER_EXCHANGE_NAME,
                    queue=dead_queue_name,
                    routing_key=queue_name,
                )
            logger.info("RabbitMQ infrastructure initialized queues=%d", len(QUEUES))
        finally:
            connection.close()

    def test_connection(self) -> bool:
        """Test RabbitMQ connection."""
        try:
            logger.info(
                "Testing RabbitMQ connection host=%s port=%d",
                self.config.host,
                self.config.port,
            )
            connection = self.config.get_connection()
            connection.close()
            logger.info("RabbitMQ connection successful")
            return True
        except Exception as exc:
            logger.error("RabbitMQ connection failed: %s", type(exc).__name__)
            return False


def get_config() -> RabbitMQConfig:
    """Factory function to get RabbitMQ config."""
    return RabbitMQConfig()


def initialize_queues() -> None:
    """Initialize all RabbitMQ infrastructure on startup."""
    config = get_config()
    manager = QueueManager(config)

    if not manager.test_connection():
        raise RuntimeError(
            f"Cannot connect to RabbitMQ at {config.host}:{config.port}. "
            "Is it running? Run: docker-compose up -d"
        )

    manager.declare_infrastructure()

"""
QualityWorker: Performs data quality checks on ingested data.

Third stage of the async pipeline.
Consumes QualityCheckMessage from 'quality.check' queue.
Produces MeasureRunMessage to 'measures.run' queue.

Usage:
    python -m services.workers.quality_worker
"""

import asyncio
import logging
import sys
from uuid import UUID

from psycopg.types.json import Jsonb

from services.data_ingestion.app.progress import progress_tracker
from services.document_intake import db
from services.integration import pallavi
from services.integration.config import SETTINGS
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import (
    MeasureRunMessage,
    Worker,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class QualityWorker(Worker):
    """
    Consumes QualityCheckMessage from 'quality.check' queue.

    Performs data quality validations on the ingested dataset.
    Checks data completeness, validity, and consistency.

    Then publishes MeasureRunMessage to 'measures.run' queue.
    """

    def __init__(self):
        super().__init__(queue_name="quality.check", prefetch_count=1)
        logger.info("QualityWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a quality check message.

        Args:
            message: QualityCheckMessage dict with:
                - batch_id: UUID of the batch to check
                - check_type: "comprehensive", "basic", "specific_fields"

        Returns:
            True if checks passed (or were run), False if critical errors
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")
            check_type = message.get("check_type", "comprehensive")

            if not job_id:
                logger.error("Quality message is missing job_id: batch_id=%s", batch_id)
                return False

            # Send progress: starting
            asyncio.run(progress_tracker.notify(
                batch_id, "quality", "starting",
                {"message": f"Running {check_type} quality checks"}
            ))

            logger.info(
                f"Running {check_type} data quality checks: batch_id={batch_id}"
            )

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "quality", "processing",
                {"pct": 33, "message": "Validating data completeness"}
            ))

            # Run quality checks based on type
            checks = self._run_quality_checks(batch_id, check_type)

            if not checks:
                logger.warning(f"No data found for quality checks: batch_id={batch_id}")
                # Still publish next message - we'll fail downstream if data is really missing
                pass

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "quality", "processing",
                {"pct": 66, "message": "Validating data consistency"}
            ))

            logger.info(
                f"✓ Quality checks complete: "
                f"warnings={checks.get('warning_count', 0)}, "
                f"errors={checks.get('error_count', 0)}"
            )

            # Send progress: publishing
            asyncio.run(progress_tracker.notify(
                batch_id, "quality", "processing",
                {"pct": 90, "message": "Publishing measure run message"}
            ))

            # Publish MeasureRunMessage to next queue
            measure_msg = MeasureRunMessage(
                batch_id=batch_id,
                job_id=job_id,
                measure_set="HEDIS",  # Default to HEDIS
            )

            with db.connect() as conn:
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'measuring',
                              counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"quality": checks}), batch_id),
                )
                self.enqueue_next(conn, "measures.run", measure_msg)
            logger.info("Queued MeasureRunMessage in outbox for batch_id=%s", batch_id)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "quality", "complete",
                {"pct": 100, "message": f"Quality checks complete: {checks.get('error_count', 0)} errors"}
            ))

            return True

        except Exception as exc:
            logger.error("Quality worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "quality", "error",
                {"message": "Quality worker failed"}
            ))
            return False

    def _run_quality_checks(self, batch_id: str, check_type: str) -> dict:
        """
        Run data quality checks on the batch.

        Checks include:
        - Completeness: Required fields populated
        - Validity: Values match expected formats
        - Consistency: Cross-record references valid
        - Uniqueness: No duplicate IDs

        Args:
            batch_id: Batch UUID
            check_type: Type of checks to run

        Returns:
            Dict with check results, warning/error counts
        """
        try:
            result = pallavi.run_checks(UUID(batch_id), None, SETTINGS.actor)
            return {
                "warning_count": result.get("anomalies", 0),
                "error_count": result.get("failed", 0),
                "checks_run": result.get("checks", 0),
            }

        except Exception as exc:
            logger.error("Quality checks failed for batch_id=%s: %s", batch_id, type(exc).__name__)
            raise


def main():
    """Start the QualityWorker."""
    logger.info("=" * 70)
    logger.info("HEDIS QUALITY WORKER")
    logger.info("=" * 70)
    logger.info("Listening on queue: quality.check")
    logger.info("Processing: Data quality validation")
    logger.info("")
    logger.info("Initializing RabbitMQ infrastructure...")
    initialize_queues()
    logger.info("")
    logger.info("Checks performed:")
    logger.info("  • Duplicate detection")
    logger.info("  • Required field validation")
    logger.info("  • Format validation")
    logger.info("  • Reference validity")
    logger.info("")
    logger.info("Next queue: measures.run (auto-publishes after checks)")
    logger.info("=" * 70)
    logger.info("")

    worker = QualityWorker()
    worker.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal QualityWorker error: %s", type(exc).__name__)
        sys.exit(1)

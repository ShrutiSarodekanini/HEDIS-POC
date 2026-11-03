"""
GapsWorker: Prioritizes and ranks care gaps based on HEDIS measures.

This stage of the async pipeline consumes gap data and applies scoring rules
to prioritize which gaps should be addressed first (e.g., by clinical urgency,
patient compliance history, or administrative factors).

It consumes messages from 'gaps.prioritize' queue and publishes to 'documents.attach'.

Usage:
    python -m services.workers.gaps_worker
"""

import asyncio
import logging
import sys
from datetime import date

from psycopg.types.json import Jsonb

from services.data_ingestion.app.progress import progress_tracker
from services.document_intake import db
from services.gap_priority.config import load_rules
from services.gap_priority.runner import score_all
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import DocumentAttachMessage, Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class GapsWorker(Worker):
    """
    Consumes GapPrioritizeMessage from 'gaps.prioritize' queue.

    Scores and ranks care gaps by priority (urgency, patient factors, etc.)
    and publishes DocumentAttachMessage to 'documents.attach' queue.
    """

    def __init__(self):
        super().__init__(queue_name="gaps.prioritize", prefetch_count=1)
        logger.info("GapsWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a gap prioritization message.

        Args:
            message: GapPrioritizeMessage dict with:
                - batch_id: UUID of the batch
                - member_id: optional specific member
                - gap_type: optional gap category (medication, lab, visit, etc.)

        Returns:
            True if processing succeeded, False if it failed
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")
            gap_type = message.get("gap_type")

            if not job_id:
                logger.error("Gap message is missing job_id: batch_id=%s", batch_id)
                return False

            # Send progress: starting
            asyncio.run(progress_tracker.notify(
                batch_id, "gaps", "starting",
                {"message": f"Prioritizing {gap_type or 'all'} gaps"}
            ))

            logger.info("Prioritizing gaps for batch_id=%s, gap_type=%s", batch_id, gap_type or "all")

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "gaps", "processing",
                {"pct": 33, "message": "Loading gap data"}
            ))

            with db.connect() as conn:
                summary = score_all(conn, date.today(), load_rules())
                doc_msg = DocumentAttachMessage(
                    batch_id=batch_id,
                    job_id=job_id,
                )
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'attaching',
                              counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"prioritization": {"gaps_scored": summary.gaps_scored}}), batch_id),
                )
                self.enqueue_next(conn, "documents.attach", doc_msg)

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "gaps", "processing",
                {"pct": 66, "message": "Calculating priority scores"}
            ))

            # TODO: Publish DocumentAttachMessage when gaps are prioritized
            # with Publisher() as pub:
            #     doc_message = DocumentAttachMessage(
            #         batch_id=batch_id,
            #         member_id=member_id,
            #     )
            #     pub.publish("documents.attach", doc_message)

            # Send progress: publishing
            asyncio.run(progress_tracker.notify(
                batch_id, "gaps", "processing",
                {"pct": 90, "message": "Publishing document attachment message"}
            ))

            logger.info("Queued DocumentAttachMessage in outbox for batch_id=%s", batch_id)

            logger.info("Gaps prioritized for batch_id=%s: %d gaps", batch_id, summary.gaps_scored)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "gaps", "complete",
                {"pct": 100, "message": "Gaps prioritized for batch"}
            ))

            return True

        except Exception as exc:
            logger.error("Gap worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "gaps", "error",
                {"message": "Gap worker failed"}
            ))
            return False


def main() -> None:
    """Initialize queues and start the worker."""
    try:
        logger.info("Starting GapsWorker...")
        initialize_queues()  # CRITICAL: ensure queues exist before subscribing

        worker = GapsWorker()
        worker.run()
    except KeyboardInterrupt:
        logger.info("GapsWorker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal GapsWorker error: %s", type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

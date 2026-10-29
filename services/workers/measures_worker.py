"""
MeasuresWorker: Calculates HEDIS quality measures from patient data.

This stage of the async pipeline consumes converted and loaded data
and computes quality measures (e.g., medication adherence, cancer screening).

It consumes messages from 'measures.run' queue and publishes to 'gaps.prioritize'.

Usage:
    python -m services.workers.measures_worker
"""

import asyncio
import logging
import sys

from psycopg.types.json import Jsonb

from services.data_ingestion import team_schema_loader
from services.data_ingestion.app.progress import progress_tracker
from services.document_intake import db
from services.integration import measures
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import GapPrioritizeMessage, Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MeasuresWorker(Worker):
    """
    Consumes MeasureRunMessage from 'measures.run' queue.

    Calculates HEDIS quality measures for members/patients
    and publishes GapPrioritizeMessage to 'gaps.prioritize' queue.
    """

    def __init__(self):
        super().__init__(queue_name="measures.run", prefetch_count=1)
        logger.info("MeasuresWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a measure calculation message.

        Args:
            message: MeasureRunMessage dict with:
                - batch_id: UUID of the batch
                - measure_set: "HEDIS", "CMS", etc.
                - member_ids: optional list of specific members

        Returns:
            True if processing succeeded, False if it failed
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")
            measure_set = message.get("measure_set", "HEDIS")
            member_ids = message.get("member_ids")

            if not job_id:
                logger.error("Measure message is missing job_id: batch_id=%s", batch_id)
                return False

            # Send progress: starting
            asyncio.run(progress_tracker.notify(
                batch_id, "measures", "starting",
                {"message": f"Calculating {measure_set} measures"}
            ))

            logger.info(
                f"Processing measures for batch {batch_id}, measure_set={measure_set}, "
                f"members={len(member_ids) if member_ids else 'all'}"
            )

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "measures", "processing",
                {"pct": 33, "message": "Extracting member data"}
            ))

            with db.connect() as conn:
                patient_ids, attributed_org = team_schema_loader.job_patients(conn, job_id)
                if not patient_ids:
                    raise LookupError("No loaded patients are available for measure calculation")
                run = measures.run_measures(conn, list(patient_ids), attributed_org, batch_id.replace("-", "")[:8])
                measure_count = len(run.measures)
                gap_msg = GapPrioritizeMessage(
                    batch_id=batch_id,
                    job_id=job_id,
                )
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'prioritizing',
                              counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"measures": {"count": measure_count}}), batch_id),
                )
                self.enqueue_next(conn, "gaps.prioritize", gap_msg)

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "measures", "processing",
                {"pct": 66, "message": "Computing measure values"}
            ))

            # TODO: Publish GapPrioritizeMessage when actual measures are calculated
            # with Publisher() as pub:
            #     gap_message = GapPrioritizeMessage(
            #         batch_id=batch_id,
            #         gap_type=None,  # trigger full gap prioritization
            #     )
            #     pub.publish("gaps.prioritize", gap_message)

            # Send progress: publishing
            asyncio.run(progress_tracker.notify(
                batch_id, "measures", "processing",
                {"pct": 90, "message": "Publishing gap prioritization message"}
            ))

            logger.info("Queued GapPrioritizeMessage in outbox for batch_id=%s", batch_id)

            logger.info("Measures processed for batch_id=%s: %d measures", batch_id, measure_count)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "measures", "complete",
                {"pct": 100, "message": "Measures complete for batch"}
            ))

            return True

        except Exception as exc:
            logger.error("Measure worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "measures", "error",
                {"message": "Measure worker failed"}
            ))
            return False


def main() -> None:
    """Initialize queues and start the worker."""
    try:
        logger.info("Starting MeasuresWorker...")
        initialize_queues()  # CRITICAL: ensure queues exist before subscribing

        worker = MeasuresWorker()
        worker.run()
    except KeyboardInterrupt:
        logger.info("MeasuresWorker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal MeasuresWorker error: %s", type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

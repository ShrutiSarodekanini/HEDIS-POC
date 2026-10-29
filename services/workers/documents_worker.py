"""
DocumentsWorker: Attaches supporting evidence documents to care gaps.

This is the final stage of the async pipeline. It consumes attachment instructions
and links evidence documents (e.g., patient charts, lab results) to identified care gaps.

It consumes messages from 'documents.attach' queue.

Usage:
    python -m services.workers.documents_worker
"""

import asyncio
import logging
import sys
from uuid import UUID

from services.data_ingestion import team_schema_loader
from services.data_ingestion.app.progress import progress_tracker
from services.document_intake import db
from services.document_intake.db import audit
from services.integration import stored_evidence
from services.integration.config import SETTINGS
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DocumentsWorker(Worker):
    """
    Consumes DocumentAttachMessage from 'documents.attach' queue.

    Attaches evidence documents (uploaded files, chart excerpts, etc.)
    to care gaps to support gap closure and member outreach.

    This is the final stage of the pipeline.
    """

    def __init__(self):
        super().__init__(queue_name="documents.attach", prefetch_count=1)
        logger.info("DocumentsWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a document attachment message.

        Args:
            message: DocumentAttachMessage dict with:
                - batch_id: UUID of the batch
                - gap_id: optional specific gap to attach to
                - member_id: optional specific member

        Returns:
            True if processing succeeded, False if it failed
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")

            if not job_id:
                logger.error("Document message is missing job_id: batch_id=%s", batch_id)
                return False

            # Send progress: starting
            asyncio.run(progress_tracker.notify(
                batch_id, "documents", "starting",
                {"message": "Attaching supporting documents"}
            ))

            logger.info("Attaching documents for batch_id=%s", batch_id)

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "documents", "processing",
                {"pct": 33, "message": "Searching for relevant documents"}
            ))

            with db.connect() as conn:
                patient_ids, _ = team_schema_loader.job_patients(conn, job_id)
            if SETTINGS.evidence_dir is None:
                attached = {"skipped": "evidence directory is not configured"}
            else:
                attached = stored_evidence.attach(SETTINGS.evidence_dir, list(patient_ids), lambda _done, _total: None)

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "documents", "processing",
                {"pct": 66, "message": "Linking documents to gaps"}
            ))

            with db.connect() as conn:
                conn.execute("UPDATE source_batch SET status = 'completed' WHERE batch_id = %s", (UUID(batch_id),))
                audit(conn, "batch", batch_id, "documents.attached",
                      {key: (len(value) if isinstance(value, list) else value) for key, value in attached.items()},
                      actor=SETTINGS.actor)
                self.complete_current(conn)

            logger.info("Document attachment completed for batch_id=%s", batch_id)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "documents", "complete",
                {"pct": 100, "message": "Document attachment complete for batch"}
            ))

            return True

        except Exception as exc:
            logger.error("Document worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "documents", "error",
                {"message": "Document worker failed"}
            ))
            return False


def main() -> None:
    """Initialize queues and start the worker."""
    try:
        logger.info("Starting DocumentsWorker...")
        initialize_queues()  # CRITICAL: ensure queues exist before subscribing

        worker = DocumentsWorker()
        worker.run()
    except KeyboardInterrupt:
        logger.info("DocumentsWorker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal DocumentsWorker error: %s", type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

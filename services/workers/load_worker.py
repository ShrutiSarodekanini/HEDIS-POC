"""
LoadWorker: Loads FHIR resources into PostgreSQL database and populates team schema.

Second stage of the async pipeline.
Consumes LoadMessage from 'ingestion.load' queue.
Produces QualityCheckMessage to 'quality.check' queue.

Usage:
    python -m services.workers.load_worker
"""

import asyncio
import logging
import sys
from uuid import UUID

from psycopg.types.json import Jsonb
from sqlalchemy import select

from services.data_ingestion import team_schema_loader
from services.data_ingestion.app.db import session_scope
from services.data_ingestion.app.models import FhirResource
from services.data_ingestion.app.progress import progress_tracker
from services.data_ingestion.app.repository import (
    mark_validated_resources_persisted,
)
from services.document_intake import db
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import (
    QualityCheckMessage,
    Worker,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class LoadWorker(Worker):
    """
    Consumes LoadMessage from 'ingestion.load' queue.

    Loads FHIR resources into PostgreSQL and extracts data into team schema
    (Patient, Encounter, Condition, etc. tables).

    Then publishes QualityCheckMessage to 'quality.check' queue.
    """

    def __init__(self):
        super().__init__(queue_name="ingestion.load", prefetch_count=1)
        logger.info("LoadWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a load message.

        Args:
            message: LoadMessage dict with:
                - batch_id: UUID of the batch to load
                - resource_count: Expected number of FHIR resources
                - started_at: ISO timestamp when conversion started

        Returns:
            True if loading succeeded, False if it failed
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")
            resource_count = message.get("resource_count")

            if not job_id:
                logger.error("Load message is missing job_id: batch_id=%s", batch_id)
                return False

            # Send progress: starting
            asyncio.run(progress_tracker.notify(
                batch_id, "load", "starting",
                {"message": f"Loading {resource_count} FHIR resources"}
            ))

            logger.info(
                f"Loading {resource_count} FHIR resources to database: batch_id={batch_id}"
            )

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "load", "processing",
                {"pct": 25, "message": "Persisting FHIR resources to database"}
            ))

            # Load FHIR resources into PostgreSQL
            loaded_count = self._load_fhir_resources(job_id)

            if loaded_count == 0:
                logger.error(f"No FHIR resources found for batch_id={batch_id}")
                asyncio.run(progress_tracker.notify(
                    batch_id, "load", "error",
                    {"message": "No FHIR resources found"}
                ))
                return False

            logger.info(
                f"✓ Loaded {loaded_count} FHIR resources to fhir_resources table"
            )

            # Send progress: extracting
            asyncio.run(progress_tracker.notify(
                batch_id, "load", "processing",
                {"pct": 50, "message": "Extracting data to team schema"}
            ))

            quality_msg = QualityCheckMessage(
                batch_id=batch_id,
                job_id=job_id,
                check_type="comprehensive",
            )

            # Extract data and commit the next stage message in the same transaction.
            extracted_count = self._extract_team_schema(batch_id, job_id, quality_msg)

            logger.info(
                f"✓ Extracted {extracted_count} records into team schema tables"
            )

            # Send progress: publishing
            asyncio.run(progress_tracker.notify(
                batch_id, "load", "processing",
                {"pct": 90, "message": "Publishing quality check message"}
            ))

            logger.info("Queued QualityCheckMessage in outbox for batch_id=%s", batch_id)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "load", "complete",
                {"pct": 100, "message": f"Load complete: {loaded_count} resources, {extracted_count} extracted"}
            ))

            return True

        except Exception as exc:
            logger.error("Load worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "load", "error",
                {"message": "Load worker failed"}
            ))
            return False

    def _load_fhir_resources(self, job_id: str) -> int:
        """
        Load validated FHIR resources into fhir_resources table.

        Args:
            job_id: Conversion job ID

        Returns:
            Number of resources loaded
        """
        try:
            with session_scope() as session:
                # Mark validated resources as persisted
                # This updates the fhir_resources table with validated resources
                persisted = mark_validated_resources_persisted(session, job_id)
                logger.info(f"Marked {persisted} resources as persisted")

                # Verify resources exist in fhir_resources table
                result = session.execute(
                    select(FhirResource).where(FhirResource.job_id == job_id)
                )
                resources = result.scalars().all()

                return len(resources)

        except Exception as exc:
            logger.error("FHIR persistence failed for job_id=%s: %s", job_id, type(exc).__name__)
            return 0

    def _extract_team_schema(
        self,
        batch_id: str,
        job_id: str,
        quality_msg: QualityCheckMessage,
    ) -> int:
        """
        Extract FHIR resources into team schema tables.

        Populates:
        - patient (Patient FHIR resources)
        - encounter (Encounter FHIR resources)
        - condition (Condition FHIR resources)
        - observation (Observation FHIR resources)
        - procedure (Procedure FHIR resources)
        - medication_request (MedicationRequest FHIR resources)
        - coverage (Coverage FHIR resources)
        - etc.

        Args:
            batch_id: Source batch UUID
            job_id: Conversion job ID

        Returns:
            Total number of records extracted
        """
        try:
            with db.connect() as conn:
                loaded = team_schema_loader.load_job(conn, job_id, UUID(batch_id))
                extracted = sum(loaded.inserted.values())
                load_counts = {
                    "patients": len(loaded.patient_ids),
                    "inserted": loaded.inserted,
                    "resource_types": loaded.resource_types,
                }
                conn.execute(
                    """INSERT INTO batch_run (batch_id, ingest_job_id, status, step, counts)
                       VALUES (%s, %s, 'running', 'checking', %s)
                       ON CONFLICT (batch_id) DO UPDATE
                           SET ingest_job_id = EXCLUDED.ingest_job_id,
                               status = 'running', step = 'checking',
                               counts = batch_run.counts || EXCLUDED.counts,
                               updated_at = now()""",
                    (batch_id, job_id, Jsonb({"load": load_counts})),
                )
                self.enqueue_next(conn, "quality.check", quality_msg)
                logger.info("Loaded job %s into team schema: %d records", job_id, extracted)
                return extracted

        except Exception as exc:
            logger.error("Team schema load failed for job_id=%s: %s", job_id, type(exc).__name__)
            raise


def main():
    """Start the LoadWorker."""
    logger.info("=" * 70)
    logger.info("HEDIS LOAD WORKER")
    logger.info("=" * 70)
    logger.info("Listening on queue: ingestion.load")
    logger.info("Processing: FHIR → PostgreSQL + Team Schema")
    logger.info("")
    logger.info("Initializing RabbitMQ infrastructure...")
    initialize_queues()
    logger.info("")
    logger.info("Next queue: quality.check (auto-publishes after success)")
    logger.info("")
    logger.info("Workflow:")
    logger.info("  1. Receive LoadMessage with batch_id")
    logger.info("  2. Load FHIR resources into fhir_resources table")
    logger.info("  3. Extract into team schema (Patient, Encounter, etc.)")
    logger.info("  4. Publish QualityCheckMessage → quality.check queue")
    logger.info("=" * 70)
    logger.info("")

    worker = LoadWorker()
    worker.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal LoadWorker error: %s", type(exc).__name__)
        sys.exit(1)

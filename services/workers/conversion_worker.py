"""
ConversionWorker: Converts raw data (CSV, FHIR) to standardized FHIR format.

This is the first stage of the async pipeline.
It consumes messages from 'ingestion.convert' queue and processes them.

Usage:
    python -m services.workers.conversion_worker
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from services.data_ingestion.app.db import session_scope
from services.data_ingestion.app.models import ConversionJob, SourceBatch
from services.data_ingestion.app.pipeline import job_manager, run_conversion
from services.data_ingestion.app.progress import progress_tracker
from services.document_intake import db
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import LoadMessage, Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ConversionWorker(Worker):
    """
    Consumes ConversionMessage from 'ingestion.convert' queue.

    Converts raw data (CSV, JSON, FHIR) to standard FHIR format
    and publishes LoadMessage to 'ingestion.load' queue.
    """

    def __init__(self):
        super().__init__(queue_name="ingestion.convert", prefetch_count=1)
        logger.info("ConversionWorker initialized")

    def process_message(self, message: dict) -> bool:
        """
        Process a conversion message.

        Args:
            message: ConversionMessage dict with:
                - batch_id: UUID of the batch
                - source_system: "synthea", "fhir_server", etc.
                - file_path: Path to source file(s)
                - file_type: "csv", "json", "fhir"

        Returns:
            True if conversion succeeded, False if it failed
        """
        try:
            batch_id = message.get("batch_id")
            job_id = message.get("job_id")
            source_system = message.get("source_system")
            file_path = message.get("file_path")
            file_type = message.get("file_type")
            explicit_adapter = message.get("adapter_name")

            if not all((batch_id, source_system, file_path, file_type)):
                logger.error("Conversion message is missing required fields")
                return False

            logger.info(
                f"\n📄 PROCESSING CONVERSION MESSAGE\n"
                f"  Batch ID: {batch_id}\n"
                f"  Source System: {source_system}\n"
                f"  File Type: {file_type}"
            )

            # Verify file/directory exists
            if not Path(file_path).exists():
                logger.error("Source file or directory not found for batch_id=%s", batch_id)
                asyncio.run(progress_tracker.notify(
                    batch_id, "conversion", "error",
                    {"message": "The ingestion source is unavailable"}
                ))
                return False

            # Determine input directory and adapter name from source system
            path_obj = Path(file_path)
            input_dir = str(path_obj) if path_obj.is_dir() else str(path_obj.parent)
            adapter_name = explicit_adapter or self._get_adapter_name(source_system, file_type)

            job_id = job_id or self._create_conversion_job(batch_id, input_dir)
            if not job_id:
                return False

            with db.connect() as conn:
                conn.execute(
                    """INSERT INTO batch_run (batch_id, ingest_job_id, status, step, counts)
                       VALUES (%s, %s, 'running', 'conversion', '{}'::jsonb)
                       ON CONFLICT (batch_id) DO UPDATE
                           SET ingest_job_id = EXCLUDED.ingest_job_id, updated_at = now()""",
                    (batch_id, job_id),
                )

            asyncio.run(progress_tracker.notify(
                batch_id, "conversion", "starting",
                {"message": f"Starting conversion from {source_system}"}
            ))

            # Send progress: processing
            asyncio.run(progress_tracker.notify(
                batch_id, "conversion", "processing",
                {"pct": 25, "message": "Running conversion pipeline"}
            ))

            # Run the conversion pipeline
            try:
                asyncio.run(progress_tracker.notify(
                    batch_id, "conversion", "processing",
                    {"pct": 50, "message": "Parsing and converting data"}
                ))
                # Ensure job state is initialized in job_manager before running
                job_manager.ensure_state(job_id, batch_id)
                run_conversion(job_id, input_dir, adapter_name)
            except Exception as exc:
                logger.error("Conversion pipeline failed for batch_id=%s: %s", batch_id, type(exc).__name__)
                asyncio.run(progress_tracker.notify(
                    batch_id, "conversion", "error",
                    {"message": "Conversion failed"}
                ))
                return False

            # Get conversion results
            resource_count = self._get_resource_count(job_id)
            if resource_count == 0:
                logger.warning("Conversion produced no FHIR resources")
                asyncio.run(progress_tracker.notify(
                    batch_id, "conversion", "error",
                    {"message": "No FHIR resources produced"}
                ))
                return False

            # Send progress: processing complete
            asyncio.run(progress_tracker.notify(
                batch_id, "conversion", "processing",
                {"pct": 90, "message": f"Publishing load message ({resource_count} resources)"}
            ))

            logger.info(f"✓ Conversion complete: {resource_count} FHIR resources created")

            # Commit the completed stage and its next message together.
            load_msg = LoadMessage(
                batch_id=batch_id,
                job_id=job_id,
                resource_count=resource_count,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            with db.connect() as conn:
                conn.execute(
                    """UPDATE batch_run SET status = 'running', step = 'loading', updated_at = now()
                         WHERE batch_id = %s""",
                    (batch_id,),
                )
                self.enqueue_next(conn, "ingestion.load", load_msg)
            logger.info("Queued LoadMessage in outbox for batch_id=%s", batch_id)

            # Send progress: complete
            asyncio.run(progress_tracker.notify(
                batch_id, "conversion", "complete",
                {"pct": 100, "message": f"Conversion complete: {resource_count} resources"}
            ))

            return True

        except Exception as exc:
            logger.error("Conversion worker failed for batch_id=%s: %s", message.get("batch_id"), type(exc).__name__)
            asyncio.run(progress_tracker.notify(
                message.get("batch_id"), "conversion", "error",
                {"message": "Conversion worker failed"}
            ))
            return False

    def _get_adapter_name(self, source_system: str, file_type: str) -> str:
        """Map source system + file type to adapter name."""
        source = source_system.lower()
        source_adapters = {
            "synthea_csv",
            "ehr_fhir_r4",
            "ccda_hie",
            "claims_837",
            "ncpdp_pharmacy",
        }
        if source in source_adapters:
            return source
        if source == "synthea":
            return "synthea_csv"
        if source == "claims":
            return "claims_837"
        if source in {"ncpdp", "pharmacy"}:
            return "ncpdp_pharmacy"

        normalized_type = file_type.lower()
        if normalized_type in {"fhir", "json", "ndjson"}:
            return "ehr_fhir_r4"
        if normalized_type in {"ccda", "xml"}:
            return "ccda_hie"
        if normalized_type in {"837", "x12", "edi"}:
            return "claims_837"
        if normalized_type in {"ncpdp", "pharmacy", "rx"}:
            return "ncpdp_pharmacy"
        return "synthea_csv"

    def _create_conversion_job(self, batch_id: str, input_dir: str) -> str | None:
        """Create a ConversionJob linked to the batch."""
        try:
            with session_scope() as session:
                batch_uuid = UUID(batch_id)
                if session.get(SourceBatch, batch_uuid) is None:
                    logger.error("Cannot create conversion job: source batch not found")
                    return None
                job = ConversionJob(input_dir=input_dir, status="pending", summary={}, batch_id=batch_uuid)
                session.add(job)
                session.flush()
                logger.info(f"Created ConversionJob: {job.id}")
                return job.id
        except Exception as exc:
            logger.error("Error creating conversion job for batch_id=%s: %s", batch_id, type(exc).__name__)
            return None

    def _get_resource_count(self, job_id: str) -> int:
        """Get the number of resources generated by a job."""
        try:
            with session_scope() as session:
                result = session.execute(
                    select(ConversionJob).where(ConversionJob.id == job_id)
                )
                job = result.scalar_one_or_none()
                if job:
                    return job.resources_generated or 0
                return 0
        except Exception as exc:
            logger.error("Error getting resource count for job_id=%s: %s", job_id, type(exc).__name__)
            return 0


def main():
    """Start the ConversionWorker."""
    logger.info("=" * 70)
    logger.info("HEDIS CONVERSION WORKER")
    logger.info("=" * 70)
    logger.info("Listening on queue: ingestion.convert")
    logger.info("Processing: CSV/JSON/FHIR → Standard FHIR")
    logger.info("")
    logger.info("Initializing RabbitMQ infrastructure...")
    initialize_queues()
    logger.info("")
    logger.info("To test, publish a message:")
    logger.info("""
    from services.rabbitmq_messages import Publisher, ConversionMessage

    msg = ConversionMessage(
        batch_id="test-001",
        source_system="synthea",
        file_path="data/seed/synthea/patients.csv",
        file_type="csv"
    )

    with Publisher() as pub:
        pub.publish("ingestion.convert", msg)
    """)
    logger.info("=" * 70)
    logger.info("")

    worker = ConversionWorker()
    worker.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal ConversionWorker error: %s", type(exc).__name__)
        sys.exit(1)

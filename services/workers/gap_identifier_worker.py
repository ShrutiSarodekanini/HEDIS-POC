"""Checkpoint care gaps produced by measure calculation before prioritization."""

import logging
import sys

from psycopg.types.json import Jsonb

from services.data_ingestion import team_schema_loader
from services.document_intake import db
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import GapPrioritizeMessage, Worker

logger = logging.getLogger(__name__)


class GapIdentifierWorker(Worker):
    def __init__(self) -> None:
        super().__init__(queue_name="gaps.identify", prefetch_count=1)

    def process_message(self, message: dict) -> bool:
        batch_id = message.get("batch_id")
        job_id = message.get("job_id")
        if not batch_id or not job_id:
            logger.error("Gap-identification message is missing required identifiers")
            return False
        try:
            with db.connect() as conn:
                patient_ids, _ = team_schema_loader.job_patients(conn, job_id)
                gap_count = conn.execute(
                    "SELECT count(*) FROM care_gap WHERE patient_id = ANY(%s)",
                    (list(patient_ids),),
                ).fetchone()[0]
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'prioritizing', counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"gap_identification": {"gaps": gap_count}}), batch_id),
                )
                self.enqueue_next(
                    conn,
                    "gaps.prioritize",
                    GapPrioritizeMessage(batch_id=batch_id, job_id=job_id),
                )
            return True
        except Exception as exc:
            logger.error("Gap identifier failed for batch_id=%s: %s", batch_id, type(exc).__name__)
            return False


def main() -> None:
    initialize_queues()
    GapIdentifierWorker().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
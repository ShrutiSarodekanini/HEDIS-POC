"""Extract proposed evidence for open gaps and hand off to star calculation."""

import logging
import sys

from psycopg.types.json import Jsonb

from services.document_intake import db
from services.integration import evidence
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import EvidenceExtractionMessage, StarCalculationMessage, Worker

logger = logging.getLogger(__name__)


class EvidenceWorker(Worker):
    def __init__(self) -> None:
        super().__init__(queue_name="evidence.extract", prefetch_count=1)

    def process_message(self, message: dict) -> bool:
        batch_id = message.get("batch_id")
        job_id = message.get("job_id")
        gap_id = message.get("gap_id")
        remaining_gap_ids = message.get("remaining_gap_ids", [])
        if not batch_id or not job_id or not gap_id:
            logger.error("Evidence message is missing required identifiers")
            return False
        try:
            with db.connect() as conn:
                saved = evidence.find_evidence(conn, gap_id)["saved"]

            with db.connect() as conn:
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'calculating_stars', counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"evidence": {"last_gap_proposed": saved}}), batch_id),
                )
                if remaining_gap_ids:
                    next_queue = "evidence.extract"
                    next_message = EvidenceExtractionMessage(
                        batch_id=batch_id,
                        job_id=job_id,
                        gap_id=remaining_gap_ids[0],
                        remaining_gap_ids=remaining_gap_ids[1:],
                    )
                else:
                    next_queue = "stars.calculate"
                    next_message = StarCalculationMessage(batch_id=batch_id, job_id=job_id)
                self.enqueue_next(conn, next_queue, next_message)
            return True
        except Exception as exc:
            logger.error("Evidence worker failed for batch_id=%s: %s", batch_id, type(exc).__name__)
            return False


def main() -> None:
    initialize_queues()
    EvidenceWorker().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
"""Validate configured value sets before measure calculation."""

import logging
import sys

from psycopg.types.json import Jsonb

from services.document_intake import db
from services.integration.config import SETTINGS
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import MeasureRunMessage, Worker

logger = logging.getLogger(__name__)


class ValueSetsWorker(Worker):
    def __init__(self) -> None:
        super().__init__(queue_name="valuesets.resolve", prefetch_count=1)

    def process_message(self, message: dict) -> bool:
        batch_id = message.get("batch_id")
        job_id = message.get("job_id")
        measure_set = message.get("measure_set", "HEDIS")
        if not batch_id or not job_id:
            logger.error("Value-set message is missing required identifiers")
            return False
        try:
            with db.connect() as conn:
                measure_count = conn.execute(
                    "SELECT count(*) FROM measure_rule WHERE measurement_year = %s",
                    (SETTINGS.measurement_year,),
                ).fetchone()[0]
                if not measure_count:
                    raise LookupError("No configured measure value sets are available")
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'measuring', counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"value_sets": {"measures_ready": measure_count}}), batch_id),
                )
                self.enqueue_next(
                    conn,
                    "measures.run",
                    MeasureRunMessage(batch_id=batch_id, job_id=job_id, measure_set=measure_set),
                )
            return True
        except Exception as exc:
            logger.error("Value-set worker failed for batch_id=%s: %s", batch_id, type(exc).__name__)
            return False


def main() -> None:
    initialize_queues()
    ValueSetsWorker().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
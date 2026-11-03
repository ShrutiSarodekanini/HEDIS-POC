"""Calculate and persist a batch-level star-rating summary."""

import logging
import sys

from psycopg.types.json import Jsonb
from sqlalchemy import text

from services.document_intake import db
from services.integration import stars
from services.integration.config import SETTINGS
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import Worker

logger = logging.getLogger(__name__)


class StarsWorker(Worker):
    def __init__(self) -> None:
        super().__init__(queue_name="stars.calculate", prefetch_count=1)

    def process_message(self, message: dict) -> bool:
        batch_id = message.get("batch_id")
        job_id = message.get("job_id")
        if not batch_id or not job_id:
            logger.error("Star message is missing required identifiers")
            return False
        try:
            with stars.session() as session:
                measures = session.execute(
                    text("SELECT DISTINCT measure_id FROM v_measure_result_latest WHERE measurement_year = :year"),
                    {"year": SETTINGS.measurement_year},
                ).scalars().all()
                cards = [stars.measure_card(session, measure_id, SETTINGS.measurement_year) for measure_id in measures]
            assigned = [card["stars"] for card in cards if card["stars"] is not None]
            summary = {
                "measures": len(cards),
                "rated": len(assigned),
                "average": round(sum(assigned) / len(assigned), 2) if assigned else None,
            }
            with db.connect() as conn:
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'prioritized', step = 'completed', counts = counts || %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"stars": summary}), batch_id),
                )
                self.complete_current(conn)
            return True
        except Exception as exc:
            logger.error("Star worker failed for batch_id=%s: %s", batch_id, type(exc).__name__)
            return False


def main() -> None:
    initialize_queues()
    StarsWorker().run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
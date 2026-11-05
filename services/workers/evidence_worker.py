"""Extract proposed evidence for open gaps one at a time and hand off to star calculation."""

import logging
import sys
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from services.document_intake import db
from services.integration import evidence
from services.rabbitmq_config import initialize_queues
from services.rabbitmq_messages import EvidenceExtractionMessage, StarCalculationMessage, Worker

logger = logging.getLogger(__name__)


def _extract_gap(batch_id: str, gap_id: str) -> tuple[int, bool]:
    """(findings saved, failed) for one gap. A failed gap is recorded and the chain carries on with the next one."""
    try:
        with db.connect() as conn:
            result = evidence.find_evidence(conn, gap_id)
    except Exception as exc:
        logger.error("Evidence extraction failed for batch_id=%s gap_id=%s: %s", batch_id, gap_id, type(exc).__name__)
        return 0, True
    calls = result.get("calls") or []
    # find_evidence records model errors per call and returns normally, so every call failing counts as a failed gap.
    if calls and all(call.get("error") for call in calls):
        logger.error("Evidence extraction failed for batch_id=%s gap_id=%s: every model call failed", batch_id, gap_id)
        return 0, True
    return int(result.get("saved") or 0), False


def _evidence_progress(done: int, total: int, proposed: int, failed: int) -> dict:
    """The counts.progress.evidence entry, in the shape progress_tracker.notify stores."""
    finished = done >= total
    if finished:
        message = f"AI evidence complete: {proposed} findings from {total} gaps" + (f", {failed} failed" if failed else "")
    else:
        message = f"AI evidence {done} of {total}"
    return {"status": "complete" if finished else "processing", "updated_at": datetime.now(timezone.utc).isoformat(),
            "pct": 100 if finished else round(done * 100 / total), "message": message}


class EvidenceWorker(Worker):
    def __init__(self) -> None:
        super().__init__(queue_name="evidence.extract", prefetch_count=1)

    def process_message(self, message: dict) -> bool:
        batch_id = message.get("batch_id")
        job_id = message.get("job_id")
        gap_id = message.get("gap_id")
        remaining_gap_ids: list[str] = message.get("remaining_gap_ids") or []
        if not batch_id or not job_id or not gap_id:
            logger.error("Evidence message is missing required identifiers")
            return False
        total = int(message.get("total_gaps") or 0) or 1 + len(remaining_gap_ids)
        done = total - len(remaining_gap_ids)
        saved, gap_failed = _extract_gap(batch_id, gap_id)
        proposed_total = int(message.get("proposed_total") or 0) + saved
        failed_gaps = int(message.get("failed_gaps") or 0) + (1 if gap_failed else 0)
        counts = {"gaps_total": total, "gaps_done": done, "proposed": proposed_total, "failed": failed_gaps}
        progress = _evidence_progress(done, total, proposed_total, failed_gaps)
        try:
            with db.connect() as conn:
                # Progress is written here rather than through progress_tracker.notify, which would also
                # overwrite step with the stage name "evidence".
                conn.execute(
                    """UPDATE batch_run
                          SET status = 'running', step = 'extracting_evidence',
                              counts = jsonb_set(counts || %s, '{progress}',
                                  coalesce(counts->'progress', '{}'::jsonb) || %s, true),
                              updated_at = now()
                        WHERE batch_id = %s""",
                    (Jsonb({"evidence": counts}), Jsonb({"evidence": progress}), batch_id),
                )
                if remaining_gap_ids:
                    next_queue = "evidence.extract"
                    next_message = EvidenceExtractionMessage(
                        batch_id=batch_id,
                        job_id=job_id,
                        gap_id=remaining_gap_ids[0],
                        remaining_gap_ids=remaining_gap_ids[1:],
                        total_gaps=total,
                        proposed_total=proposed_total,
                        failed_gaps=failed_gaps,
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
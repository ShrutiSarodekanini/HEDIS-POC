"""
Progress Tracking for WebSocket live updates.

Tracks batch processing progress and notifies connected WebSocket clients
as workers process messages through the pipeline.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from services.document_intake import db

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Manages batch processing progress and WebSocket notifications."""

    def __init__(self) -> None:
        self.connections: dict[str, set[Any]] = defaultdict(set)
        self.progress: dict[str, dict[str, Any]] = {}

    def register(self, batch_id: str, connection: Any) -> None:
        """Register a WebSocket connection for a batch."""
        self.connections[batch_id].add(connection)
        logger.debug("Registered WebSocket for batch_id=%s", batch_id)

    def unregister(self, batch_id: str, connection: Any) -> None:
        """Unregister a WebSocket connection."""
        self.connections[batch_id].discard(connection)
        if not self.connections[batch_id]:
            del self.connections[batch_id]
        logger.debug("Unregistered WebSocket for batch_id=%s", batch_id)

    async def notify(
        self,
        batch_id: str,
        stage: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Notify all connected clients of progress.

        Args:
            batch_id: Batch ID
            stage: Processing stage (conversion, load, quality, etc.)
            status: Status (starting, processing, complete, error)
            details: Additional details (pct, message, error, etc.)
        """
        # Update internal progress tracking
        if batch_id not in self.progress:
            self.progress[batch_id] = {
                "batch_id": batch_id,
                "stages": {},
                "current_stage": stage,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
            }

        # Update stage progress
        self.progress[batch_id]["stages"][stage] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **(details or {}),
        }

        # Update overall status
        if status == "complete":
            self.progress[batch_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

        self._persist(batch_id, stage, status, self.progress[batch_id]["stages"][stage])

        # Notify all connected clients
        message = {
            "batch_id": batch_id,
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
            "all_progress": self.progress[batch_id],
        }

        for connection in self.connections.get(batch_id, set()):
            try:
                await connection.send_json(message)
                logger.debug("Sent progress update for batch_id=%s stage=%s status=%s", batch_id, stage, status)
            except Exception as exc:
                logger.error("Failed to send progress update: %s", type(exc).__name__)

    def get_progress(self, batch_id: str) -> dict[str, Any]:
        """Get current progress for a batch."""
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, step, counts->'progress', started_at, updated_at FROM batch_run WHERE batch_id = %s",
                (batch_id,),
            ).fetchone()
        if row is None:
            return self.progress.get(batch_id, {})
        status, step, stages, started_at, updated_at = row
        return {
            "batch_id": batch_id,
            "status": status,
            "current_stage": step,
            "stages": stages or {},
            "started_at": started_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        }

    def get_all_progress(self) -> dict[str, dict[str, Any]]:
        """Get progress for all batches."""
        with db.connect() as conn:
            batch_ids = [row[0] for row in conn.execute(
                "SELECT batch_id FROM batch_run ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()]
        return {batch_id: self.get_progress(batch_id) for batch_id in batch_ids}

    def _persist(self, batch_id: str, stage: str, status: str, stage_progress: dict[str, Any]) -> None:
        overall_status = "prioritized" if stage == "documents" and status == "complete" else "running"
        step = "done" if overall_status == "prioritized" else stage
        error = None
        if status == "error":
            overall_status = "failed"
            error = f"{stage} stage failed"
        try:
            with db.connect() as conn:
                conn.execute(
                    """UPDATE batch_run
                          SET status = %s, step = %s,
                              counts = jsonb_set(counts, '{progress}',
                                  coalesce(counts->'progress', '{}'::jsonb) || %s, true),
                              error = %s, updated_at = now()
                        WHERE batch_id = %s""",
                    (overall_status, step, Jsonb({stage: stage_progress}), error, batch_id),
                )
        except Exception as exc:
            logger.error("Could not persist progress for batch_id=%s: %s", batch_id, type(exc).__name__)
            raise


# Global instance
progress_tracker = ProgressTracker()

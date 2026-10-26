"""Export confirmed evidence (supplemental_records) for the measure engine as a CSV, once per record.

Each export writes the records no earlier export contains to <INTEGRATION_WORK_DIR>/exports, then records the run
(export_runs, with the file's sha256) and its records (export_run_items) and one audit event, in one transaction.
Both tables cannot be changed afterwards (database rule), so an export is a fixed record of what was sent.
"""

import csv
import hashlib
import io
import logging
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from services.document_intake.db import audit

from .config import SETTINGS

log = logging.getLogger(__name__)

COLUMNS = ["supplemental_record_id", "patient_id", "measure_id", "measurement_year", "evidence_kind", "evidence_type",
           "service_date_from", "service_date_to", "value", "document_reference_id", "page_no", "evidence_id",
           "decision_id"]

# Only gaps whose every required part is confirmed (parts.py); a partially evidenced gap's records wait.
_UNEXPORTED = """
SELECT s.id, s.patient_id, s.measure_id, s.measurement_year, s.evidence_kind, s.evidence_type, s.service_date_from,
       s.service_date_to, s.value, s.document_reference_id, s.page_no, s.evidence_id, s.decision_id
  FROM supplemental_records s
  JOIN evidence_proposal p ON p.evidence_id = s.evidence_id
  JOIN care_gap_detail d ON d.gap_id = p.gap_id
 WHERE NOT EXISTS (SELECT 1 FROM export_run_items i WHERE i.supplemental_record_id = s.id)
   AND d.review_status IN ('closed_pending_engine', 'excluded_pending_engine')
 ORDER BY s.id
"""


def export_supplemental(conn: psycopg.Connection, actor: str) -> dict:
    """Write every not yet exported supplemental record of a fully confirmed gap to a new CSV. Returns {"rows": 0}
    when nothing is ready."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('supplemental_export'))")  # one export at a time
    rows = conn.execute(_UNEXPORTED).fetchall()
    if not rows:
        conn.rollback()
        return {"rows": 0}
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(["" if v is None else v for v in row] for row in rows)
    data = text.getvalue().encode("utf-8")
    sha256 = hashlib.sha256(data).hexdigest()
    path = SETTINGS.work_dir / "exports" / f"supplemental-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        run_id = conn.execute(
            """INSERT INTO export_runs (actor, filter, row_count, file_sha256, file_path)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (actor, Jsonb({"unexported_only": True}), len(rows), sha256, str(path))).fetchone()[0]
        conn.execute("""INSERT INTO export_run_items (export_run_id, supplemental_record_id)
                        SELECT %s, unnest(%s::bigint[])""", (run_id, [r[0] for r in rows]))
        audit(conn, "export_run", str(run_id), "supplemental.exported",
              {"rows": len(rows), "file_sha256": sha256, "file": path.name}, actor=actor)
        conn.commit()
    except Exception:
        conn.rollback()
        path.unlink(missing_ok=True)
        raise
    log.info("export run %s: %d supplemental records", run_id, len(rows))
    return {"export_run_id": run_id, "rows": len(rows), "file_path": str(path), "sha256": sha256}

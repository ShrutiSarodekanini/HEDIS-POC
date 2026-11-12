"""Ask the engine to rerun one gap's patient and measure with the confirmed supplemental data.

This replaces the manual "Close gap" button: no person sets care_gap.status. Once every required part of a gap's
evidence is confirmed (review status closed_pending_engine or excluded_pending_engine), a reviewer starts this rerun.
The engine reads the supplemental data (supplemental.py) and closes the gap only if the measure is now met or the
patient excluded (measures._close_met_gaps). The request is audited with the signed-in user, and any closure is
audited by the engine run.
"""

import uuid

import psycopg

from services.document_intake.db import audit

from . import parts, pipeline, supplemental
from .evidence import Conflict

_GAP = """
SELECT g.patient_id, g.measure_id, g.measurement_year, g.status, d.review_status
  FROM care_gap g LEFT JOIN care_gap_detail d ON d.gap_id = g.gap_id
 WHERE g.gap_id = %s
"""

_RECORDS = """
SELECT id, patient_id, measure_id, evidence_type, service_date_from, value
  FROM supplemental_records
 WHERE patient_id = %s AND measure_id = %s AND measurement_year = %s
 ORDER BY id
"""

# The newest calculation-ready batch whose ingestion job holds this patient, so gaps from older batches can rerun.
_BATCH = """
SELECT b.batch_id::text, b.ingest_job_id
  FROM batch_run b
 WHERE b.status IN ('ready', 'prioritized') AND b.ingest_job_id IS NOT NULL
   AND EXISTS (SELECT 1 FROM fhir_resources r
                WHERE r.job_id = b.ingest_job_id AND r.resource_type = 'Patient' AND r.resource_id = %s
                  AND r.persisted AND r.lifecycle_status <> 'rejected')
 ORDER BY b.started_at DESC
 LIMIT 1
"""


def request_rerun(conn: psycopg.Connection, gap_id: str, user_id: int, actor: str) -> dict:
    """Start an engine rerun for the gap's patient and measure. Returns the batch to watch.

    actor is the signed-in user. Raises LookupError for an unknown gap, Conflict when the gap is closed, its evidence
    is not complete, none of its confirmed evidence can be read by the engine, or no batch holds the patient, and
    ValueError when another calculation is running."""
    try:
        gap_uuid = uuid.UUID(gap_id)
    except ValueError as exc:
        raise LookupError(f"gap {gap_id} not found") from exc
    row = conn.execute(_GAP, (gap_uuid,)).fetchone()
    if row is None:
        raise LookupError(f"gap {gap_id} not found")
    patient_id, measure_id, year, status, review_status = row
    if status == "closed":
        raise Conflict("This gap is already closed")
    if review_status not in parts.WAITING_FOR_ENGINE:
        raise Conflict("Confirm the evidence for every required part before asking the engine to rerun this gap")
    records = [supplemental.Record(*r) for r in conn.execute(_RECORDS, (patient_id, measure_id, year)).fetchall()]
    inputs = supplemental.build(records)
    if not inputs.used:
        reasons = "; ".join(u.reason for u in inputs.unusable) or "there are no supplemental records"
        raise Conflict(f"The engine cannot use this gap's confirmed evidence: {reasons}")
    batch = conn.execute(_BATCH, (patient_id,)).fetchone()
    if batch is None:
        raise Conflict("No calculation-ready batch holds this patient's records, so the engine cannot rerun it")
    conn.rollback()
    batch_id = pipeline.start_recalculation([measure_id], [patient_id], user_id, batch=(batch[0], batch[1]))
    audit(conn, "care_gap", str(gap_uuid), "gap.engine_rerun_requested",
          {"measure_id": measure_id, "batch_id": batch_id, "supplemental_record_ids": inputs.used,
           "unusable_record_ids": [u.record_id for u in inputs.unusable]}, actor=actor)
    conn.commit()
    return {"gap_id": str(gap_uuid), "batch_id": batch_id, "status": "running",
            "engine_inputs": len(inputs.used),
            "not_usable": [{"supplemental_record_id": u.record_id, "evidence_type": u.evidence_type,
                            "reason": u.reason} for u in inputs.unusable]}

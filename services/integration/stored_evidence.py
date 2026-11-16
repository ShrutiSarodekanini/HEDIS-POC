"""Attach stored evidence: documents kept in evidence_library go to their patients after a batch finds its gaps.

evidence_library holds each document's bytes and its synthea_patient_id; scripts/db/load_seed.py fills it from
data/seed/stored_evidence. The ingestion mapping gives every patient a new FHIR id and keeps the Synthea id as a
Patient identifier with system urn:synthea:patients, so documents are matched through that identifier, never through
patient_id. Each document goes through documents.upload, the same intake and OCR path as an upload.

Documents belong to patients, not gaps: nothing links a document to a gap, and the AI does not run here. Each
document has its own transaction, so one bad file does not undo or stop the others; intake recognises a file it has
seen before, so running twice attaches nothing new.
"""

import logging
from collections.abc import Callable

from services.document_intake import db

from . import documents

log = logging.getLogger(__name__)

SYNTHEA_PATIENT_SYSTEM = "urn:synthea:patients"

_PATIENTS_BY_SYNTHEA_ID = """
SELECT i->>'value', r.resource_id
  FROM fhir_resource_raw r, jsonb_array_elements(r.raw->'identifier') AS i
 WHERE r.resource_type = 'Patient' AND r.resource_id = ANY(%s) AND i->>'system' = %s
"""

_LIBRARY_ENTRIES = "SELECT doc_id, synthea_patient_id FROM evidence_library ORDER BY doc_id"
_LIBRARY_FILE = "SELECT file_name, content FROM evidence_library WHERE doc_id = %s"


def attach(patient_ids: list[str], progress: Callable[[int, int], None]) -> dict:
    """Send each evidence_library document of this batch's patients through intake. Returns the counts.

    An empty library attaches nothing. Raises RuntimeError when every document sent failed.
    """
    with db.connect() as conn:
        entries = conn.execute(_LIBRARY_ENTRIES).fetchall()
        by_synthea_id = dict(conn.execute(_PATIENTS_BY_SYNTHEA_ID, (patient_ids, SYNTHEA_PATIENT_SYSTEM)).fetchall())
    counts: dict = {"documents": len(entries), "attached": 0, "already_attached": 0, "not_in_batch": 0, "failed": []}
    for n, (doc_id, synthea_patient_id) in enumerate(entries, 1):
        progress(n, len(entries))
        patient_id = by_synthea_id.get(synthea_patient_id)
        if patient_id is None:
            counts["not_in_batch"] += 1
            continue
        error = _send(doc_id, patient_id)
        if error is None:
            counts["attached"] += 1
        elif error == "duplicate":
            counts["already_attached"] += 1
        else:
            counts["failed"].append({"doc_id": doc_id, "error": error})
    sent = counts["attached"] + counts["already_attached"] + len(counts["failed"])
    log.info("stored evidence: %d attached, %d already attached, %d not in batch, %d failed", counts["attached"],
             counts["already_attached"], counts["not_in_batch"], len(counts["failed"]))
    if sent and len(counts["failed"]) == sent:
        raise RuntimeError(f"all {sent} documents failed to attach; first: {counts['failed'][0]['error']}")
    return counts


def _send(doc_id: str, patient_id: str) -> str | None:
    """None when attached, "duplicate" when intake has seen the file, otherwise the first line of the failure."""
    try:
        with db.connect() as conn:
            file_name, content = conn.execute(_LIBRARY_FILE, (doc_id,)).fetchone()
            result = documents.upload(conn, patient_id, bytes(content), file_name)
    except Exception as exc:  # noqa: BLE001 - one document's failure is recorded and the others still go
        return _first_line(f"{type(exc).__name__}: {exc}")
    if result["status"] == "duplicate":
        return "duplicate"
    if result["status"] != "received" or result["processing_status"] == "failed":
        return _first_line(result.get("note") or f"intake status {result['status']}")
    return None


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0][:200]

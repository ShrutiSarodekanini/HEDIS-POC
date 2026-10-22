"""Attach stored evidence: documents already in a folder go to their patients after a batch finds its gaps.

INTEGRATION_EVIDENCE_DIR holds the files and manifest.json (a list of entries with doc_id, file and
synthea_patient_id). The ingestion mapping gives every patient a new FHIR id and keeps the Synthea id as a Patient
identifier with system urn:synthea:patients, so documents are matched through that identifier, never through
patient_id. Each document goes through documents.upload, the same intake and OCR path as an upload.

Documents belong to patients, not gaps: nothing links a document to a gap, and the AI does not run here. Each
document has its own transaction, so one bad file does not undo or stop the others; intake recognises a file it has
seen before, so running twice attaches nothing new.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path

from services.document_intake import db

from . import documents

log = logging.getLogger(__name__)

SYNTHEA_PATIENT_SYSTEM = "urn:synthea:patients"

_PATIENTS_BY_SYNTHEA_ID = """
SELECT i->>'value', r.resource_id
  FROM fhir_resource_raw r, jsonb_array_elements(r.raw->'identifier') AS i
 WHERE r.resource_type = 'Patient' AND r.resource_id = ANY(%s) AND i->>'system' = %s
"""


class EvidenceSetupError(RuntimeError):
    """The evidence folder or its manifest cannot be used; the step fails before any document is sent."""


def read_manifest(folder: Path) -> list[dict]:
    """The manifest entries, after checking the folder, the manifest and every listed file exist."""
    if not folder.is_dir():
        raise EvidenceSetupError("evidence folder not found (INTEGRATION_EVIDENCE_DIR)")
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise EvidenceSetupError("manifest.json not found in the evidence folder")
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceSetupError(f"manifest.json is not valid JSON (line {exc.lineno})") from exc
    if not isinstance(entries, list):
        raise EvidenceSetupError("manifest.json must be a list of documents")
    for n, entry in enumerate(entries, 1):
        missing = [k for k in ("doc_id", "file", "synthea_patient_id") if not entry.get(k)]
        if missing:
            raise EvidenceSetupError(f"manifest entry {n} has no {', '.join(missing)}")
        if not (folder / entry["file"]).is_file():
            raise EvidenceSetupError(f"file for {entry['doc_id']} is listed in manifest.json but does not exist")
    return entries


def attach(folder: Path, patient_ids: list[str], progress: Callable[[int, int], None]) -> dict:
    """Send each manifest document of this batch's patients through intake. Returns the counts.

    Raises EvidenceSetupError for an unusable folder or manifest, and RuntimeError when every document sent failed.
    """
    entries = read_manifest(folder)
    with db.connect() as conn:
        by_synthea_id = dict(conn.execute(_PATIENTS_BY_SYNTHEA_ID, (patient_ids, SYNTHEA_PATIENT_SYSTEM)).fetchall())
    counts: dict = {"documents": len(entries), "attached": 0, "already_attached": 0, "not_in_batch": 0, "failed": []}
    for n, entry in enumerate(entries, 1):
        progress(n, len(entries))
        patient_id = by_synthea_id.get(entry["synthea_patient_id"])
        if patient_id is None:
            counts["not_in_batch"] += 1
            continue
        error = _send(folder / entry["file"], patient_id)
        if error is None:
            counts["attached"] += 1
        elif error == "duplicate":
            counts["already_attached"] += 1
        else:
            counts["failed"].append({"doc_id": entry["doc_id"], "error": error})
    sent = counts["attached"] + counts["already_attached"] + len(counts["failed"])
    log.info("stored evidence: %d attached, %d already attached, %d not in batch, %d failed", counts["attached"],
             counts["already_attached"], counts["not_in_batch"], len(counts["failed"]))
    if sent and len(counts["failed"]) == sent:
        raise RuntimeError(f"all {sent} documents failed to attach; first: {counts['failed'][0]['error']}")
    return counts


def _send(path: Path, patient_id: str) -> str | None:
    """None when attached, "duplicate" when intake has seen the file, otherwise the first line of the failure."""
    try:
        with db.connect() as conn:
            result = documents.upload(conn, patient_id, path.read_bytes(), path.name)
    except Exception as exc:  # noqa: BLE001 - one document's failure is recorded and the others still go
        return _first_line(f"{type(exc).__name__}: {exc}")
    if result["status"] == "duplicate":
        return "duplicate"
    if result["status"] != "received" or result["processing_status"] == "failed":
        return _first_line(result.get("note") or f"intake status {result['status']}")
    return None


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0][:200]

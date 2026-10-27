"""Document upload for one patient: received the same way as the document intake CLI's "ingest", then processed.

The patient is looked up by patient_id and passed to intake as its member ID (patient.mrn), exactly as
`python -m services.document_intake.cli ingest <path> --member <mrn>` does. intake.receive_file already processes a
new file; a file still pending afterwards is processed once more, as "process-pending" would.
"""

import psycopg

from services.document_intake import intake


def upload(conn: psycopg.Connection, patient_id: str, data: bytes, filename: str) -> dict:
    """Receive and convert one document for a patient. Raises LookupError for an unknown patient."""
    row = conn.execute("SELECT mrn FROM patient WHERE patient_id = %s", (patient_id,)).fetchone()
    if row is None or not row[0]:
        raise LookupError("patient not found")
    result = intake.receive_file(conn, data, filename, None, row[0])
    file_id = result.get("source_file_id")
    if result.get("status") == "received" and file_id and result.get("processing_status") == "pending":
        result = {**intake.process_file(conn, file_id), "status": "received"}
    documents = []
    if file_id:
        documents = [r[0] for r in conn.execute(
            """SELECT c.document_reference_id FROM document_conversion c
                WHERE c.source_file_id = %s OR c.source_file_id IN (SELECT id FROM source_files WHERE parent_file_id = %s)
                ORDER BY 1""", (file_id, file_id)).fetchall()]
    return {"status": result.get("status"), "source_file_id": file_id,
            "processing_status": result.get("processing_status"), "match_status": result.get("match_status"),
            "note": result.get("processing_note") or result.get("message"), "documents": documents}

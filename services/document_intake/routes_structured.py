"""HTTP routes for structured files (US-02 T4)."""

from fastapi import APIRouter, HTTPException

from .db import connect

router = APIRouter()


def _require_file(conn, source_file_id: int) -> None:
    if conn.execute("SELECT 1 FROM source_files WHERE id = %s", (source_file_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"file {source_file_id} not found")


@router.get("/api/structured/{source_file_id}/rows")
def list_rows(source_file_id: int, status: str | None = None) -> list[dict]:
    """Every row of a structured file with its status and reject reason. Filter with ?status=rejected."""
    with connect() as conn:
        _require_file(conn, source_file_id)
        cur = conn.execute(
            """SELECT s.row_number, s.patient_id, p.mrn, s.record_type, s.code_system, s.code, s.description,
                      s.value_text, s.value_num, s.unit, s.observed_on, s.status, s.reject_reason, s.raw
               FROM structured_records s LEFT JOIN patient p ON p.patient_id = s.patient_id
               WHERE s.source_file_id = %s AND (%s::text IS NULL OR s.status = %s)
               ORDER BY s.row_number""", (source_file_id, status, status))
        names = [d.name for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


@router.get("/api/structured/{source_file_id}/errors")
def list_errors(source_file_id: int) -> list[dict]:
    """The rejected rows of a structured file: [{row, reason, raw}]."""
    with connect() as conn:
        _require_file(conn, source_file_id)
        rows = conn.execute(
            """SELECT row_number, reject_reason, raw FROM structured_records
               WHERE source_file_id = %s AND status = 'rejected' ORDER BY row_number""", (source_file_id,)).fetchall()
    return [{"row": n, "reason": reason, "raw": raw} for n, reason, raw in rows]

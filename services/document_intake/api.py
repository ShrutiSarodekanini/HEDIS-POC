"""HTTP API for document intake (US-02 T1). No sign-in: authentication is out of scope for the POC.

Run: python -m uvicorn services.document_intake.api:app --port 8765
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, intake
from .config import REPO_ROOT

log = logging.getLogger(__name__)

app = FastAPI(title="HEDIS document intake", version="0.1.0")

ProcessingStatus = Literal["pending", "processing", "done", "failed", "rejected", "duplicate"]
MatchStatus = Literal["matched", "needs_match", "unmatched"]


def get_conn() -> Iterator[psycopg.Connection]:
    """One connection per request: committed when the request succeeds, rolled back on an error."""
    with db.connect() as conn:
        yield conn


# --- Response models ---------------------------------------------------------------------------------------------

class FileOut(BaseModel):
    """A received file (the stored bytes are never returned)."""
    id: int
    sha256: str
    original_name: str
    stored_path: str
    detected_type: str | None
    detected_kind: str | None
    size_bytes: int | None
    received_at: datetime
    source_org_id: str | None
    member_id: str | None
    match_status: str
    processing_status: str
    processing_note: str | None
    encrypted: bool
    parent_file_id: int | None
    scan_result: str | None


class PageOut(BaseModel):
    page_no: int
    method: str
    confidence: float | None
    handwritten: bool
    low_confidence: bool
    text: str
    image_path: str | None
    ocr_engine: str | None


class DocumentOut(BaseModel):
    id: str
    source_file_id: int
    member_id: str | None
    doc_type: str | None
    document_date: date | None
    source_org_id: str | None
    near_duplicate_of: str | None
    page_count: int | None
    conversion_status: str
    conversion_note: str | None
    pages: list[PageOut]


class DocumentsOut(BaseModel):
    """The documents found, in the order asked, and the ids that matched nothing."""
    documents: list[DocumentOut]
    not_found: list[str]


# --- Routes ------------------------------------------------------------------------------------------------------

@app.get("/api/files/{file_id}", response_model=FileOut)
def get_file(file_id: int, conn: psycopg.Connection = Depends(get_conn)):
    """One file's row."""
    row = intake.get_file(conn, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"file {file_id} not found")
    return row


@app.get("/api/files", response_model=list[FileOut])
def list_files(status: ProcessingStatus | None = None, match_status: MatchStatus | None = None,
               limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0),
               conn: psycopg.Connection = Depends(get_conn)):
    """Files, optionally filtered by processing status and match status."""
    return intake.list_files(conn, status, match_status, limit, offset)


@app.get("/api/documents", response_model=DocumentsOut)
def get_documents(ids: list[str] = Query(..., alias="id", min_length=1, max_length=50),
                  conn: psycopg.Connection = Depends(get_conn)):
    """Several documents with their pages in one call: ?id=D-101&id=D-601. Unknown ids are listed, not fatal."""
    wanted = list(dict.fromkeys(ids))
    docs = intake.get_documents(conn, wanted)
    found = {doc["id"] for doc in docs}
    return {"documents": docs, "not_found": [i for i in wanted if i not in found]}


@app.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, conn: psycopg.Connection = Depends(get_conn)):
    """A document with its pages."""
    doc = intake.get_document(conn, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")
    return doc


@app.get("/api/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: str, page_no: int, conn: psycopg.Connection = Depends(get_conn)):
    """The rendered PNG of one page, for the review screen."""
    row = conn.execute("SELECT image_path FROM document_pages WHERE document_id = %s AND page_no = %s",
                       (document_id, page_no)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"page {page_no} of document {document_id} not found")
    if not row[0]:
        raise HTTPException(status_code=404, detail="this page has no image")
    path = Path(row[0])
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="the page image file is missing from storage")
    return FileResponse(path, media_type="image/png")


try:
    from .routes_structured import router as _structured_router
    app.include_router(_structured_router)
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}.routes_structured":
        raise
except ImportError:
    log.warning("structured file routes could not be loaded", exc_info=True)

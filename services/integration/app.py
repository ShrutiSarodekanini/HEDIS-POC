"""One small API app for the end-to-end flow, with the work list page at "/".

    python -m services.integration.app            then open http://localhost:8780

Endpoints stay thin; the work is in pipeline, work, documents and evidence. Errors return a plain message with a
deliberate status code; stack traces stay in the server log. Listens on 127.0.0.1 only. Synthetic data only.
"""

import logging
import shutil
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Literal

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from services.document_intake import api as intake_api
from services.document_intake import db
from services.gap_priority.config import load_rules

from . import evidence, openrouter_status, pipeline, work
from .config import SETTINGS

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("static") / "index.html"
app = FastAPI(title="HEDIS POC integration", version="0.1.0")


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


@app.exception_handler(LookupError)
def _not_found(request: Request, exc: LookupError) -> JSONResponse:
    if isinstance(exc, (KeyError, IndexError)):  # a bug, not a missing record
        return _server_error(request, exc)
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(evidence.Conflict)
def _conflict(_: Request, exc: evidence.Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValueError)
def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
def _server_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("%s %s failed: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": f"The server could not finish this request "
                                                            f"({type(exc).__name__}). See the server log."})


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@app.post("/api/batches", status_code=202)
def upload_batch(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "Upload a .zip file of Synthea CSV files.")
    target = SETTINGS.work_dir / "uploads" / f"{uuid.uuid4()}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return {"batch_id": pipeline.start_batch(target), "status": "running"}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    found = pipeline.batch_status(conn, batch_id)
    if found is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return found


@app.get("/api/measures")
def get_measures(as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return work.measures(conn, as_of or date.today())


@app.get("/api/gaps")
def get_gaps(role: str | None = None, tier: Literal["high", "medium", "low"] | None = None,
             measure: str | None = None, limit: int = 400, conn: psycopg.Connection = Depends(get_conn)) -> list:
    return work.work_list(conn, role, tier, measure, limit)


@app.get("/api/gaps/{gap_id}")
def get_gap(gap_id: str, as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    found = work.gap_detail(conn, gap_id, as_of or date.today())
    if found is None:
        raise HTTPException(404, f"gap {gap_id} not found")
    return found


@app.post("/api/gaps/{gap_id}/evidence")
def find_evidence(gap_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return evidence.find_evidence(conn, gap_id)


class DecisionIn(BaseModel):
    decision: Literal["confirm", "reject"]
    reason: str | None = None


@app.post("/api/evidence/{proposal_id}/decision")
def decide(proposal_id: str, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return evidence.decide(conn, proposal_id, body.decision, body.reason)


@app.get("/api/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return {"status": "ok", "schema": schema, "migration": version, "rules_version": load_rules().version,
            "engine_built": (SETTINGS.engine_root / "build" / "cli.js").exists(),
            "evidence_dir_set": SETTINGS.evidence_dir is not None, "openrouter": openrouter_status.status()}


app.add_api_route("/api/documents/{document_id}/pages/{page_no}/image", intake_api.get_page_image, methods=["GET"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"HEDIS POC integration on http://localhost:{SETTINGS.port}")
    uvicorn.run(app, host="127.0.0.1", port=SETTINGS.port, log_level="info")

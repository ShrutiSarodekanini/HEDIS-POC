"""One small API app for the end-to-end flow, with the Ops Console at "/".

    python -m services.integration.app            then open http://localhost:8780

Endpoints stay thin; the work is in pipeline, work, documents and evidence. Errors return a plain message with a
deliberate status code; stack traces stay in the server log. Listens on 127.0.0.1 only. Synthetic data only.
"""

import logging
import re
import shutil
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

import psycopg
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.data_ingestion.app import api as ingestion_api
from services.document_intake import api as intake_api
from services.document_intake import db
from services.gap_priority.config import load_rules

from . import calculation, evidence, export, openrouter_status, pallavi, pipeline, quality, reset, stars, work
from .config import SETTINGS

log = logging.getLogger(__name__)

UI_DIR = SETTINGS.ui_dir or Path(__file__).with_name("ui") / "redesign"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop = threading.Event()
    threading.Thread(target=pipeline.watch_ingestion, args=(stop,), name="ingestion-watcher", daemon=True).start()
    try:
        yield
    finally:
        stop.set()


def _ingestion_routes() -> APIRouter:
    """Jyothi's ingestion and engine endpoints as they are, at /api/ingestion/... instead of her own /api/..."""
    router = APIRouter()
    for route in ingestion_api.router.routes:
        if isinstance(route, APIRoute):
            router.add_api_route(route.path.removeprefix(ingestion_api.router.prefix), route.endpoint,
                                 methods=sorted(route.methods), name=route.name)
        elif hasattr(route, 'endpoint') and hasattr(route, 'path') and 'websocket' in str(type(route)).lower():
            # Copy WebSocket routes by path  (FastAPI WebSocketRoute)
            path = route.path.removeprefix(ingestion_api.router.prefix)
            router.add_api_websocket_route(path, route.endpoint, name=getattr(route, 'name', None))
    return router


app = FastAPI(title="HEDIS POC integration", version="0.1.0", lifespan=_lifespan)
app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")
app.include_router(_ingestion_routes(), prefix="/api/ingestion")


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


@app.exception_handler(reset.ResetRefused)
def _reset_refused(_: Request, exc: reset.ResetRefused) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


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
    return (UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/ingestion", response_class=HTMLResponse)
def pipeline_monitor() -> str:
    """Serve the real-time pipeline progress monitor dashboard"""
    dashboard_path = Path(__file__).parent / "ui" / "redesign" / "pipeline-monitor.html"
    return dashboard_path.read_text(encoding="utf-8")


@app.post("/api/batches", status_code=202)
def upload_batch(file: UploadFile | None = File(None), files: list[UploadFile] | None = File(None)) -> dict:
    """One .zip of Synthea CSV files, or several .csv files, in one request (form field "file" or "files")."""
    uploads = ([file] if file else []) + (files or [])
    names = [Path(u.filename or "").name for u in uploads]
    kinds = {Path(n).suffix.lower() for n in names}
    if not uploads:
        raise HTTPException(400, "Choose one .zip file, or one or more Synthea .csv files.")
    if not kinds <= {".zip", ".csv"}:
        raise HTTPException(400, "Only a .zip file or Synthea .csv files can be uploaded.")
    if len(kinds) > 1:
        raise HTTPException(400, "Upload either one .zip file or .csv files, not both in one batch.")
    if kinds == {".zip"} and len(uploads) > 1:
        raise HTTPException(400, "Upload one .zip file at a time.")
    if any(not _SAFE_NAME.fullmatch(n) for n in names):
        raise HTTPException(400, "File names may use letters, digits, '.', '_' and '-' only.")
    if kinds == {".zip"}:
        target = SETTINGS.work_dir / "uploads" / f"{uuid.uuid4()}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            shutil.copyfileobj(uploads[0].file, handle)
    else:
        target = SETTINGS.work_dir / "uploads" / str(uuid.uuid4())
        target.mkdir(parents=True)
        for upload, name in zip(uploads, names):
            with (target / name.lower()).open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
    return {"batch_id": pipeline.start_batch(target), "status": "running", "files": len(uploads)}


_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")


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
    reviewer: str | None = None


@app.post("/api/evidence/{proposal_id}/decision")
def decide(proposal_id: str, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return evidence.decide(conn, proposal_id, body.decision, body.reason, body.reviewer)


class ExportIn(BaseModel):
    reviewer: str | None = None


@app.post("/api/supplemental/export")
def export_supplemental(body: ExportIn | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return export.export_supplemental(conn, evidence.clean_reviewer(body.reviewer if body else None))


class ResetIn(BaseModel):
    confirm: str


@app.post("/api/reset")
def start_over(body: ResetIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Development only: empty the data tables and keep the reference data (see reset.py for the guards)."""
    return reset.reset(conn, body.confirm)


@app.get("/api/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return {"status": "ok", "schema": schema, "migration": version, "rules_version": load_rules().version,
            "engine_built": (SETTINGS.engine_root / "build" / "cli.js").exists(),
            "evidence_dir_set": SETTINGS.evidence_dir is not None, "reset_allowed": reset.allowed(),
            "openrouter": openrouter_status.status(),
            "services": {"ingestion": {"prefix": "/api/ingestion", "schema": schema},
                         "quality": {**pallavi.status(), "schema": schema}}}


@app.get("/api/quality")
def get_quality(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return quality.overview(conn)


@app.get("/api/quality/runs")
def get_quality_runs(batch_id: uuid.UUID, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return quality.runs(conn, batch_id)


@app.get("/api/quality/runs/{run_id}")
def get_quality_run(run_id: int, severity: Literal["critical", "high", "medium", "low"] | None = None,
                    conn: psycopg.Connection = Depends(get_conn)) -> dict:
    found = quality.run_detail(conn, run_id, severity)
    if found is None:
        raise HTTPException(404, f"data quality run {run_id} not found")
    return found


class QualityRunIn(BaseModel):
    batch_id: uuid.UUID
    measure_id: str | None = None


@app.post("/api/quality/runs")
def run_quality(body: QualityRunIn) -> dict:
    """Run the data quality checks again on one loaded batch (every measure, or one); returns the new run."""
    return pallavi.run_checks(body.batch_id, body.measure_id, SETTINGS.actor)


@app.get("/api/quality/coverage")
def get_coverage_report(payer: str | None = None, compliance: Literal["Compliant", "Violation"] | None = None,
                        start_date: date | None = None, end_date: date | None = None, limit: int = 1000,
                        conn: psycopg.Connection = Depends(get_conn)) -> dict:
    compliant = None if compliance is None else compliance == "Compliant"
    return quality.coverage_report(conn, payer, compliant, start_date, end_date, min(max(limit, 1), 5000))


@app.get("/api/flow")
def get_flow(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return pipeline.flow(conn)


@app.get("/api/calculation")
def get_calculation(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return calculation.overview(conn)


@app.get("/api/calculation/{measure_id}/patients")
def get_calculation_patients(measure_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return calculation.patients(conn, measure_id)


class CalculationIn(BaseModel):
    measures: list[str] | None = None


@app.post("/api/calculation/run", status_code=202)
def run_calculation(body: CalculationIn | None = None) -> dict:
    """Run the measure engine again for the latest finished batch (all measures, or the chosen ones)."""
    return {"batch_id": pipeline.start_recalculation(body.measures if body else None), "status": "running"}


@app.get("/api/stars/catalog")
def get_star_catalog() -> dict:
    rules = load_rules()
    with stars.session() as s:
        return {"measurement_year": SETTINGS.measurement_year, "star_year": rules.star_year,
                "measures": stars.catalog(s, SETTINGS.measurement_year, rules.star_year)}


class StarCalculationIn(BaseModel):
    measure_id: str
    rate: float | None = None
    denominator: int


@app.post("/api/stars/calculate")
def calculate_star(body: StarCalculationIn) -> dict:
    with stars.session() as s:
        return stars.calculate(s, body.measure_id, SETTINGS.measurement_year, body.rate, body.denominator)


class WhatIfIn(BaseModel):
    measure_id: str
    gap_ids: list[str] = []


@app.post("/api/stars/what-if")
def star_what_if(body: WhatIfIn) -> dict:
    with stars.session() as s:
        return stars.what_if(s, body.measure_id, body.gap_ids)


app.add_api_route("/api/documents/{document_id}/pages/{page_no}/image", intake_api.get_page_image, methods=["GET"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"HEDIS POC integration on http://localhost:{SETTINGS.port}")
    uvicorn.run(app, host="127.0.0.1", port=SETTINGS.port, log_level="info")

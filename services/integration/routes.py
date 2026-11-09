"""The integration endpoints (batches, work list, AI evidence, export, data quality, stars, health) for root app.py.

Formerly the separate app on port 8780. Same paths, methods, parameters, request models, responses and auth levels;
root app.py includes the router with its own auth dependencies. Errors are mapped the way that app mapped them
(LookupError 404, Conflict 409, ResetRefused and AccessNotApproved 403, ValueError 400, anything else 500 with the type
only), but by this router's route class, so root app.py's own routes keep their behaviour. The mapped error is raised
as an HTTPException (same status and {"detail": ...} body), so a failed request still rolls its connection back.
"""

import logging
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Iterator, Literal

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from common import app_metrics
from services.document_intake import api as intake_api
from services.document_intake import db
from services.gap_priority.config import load_rules

from . import auth, dq_checks, evidence, export, openrouter_status, pipeline, quality, reset, stars, work
from .config import SETTINGS

log = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _mapped_error(method: str, path: str, exc: Exception) -> HTTPException:
    """The 8780 app's exception handlers, most specific first (Starlette picked them by the exception's MRO)."""
    if isinstance(exc, evidence.Conflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, (reset.ResetRefused, auth.AccessNotApproved)):
        return HTTPException(403, str(exc))
    if isinstance(exc, LookupError) and not isinstance(exc, (KeyError, IndexError)):  # those are bugs, not misses
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    log.error("%s %s failed: %s", method, path, type(exc).__name__)
    return HTTPException(500, f"The server could not finish this request ({type(exc).__name__}). See the server log.")


class MappedErrorsRoute(APIRoute):
    """Applies _mapped_error to this router's routes only; HTTP and validation errors keep FastAPI's handling."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        path = self.path_format

        async def mapped(request: Request) -> Response:
            try:
                return await handler(request)
            except (StarletteHTTPException, RequestValidationError):
                raise
            except Exception as exc:  # noqa: BLE001 - mapped to a status code exactly as the 8780 app did
                # Raised, not returned: the connection dependency must still see a failure and roll back.
                raise _mapped_error(request.method, path, exc) from exc

        return mapped


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


class DecisionIn(BaseModel):
    decision: Literal["confirm", "reject"]
    reason: str | None = None


class ResetIn(BaseModel):
    confirm: str


class QualityRunIn(BaseModel):
    batch_id: uuid.UUID
    measure_id: str | None = None


class StarCalculationIn(BaseModel):
    measure_id: str
    rate: float | None = None
    denominator: int


class WhatIfIn(BaseModel):
    measure_id: str
    gap_ids: list[str] = []


def build_router(current_user_dep: Callable[..., auth.AuthUser],
                 admin_dep: Callable[..., auth.AuthUser]) -> APIRouter:
    """The router, with the caller's dependencies for "any signed-in user" and "admin"."""
    router = APIRouter(route_class=MappedErrorsRoute)
    openrouter_status.warm()  # so the first /api/health after startup answers from cache
    track = app_metrics.track_route

    @router.post("/api/batches", status_code=202)
    @track("batch upload")
    def upload_batch(file: UploadFile | None = File(None), files: list[UploadFile] | None = File(None),
                     _: auth.AuthUser = Depends(admin_dep)) -> dict:
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

    @router.get("/api/batches/latest")
    @track("latest batch")
    def get_latest_batch(conn: psycopg.Connection = Depends(get_conn),
                         _: auth.AuthUser = Depends(current_user_dep)) -> dict | None:
        return pipeline.batch_status(conn, "latest")

    @router.get("/api/batches/{batch_id}")
    @track("batch status")
    def get_batch(batch_id: str, conn: psycopg.Connection = Depends(get_conn),
                  _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        found = pipeline.batch_status(conn, batch_id)
        if found is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        return found

    @router.get("/api/measures")
    @track("measures")
    def get_measures(as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn),
                     _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return work.measures(conn, as_of or date.today())

    @router.get("/api/gaps")
    @track("work list")
    def get_gaps(role: str | None = None, tier: Literal["high", "medium", "low"] | None = None,
                 measure: str | None = None, limit: int = 400, conn: psycopg.Connection = Depends(get_conn),
                 _: auth.AuthUser = Depends(current_user_dep)) -> list:
        return work.work_list(conn, role, tier, measure, limit)

    @router.get("/api/gaps/{gap_id}")
    @track("gap detail")
    def get_gap(gap_id: str, as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn),
                _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        found = work.gap_detail(conn, gap_id, as_of or date.today())
        if found is None:
            raise HTTPException(404, f"gap {gap_id} not found")
        return found

    @router.post("/api/gaps/{gap_id}/evidence")
    @app_metrics.track_evidence
    def find_evidence(gap_id: str, conn: psycopg.Connection = Depends(get_conn),
                      _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return evidence.find_evidence(conn, gap_id)

    @router.post("/api/gaps/{gap_id}/close")
    @track("close gap")
    def close_gap(gap_id: str, conn: psycopg.Connection = Depends(get_conn),
                  user: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return evidence.close_gap(conn, gap_id, user.user_id, user.email)

    @router.post("/api/evidence/{proposal_id}/decision")
    @app_metrics.track_evidence
    def decide(proposal_id: str, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn),
               user: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return evidence.decide(conn, proposal_id, body.decision, body.reason, user.email)

    @router.post("/api/supplemental/export")
    @track("supplemental export")
    def export_supplemental(conn: psycopg.Connection = Depends(get_conn),
                            user: auth.AuthUser = Depends(admin_dep)) -> dict:
        return export.export_supplemental(conn, evidence.clean_reviewer(user.email))

    @router.post("/api/reset")
    @track("start over")
    def start_over(body: ResetIn, conn: psycopg.Connection = Depends(get_conn),
                   _: auth.AuthUser = Depends(admin_dep)) -> dict:
        """Development only: empty the data tables and keep the reference data (see reset.py for the guards)."""
        return reset.reset(conn, body.confirm)

    @router.get("/api/health")
    @track("health")
    def health(conn: psycopg.Connection = Depends(get_conn),
               _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        return {"status": "ok", "schema": schema, "migration": version, "rules_version": load_rules().version,
                "engine_built": (SETTINGS.engine_root / "build" / "cli.js").exists(),
                "evidence_dir_set": SETTINGS.evidence_dir is not None, "reset_allowed": reset.allowed(),
                "openrouter": openrouter_status.status(),
                "services": {"ingestion": {"prefix": "/api/ingestion", "schema": schema},
                             "quality": {**dq_checks.status(), "schema": schema}}}

    @router.get("/api/quality")
    @track("quality overview")
    def get_quality(conn: psycopg.Connection = Depends(get_conn),
                    _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return quality.overview(conn)

    @router.get("/api/quality/runs")
    @track("quality runs")
    def get_quality_runs(batch_id: uuid.UUID, conn: psycopg.Connection = Depends(get_conn),
                         _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        return quality.runs(conn, batch_id)

    @router.get("/api/quality/runs/{run_id}")
    @track("quality run detail")
    def get_quality_run(run_id: int, severity: Literal["critical", "high", "medium", "low"] | None = None,
                        conn: psycopg.Connection = Depends(get_conn),
                        _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        found = quality.run_detail(conn, run_id, severity)
        if found is None:
            raise HTTPException(404, f"data quality run {run_id} not found")
        return found

    @router.post("/api/quality/runs")
    @track("quality run")
    def run_quality(body: QualityRunIn, _: auth.AuthUser = Depends(admin_dep)) -> dict:
        """Run the data quality checks again on one loaded batch (every measure, or one); returns the new run."""
        return dq_checks.run_checks(body.batch_id, body.measure_id, SETTINGS.actor)

    @router.get("/api/quality/coverage")
    @track("coverage report")
    def get_coverage_report(payer: str | None = None, compliance: Literal["Compliant", "Violation"] | None = None,
                            start_date: date | None = None, end_date: date | None = None, limit: int = 1000,
                            conn: psycopg.Connection = Depends(get_conn),
                            _: auth.AuthUser = Depends(admin_dep)) -> dict:
        compliant = None if compliance is None else compliance == "Compliant"
        return quality.coverage_report(conn, payer, compliant, start_date, end_date, min(max(limit, 1), 5000))

    @router.get("/api/stars/catalog")
    @track("stars catalog")
    def get_star_catalog(_: auth.AuthUser = Depends(current_user_dep)) -> dict:
        rules = load_rules()
        with stars.session() as s:
            return {"measurement_year": SETTINGS.measurement_year, "star_year": rules.star_year,
                    "measures": stars.catalog(s, SETTINGS.measurement_year, rules.star_year)}

    @router.post("/api/stars/calculate")
    @track("stars calculate")
    def calculate_star(body: StarCalculationIn, _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        with stars.session() as s:
            return stars.calculate(s, body.measure_id, SETTINGS.measurement_year, body.rate, body.denominator)

    @router.post("/api/stars/what-if")
    @track("stars what-if")
    def star_what_if(body: WhatIfIn, _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        with stars.session() as s:
            return stars.what_if(s, body.measure_id, body.gap_ids)

    @router.get("/api/documents/{document_id}/pages/{page_no}")
    @track("page text")
    def get_page(document_id: str, page_no: int, conn: psycopg.Connection = Depends(get_conn),
                 _: auth.AuthUser = Depends(current_user_dep)) -> dict:
        row = conn.execute(
            """SELECT text, method, handwritten, image_path IS NOT NULL
                 FROM document_pages WHERE document_reference_id = %s AND page_no = %s""",
            (document_id, page_no)).fetchone()
        if row is None:
            raise HTTPException(404, f"page {page_no} of document {document_id} not found")
        return {"document_id": document_id, "page_no": page_no, "text": row[0], "method": row[1],
                "handwritten": row[2], "has_image": row[3]}

    router.add_api_route("/api/documents/{document_id}/pages/{page_no}/image",
                         track("page image")(intake_api.get_page_image), methods=["GET"],
                         dependencies=[Depends(current_user_dep)])
    return router

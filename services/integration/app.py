"""One small API app for the end-to-end flow, with the work list page at "/".

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
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

import psycopg
import uvicorn
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from services.data_ingestion.app import api as ingestion_api
from services.document_intake import api as intake_api
from services.document_intake import db
from services.document_intake.db import audit
from services.gap_priority.config import load_rules

from . import (
    auth,
    calculation,
    dq_checks,
    evidence,
    export,
    openrouter_status,
    pipeline,
    quality,
    reset,
    stars,
    work,
)
from .config import SETTINGS

log = logging.getLogger(__name__)

UI_DIR = SETTINGS.ui_dir or Path(__file__).with_name("ui") / "redesign"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    with db.connect() as conn:
        auth.ensure_default_users(conn)
    pipeline.recover_interrupted_batches()
    pipeline.record_missing_upload_sizes()
    stop = threading.Event()
    threading.Thread(target=pipeline.watch_ingestion, args=(stop,), name="ingestion-watcher", daemon=True).start()
    try:
        yield
    finally:
        stop.set()


def _ingestion_routes() -> APIRouter:
    """The ingestion and engine endpoints as they are, at /api/ingestion/... instead of their own /api/..."""
    router = APIRouter()
    for route in ingestion_api.router.routes:
        if isinstance(route, APIRoute):
            router.add_api_route(route.path.removeprefix(ingestion_api.router.prefix), route.endpoint,
                                 methods=sorted(route.methods), name=route.name)
    return router


app = FastAPI(title="HEDIS POC integration", version="0.1.0", lifespan=_lifespan)
app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")
# handler is the route template (/api/gaps/{gap_id}), so ids in paths never become label values.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def get_conn() -> Iterator[psycopg.Connection]:
    with db.connect() as conn:
        yield conn


def current_auth_user(conn: psycopg.Connection = Depends(get_conn),
                      authorization: str | None = Header(default=None)) -> auth.AuthUser:
    return auth.current_user(conn, authorization)


def dashboard_user(user: auth.AuthUser = Depends(current_auth_user)) -> auth.AuthUser:
    return user


def admin_user(user: auth.AuthUser = Depends(current_auth_user)) -> auth.AuthUser:
    return auth.ensure_admin(user)


app.include_router(_ingestion_routes(), prefix="/api/ingestion", dependencies=[Depends(admin_user)])


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


@app.exception_handler(auth.AccessNotApproved)
def _access_not_approved(_: Request, exc: auth.AccessNotApproved) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(Exception)
def _server_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("%s %s failed: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": f"The server could not finish this request "
                                                            f"({type(exc).__name__}). See the server log."})


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return (UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


class SignInIn(BaseModel):
    email: str
    password: str


class SignUpIn(BaseModel):
    email: str
    password: str


class ForgotPasswordIn(BaseModel):
    email: str
    new_password: str


@app.post("/api/auth/signin")
def signin(body: SignInIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    user, token, expires_at = auth.signin(conn, body.email, body.password)
    conn.commit()
    return {"token": token, "token_type": "bearer", "expires_at": expires_at.isoformat(),
            "expires_in_hours": auth.SESSION_TTL_HOURS, "user": {"email": user.email, "role": user.role}}


@app.post("/api/auth/signup")
def signup(body: SignUpIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    auth.signup(conn, body.email, body.password)
    conn.commit()
    return {"status": "pending", "message": "Your account is awaiting administrator approval."}


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    auth.reset_password_for_email(conn, body.email, body.new_password)
    conn.commit()
    return {"status": "ok", "message": "Password has been reset. Sign in with the new password."}


@app.get("/api/auth/me")
def me(user: auth.AuthUser = Depends(dashboard_user)) -> dict:
    return {"email": user.email, "role": user.role}


@app.post("/api/auth/signout")
def signout(conn: psycopg.Connection = Depends(get_conn),
            authorization: str | None = Header(default=None),
            _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    auth.signout(conn, authorization)
    conn.commit()
    return {"status": "ok"}


class UserAccessIn(BaseModel):
    access_status: Literal["approved", "rejected"]


class UserRoleIn(BaseModel):
    role: Literal["admin", "user"]


@app.get("/api/admin/users")
def get_admin_users(conn: psycopg.Connection = Depends(get_conn),
                    _: auth.AuthUser = Depends(admin_user)) -> dict:
    return {"users": auth.list_users(conn)}


@app.put("/api/admin/users/{user_id}/access")
def update_user_access(user_id: int, body: UserAccessIn, conn: psycopg.Connection = Depends(get_conn),
                       actor: auth.AuthUser = Depends(admin_user)) -> dict:
    user = auth.set_access_status(conn, user_id, body.access_status, actor.user_id)
    audit(conn, "app_user", str(user_id), f"user.{body.access_status}", {}, actor=str(actor.user_id))  # phi-ok: enum only
    conn.commit()
    return user


@app.put("/api/admin/users/{user_id}/role")
def update_user_role(user_id: int, body: UserRoleIn, conn: psycopg.Connection = Depends(get_conn),
                     actor: auth.AuthUser = Depends(admin_user)) -> dict:
    user = auth.set_role(conn, user_id, body.role, actor.user_id)
    audit(conn, "app_user", str(user_id), "user.role_changed", {"role": body.role}, actor=str(actor.user_id))  # phi-ok: enum only
    conn.commit()
    return user


@app.post("/api/batches", status_code=202)
def upload_batch(file: UploadFile | None = File(None), files: list[UploadFile] | None = File(None),
                 _: auth.AuthUser = Depends(admin_user)) -> dict:
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


@app.get("/api/batches/latest")
def get_latest_batch(conn: psycopg.Connection = Depends(get_conn),
                     _: auth.AuthUser = Depends(dashboard_user)) -> dict | None:
    return pipeline.batch_status(conn, "latest")


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str, conn: psycopg.Connection = Depends(get_conn),
              _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    found = pipeline.batch_status(conn, batch_id)
    if found is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return found


@app.get("/api/measures")
def get_measures(as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn),
                 _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    return work.measures(conn, as_of or date.today())


@app.get("/api/gaps")
def get_gaps(role: str | None = None, tier: Literal["high", "medium", "low"] | None = None,
             measure: str | None = None, limit: int = 400, conn: psycopg.Connection = Depends(get_conn),
             _: auth.AuthUser = Depends(dashboard_user)) -> list:
    return work.work_list(conn, role, tier, measure, limit)


@app.get("/api/gaps/{gap_id}")
def get_gap(gap_id: str, as_of: date | None = None, conn: psycopg.Connection = Depends(get_conn),
            _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    found = work.gap_detail(conn, gap_id, as_of or date.today())
    if found is None:
        raise HTTPException(404, f"gap {gap_id} not found")
    return found


@app.post("/api/gaps/{gap_id}/evidence")
def find_evidence(gap_id: str, conn: psycopg.Connection = Depends(get_conn),
                  _: auth.AuthUser = Depends(admin_user)) -> dict:
    return evidence.find_evidence(conn, gap_id)


class DecisionIn(BaseModel):
    decision: Literal["confirm", "reject"]
    reason: str | None = None
    reviewer: str | None = None


@app.post("/api/evidence/{proposal_id}/decision")
def decide(proposal_id: str, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn),
           _: auth.AuthUser = Depends(admin_user)) -> dict:
    return evidence.decide(conn, proposal_id, body.decision, body.reason, body.reviewer)


class ExportIn(BaseModel):
    reviewer: str | None = None


@app.post("/api/supplemental/export")
def export_supplemental(body: ExportIn | None = None, conn: psycopg.Connection = Depends(get_conn),
                        _: auth.AuthUser = Depends(admin_user)) -> dict:
    return export.export_supplemental(conn, evidence.clean_reviewer(body.reviewer if body else None))


class ResetIn(BaseModel):
    confirm: str


@app.post("/api/reset")
def start_over(body: ResetIn, conn: psycopg.Connection = Depends(get_conn),
               _: auth.AuthUser = Depends(admin_user)) -> dict:
    """Development only: empty the data tables and keep the reference data (see reset.py for the guards)."""
    return reset.reset(conn, body.confirm)


@app.get("/api/health")
def health(conn: psycopg.Connection = Depends(get_conn),
           _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return {"status": "ok", "schema": schema, "migration": version, "rules_version": load_rules().version,
            "engine_built": (SETTINGS.engine_root / "build" / "cli.js").exists(),
            "evidence_dir_set": SETTINGS.evidence_dir is not None, "reset_allowed": reset.allowed(),
            "openrouter": openrouter_status.status(),
            "services": {"ingestion": {"prefix": "/api/ingestion", "schema": schema},
                         "quality": {**dq_checks.status(), "schema": schema}}}


@app.get("/api/quality")
def get_quality(conn: psycopg.Connection = Depends(get_conn),
                _: auth.AuthUser = Depends(admin_user)) -> dict:
    return quality.overview(conn)


@app.get("/api/quality/runs")
def get_quality_runs(batch_id: uuid.UUID, conn: psycopg.Connection = Depends(get_conn),
                     _: auth.AuthUser = Depends(admin_user)) -> dict:
    return quality.runs(conn, batch_id)


@app.get("/api/quality/runs/{run_id}")
def get_quality_run(run_id: int, severity: Literal["critical", "high", "medium", "low"] | None = None,
                    conn: psycopg.Connection = Depends(get_conn),
                    _: auth.AuthUser = Depends(admin_user)) -> dict:
    found = quality.run_detail(conn, run_id, severity)
    if found is None:
        raise HTTPException(404, f"data quality run {run_id} not found")
    return found


class QualityRunIn(BaseModel):
    batch_id: uuid.UUID
    measure_id: str | None = None


@app.post("/api/quality/runs")
def run_quality(body: QualityRunIn, _: auth.AuthUser = Depends(admin_user)) -> dict:
    """Run the data quality checks again on one loaded batch (every measure, or one); returns the new run."""
    return dq_checks.run_checks(body.batch_id, body.measure_id, SETTINGS.actor)


@app.get("/api/quality/coverage")
def get_coverage_report(payer: str | None = None, compliance: Literal["Compliant", "Violation"] | None = None,
                        start_date: date | None = None, end_date: date | None = None, limit: int = 1000,
                        conn: psycopg.Connection = Depends(get_conn),
                        _: auth.AuthUser = Depends(admin_user)) -> dict:
    compliant = None if compliance is None else compliance == "Compliant"
    return quality.coverage_report(conn, payer, compliant, start_date, end_date, min(max(limit, 1), 5000))


@app.get("/api/flow")
def get_flow(conn: psycopg.Connection = Depends(get_conn),
             _: auth.AuthUser = Depends(dashboard_user)) -> dict:
    return pipeline.flow(conn)


@app.get("/api/calculation")
def get_calculation(conn: psycopg.Connection = Depends(get_conn),
                    _: auth.AuthUser = Depends(admin_user)) -> dict:
    return calculation.overview(conn)


@app.get("/api/calculation/patients")
def get_calculation_candidates(offset: int = 0, limit: int = 10, conn: psycopg.Connection = Depends(get_conn),
                               _: auth.AuthUser = Depends(admin_user)) -> dict:
    """List a small page of persisted patients available for a calculation run."""
    return calculation.run_candidates(conn, offset, limit)


@app.get("/api/calculation/{measure_id}/patients")
def get_calculation_patients(measure_id: str, conn: psycopg.Connection = Depends(get_conn),
                             _: auth.AuthUser = Depends(admin_user)) -> dict:
    return calculation.patients(conn, measure_id)


class CalculationIn(BaseModel):
    measures: list[str] | None = None
    patient_ids: list[str]


@app.post("/api/calculation/run", status_code=202)
def run_calculation(body: CalculationIn | None = None, _: auth.AuthUser = Depends(admin_user)) -> dict:
    """Run selected patients from the latest calculation-ready batch."""
    if body is None:
        raise ValueError("Select at least one patient before starting the calculation.")
    return {"batch_id": pipeline.start_recalculation(body.measures, body.patient_ids), "status": "running"}


@app.get("/api/stars/catalog")
def get_star_catalog(_: auth.AuthUser = Depends(admin_user)) -> dict:
    rules = load_rules()
    with stars.session() as s:
        return {"measurement_year": SETTINGS.measurement_year, "star_year": rules.star_year,
                "measures": stars.catalog(s, SETTINGS.measurement_year, rules.star_year)}


class StarCalculationIn(BaseModel):
    measure_id: str
    rate: float | None = None
    denominator: int


@app.post("/api/stars/calculate")
def calculate_star(body: StarCalculationIn, _: auth.AuthUser = Depends(admin_user)) -> dict:
    with stars.session() as s:
        return stars.calculate(s, body.measure_id, SETTINGS.measurement_year, body.rate, body.denominator)


class WhatIfIn(BaseModel):
    measure_id: str
    gap_ids: list[str] = []


@app.post("/api/stars/what-if")
def star_what_if(body: WhatIfIn, _: auth.AuthUser = Depends(admin_user)) -> dict:
    with stars.session() as s:
        return stars.what_if(s, body.measure_id, body.gap_ids)


app.add_api_route("/api/documents/{document_id}/pages/{page_no}/image", intake_api.get_page_image, methods=["GET"],
                  dependencies=[Depends(admin_user)])


if __name__ == "__main__":
    SETTINGS.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = RotatingFileHandler(SETTINGS.log_dir / "hedis-integration.log", maxBytes=10 * 1024 * 1024,
                                   backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), log_file])
    print(f"HEDIS POC integration on http://localhost:{SETTINGS.port}")
    # log_config=None keeps uvicorn's loggers on the root handlers above, so its lines reach the file too.
    uvicorn.run(app, host="127.0.0.1", port=SETTINGS.port, log_level="info", log_config=None)

"""Pallavi's ingestion API and data quality worker, run unchanged as two child processes on their own schema.

Her tables reuse the lead's names (patient, encounter, observation and others) with different columns, her API paths
are the same as Jyothi's, and her settings read DATABASE_URL once at import. So her code cannot share this process or
the team schema: it runs on its own schema (INTEGRATION_PALLAVI_SCHEMA, for example pallavi20), where her own
create_all makes her tables, and this app forwards /team/pallavi/<path> to her API. Her screens show "separate data".

    API:    python -m uvicorn services.data_ingestion_pallavi.main:app --port INTEGRATION_PALLAVI_PORT (8793)
    worker: python -m services.data_quality_pallavi.main   (runs her checks after each ingestion she completes)

Her logs go to <work_dir>/pallavi/*.log, which is git-ignored. Off when INTEGRATION_PALLAVI_SCHEMA is not set.

Her FHIR mapping needs fhir.resources 6.5 with pydantic 1 (her pins in requirements.txt), while Jyothi's needs
fhir.resources 8. So her processes run on their own Python: INTEGRATION_PALLAVI_PYTHON, or the virtual environment
<work_dir>/pallavi/venv that run_demo.ps1 -Integration -Setup creates from her pins; otherwise the app's Python.
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from psycopg import sql

from services.document_intake import db

from .config import REPO_ROOT, SETTINGS

log = logging.getLogger(__name__)

SCHEMA = os.environ.get("INTEGRATION_PALLAVI_SCHEMA", "").strip()
PORT = int(os.environ.get("INTEGRATION_PALLAVI_PORT", "8793"))
BASE_URL = f"http://127.0.0.1:{PORT}"
PROXY_TIMEOUT_SECONDS = 900   # her "Run checks now" runs the whole check set inside the request
API_START_SECONDS = 120
_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_SEARCH_PATH = re.compile(r"search_path%3D[A-Za-z0-9_]+")
_HOP_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


@dataclass
class _Processes:
    started: bool = False
    api: subprocess.Popen | None = None
    worker: subprocess.Popen | None = None
    logs: list = field(default_factory=list)


_RUNNING = _Processes()
_LOCK = threading.Lock()


def enabled() -> bool:
    return bool(SCHEMA)


def _python() -> str:
    explicit = os.environ.get("INTEGRATION_PALLAVI_PYTHON", "").strip()
    if explicit:
        return explicit
    venv = SETTINGS.work_dir / "pallavi" / "venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    found = venv.with_suffix(".exe") if os.name == "nt" else venv
    return str(found) if found.exists() else sys.executable


def _database_url() -> str:
    """The app's DATABASE_URL with her schema on the search path instead of the team schema."""
    url = os.environ["DATABASE_URL"]
    if _SEARCH_PATH.search(url):
        return _SEARCH_PATH.sub(f"search_path%3D{SCHEMA}", url)
    return url + ("&" if "?" in url else "?") + f"options=-csearch_path%3D{SCHEMA}"


def start() -> None:
    """Create her schema if needed and start her API and worker. Does nothing when switched off."""
    if not enabled():
        return
    if not _SCHEMA_NAME.fullmatch(SCHEMA) or SCHEMA == "public":
        raise ValueError("INTEGRATION_PALLAVI_SCHEMA must be a plain lower-case name other than public")
    with _LOCK:
        if _RUNNING.started:
            return
        with db.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA)))
        folder = SETTINGS.work_dir / "pallavi"
        folder.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "DATABASE_URL": _database_url(),
               "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")]))}
        _RUNNING.api = _launch("api", [_python(), "-m", "uvicorn", "services.data_ingestion_pallavi.main:app",
                                       "--host", "127.0.0.1", "--port", str(PORT)], folder, env)
        _RUNNING.started = True
    # Her API creates her tables on start (create_all); the worker reads them, so it starts once the API answers.
    threading.Thread(target=_start_worker_when_ready, args=(folder, env), name="pallavi-worker-start",
                     daemon=True).start()
    log.info("Pallavi's API starting on port %d, schema %s, %s Python", PORT, SCHEMA,
             "her own" if _python() != sys.executable else "the app's")


def _launch(name: str, command: list[str], folder, env: dict) -> subprocess.Popen:
    out = (folder / f"{name}.log").open("ab")
    _RUNNING.logs.append(out)
    return subprocess.Popen(command, cwd=folder, env=env, stdout=out, stderr=subprocess.STDOUT)


def _start_worker_when_ready(folder, env: dict) -> None:
    deadline = time.monotonic() + API_START_SECONDS
    while time.monotonic() < deadline:
        if _RUNNING.api is None or _RUNNING.api.poll() is not None:
            log.error("Pallavi's API stopped before it was ready; her data quality worker was not started")
            return
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        log.error("Pallavi's API did not answer within %d s; her data quality worker was not started",
                  API_START_SECONDS)
        return
    with _LOCK:
        if _RUNNING.started and _RUNNING.worker is None:
            _RUNNING.worker = _launch("worker", [_python(), "-m", "services.data_quality_pallavi.main",
                                                 "--poll-seconds", "5"], folder, env)
            log.info("Pallavi's data quality worker started on schema %s", SCHEMA)


def stop() -> None:
    with _LOCK:
        for process in (_RUNNING.api, _RUNNING.worker):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
        for out in _RUNNING.logs:
            out.close()
        _RUNNING.logs.clear()
        _RUNNING.api = _RUNNING.worker = None
        _RUNNING.started = False


def status() -> dict:
    def state(process: subprocess.Popen | None) -> str:
        if process is None:
            return "not started"
        code = process.poll()
        return "running" if code is None else f"stopped (exit {code})"

    return {"enabled": enabled(), "schema": SCHEMA or None, "port": PORT if enabled() else None,
            "api": state(_RUNNING.api), "worker": state(_RUNNING.worker), "separate_data": True}


router = APIRouter()


@router.api_route("/team/pallavi/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def forward(path: str, request: Request) -> Response:
    """Forward a request to her API as it is, and return her answer as it is."""
    if not enabled():
        raise HTTPException(503, "Pallavi's data quality is switched off in this app (no INTEGRATION_PALLAVI_SCHEMA).")
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept")}
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_SECONDS) as client:
            answer = await client.request(request.method, f"{BASE_URL}/{path}", params=request.query_params,
                                          content=await request.body(), headers=headers)
    except httpx.ConnectError as exc:
        raise HTTPException(503, f"Pallavi's API is not answering on port {PORT} ({status()['api']}).") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"Pallavi's API did not answer within {PROXY_TIMEOUT_SECONDS} seconds.") from exc
    return Response(content=answer.content, status_code=answer.status_code,
                    headers={k: v for k, v in answer.headers.items() if k.lower() not in _HOP_HEADERS})

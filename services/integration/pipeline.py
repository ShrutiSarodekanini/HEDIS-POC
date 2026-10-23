"""Pipeline runner: one uploaded Synthea zip from CSV files to a prioritized work list, in a background thread.

    ingestion (upload-zip, run_conversion, run_persist_stage) -> team_schema_loader -> measures -> score_all
    -> stored_evidence (when INTEGRATION_EVIDENCE_DIR is set)

batch_run shows where a batch is; after every step its status, step and counts are updated and a domain_event is
written (batch.received, batch.converted, batch.persisted, batch.loaded, measures.received, gaps.identified,
gaps.prioritized, documents.attached, or batch.failed with the step and reason). Errors keep only their first line,
so row values in database error details never reach batch_run, events or logs.
"""

import asyncio
import logging
import shutil
import threading
import uuid
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from services.data_ingestion import team_schema_loader
from services.data_ingestion.app.api import upload_dataset, upload_dataset_zip
from services.data_ingestion.app.db import session_scope
from services.data_ingestion.app.models import ConversionJob
from services.data_ingestion.app.pipeline import job_manager, run_conversion, run_persist_stage
from services.document_intake import db
from services.document_intake.db import audit
from services.gap_priority.config import load_rules
from services.gap_priority.runner import score_all

from . import measures, stored_evidence
from .config import SETTINGS

log = logging.getLogger(__name__)

_ONE_AT_A_TIME = threading.Lock()   # batches share the engine work folder and the ingestion tables


class StepFailed(RuntimeError):
    """A step that could not finish; the message is safe to store and show."""


def start_batch(upload: Path) -> str:
    """Record the batch, then run it in a background thread. upload is one .zip file or a folder of .csv files.
    Returns the batch_id to poll."""
    batch_id = uuid.uuid4()
    files = sorted(upload.glob("*.csv")) if upload.is_dir() else [upload]
    received = {"upload": "csv" if upload.is_dir() else "zip", "files": len(files),
                "bytes": sum(f.stat().st_size for f in files)}
    with db.connect() as conn:
        conn.execute("""INSERT INTO source_batch (batch_id, source_system, status, resource_count, started_at)
                        VALUES (%s, %s, 'running', 0, now())""", (batch_id, team_schema_loader.SOURCE_SYSTEM))
        conn.execute("INSERT INTO batch_run (batch_id, status, step) VALUES (%s, 'running', 'received')",
                     (str(batch_id),))
        _event(conn, batch_id, "batch.received", received)
        audit(conn, "batch", str(batch_id), "batch.received", received, actor=SETTINGS.actor)
    threading.Thread(target=run_batch, args=(upload, batch_id), name=f"batch-{batch_id}", daemon=True).start()
    return str(batch_id)


def run_batch(upload: Path, batch_id: uuid.UUID) -> None:
    """Every step for one batch. A failure is recorded on the batch; nothing is swallowed."""
    step = "waiting"
    with _ONE_AT_A_TIME:
        try:
            step = "converting"
            _progress(batch_id, step)
            job_id, conversion = _convert(upload)
            _progress(batch_id, "persisting", "batch.converted", {"conversion": conversion}, ingest_job_id=job_id)

            step = "persisting"
            persisted = _persist(job_id)
            _progress(batch_id, "loading", "batch.persisted", {"persisted": persisted})

            step = "loading"
            with db.connect() as conn:
                loaded = team_schema_loader.load_job(conn, job_id, batch_id)
                _check_loaded(conn, job_id, persisted, loaded)
                load_counts = {"patients": len(loaded.patient_ids), "inserted": loaded.inserted,
                               "resource_types": loaded.resource_types,
                               "attributed_patients": sum(1 for v in loaded.attributed_org.values() if v)}
                _progress(batch_id, "measuring", "batch.loaded", {"load": load_counts}, conn=conn)

            step = "measuring"
            with db.connect() as conn:
                run = measures.run_measures(conn, list(loaded.patient_ids), loaded.attributed_org, batch_id.hex[:8])
                engine = run.counts()
                _event(conn, batch_id, "measures.received",
                       {"engine_run_id": run.run_id, "patients": run.patients, "measures": engine["measures"]})
                gaps = measures.gap_counts(conn, list(loaded.patient_ids))
                _progress(batch_id, "prioritizing", "gaps.identified",
                          {"measures": engine, "open_gaps": gaps}, conn=conn)

            step = "prioritizing"
            with db.connect() as conn:
                summary = score_all(conn, date.today(), load_rules())
                scored = {"gaps_scored": summary.gaps_scored, "tiers": summary.tiers, "channels": summary.channels,
                          "rules_version": summary.rules_version, "as_of": summary.as_of.isoformat()}
                _progress(batch_id, "attaching", "gaps.prioritized", {"prioritization": scored}, conn=conn)

            step = "attaching"
            attached = _attach(batch_id, list(loaded.patient_ids))
            with db.connect() as conn:
                _event(conn, batch_id, "documents.attached", attached)
                audit(conn, "batch", str(batch_id), "documents.attached",
                      {k: (len(v) if isinstance(v, list) else v) for k, v in attached.items()}, actor=SETTINGS.actor)
                conn.execute("UPDATE source_batch SET status = 'completed' WHERE batch_id = %s", (batch_id,))
                _progress(batch_id, "done", None, {"documents": attached}, status="prioritized", conn=conn)
                audit(conn, "batch", str(batch_id), "batch.prioritized", {"gaps_scored": summary.gaps_scored},
                      actor=SETTINGS.actor)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the batch with its step and reason
            _fail(batch_id, step, exc)
        finally:
            if upload.is_dir():
                shutil.rmtree(upload, ignore_errors=True)
            else:
                upload.unlink(missing_ok=True)


def _convert(upload: Path) -> tuple[str, dict]:
    """The ingestion service's own upload (upload_dataset_zip for a zip, upload_dataset for CSV files) and
    conversion, called as they are."""
    try:
        if upload.is_dir():
            with ExitStack() as stack:
                files = [UploadFile(file=stack.enter_context(path.open("rb")), filename=path.name)
                         for path in sorted(upload.glob("*.csv"))]
                uploaded = asyncio.run(upload_dataset(files))
        else:
            with upload.open("rb") as handle:
                uploaded = asyncio.run(upload_dataset_zip(UploadFile(file=handle, filename=upload.name)))
    except HTTPException as exc:
        raise StepFailed(f"the upload was refused: {exc.detail}") from exc
    if uploaded["missing_required_files"]:
        raise StepFailed(f"the upload is missing {', '.join(uploaded['missing_required_files'])}")
    job_id = job_manager.create_job(uploaded["input_dir"])
    try:
        run_conversion(job_id, uploaded["input_dir"])
    finally:
        shutil.rmtree(uploaded["input_dir"], ignore_errors=True)
    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        status, generated, errors = job.status, job.resources_generated, job.error_count
    if status not in ("completed", "partially_completed"):
        raise StepFailed(f"ingestion job {job_id} ended as {status}")
    return job_id, {"job_id": job_id, "job_status": status, "row_counts": uploaded["row_counts"],
                    "resources_generated": generated, "errors": errors}


def _attach(batch_id: uuid.UUID, patient_ids: list[str]) -> dict:
    """Stored evidence for this batch's patients, or a skip note when no evidence folder is configured."""
    if SETTINGS.evidence_dir is None:
        return {"skipped": "INTEGRATION_EVIDENCE_DIR is not set"}

    def progress(done: int, total: int) -> None:
        with db.connect() as conn:
            conn.execute("""UPDATE batch_run SET counts = counts || %s, updated_at = now() WHERE batch_id = %s""",
                         (Jsonb({"attaching": f"{done} of {total}"}), str(batch_id)))

    return stored_evidence.attach(SETTINGS.evidence_dir, patient_ids, progress)


def _persist(job_id: str) -> int:
    """The ingestion service's persist stage, as its Persist Resources button runs it. Returns its persisted total."""
    try:
        persisted = run_persist_stage(job_id)
    except Exception as exc:
        raise StepFailed(f"persist stage failed with {type(exc).__name__} ({_job_counts(job_id)})") from exc
    if not persisted:
        raise StepFailed(f"ingestion job {job_id} persisted no resources ({_job_counts(job_id)})")
    return persisted


def _job_counts(job_id: str) -> str:
    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        return (f"generated {job.resources_generated}, validated {job.resources_validated}, "
                f"persisted {job.resources_persisted}")


def _check_loaded(conn: psycopg.Connection, job_id: str, persisted: int,
                  loaded: team_schema_loader.LoadResult) -> None:
    """The job must show what its persist stage reported, and every persisted patient must be loaded."""
    recorded = conn.execute("SELECT resources_persisted FROM conversion_jobs WHERE id = %s", (job_id,)).fetchone()[0]
    patients = loaded.resource_types.get("Patient", 0)
    if recorded != persisted or len(loaded.patient_ids) != patients:
        raise StepFailed(f"load does not match the persist stage: job shows {recorded} persisted, stage returned "
                         f"{persisted}; {len(loaded.patient_ids)} patients loaded of {patients} persisted")


def _progress(batch_id: uuid.UUID, next_step: str, event: str | None = None, counts: dict | None = None,
              status: str = "running", ingest_job_id: str | None = None,
              conn: psycopg.Connection | None = None) -> None:
    """Update batch_run and write the step's domain event, in the given transaction or a new one."""
    if conn is None:
        with db.connect() as own:
            _progress(batch_id, next_step, event, counts, status, ingest_job_id, own)
        return
    conn.execute(
        """UPDATE batch_run SET status = %s, step = %s, counts = counts || %s,
               ingest_job_id = coalesce(%s, ingest_job_id), updated_at = now()
            WHERE batch_id = %s""",
        (status, next_step, Jsonb(counts or {}), ingest_job_id, str(batch_id)))
    if event:
        _event(conn, batch_id, event, counts or {})
    log.info("batch %s: %s", batch_id, event or next_step)


def _fail(batch_id: uuid.UUID, step: str, exc: Exception) -> None:
    reason = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:300]}"
    log.error("batch %s failed while %s: %s", batch_id, step, type(exc).__name__)
    with db.connect() as conn:
        conn.execute("""UPDATE batch_run SET status = 'failed', step = %s, error = %s, updated_at = now()
                         WHERE batch_id = %s""", (step, reason, str(batch_id)))
        conn.execute("UPDATE source_batch SET status = 'failed' WHERE batch_id = %s", (batch_id,))
        _event(conn, batch_id, "batch.failed", {"step": step, "reason": reason})
        audit(conn, "batch", str(batch_id), "batch.failed", {"step": step}, actor=SETTINGS.actor)


def _event(conn: psycopg.Connection, batch_id: uuid.UUID, event_type: str, payload: dict) -> None:
    conn.execute("INSERT INTO domain_event (event_type, batch_id, payload, published) VALUES (%s, %s, %s, false)",
                 (event_type, batch_id, Jsonb(payload)))


def batch_status(conn: psycopg.Connection, batch_id: str) -> dict | None:
    """One batch by id, or the most recently started one when batch_id is "latest"."""
    row = conn.execute(
        """SELECT batch_id, ingest_job_id, status, step, counts, error, started_at, updated_at
             FROM batch_run WHERE batch_id = %(id)s OR %(id)s = 'latest'
            ORDER BY started_at DESC LIMIT 1""", {"id": batch_id}).fetchone()
    if row is None:
        return None
    keys = ("batch_id", "ingest_job_id", "status", "step", "counts", "error", "started_at", "updated_at")
    return dict(zip(keys, row))

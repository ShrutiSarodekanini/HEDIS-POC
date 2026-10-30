"""Pipeline runner: one uploaded Synthea zip from CSV files to a prioritized work list, in a background thread.

    ingestion (upload-zip, run_conversion, run_persist_stage) -> team_schema_loader -> data quality checks
    -> measures -> gap scoring.

batch_run shows where a batch is; after every step its status, step and counts are updated and a domain_event is
written (batch.received, batch.converted, batch.persisted, batch.loaded, quality.checked or quality.failed,
measures.received, gaps.identified, gaps.prioritized, or batch.failed with the step and reason).
Errors keep only their first line, so row values in database error details never reach batch_run, events or logs.
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
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from starlette.datastructures import UploadFile

from services.data_ingestion import team_schema_loader
from services.data_ingestion.app.api import upload_dataset, upload_dataset_zip
from services.data_ingestion.app.db import session_scope
from services.data_ingestion.app.models import ConversionJob
from services.data_ingestion.app.pipeline import (
    job_manager,
    run_conversion,
    run_persist_stage,
)
from services.document_intake import db
from services.document_intake.db import audit
from services.gap_priority.config import load_rules
from services.gap_priority.runner import score_all

from . import calculation, dq_checks, measures, metrics
from .config import SETTINGS

log = logging.getLogger(__name__)

_ONE_AT_A_TIME = threading.Lock()   # batches share the engine work folder and the ingestion tables


class StepFailed(RuntimeError):
    """A step that could not finish; the message is safe to store and show."""


def recover_interrupted_batches() -> None:
    """Record an interrupted calculation while returning its already persisted batch to ready."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT batch_id, step
                 FROM batch_run
                WHERE status = 'running'
                   OR (status = 'failed' AND error = 'Calculation interrupted by an integration-service restart.')
                FOR UPDATE"""
        ).fetchall()
        for batch_id, step in rows:
            conn.execute(
                """UPDATE batch_run
                      SET status = 'ready', step = 'ready', error = NULL,
                          counts = counts || %s, updated_at = now()
                    WHERE batch_id = %s""",
                (Jsonb({"interrupted_calculation": {"step": step, "reason_code": "service_restarted"}}),
                 str(batch_id)),
            )
            conn.execute("UPDATE source_batch SET status = 'completed' WHERE batch_id = %s", (batch_id,))
            _event(conn, batch_id, "calculation.interrupted", {"step": step, "reason_code": "service_restarted"})
            audit(conn, "batch", str(batch_id), "calculation.interrupted",
                  {"step": step, "reason_code": "service_restarted"}, actor=SETTINGS.actor)
    if rows:
        log.warning("recorded %s interrupted calculations and returned their batches to ready", len(rows))


def record_missing_upload_sizes() -> None:
    """Write immutable byte-size metadata for existing batches while their input directories remain available."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT b.batch_id, c.input_dir
                 FROM batch_run b
                 JOIN conversion_jobs c ON c.id = b.ingest_job_id
                WHERE NOT EXISTS (
                    SELECT 1
                      FROM audit_events a
                     WHERE a.entity_type = 'batch'
                       AND a.entity_id = b.batch_id::text
                       AND a.payload ? 'bytes'
                )"""
        ).fetchall()
        recorded = 0
        for batch_id, input_dir in rows:
            path = Path(input_dir)
            if not path.is_dir():
                continue
            byte_count = sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
            audit(conn, "batch", str(batch_id), "batch.input_size_recorded",
                  {"bytes": byte_count}, actor=SETTINGS.actor)
            recorded += 1
    if recorded:
        log.info("recorded upload sizes for %s batches", recorded)


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
            metrics.BATCH_RUNNING.set(1)
            step = "converting"
            _progress(batch_id, step)
            job_id, conversion = _convert(upload)
            _progress(batch_id, "persisting", "batch.converted", {"conversion": conversion}, ingest_job_id=job_id)

            step = "persisting"
            persisted = _persist(job_id)
            _progress(batch_id, "loading", "batch.persisted", {"persisted": persisted})
            step = "loading"
            _after_persist(batch_id, job_id, persisted)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the batch with its step and reason
            _fail(batch_id, _current_step(batch_id) if step == "loading" else step, exc)
        finally:
            metrics.BATCH_RUNNING.set(0)
            if upload.is_dir():
                shutil.rmtree(upload, ignore_errors=True)
            else:
                upload.unlink(missing_ok=True)


def start_from_job(job_id: str) -> str:
    """A batch for an ingestion job that was converted and persisted on the Data Ingestion screen (the ingestion
    endpoints); our steps continue from loading. Returns the batch_id."""
    batch_id = uuid.uuid4()
    with db.connect() as conn:
        persisted = conn.execute("SELECT resources_persisted FROM conversion_jobs WHERE id = %s",
                                 (job_id,)).fetchone()[0]
        received = {"source": "data ingestion screen", "job_id": job_id, "persisted": persisted}
        input_dir = conn.execute("SELECT input_dir FROM conversion_jobs WHERE id = %s", (job_id,)).fetchone()[0]
        path = Path(input_dir)
        if path.is_dir():
            received["bytes"] = sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
        conn.execute("""INSERT INTO source_batch (batch_id, source_system, status, resource_count, started_at)
                        VALUES (%s, %s, 'running', 0, now())""", (batch_id, team_schema_loader.SOURCE_SYSTEM))
        conn.execute("""INSERT INTO batch_run (batch_id, ingest_job_id, status, step, counts)
                        VALUES (%s, %s, 'running', 'waiting', %s)""",
                     (str(batch_id), job_id, Jsonb({"persisted": persisted})))
        _event(conn, batch_id, "batch.received", received)
        _event(conn, batch_id, "batch.persisted", {"persisted": persisted})
        audit(conn, "batch", str(batch_id), "batch.received", received, actor=SETTINGS.actor)
    threading.Thread(target=_run_from_job, args=(batch_id, job_id, persisted), name=f"batch-{batch_id}",
                     daemon=True).start()
    return str(batch_id)


def _run_from_job(batch_id: uuid.UUID, job_id: str, persisted: int) -> None:
    with _ONE_AT_A_TIME:
        try:
            metrics.BATCH_RUNNING.set(1)
            _progress(batch_id, "loading")
            _after_persist(batch_id, job_id, persisted)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the batch with its step and reason
            _fail(batch_id, _current_step(batch_id), exc)
        finally:
            metrics.BATCH_RUNNING.set(0)


def _current_step(batch_id: uuid.UUID) -> str:
    with db.connect() as conn:
        row = conn.execute("SELECT step FROM batch_run WHERE batch_id = %s", (str(batch_id),)).fetchone()
    return row[0] if row else "waiting"


def _after_persist(batch_id: uuid.UUID, job_id: str, persisted: int) -> None:
    """Load and check persisted data, then automatically run measures and score the resulting gaps."""
    with db.connect() as conn:
        loaded = team_schema_loader.load_job(conn, job_id, batch_id)
        _check_loaded(conn, job_id, persisted, loaded)
        load_counts = {"patients": len(loaded.patient_ids), "inserted": loaded.inserted,
                       "resource_types": loaded.resource_types,
                       "attributed_patients": sum(1 for v in loaded.attributed_org.values() if v)}
        _progress(batch_id, "checking", "batch.loaded", {"load": load_counts}, conn=conn)

    quality = _check_quality(batch_id)
    with db.connect() as conn:
        _progress(batch_id, "measuring", None, {"quality": quality}, conn=conn)
        run = measures.run_measures(conn, list(loaded.patient_ids), loaded.attributed_org, batch_id.hex[:8])
        engine = run.counts()
        _event(conn, batch_id, "measures.received",
               {"engine_run_id": run.run_id, "patients": run.patients, "measures": engine["measures"]})
        gaps = measures.gap_counts(conn, list(loaded.patient_ids))
        _progress(batch_id, "prioritizing", "gaps.identified", {"measures": engine, "open_gaps": gaps}, conn=conn)

    with db.connect() as conn:
        summary = score_all(conn, date.today(), load_rules())
        conn.execute("UPDATE source_batch SET status = 'completed' WHERE batch_id = %s", (batch_id,))
        _progress(batch_id, "done", "gaps.prioritized", {"prioritization": _scored(summary)},
                  status="prioritized", conn=conn)
        audit(conn, "batch", str(batch_id), "batch.prioritized",
              {"gaps_scored": summary.gaps_scored}, actor=SETTINGS.actor)
    metrics.PATIENTS_PROCESSED.inc(len(loaded.patient_ids))


def _scored(summary) -> dict:
    return {"gaps_scored": summary.gaps_scored, "tiers": summary.tiers, "channels": summary.channels,
            "rules_version": summary.rules_version, "as_of": summary.as_of.isoformat()}


def _check_quality(batch_id: uuid.UUID) -> dict:
    """The data quality checks on the loaded batch. The measures do not read their results, so a failed run does not
    stop the batch: run_checks stores the failure on quality_run with a quality.failed event, and the batch shows it."""
    try:
        run = dq_checks.run_checks(batch_id, None, SETTINGS.actor)
    except Exception as exc:  # noqa: BLE001 - recorded on quality_run and on the batch; the next steps do not need it
        log.error("batch %s: data quality checks failed: %s", batch_id, type(exc).__name__)
        return {"status": "failed", "reason": f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:300]}"}
    return {key: run[key] for key in ("run_id", "status", "checks", "failed", "anomalies")}


_WAITING_JOBS = """
SELECT j.id FROM conversion_jobs j
 WHERE j.resources_persisted > 0 AND j.status IN ('completed', 'partially_completed')
   AND NOT EXISTS (SELECT 1 FROM batch_run b WHERE b.ingest_job_id = j.id)
 ORDER BY j.started_at
"""


def watch_ingestion(stop: threading.Event, every_seconds: float = 3.0) -> None:
    """Start our steps for each ingestion job persisted on the Data Ingestion screen. Jobs started by POST
    /api/batches already have their batch_run before they persist, so they are never picked twice."""
    while not stop.wait(every_seconds):
        try:
            with db.connect() as conn:
                waiting = [r[0] for r in conn.execute(_WAITING_JOBS).fetchall()]
            for job_id in waiting:
                log.info("ingestion job %s persisted; starting batch %s", job_id, start_from_job(job_id))
        except Exception as exc:  # noqa: BLE001 - the watcher keeps running; the reason is logged every round
            log.error("ingestion watcher round failed: %s", type(exc).__name__)


def start_recalculation(measure_ids: list[str] | None, selected_patient_ids: list[str]) -> str:
    """Run selected persisted patients, then prioritize their resulting gaps."""
    if not selected_patient_ids:
        raise ValueError("Select at least one patient before starting the calculation.")
    with db.connect() as conn:
        row = conn.execute("""SELECT batch_id, ingest_job_id FROM batch_run
                               WHERE status IN ('ready', 'prioritized') AND ingest_job_id IS NOT NULL
                               ORDER BY started_at DESC LIMIT 1""").fetchone()
        if row is not None:
            available_ids = calculation.run_candidate_ids(conn)
    if row is None:
        raise LookupError("No calculation-ready batch yet. Ingest and persist a batch first.")
    batch_id = uuid.UUID(row[0])
    available = set(available_ids)
    selected = list(dict.fromkeys(selected_patient_ids))
    if unknown := set(selected) - available:
        raise ValueError(f"{len(unknown)} selected patients are not available for calculation.")
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        raise ValueError("A batch is running. Wait for it to finish, then run the calculation.")
    try:
        _progress(batch_id, "measuring", "calculation.requested",
                  {"recalculation": {"requested": measure_ids or "all", "patient_count": len(selected)}})
    except Exception:
        _ONE_AT_A_TIME.release()
        raise
    threading.Thread(target=_recalculate, args=(batch_id, measure_ids, selected), name=f"calc-{batch_id}",
                     daemon=True).start()
    return str(batch_id)


def _recalculate(batch_id: uuid.UUID, measure_ids: list[str] | None, selected_patient_ids: list[str]) -> None:
    step = "measuring"
    try:
        metrics.BATCH_RUNNING.set(1)
        with db.connect() as conn:
            selected = list(dict.fromkeys(selected_patient_ids))
            attributed = dict.fromkeys(selected)
            attributed.update(dict(conn.execute(
                """SELECT patient_id, mode() WITHIN GROUP (ORDER BY organization_id)
                     FROM encounter
                    WHERE organization_id IS NOT NULL AND patient_id = ANY(%s)
                    GROUP BY patient_id""",
                (selected,),
            ).fetchall()))
            run = measures.run_measures(conn, selected, attributed, batch_id.hex[:8],
                                        measure_ids=measure_ids)
            engine = run.counts()
            _event(conn, batch_id, "measures.received",
                   {"engine_run_id": run.run_id, "patients": run.patients, "measures": engine["measures"]})
            gaps = measures.gap_counts(conn, selected)
            _progress(batch_id, "prioritizing", "gaps.identified",
                      {"recalculation": {"requested": measure_ids or "all", "measures": engine,
                                         "open_gaps": gaps}}, conn=conn)
        step = "prioritizing"
        with db.connect() as conn:
            summary = score_all(conn, date.today(), load_rules())
            _progress(batch_id, "done", "gaps.prioritized", {"prioritization": _scored(summary)},
                      status="prioritized", conn=conn)
            audit(conn, "batch", str(batch_id), "calculation.completed",
                  {"engine_run_id": run.run_id, "gaps_scored": summary.gaps_scored}, actor=SETTINGS.actor)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the batch with its step and reason
        _fail(batch_id, step, exc)
    finally:
        metrics.BATCH_RUNNING.set(0)
        _ONE_AT_A_TIME.release()


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
    if next_step == "done":
        metrics.batch_ended(batch_id)
    else:
        metrics.step_started(batch_id, next_step)
    if status == "prioritized":
        metrics.BATCHES.labels("prioritized").inc()


def _fail(batch_id: uuid.UUID, step: str, exc: Exception) -> None:
    reason = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:300]}"
    log.error("batch %s failed while %s: %s", batch_id, step, type(exc).__name__)
    metrics.batch_ended(batch_id)
    metrics.BATCHES.labels("failed").inc()
    metrics.BATCH_FAILURES.labels(step).inc()
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


_TABLE_COUNTS = """
SELECT (SELECT count(*) FROM conversion_jobs) AS conversion_jobs,
       (SELECT count(*) FROM fhir_resources) AS fhir_resources,
       (SELECT count(*) FROM fhir_resources WHERE persisted) AS fhir_resources_persisted,
       (SELECT count(*) FROM validation_issues) AS validation_issues,
       (SELECT count(*) FROM patient) AS patient,
       (SELECT count(*) FROM encounter) AS encounter,
       (SELECT count(*) FROM observation) AS observation,
       (SELECT count(*) FROM fhir_resource_raw) AS fhir_resource_raw,
       (SELECT count(*) FROM feed_completeness_report) AS feed_completeness_report,
       (SELECT count(*) FROM quality_run) AS quality_run,
       (SELECT count(*) FROM quality_check_result) AS quality_check_result,
       (SELECT count(*) FROM data_anomaly) AS data_anomaly,
       (SELECT count(*) FROM engine_runs) AS engine_runs,
       (SELECT count(*) FROM measure_result) AS measure_result,
       (SELECT count(*) FROM care_gap) AS care_gap,
       (SELECT count(*) FROM care_gap WHERE status = 'open') AS care_gap_open,
       (SELECT count(*) FROM care_gap_detail) AS care_gap_detail,
       (SELECT count(*) FROM gap_priority_score) AS gap_priority_score,
       (SELECT count(*) FROM document_reference) AS document_reference,
       (SELECT count(*) FROM evidence_proposal) AS evidence_proposal,
       (SELECT count(*) FROM review_decisions) AS review_decisions,
       (SELECT count(*) FROM supplemental_records) AS supplemental_records,
       (SELECT count(*) FROM star_scenario) AS star_scenario
"""


def flow(conn: psycopg.Connection) -> dict:
    """The latest batch across the screens: its ingestion job, our steps and the rows each step has stored."""
    batch = batch_status(conn, "latest")
    job = None
    if batch and batch["ingest_job_id"]:
        row = conn.execute(
            """SELECT id, status, started_at, completed_at, total_source_records, resources_generated,
                      resources_validated, resources_persisted, warning_count, error_count
                 FROM conversion_jobs WHERE id = %s""", (batch["ingest_job_id"],)).fetchone()
        keys = ("id", "status", "started_at", "completed_at", "total_source_records", "resources_generated",
                "resources_validated", "resources_persisted", "warning_count", "error_count")
        job = dict(zip(keys, row)) if row else None
    latest_job = conn.execute(
        """SELECT j.id, j.status, j.resources_generated, j.resources_persisted,
                  EXISTS (SELECT 1 FROM batch_run b WHERE b.ingest_job_id = j.id) AS has_batch
             FROM conversion_jobs j ORDER BY j.started_at DESC NULLS LAST LIMIT 1""").fetchone()
    cur = conn.execute(_TABLE_COUNTS)
    counts = dict(zip([c.name for c in cur.description], cur.fetchone()))
    return {"batch": batch, "ingest_job": job,
            "latest_ingest_job": dict(zip(("id", "status", "resources_generated", "resources_persisted",
                                           "has_batch"), latest_job)) if latest_job else None,
            "tables": counts}

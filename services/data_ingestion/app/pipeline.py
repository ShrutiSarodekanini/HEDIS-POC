from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .db import session_scope
from .mapping import bundles_by_patient, map_dataset
from .repository import (
    complete_job,
    create_job,
    mark_validated_resources_persisted,
    persist_row_issues,
    persist_validation_messages,
    start_job,
    upsert_resource,
)
from .synthea import parse_dataset
from .validator import validate_resource


STAGES = ["csv_input", "mapping", "fhir_generation", "validation", "postgresql_storage"]

logger = logging.getLogger("uvicorn.error")


def _emit(message: str) -> None:
    logger.info(message)
    print(message, flush=True)


@dataclass
class StageState:
    name: str
    status: str = "pending"
    details: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    elapsed_seconds: float | None = None


@dataclass
class RuntimeState:
    job_id: str
    status: str = "pending"
    stages: dict[str, StageState] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class JobManager:
    def __init__(self) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._lock = threading.Lock()

    def create_job(self, input_dir: str) -> str:
        with session_scope() as session:
            job = create_job(session, input_dir)
            job_id = job.id

        state = RuntimeState(
            job_id=job_id,
            status="pending",
            stages={stage: StageState(name=stage) for stage in STAGES},
        )
        with self._lock:
            self._states[job_id] = state
        return job_id

    def get_state(self, job_id: str) -> RuntimeState | None:
        with self._lock:
            return self._states.get(job_id)

    def set_stage(self, job_id: str, stage: str, status: str, details: str = "") -> None:
        with self._lock:
            state = self._states[job_id]
            current = state.stages[stage]
            current.status = status
            current.details = details

            now = time.time()
            if status == "running":
                current.started_at = current.started_at or now
                current.completed_at = None
                current.elapsed_seconds = None
            elif status in {"completed", "failed", "partially_completed"}:
                current.completed_at = now
                if current.started_at is not None:
                    current.elapsed_seconds = round(current.completed_at - current.started_at, 3)

        _emit(
            "[pipeline] "
            f"job={job_id} stage={stage} status={status} "
            f"elapsed_seconds={current.elapsed_seconds} details={details}"
        )

    def set_job_status(self, job_id: str, status: str) -> None:
        with self._lock:
            state = self._states[job_id]
            state.status = status
            if status in {"completed", "partially_completed", "failed"}:
                state.completed_at = time.time()

        _emit(f"[pipeline] job={job_id} status={status}")


job_manager = JobManager()


def run_conversion(job_id: str, input_dir: str) -> None:
    stage = job_manager.set_stage
    job_manager.set_job_status(job_id, "running")
    conversion_started_at = time.time()
    _emit(f"[pipeline] job={job_id} conversion_started input_dir={input_dir}")

    summary: dict[str, Any] = {
        "total_source_records": 0,
        "resources_generated": 0,
        "resources_validated": 0,
        "resources_persisted": 0,
        "warning_count": 0,
        "error_count": 0,
        "patients": 0,
        "bundles": {},
        "files": {},
    }

    try:
        with session_scope() as session:
            start_job(session, job_id)

        stage(job_id, "csv_input", "running", "Parsing CSV files")
        parsed = parse_dataset(input_dir)
        summary["total_source_records"] = parsed.total_rows
        summary["files"] = {
            "found": parsed.metadata.found_files,
            "missing_required": parsed.metadata.missing_required_files,
            "row_counts": parsed.metadata.row_counts,
        }
        summary["patients"] = len(parsed.records_by_file.get("patients.csv", []))
        stage(job_id, "csv_input", "completed", f"Discovered {len(parsed.metadata.found_files)} supported files")

        stage(job_id, "mapping", "running", "Applying CSV-to-FHIR mapping rules")
        mapped = map_dataset(parsed)
        stage(job_id, "mapping", "completed", f"Mapped {len(mapped.resources)} resources")

        stage(job_id, "fhir_generation", "running", "Generating FHIR resources and bundles")
        bundles = bundles_by_patient(mapped.resources)
        summary["bundles"] = bundles
        summary["resources_generated"] = len(mapped.resources)
        stage(job_id, "fhir_generation", "completed", f"Generated {len(bundles)} patient bundles")

        all_issues = [*parsed.issues, *mapped.issues]

        stage(job_id, "validation", "running", "Validating FHIR resources")
        validated_count = 0
        with session_scope() as session:
            persist_row_issues(session, job_id, all_issues)

            for resource in mapped.resources:
                result = validate_resource(resource)
                validation_status = result.status
                lifecycle_status = "validated" if result.status == "validated" else "quarantined"
                if any(msg.severity == "error" for msg in result.messages):
                    lifecycle_status = "rejected"

                row = upsert_resource(
                    session,
                    job_id=job_id,
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                    patient_id=resource.patient_id,
                    source_file=resource.source_file,
                    source_record_id=resource.source_record_id,
                    source_fingerprint=resource.source_fingerprint,
                    resource_json=resource.fhir,
                    validation_status=validation_status,
                    lifecycle_status=lifecycle_status,
                    metadata_json={"references": resource.references},
                )

                persist_validation_messages(
                    session,
                    job_id=job_id,
                    resource_pk=row.id,
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                    source_file=resource.source_file,
                    source_record_id=resource.source_record_id,
                    validation=result,
                )

                if result.status == "validated":
                    validated_count += 1

        summary["resources_validated"] = validated_count
        stage(job_id, "validation", "completed", f"Validated {validated_count} resources")

        warning_count = sum(1 for issue in all_issues if issue.severity == "warning")
        error_count = sum(1 for issue in all_issues if issue.severity == "error")
        summary["warning_count"] = warning_count
        summary["error_count"] = error_count

        stage(job_id, "postgresql_storage", "pending", "Run persistence stage explicitly")

        state = job_manager.get_state(job_id)
        if state is not None:
            summary["timelogs"] = {
                "conversion_started_at": state.started_at,
                "conversion_completed_at": time.time(),
                "conversion_elapsed_seconds": round(time.time() - conversion_started_at, 3),
                "stages": {
                    name: {
                        "status": s.status,
                        "started_at": s.started_at,
                        "completed_at": s.completed_at,
                        "elapsed_seconds": s.elapsed_seconds,
                        "details": s.details,
                    }
                    for name, s in state.stages.items()
                },
            }

        final_status = "completed"
        if error_count > 0 or validated_count < len(mapped.resources):
            final_status = "partially_completed"

        with session_scope() as session:
            complete_job(session, job_id, final_status, summary)

        job_manager.set_job_status(job_id, final_status)
        _emit(
            "[pipeline] "
            f"job={job_id} conversion_finished status={final_status} "
            f"total_source_records={summary.get('total_source_records')} "
            f"resources_generated={summary.get('resources_generated')} "
            f"resources_validated={summary.get('resources_validated')} "
            f"resources_persisted={summary.get('resources_persisted')} "
            f"warnings={summary.get('warning_count')} "
            f"errors={summary.get('error_count')} "
            f"elapsed_seconds={round(time.time() - conversion_started_at, 3)}"
        )
    except Exception as exc:  # pragma: no cover - defensive error path
        stage(job_id, "csv_input", "failed", str(exc))
        logger.exception("[pipeline] job=%s conversion_failed error=%s", job_id, exc)
        print(f"[pipeline] job={job_id} conversion_failed error={exc}", flush=True)
        with session_scope() as session:
            summary["error_count"] = summary.get("error_count", 0) + 1
            summary["timelogs"] = {
                "conversion_started_at": conversion_started_at,
                "conversion_completed_at": time.time(),
                "conversion_elapsed_seconds": round(time.time() - conversion_started_at, 3),
            }
            complete_job(session, job_id, "failed", summary)
        job_manager.set_job_status(job_id, "failed")


def run_persist_stage(job_id: str) -> int:
    stage = job_manager.set_stage
    stage(job_id, "postgresql_storage", "running", "Marking validated resources as persisted")
    _emit(f"[pipeline] job={job_id} persist_stage_started")
    with session_scope() as session:
        persisted = mark_validated_resources_persisted(session, job_id)

        from .models import ConversionJob

        job = session.get(ConversionJob, job_id)
        if job:
            summary = job.summary or {}
            summary["resources_persisted"] = persisted
            job.summary = summary
            job.resources_persisted = persisted
            if job.status == "completed" and persisted == 0:
                job.status = "partially_completed"

    stage(job_id, "postgresql_storage", "completed", f"Persisted {persisted} resources")
    _emit(f"[pipeline] job={job_id} persist_stage_finished persisted={persisted}")
    return persisted


def launch_job(job_id: str, input_dir: str) -> None:
    thread = threading.Thread(target=run_conversion, args=(job_id, input_dir), daemon=True)
    thread.start()

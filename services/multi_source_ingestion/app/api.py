from __future__ import annotations

import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from .config import settings
from .db import session_scope
from .adapters import registry
from .models import FhirResource
from .pipeline import STAGES, job_manager, launch_job, run_persist_stage
from .repository import (
    as_issue_dict,
    as_job_dict,
    as_resource_dict,
    enqueue_domain_event,
    list_issues_for_job,
    list_jobs,
    list_resources_for_job,
)
from .synthea import SUPPORTED_FILES, discover_dataset


router = APIRouter(prefix="/api", tags=["conversion"])


def _require_input_dir(input_dir: str) -> str:
    cleaned = (input_dir or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="input_dir is required. Upload a folder/zip first or enter a valid path.")
    return cleaned


def _job_status_payload(job_id: str) -> dict:
    state = job_manager.get_state(job_id)
    if state is None:
        with session_scope() as session:
            resources = list_resources_for_job(session, job_id)
        progress: dict[str, dict[str, int]] = {}
        for resource in resources:
            entry = progress.setdefault(
                resource.resource_type,
                {"total": 0, "processed": 0, "warnings": 0, "rejected": 0},
            )
            entry["total"] += 1
            entry["processed"] += 1
            if resource.validation_status in {"unavailable", "partial"}:
                entry["warnings"] += 1
            if resource.lifecycle_status == "rejected":
                entry["rejected"] += 1
        return {"job_id": job_id, "status": "unknown", "stages": [], "resource_progress": progress}

    return {
        "job_id": job_id,
        "status": state.status,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "resource_progress": state.resource_progress,
        "stages": [
            {
                "name": s.name,
                "status": s.status,
                "details": s.details,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "elapsed_seconds": s.elapsed_seconds,
            }
            for s in [state.stages[name] for name in STAGES]
        ],
    }


@router.post("/datasets/upload")
async def upload_dataset(request: Request) -> dict:
    started_at = time.time()
    form = await request.form(max_files=float("inf"))
    files = [item for _, item in form.multi_items() if isinstance(item, StarletteUploadFile)]
    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)

    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    supported = {name.lower() for name in SUPPORTED_FILES.keys()}
    saved_files: list[str] = []

    for uploaded in files:
        original_name = uploaded.filename or ""
        target_name = Path(original_name).name.lower()
        if target_name not in supported:
            continue

        target_path = dataset_dir / target_name
        with target_path.open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        saved_files.append(target_name)

    if not saved_files:
        raise HTTPException(
            status_code=400,
            detail="No supported Synthea CSV files were uploaded. Include files like patients.csv, encounters.csv, observations.csv, etc.",
        )

    metadata = discover_dataset(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files,
        "missing_required_files": metadata.missing_required_files,
        "row_counts": metadata.row_counts,
        "total_rows": sum(metadata.row_counts.values()),
        "timelog": {
            "step": "upload_dataset",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(completed_at - started_at, 3),
        },
    }


@router.post("/datasets/upload-ccda")
async def upload_ccda_dataset(request: Request) -> dict:
    started_at = time.time()
    form = await request.form(max_files=float("inf"))
    files = [item for _, item in form.multi_items() if isinstance(item, StarletteUploadFile)]
    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)
    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    supported_extensions = {".xml", ".ccda"}
    for uploaded in files:
        target_name = Path(uploaded.filename or "").name.lower()
        if Path(target_name).suffix not in supported_extensions:
            continue
        with (dataset_dir / target_name).open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        saved_files.append(target_name)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No C-CDA files were uploaded. Select .xml or .ccda documents.")

    adapter = registry.get_adapter("ccda_hie")
    metadata = adapter.discover(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files,
        "row_counts": metadata.row_counts,
        "timelog": {
            "step": "upload_ccda_dataset",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(completed_at - started_at, 3),
        },
    }


@router.post("/datasets/upload-zip")
async def upload_dataset_zip(file: UploadFile = File(...)) -> dict:
    started_at = time.time()
    filename = (file.filename or "").lower()
    if not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip file.")

    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)

    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    zip_path = dataset_dir / "dataset.zip"
    with zip_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    supported = {name.lower() for name in SUPPORTED_FILES.keys()}
    saved_files: list[str] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue

                member_name = Path(member.filename).name.lower()
                if member_name not in supported:
                    continue

                target = dataset_dir / member_name
                with archive.open(member, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                saved_files.append(member_name)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ZIP file: {exc}") from exc

    if not saved_files:
        raise HTTPException(
            status_code=400,
            detail="ZIP did not contain supported Synthea CSV files (patients.csv, encounters.csv, etc.).",
        )

    metadata = discover_dataset(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files,
        "missing_required_files": metadata.missing_required_files,
        "row_counts": metadata.row_counts,
        "total_rows": sum(metadata.row_counts.values()),
        "timelog": {
            "step": "upload_dataset_zip",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(completed_at - started_at, 3),
        },
    }


@router.get("/datasets")
def list_available_datasets(base_dir: str | None = None) -> dict:
    base = Path(base_dir or settings.datasets_base_dir)
    datasets = []

    if not base.exists():
        return {"base_dir": str(base), "datasets": []}

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        metadata = discover_dataset(str(child))
        if metadata.found_files:
            datasets.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "found_files": metadata.found_files,
                    "missing_required_files": metadata.missing_required_files,
                    "row_counts": metadata.row_counts,
                }
            )

    return {"base_dir": str(base), "datasets": datasets}


@router.get("/datasets/inspect")
def inspect_dataset(input_dir: str) -> dict:
    cleaned = _require_input_dir(input_dir)
    path = Path(cleaned)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="Input directory not found")

    metadata = discover_dataset(cleaned)
    return {
        "input_dir": metadata.input_dir,
        "found_files": metadata.found_files,
        "missing_required_files": metadata.missing_required_files,
        "row_counts": metadata.row_counts,
        "total_rows": sum(metadata.row_counts.values()),
    }


@router.post("/jobs/start")
def start_conversion_job(input_dir: str, adapter_name: str = "synthea_csv") -> dict:
    cleaned = _require_input_dir(input_dir)
    path = Path(cleaned)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="Input directory not found")

    if not registry.has_adapter(adapter_name):
        raise HTTPException(status_code=400, detail={"adapter": adapter_name, "available": registry.list_adapters()})
    adapter = registry.get_adapter(adapter_name)
    metadata = adapter.discover(cleaned)
    if metadata.missing_required_files:
        raise HTTPException(status_code=400, detail={"missing_required_files": metadata.missing_required_files})
    job_id = job_manager.create_job(cleaned, adapter_name)
    launch_job(job_id, cleaned, adapter_name)
    return {"job_id": job_id, "status": "started", "adapter": adapter_name}


@router.post("/jobs/{job_id}/persist")
def persist_job_resources(job_id: str) -> dict:
    # Check if job exists in database (even if state was cleared from memory)
    with session_scope() as session:
        from .models import ConversionJob
        job = session.get(ConversionJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
    
    persisted = run_persist_stage(job_id)
    return {"job_id": job_id, "resources_persisted": persisted}


@router.get("/jobs")
def get_jobs() -> dict:
    with session_scope() as session:
        jobs = [as_job_dict(item) for item in list_jobs(session)]
    return {"jobs": jobs}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with session_scope() as session:
        jobs = [item for item in list_jobs(session) if item.id == job_id]
        if not jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = as_job_dict(jobs[0])

    return {**job, "runtime": _job_status_payload(job_id)}


@router.get("/jobs/{job_id}/status")
def get_job_status(job_id: str) -> dict:
    if job_manager.get_state(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_status_payload(job_id)


@router.get("/jobs/{job_id}/resources")
def get_job_resources(job_id: str) -> dict:
    with session_scope() as session:
        resources = [as_resource_dict(item) for item in list_resources_for_job(session, job_id)]
    return {"job_id": job_id, "resources": resources}


@router.get("/jobs/{job_id}/patients")
def get_job_patients(job_id: str) -> dict:
    with session_scope() as session:
        resources = list_resources_for_job(session, job_id)
        patients = sorted({r.patient_id for r in resources if r.patient_id})
    return {"job_id": job_id, "patients": patients}


@router.get("/jobs/{job_id}/patients/{patient_id}/resources")
def get_patient_resources(job_id: str, patient_id: str) -> dict:
    with session_scope() as session:
        resources = [
            as_resource_dict(item)
            for item in list_resources_for_job(session, job_id)
            if item.patient_id == patient_id
        ]
    return {"job_id": job_id, "patient_id": patient_id, "resources": resources}


@router.get("/jobs/{job_id}/validation")
def get_job_validation(job_id: str) -> dict:
    with session_scope() as session:
        issues = [as_issue_dict(item) for item in list_issues_for_job(session, job_id)]
    return {"job_id": job_id, "issues": issues}


@router.get("/resources/{resource_pk}")
def get_resource(resource_pk: int) -> dict:
    with session_scope() as session:
        row = session.get(FhirResource, resource_pk)
        if not row:
            raise HTTPException(status_code=404, detail="Resource not found")
        return as_resource_dict(row)


# Coverage & Gap Analysis Endpoints
@router.get("/coverage/report")
def get_coverage_report(
    payer: str | None = None,
    compliance: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 1000,
) -> dict:
    """Get coverage data with optional filtering for gap analysis dashboard."""
    from .models import Coverage
    from datetime import datetime
    from sqlalchemy import select, and_

    with session_scope() as session:
        query = select(Coverage)

        # Apply filters
        filters = []
        if payer:
            filters.append(Coverage.payer == payer)
        if compliance == "Compliant":
            filters.append(Coverage.continuous_enrollment_compliant.is_(True))
        elif compliance == "Violation":
            filters.append(Coverage.continuous_enrollment_compliant.is_(False))
        if start_date:
            try:
                dt = datetime.fromisoformat(start_date).date()
                filters.append(Coverage.period_end >= dt)
            except ValueError:  # an unparseable date filter is ignored, as before
                pass
        if end_date:
            try:
                dt = datetime.fromisoformat(end_date).date()
                filters.append(Coverage.period_start <= dt)
            except ValueError:
                pass

        if filters:
            query = query.where(and_(*filters))

        query = query.limit(limit)
        coverages = session.execute(query).scalars().all()

        # Build summary statistics
        total = len(coverages)
        continuous = sum(1 for c in coverages if c.continuous_enrollment_compliant)
        violations = sum(1 for c in coverages if c.gap_detected)
        compliance_rate = int((continuous / total * 100)) if total > 0 else 0

        # Convert to dicts
        records = []
        for cov in coverages:
            records.append({
                "patient_id": cov.patient_id,
                "coverage_id": cov.coverage_id,
                "payer": cov.payer,
                "period_start": cov.period_start.isoformat() if cov.period_start else None,
                "period_end": cov.period_end.isoformat() if cov.period_end else None,
                "gap_detected": cov.gap_detected,
                "gap_days": cov.gap_days,
                "gap_start_date": cov.gap_start_date.isoformat() if cov.gap_start_date else None,
                "gap_end_date": cov.gap_end_date.isoformat() if cov.gap_end_date else None,
                "continuous_enrollment_compliant": cov.continuous_enrollment_compliant,
            })

        return {
            "coverage_records": records,
            "summary": {
                "total_covered": total,
                "continuous_enrollment": continuous,
                "hedis_violations": violations,
                "compliance_rate": compliance_rate,
            }
        }


@router.get("/coverage/patient/{patient_id}")
def get_patient_coverage(patient_id: str) -> dict:
    """Get all coverage records for a specific patient."""
    from .models import Coverage
    from sqlalchemy import select

    with session_scope() as session:
        coverages = session.execute(
            select(Coverage).where(Coverage.patient_id == patient_id).order_by(Coverage.period_start)
        ).scalars().all()

        if not coverages:
            raise HTTPException(status_code=404, detail=f"No coverage records found for patient {patient_id}")

        records = []
        for cov in coverages:
            records.append({
                "coverage_id": cov.coverage_id,
                "patient_id": cov.patient_id,
                "payer": cov.payer,
                "period_start": cov.period_start.isoformat() if cov.period_start else None,
                "period_end": cov.period_end.isoformat() if cov.period_end else None,
                "gap_detected": cov.gap_detected,
                "gap_days": cov.gap_days,
                "gap_start_date": cov.gap_start_date.isoformat() if cov.gap_start_date else None,
                "gap_end_date": cov.gap_end_date.isoformat() if cov.gap_end_date else None,
                "continuous_enrollment_compliant": cov.continuous_enrollment_compliant,
                "source_file": cov.source_file,
                "created_at": cov.created_at.isoformat() if cov.created_at else None,
            })

        return {"patient_id": patient_id, "coverage_records": records}


@router.get("/coverage/{coverage_id}/gaps")
def get_coverage_gaps(coverage_id: str) -> dict:
    """Get gap details for a specific coverage record."""
    from .models import Coverage

    with session_scope() as session:
        cov = session.query(Coverage).filter_by(coverage_id=coverage_id).first()
        
        if not cov:
            raise HTTPException(status_code=404, detail=f"Coverage record {coverage_id} not found")

        return {
            "coverage_id": cov.coverage_id,
            "patient_id": cov.patient_id,
            "period_start": cov.period_start.isoformat() if cov.period_start else None,
            "period_end": cov.period_end.isoformat() if cov.period_end else None,
            "gap_detected": cov.gap_detected,
            "gap_days": cov.gap_days,
            "gap_start_date": cov.gap_start_date.isoformat() if cov.gap_start_date else None,
            "gap_end_date": cov.gap_end_date.isoformat() if cov.gap_end_date else None,
            "continuous_enrollment_compliant": cov.continuous_enrollment_compliant,
            "is_hedis_violation": cov.gap_detected and cov.gap_days and cov.gap_days > 45,
        }


@router.get("/dq/runs")
def list_dq_runs(job_id: str | None = None, batch_id: str | None = None, limit: int = 100) -> dict:
    from sqlalchemy import select
    from .models import DqRun

    with session_scope() as session:
        query = select(DqRun)
        if job_id:
            query = query.where(DqRun.job_id == job_id)
        if batch_id:
            query = query.where(DqRun.batch_id == batch_id)
        rows = session.execute(query.order_by(DqRun.started_at.desc()).limit(limit)).scalars().all()
        runs = [
            {
                "id": row.id,
                "job_id": row.job_id,
                "batch_id": row.batch_id,
                "trigger_event_id": row.trigger_event_id,
                "status": row.status,
                "summary": row.summary_json,
                "error_text": row.error_text,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
            for row in rows
        ]
    return {"dq_runs": runs}


@router.get("/dq/runs/{dq_run_id}/anomalies")
def list_dq_run_anomalies(dq_run_id: int, limit: int = 500) -> dict:
    from sqlalchemy import select
    from .models import DataAnomaly

    with session_scope() as session:
        rows = session.execute(
            select(DataAnomaly)
            .where(DataAnomaly.dq_run_id == dq_run_id)
            .order_by(DataAnomaly.detected_at.desc())
            .limit(limit)
        ).scalars().all()
        anomalies = [
            {
                "id": row.id,
                "dq_run_id": row.dq_run_id,
                "batch_id": row.batch_id,
                "job_id": row.job_id,
                "patient_id": row.patient_id,
                "resource_type": row.resource_type,
                "anomaly_type": row.anomaly_type,
                "severity": row.severity,
                "status": row.status,
                "details": row.details_json,
                "detected_at": row.detected_at.isoformat() if row.detected_at else None,
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in rows
        ]
    return {"dq_run_id": dq_run_id, "anomalies": anomalies}


@router.get("/dq/runs/{dq_run_id}/checks")
def list_dq_run_checks(dq_run_id: int, limit: int = 1000) -> dict:
    from sqlalchemy import select
    from .models import DqCheckResult

    with session_scope() as session:
        rows = session.execute(
            select(DqCheckResult)
            .where(DqCheckResult.dq_run_id == dq_run_id)
            .order_by(DqCheckResult.created_at.desc())
            .limit(limit)
        ).scalars().all()
        checks = [
            {
                "id": row.id,
                "dq_run_id": row.dq_run_id,
                "batch_id": row.batch_id,
                "check_name": row.check_name,
                "resource_type": row.resource_type,
                "status": row.status,
                "metric_name": row.metric_name,
                "metric_value": row.metric_value,
                "threshold_value": row.threshold_value,
                "details": row.details_json,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    return {"dq_run_id": dq_run_id, "checks": checks}


@router.get("/batches")
def list_batches(limit: int = 100) -> dict:
    from sqlalchemy import select
    from .models import ConversionJob, SourceBatch

    with session_scope() as session:
        rows = session.execute(
            select(SourceBatch).order_by(SourceBatch.started_at.desc()).limit(limit)
        ).scalars().all()
        batches = []
        for row in rows:
            latest_job = session.execute(
                select(ConversionJob)
                .where(ConversionJob.batch_id == row.id)
                .order_by(ConversionJob.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            batches.append(
                {
                    "batch_id": row.id,
                    "job_id": latest_job.id if latest_job else None,
                    "source_system": row.source_system,
                    "source_format": row.source_format,
                    "adapter_name": row.adapter_name,
                    "status": row.status,
                    "resource_count": row.resource_count,
                    "resource_count_validated": row.resource_count_validated,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
            )
    return {"batches": batches}


@router.get("/config/measures")
def get_measure_config() -> dict:
    import json

    rules_path = Path(__file__).resolve().parents[2] / "data_quality" / "config" / "dq_rules.json"
    if not rules_path.exists():
        return {"measures": []}
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    measures = []
    def _phase(rule: dict) -> int:
        value = rule.get("phase", 99)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 99
    if isinstance(data, dict):
        for measure_id, rule in sorted(data.items(), key=lambda item: (_phase(item[1]), item[0])):
            if not isinstance(rule, dict):
                continue
            measures.append(
                {
                    "measure_id": measure_id,
                    "phase": rule.get("phase"),
                    "description": rule.get("description"),
                    "required_resource_types": rule.get("required_resource_types", []),
                    "observation_codes": rule.get("observation_codes", []),
                    "required_condition_codes": rule.get("required_condition_codes", []),
                    "required_procedure_codes": rule.get("required_procedure_codes", []),
                    "required_medication_codes": rule.get("required_medication_codes", []),
                    "required_immunization_codes": rule.get("required_immunization_codes", []),
                    "clinical_evidence_mode": rule.get("clinical_evidence_mode", "any"),
                    "min_distinct_patients_with_evidence": rule.get("min_distinct_patients_with_evidence"),
                    "requires_unit": rule.get("requires_unit"),
                    "value_range": rule.get("value_range", {}),
                    "volume_drop_threshold_pct": rule.get("volume_drop_threshold_pct"),
                    "volume_spike_threshold_pct": rule.get("volume_spike_threshold_pct"),
                    "denominator_drop_threshold_pct": rule.get("denominator_drop_threshold_pct"),
                }
            )
    return {"measures": measures}


@router.get("/measures")
def get_measures(batch_id: str | None = None) -> dict:
    configured = get_measure_config().get("measures", [])
    measure_ids = [item.get("measure_id") for item in configured if item.get("measure_id")]
    return {"batch_id": batch_id, "measures": measure_ids}


@router.post("/run-checks")
def run_checks(job_id: str, batch_id: str, measure_id: str | None = None, force_rerun: bool = True) -> dict:
    from .models import ConversionJob
    from services.data_quality_rules.main import process_event_by_id

    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.batch_id != batch_id:
            raise HTTPException(status_code=400, detail="batch_id does not match job")
        event = enqueue_domain_event(
            session,
            event_type="ingestion.completed",
            batch_id=batch_id,
            job_id=job_id,
            payload_json={
                "manual_run": True,
                "force_rerun": force_rerun,
                "measure_id": measure_id,
            },
        )
        event_id = event.id

    processed = process_event_by_id(event_id)
    return {
        "event_id": event_id,
        "processed": processed,
        "job_id": job_id,
        "batch_id": batch_id,
        "measure_id": measure_id,
    }


@router.get("/summary")
def get_dq_summary(dq_run_id: int | None = None, batch_id: str | None = None, job_id: str | None = None) -> dict:
    from sqlalchemy import func, select
    from .models import DataAnomaly, DqCheckResult, DqRun

    with session_scope() as session:
        run_query = select(DqRun)
        if dq_run_id:
            run_query = run_query.where(DqRun.id == dq_run_id)
        elif batch_id:
            run_query = run_query.where(DqRun.batch_id == batch_id).order_by(DqRun.started_at.desc())
        elif job_id:
            run_query = run_query.where(DqRun.job_id == job_id).order_by(DqRun.started_at.desc())
        else:
            run_query = run_query.order_by(DqRun.started_at.desc())
        run = session.execute(run_query.limit(1)).scalar_one_or_none()
        if run is None:
            return {"dq_run": None, "kpis": {}, "severity_counts": {}, "anomaly_type_counts": {}}

        severity_rows = session.execute(
            select(DataAnomaly.severity, func.count(DataAnomaly.id))
            .where(DataAnomaly.dq_run_id == run.id)
            .group_by(DataAnomaly.severity)
        ).all()
        severity_counts = {severity: count for severity, count in severity_rows}

        type_rows = session.execute(
            select(DataAnomaly.anomaly_type, func.count(DataAnomaly.id))
            .where(DataAnomaly.dq_run_id == run.id)
            .group_by(DataAnomaly.anomaly_type)
        ).all()
        anomaly_type_counts = {anomaly_type: count for anomaly_type, count in type_rows}

        check_rows = session.execute(
            select(DqCheckResult.status, func.count(DqCheckResult.id))
            .where(DqCheckResult.dq_run_id == run.id)
            .group_by(DqCheckResult.status)
        ).all()
        check_counts = {status: count for status, count in check_rows}

        return {
            "dq_run": {
                "id": run.id,
                "job_id": run.job_id,
                "batch_id": run.batch_id,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "summary": run.summary_json,
            },
            "kpis": {
                "total_anomalies": sum(severity_counts.values()),
                "critical_anomalies": severity_counts.get("critical", 0),
                "high_anomalies": severity_counts.get("high", 0),
                "failed_checks": check_counts.get("failed", 0),
                "passed_checks": check_counts.get("passed", 0),
            },
            "severity_counts": severity_counts,
            "anomaly_type_counts": anomaly_type_counts,
        }


@router.get("/anomalies")
def list_anomalies(
    dq_run_id: int | None = None,
    batch_id: str | None = None,
    severity: str | None = None,
    anomaly_type: str | None = None,
    limit: int = 500,
) -> dict:
    from sqlalchemy import select
    from .models import DataAnomaly

    with session_scope() as session:
        query = select(DataAnomaly)
        if dq_run_id is not None:
            query = query.where(DataAnomaly.dq_run_id == dq_run_id)
        if batch_id:
            query = query.where(DataAnomaly.batch_id == batch_id)
        if severity:
            query = query.where(DataAnomaly.severity == severity)
        if anomaly_type:
            query = query.where(DataAnomaly.anomaly_type == anomaly_type)

        rows = session.execute(
            query.order_by(DataAnomaly.detected_at.desc()).limit(limit)
        ).scalars().all()
        anomalies = [
            {
                "id": row.id,
                "dq_run_id": row.dq_run_id,
                "batch_id": row.batch_id,
                "job_id": row.job_id,
                "patient_id": row.patient_id,
                "resource_type": row.resource_type,
                "anomaly_type": row.anomaly_type,
                "severity": row.severity,
                "status": row.status,
                "details": row.details_json,
                "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            }
            for row in rows
        ]
    return {"anomalies": anomalies}


@router.get("/trends/anomalies-by-batch")
def anomalies_by_batch(limit: int = 30) -> dict:
    from sqlalchemy import func, select
    from .models import DataAnomaly, DqRun

    with session_scope() as session:
        rows = session.execute(
            select(
                DataAnomaly.batch_id,
                DqRun.started_at,
                func.count(DataAnomaly.id),
            )
            .join(DqRun, DqRun.id == DataAnomaly.dq_run_id)
            .group_by(DataAnomaly.batch_id, DqRun.started_at)
            .order_by(DqRun.started_at.desc())
            .limit(limit)
        ).all()
        trends = [
            {
                "batch_id": batch_id,
                "run_started_at": started_at.isoformat() if started_at else None,
                "anomaly_count": anomaly_count,
            }
            for batch_id, started_at, anomaly_count in rows
        ]
    return {"trends": trends}

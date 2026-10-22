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
    clear_jobs_for_input_dir,
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
            filters.append(Coverage.continuous_enrollment_compliant == True)
        elif compliance == "Violation":
            filters.append(Coverage.continuous_enrollment_compliant == False)
        if start_date:
            try:
                dt = datetime.fromisoformat(start_date).date()
                filters.append(Coverage.period_end >= dt)
            except:
                pass
        if end_date:
            try:
                dt = datetime.fromisoformat(end_date).date()
                filters.append(Coverage.period_start <= dt)
            except:
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

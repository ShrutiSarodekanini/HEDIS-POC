from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from starlette.datastructures import UploadFile as StarletteUploadFile

from .adapters import registry
from .config import settings
from .db import session_scope
from .models import ConversionJob, FhirResource, SourceBatch
from .pipeline import STAGES, job_manager, run_persist_stage
from .progress import progress_tracker
from .repository import (
    as_issue_dict,
    as_job_dict,
    as_resource_dict,
    list_issues_for_job,
    list_jobs,
    list_resources_for_job,
)
from .repository import (
    create_job as create_job_record,
)
from .synthea import SUPPORTED_FILES, discover_dataset

log = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["conversion"])


def _require_input_dir(input_dir: str) -> str:
    cleaned = (input_dir or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="input_dir is required. Upload a folder/zip first or enter a valid path.")
    return cleaned


def _durable_stages(job: ConversionJob) -> list[dict]:
    saved_stages = (job.summary or {}).get("timelogs", {}).get("stages", {})
    if saved_stages:
        return [{"name": name, **saved_stages.get(name, {})} for name in STAGES]

    statuses = {name: "pending" for name in STAGES}
    if job.status in {"completed", "partially_completed"}:
        statuses.update({name: "completed" for name in STAGES})
        if not job.resources_persisted:
            statuses["postgresql_storage"] = "pending"
    elif job.status == "running":
        statuses["csv_input"] = "running"
    elif job.status == "failed":
        statuses["csv_input"] = "failed"

    details = {
        "csv_input": f"Read {job.total_source_records} source records" if job.total_source_records else "",
        "mapping": f"Mapped {job.resources_generated} resources" if job.resources_generated else "",
        "fhir_generation": f"Generated {job.resources_generated} resources" if job.resources_generated else "",
        "validation": f"Validated {job.resources_validated} resources" if job.resources_validated else "",
        "postgresql_storage": f"Persisted {job.resources_persisted} resources" if job.resources_persisted else "",
    }
    return [
        {
            "name": name,
            "status": statuses[name],
            "details": details[name],
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": None,
        }
        for name in STAGES
    ]


def _job_status_payload(job_id: str) -> dict:
    state = job_manager.get_state(job_id)
    with session_scope() as session:
        job = session.get(ConversionJob, job_id)
        resources = list_resources_for_job(session, job_id) if job is not None else []

    if job is not None and (state is None or job.status != state.status):
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
        return {
            "job_id": job_id,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "stages": _durable_stages(job),
            "resource_progress": progress,
        }

    if state is None:
        return {"job_id": job_id, "status": "unknown", "stages": [], "resource_progress": {}}

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


def _is_fhir_json(path: Path) -> bool:
    """Return whether a saved JSON upload declares a FHIR resource type."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("resourceType"), str)


def _detect_adapter_from_files(file_list: list[str], dataset_dir: Path | None = None) -> str:
    """Auto-detect an adapter from extensions and saved JSON content.
    
    Returns the most likely adapter name based on file patterns.
    Defaults to 'synthea_csv' if no specific pattern matches.
    
    JSON is considered FHIR only when its top-level object declares resourceType.
    """
    lower_files = {f.lower() for f in file_list}
    
    # C-CDA/CCD documents → ccda_hie
    if any(f.endswith(('.xml', '.ccda')) for f in lower_files):
        return "ccda_hie"
    
    # FHIR files -> ehr_fhir_r4
    if any(file_name.endswith(".fhir") for file_name in lower_files):
        return "ehr_fhir_r4"
    if dataset_dir is not None and any(
        file_name.endswith(".json") and _is_fhir_json(dataset_dir / file_name)
        for file_name in lower_files
    ):
        return "ehr_fhir_r4"
    
    # Claims 837 files → claims_837
    if any(f.endswith('.837') or f.endswith('_837') for f in lower_files):
        return "claims_837"
    
    # NCPDP pharmacy files → ncpdp_pharmacy
    # Matches: ncpdp*.*, *.ncpdp, *.ncpdp.txt, *_ncpdp*
    if any(
        f.startswith('ncpdp') or 
        f.endswith('.ncpdp') or 
        f.endswith('.ncpdp.txt') or
        '_ncpdp' in f
        for f in lower_files
    ):
        return "ncpdp_pharmacy"
    
    # Default to Synthea CSV (most common, handles .csv files)
    return "synthea_csv"


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

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    metadata = discover_dataset(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
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

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    adapter = registry.get_adapter("ccda_hie")
    metadata = adapter.discover(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
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


@router.post("/datasets/upload-fhir")
async def upload_fhir_dataset(request: Request) -> dict:
    started_at = time.time()
    form = await request.form(max_files=float("inf"))
    files = [item for _, item in form.multi_items() if isinstance(item, StarletteUploadFile)]
    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)
    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    supported_extensions = {".json", ".fhir"}
    for uploaded in files:
        target_name = Path(uploaded.filename or "").name.lower()
        if Path(target_name).suffix not in supported_extensions and "bundle" not in target_name:
            continue
        with (dataset_dir / target_name).open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        saved_files.append(target_name)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No FHIR JSON files were uploaded. Upload .json or .fhir files (e.g., bundle.json).")

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    if detected_adapter != "ehr_fhir_r4":
        raise HTTPException(status_code=400, detail="Uploaded JSON does not contain a FHIR resource.")
    
    adapter = registry.get_adapter("ehr_fhir_r4")
    if adapter is None:
        raise HTTPException(status_code=500, detail="FHIR adapter is unavailable.")
    metadata = adapter.discover(str(dataset_dir))
    
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files if metadata else saved_files,
        "row_counts": metadata.row_counts if metadata else {"fhir": len(saved_files)},
        "timelog": {
            "step": "upload_fhir_dataset",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(completed_at - started_at, 3),
        },
    }


@router.post("/datasets/upload-claims")
async def upload_claims_dataset(request: Request) -> dict:
    started_at = time.time()
    form = await request.form(max_files=float("inf"))
    files = [item for _, item in form.multi_items() if isinstance(item, StarletteUploadFile)]
    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)
    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    supported_extensions = {".837"}
    for uploaded in files:
        target_name = Path(uploaded.filename or "").name.lower()
        if Path(target_name).suffix not in supported_extensions:
            continue
        with (dataset_dir / target_name).open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        saved_files.append(target_name)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No Claims 837 files were uploaded. Select .837 EDI files.")

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    adapter = registry.get_adapter("claims_837")
    if adapter is None:
        raise HTTPException(status_code=500, detail="Claims adapter is unavailable.")
    metadata = adapter.discover(str(dataset_dir))
    
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files if metadata else saved_files,
        "row_counts": metadata.row_counts if metadata else {"claims": len(saved_files)},
        "timelog": {
            "step": "upload_claims_dataset",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(completed_at - started_at, 3),
        },
    }


@router.post("/datasets/upload-ncpdp")
async def upload_ncpdp_dataset(request: Request) -> dict:
    started_at = time.time()
    form = await request.form(max_files=float("inf"))
    files = [item for _, item in form.multi_items() if isinstance(item, StarletteUploadFile)]
    upload_root = Path("./.uploaded_datasets")
    upload_root.mkdir(parents=True, exist_ok=True)
    dataset_dir = upload_root / str(uuid.uuid4())
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    supported_extensions = (".ncpdp", ".ncpdp.txt", ".pharmacy", ".rx")
    for uploaded in files:
        target_name = Path(uploaded.filename or "").name.lower()
        if not target_name.endswith(supported_extensions):
            continue
        with (dataset_dir / target_name).open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        saved_files.append(target_name)

    if not saved_files:
        raise HTTPException(status_code=400, detail="No NCPDP pharmacy files were uploaded. Include files like ncpdp_*.txt or *_ncpdp files.")

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    adapter = registry.get_adapter("ncpdp_pharmacy")
    if adapter is None:
        raise HTTPException(status_code=500, detail="NCPDP adapter is unavailable.")
    metadata = adapter.discover(str(dataset_dir))
    
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
        "saved_files": sorted(set(saved_files)),
        "found_files": metadata.found_files if metadata else saved_files,
        "row_counts": metadata.row_counts if metadata else {"ncpdp": len(saved_files)},
        "timelog": {
            "step": "upload_ncpdp_dataset",
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
        raise HTTPException(status_code=400, detail="The uploaded ZIP file is invalid.") from exc

    if not saved_files:
        raise HTTPException(
            status_code=400,
            detail="ZIP did not contain supported Synthea CSV files (patients.csv, encounters.csv, etc.).",
        )

    detected_adapter = _detect_adapter_from_files(saved_files, dataset_dir)
    metadata = discover_dataset(str(dataset_dir))
    completed_at = time.time()
    return {
        "input_dir": str(dataset_dir.resolve()),
        "detected_adapter": detected_adapter,
        "available_adapters": registry.list_adapters(),
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
def start_conversion_job(input_dir: str, adapter_name: str = "synthea_csv",
                         async_processing: bool = False) -> dict:
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
    # Queueing is mandatory: the sync launch path is retired in favor of RabbitMQ workers.
    if not async_processing:
        log.info("/jobs/start called with async_processing=false; using queued processing")

    try:
        from services.rabbitmq_messages import ConversionMessage
        from services.reliable_messaging import enqueue_outbox_session

        with session_scope() as session:
            job = create_job_record(session, cleaned, adapter_name)
            job_id = job.id
            batch_id = str(job.batch_id)
            message = ConversionMessage(
                batch_id=batch_id,
                job_id=job_id,
                source_system="synthea" if adapter_name == "synthea_csv" else adapter_name,
                file_path=cleaned,
                file_type="csv",
                adapter_name=adapter_name,
            )
            enqueue_outbox_session(session, "ingestion.convert", message)
        job_manager.ensure_state(job_id, batch_id)
    except Exception as exc:
        log.error("Could not create queued ingestion job: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The ingestion job could not be queued.") from exc

    return {"job_id": job_id, "batch_id": batch_id, "status": "queued", "adapter": adapter_name}


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
    payload = _job_status_payload(job_id)
    if payload["status"] == "unknown":
        raise HTTPException(status_code=404, detail="Job not found")
    return payload


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
    from datetime import datetime

    from sqlalchemy import and_, select

    from .models import Coverage

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
            except ValueError:
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
    from sqlalchemy import select

    from .models import Coverage

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


# ========== Integration/Measures Support Functions ==========

def _extract_root_library_reference(path: str | Path) -> str:
    """Extract the root Library id from a FHIR Measure or measure Bundle."""
    definition_path = Path(path)
    try:
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("measure definition could not be read as JSON") from exc

    resources = [definition]
    if definition.get("resourceType") == "Bundle":
        resources = [entry.get("resource", {}) for entry in definition.get("entry", [])
                     if isinstance(entry, dict)]

    for resource in resources:
        if resource.get("resourceType") != "Measure":
            continue
        for reference in resource.get("library", []):
            if not isinstance(reference, str):
                continue
            library_id = reference.split("|", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            if library_id:
                return library_id

    raise LookupError("measure definition has no root library reference")


def _maybe_prune_run_request_bundle(bundle: dict, measure_id: str, path: str | Path) -> None:
    """Optionally prune a run request bundle to remove unnecessary resources.
    
    Modifies bundle in place to remove resources that are not needed for
    FQM execution to reduce payload size and improve performance.
    Used by integration/measures.py during measure execution.
    """
    if not isinstance(bundle, dict):
        return
    
    # Remove ValueSet resources (they're referenced but not needed for execution)
    if "entry" in bundle and isinstance(bundle["entry"], list):
        bundle["entry"] = [
            entry for entry in bundle["entry"]
            if not isinstance(entry, dict) or entry.get("resource", {}).get("resourceType") != "ValueSet"
        ]


# --- WebSocket Progress Tracking -----------------------------------------------

@router.websocket("/ws/batch/{batch_id}")
async def websocket_progress(websocket: WebSocket, batch_id: str) -> None:
    """
    WebSocket endpoint for live progress updates on batch processing.

    Clients connect with: ws://localhost:8780/api/ws/batch/{batch_id}

    Receives messages:
    - {"stage": "conversion", "status": "starting", "details": {...}}
    - {"stage": "conversion", "status": "processing", "details": {"pct": 45}}
    - {"stage": "conversion", "status": "complete", "details": {...}}

    Connect and stay open to receive live updates as workers process the batch.
    """
    await websocket.accept()
    progress_tracker.register(batch_id, websocket)

    # Send current progress if available
    current_progress = progress_tracker.get_progress(batch_id)
    if current_progress:
        await websocket.send_json({
            "type": "initial_state",
            "progress": current_progress,
        })

    try:
        previous = None
        while True:
            current = progress_tracker.get_progress(batch_id)
            if current and current != previous:
                await websocket.send_json({"type": "progress", "progress": current})
                previous = current
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        progress_tracker.unregister(batch_id, websocket)


@router.get("/batch/{batch_id}/progress")
def get_batch_progress(batch_id: str) -> dict:
    """
    Get current progress of a batch.

    Returns progress data showing what stage it's in and status of each worker.
    """
    progress = progress_tracker.get_progress(batch_id)
    if not progress:
        raise HTTPException(status_code=404, detail=f"No progress found for batch {batch_id}")
    return progress


@router.get("/progress")
def get_all_progress() -> dict:
    """Get progress for all active batches."""
    return {"batches": progress_tracker.get_all_progress()}


# --- RabbitMQ Pipeline Integration -----------------------------------------------

@router.post("/upload-async")
async def upload_file_to_pipeline(file: UploadFile = File(...)) -> dict:
    """
    Upload a file to start the async data ingestion pipeline via RabbitMQ.

    - Accepts CSV, JSON, FHIR, C-CDA, Claims 837, and NCPDP files
    - Creates a source batch record in database
    - Publishes message to RabbitMQ for asynchronous processing by ConversionWorker
    - Returns batch_id to track progress

    Example:
        curl -X POST -F "file=@patients.csv" http://localhost:8780/api/ingestion/upload-async

    Response:
        {
            "batch_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "status": "processing",
            "message": "Upload accepted. Processing started."
        }
    """
    try:
        # Import here to avoid circular dependencies
        from services.rabbitmq_messages import ConversionMessage
        from services.reliable_messaging import enqueue_outbox_session

        # Generate batch ID
        batch_id = str(uuid.uuid4())

        original_name = (file.filename or "").lower()
        suffix = Path(original_name).suffix
        file_type_map = {
            ".837": ("837", ".837"),
            ".ccda": ("ccda", ".ccda"),
            ".csv": ("csv", ".csv"),
            ".fhir": ("fhir", ".fhir"),
            ".json": ("json", ".json"),
            ".ndjson": ("ndjson", ".ndjson"),
            ".ncpdp": ("ncpdp", ".ncpdp"),
            ".rx": ("rx", ".rx"),
            ".txt": ("csv", ".csv"),
            ".xml": ("xml", ".xml"),
        }
        if original_name.endswith(".ncpdp.txt"):
            file_type, stored_suffix = "ncpdp", ".ncpdp"
        elif suffix in file_type_map:
            file_type, stored_suffix = file_type_map[suffix]
        else:
            raise HTTPException(status_code=400, detail="The uploaded file type is not supported.")

        # Create uploads directory if it doesn't exist
        upload_root = Path(".") / "data" / "uploads" / batch_id
        upload_root.mkdir(parents=True, exist_ok=True)

        # Generated names prevent patient details in client filenames from entering storage.
        file_path = upload_root / f"source{stored_suffix}"
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)

        log.info("Async upload stored: batch_id=%s, size=%d bytes", batch_id, len(contents))

        message = ConversionMessage(
            batch_id=batch_id,
            source_system="api_upload",
            file_path=str(upload_root),
            file_type=file_type,
            priority=0,
        )

        # Create the batch and its first message in one transaction.
        with session_scope() as session:
            source_batch = SourceBatch(
                batch_id=batch_id,
                source_system="api_upload",
                status="running",
                resource_count=None,
                started_at=datetime.now(timezone.utc)
            )
            session.add(source_batch)
            enqueue_outbox_session(session, "ingestion.convert", message)
        log.info("SourceBatch and conversion outbox message created: batch_id=%s", batch_id)

        return {
            "batch_id": batch_id,
            "status": "processing",
            "message": "Upload accepted. Processing started via RabbitMQ pipeline.",
        }

    except Exception as exc:
        log.error("Async upload failed: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="The upload could not be queued.") from exc


def _to_member_result(entry: dict, measure_id: str) -> dict:
    """Convert one detailed fqm-execution result into the integration result shape."""
    if not isinstance(entry, dict) or not isinstance(entry.get("patientId"), str):
        raise ValueError(f"engine result for {measure_id} has no patient id")

    populations = {
        "initial_population": False,
        "denominator": False,
        "numerator": False,
        "denominator_exclusion": False,
    }
    population_keys = {
        "initial-population": "initial_population",
        "denominator": "denominator",
        "numerator": "numerator",
        "denominator-exclusion": "denominator_exclusion",
    }
    for group in entry.get("detailedResults", []):
        if not isinstance(group, dict):
            continue
        for population in group.get("populationResults", []):
            if not isinstance(population, dict):
                continue
            key = population_keys.get(population.get("populationType"))
            if key:
                populations[key] = populations[key] or population.get("result") is True

    return {"member_id": entry["patientId"], "measure_id": measure_id, "populations": populations}

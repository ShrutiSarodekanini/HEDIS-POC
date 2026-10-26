from __future__ import annotations

import json
import subprocess
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .config import settings
from .db import session_scope
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


_BP_MEASURE_HINTS = ("BPD", "CBP", "CMS165", "CONTROLLINGHIGHBP")
_BP_OBSERVATION_CODES = {"8480-6", "8462-4", "85354-9"}
_ENGINE_EXEC_TIMEOUT_SECONDS = 75
_GENERIC_LARGE_BUNDLE_THRESHOLD = 3000
_GENERIC_VERY_LARGE_BUNDLE_THRESHOLD = 8000

_GENERIC_SOFT_KEEP_TYPES = {
    "Patient",
    "Encounter",
    "Condition",
    "Observation",
    "Procedure",
    "MedicationRequest",
    "MedicationDispense",
    "MedicationAdministration",
    "MedicationStatement",
    "CarePlan",
    "ServiceRequest",
    "DiagnosticReport",
    "Immunization",
    "AllergyIntolerance",
}

_GENERIC_HARD_KEEP_TYPES = {
    "Patient",
    "Encounter",
    "Condition",
    "Observation",
    "Procedure",
    "MedicationRequest",
    "MedicationDispense",
    "MedicationAdministration",
    "MedicationStatement",
}


def _is_bp_measure_request(measure_id: str, bundle_path: Path) -> bool:
    probe = "|".join(
        [
            str(measure_id or ""),
            bundle_path.name,
            bundle_path.parent.name,
        ]
    ).upper()
    return any(hint in probe for hint in _BP_MEASURE_HINTS)


def _observation_is_bp_related(resource: dict[str, Any]) -> bool:
    code_block = resource.get("code") if isinstance(resource, dict) else None
    codings = code_block.get("coding") if isinstance(code_block, dict) else None
    if isinstance(codings, list):
        for coding in codings:
            if not isinstance(coding, dict):
                continue
            code = coding.get("code")
            if isinstance(code, str) and code in _BP_OBSERVATION_CODES:
                return True

    text_blob = json.dumps(resource).lower()
    return "blood pressure" in text_blob or "systolic" in text_blob or "diastolic" in text_blob


def _prune_bp_bundle_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    if payload.get("resourceType") != "Bundle":
        return payload, 0, 0

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return payload, 0, 0

    keep_types = {
        "Patient",
        "Encounter",
        "Condition",
        "Observation",
        "Procedure",
        "MedicationRequest",
        "CarePlan",
        "Immunization",
    }

    pruned_entries: list[dict[str, Any]] = []
    removed_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            removed_count += 1
            continue

        resource = entry.get("resource")
        if not isinstance(resource, dict):
            removed_count += 1
            continue

        resource_type = resource.get("resourceType")
        if resource_type not in keep_types:
            removed_count += 1
            continue

        if resource_type == "Observation" and not _observation_is_bp_related(resource):
            removed_count += 1
            continue

        pruned_entries.append(entry)

    if removed_count == 0:
        return payload, len(entries), 0

    pruned_payload = dict(payload)
    pruned_payload["entry"] = pruned_entries
    return pruned_payload, len(pruned_entries), removed_count


def _bundle_entry_count(payload: dict[str, Any]) -> int:
    if payload.get("resourceType") != "Bundle":
        return 0
    entries = payload.get("entry")
    return len(entries) if isinstance(entries, list) else 0


def _prune_bundle_by_types(
    payload: dict[str, Any],
    keep_types: set[str],
) -> tuple[dict[str, Any], int, int]:
    if payload.get("resourceType") != "Bundle":
        return payload, 0, 0

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return payload, 0, 0

    pruned_entries: list[dict[str, Any]] = []
    removed_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            removed_count += 1
            continue

        resource = entry.get("resource")
        if not isinstance(resource, dict):
            removed_count += 1
            continue

        resource_type = resource.get("resourceType")
        if resource_type not in keep_types:
            removed_count += 1
            continue

        pruned_entries.append(entry)

    if removed_count == 0:
        return payload, len(entries), 0

    pruned_payload = dict(payload)
    pruned_payload["entry"] = pruned_entries
    return pruned_payload, len(pruned_entries), removed_count


def _build_execution_payload_candidates(
    payload: dict[str, Any],
    measure_id: str,
    bundle_path: Path,
) -> list[tuple[str, dict[str, Any], int, int]]:
    candidates: list[tuple[str, dict[str, Any], int, int]] = []
    seen_counts: set[int] = set()

    def append_candidate(label: str, candidate_payload: dict[str, Any], removed: int) -> None:
        count = _bundle_entry_count(candidate_payload)
        # Keep only progressively smaller payloads to avoid duplicate expensive retries.
        if count in seen_counts:
            return
        seen_counts.add(count)
        candidates.append((label, candidate_payload, count, removed))

    base_count = _bundle_entry_count(payload)
    append_candidate("original", payload, 0)

    is_bp = _is_bp_measure_request(measure_id, bundle_path)
    if is_bp:
        bp_payload, _, bp_removed = _prune_bp_bundle_payload(payload)
        if bp_removed > 0:
            append_candidate("bp_pruned", bp_payload, bp_removed)

    if base_count >= _GENERIC_LARGE_BUNDLE_THRESHOLD:
        soft_payload, _, soft_removed = _prune_bundle_by_types(payload, _GENERIC_SOFT_KEEP_TYPES)
        if soft_removed > 0:
            append_candidate("generic_soft_pruned", soft_payload, soft_removed)

    if base_count >= _GENERIC_VERY_LARGE_BUNDLE_THRESHOLD:
        hard_payload, _, hard_removed = _prune_bundle_by_types(payload, _GENERIC_HARD_KEEP_TYPES)
        if hard_removed > 0:
            append_candidate("generic_hard_pruned", hard_payload, hard_removed)

    return candidates


def _maybe_prune_run_request_bundle(temp_path: Path, measure_id: str, bundle_path: Path) -> None:
    if not _is_bp_measure_request(measure_id, bundle_path):
        return

    try:
        payload = json.loads(temp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(payload, dict):
        return

    pruned_payload, _, removed_count = _prune_bp_bundle_payload(payload)
    if removed_count <= 0:
        return

    try:
        temp_path.write_text(json.dumps(pruned_payload), encoding="utf-8")
    except OSError:
        return


def _resolve_measure_bundle_path(measure_bundle_path: str | None) -> Path:
    if not measure_bundle_path:
        raise HTTPException(status_code=400, detail="measure_bundle_path is required")

    candidate = Path(measure_bundle_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    repo_root = Path(__file__).resolve().parents[3]
    engine_root = repo_root / "services" / "measure_calc" / "engine"

    for path in (
        engine_root / measure_bundle_path,
        repo_root / measure_bundle_path,
    ):
        if path.exists():
            return path

    raise HTTPException(
        status_code=400,
        detail=f"measure_bundle_path not found: {measure_bundle_path}",
    )


def _to_member_result(cli_entry: dict, measure_id: str) -> dict:
    patient_id = cli_entry.get("patientId") or "unknown"
    detailed_results = cli_entry.get("detailedResults") or []

    population_map = {
        "initial_population": None,
        "denominator": None,
        "denominator_exclusion": None,
        "denominator_exception": None,
        "numerator": None,
    }

    pop_key_map = {
        "initial-population": "initial_population",
        "initial_population": "initial_population",
        "ipp": "initial_population",
        "denominator": "denominator",
        "den": "denominator",
        "denominator-exclusion": "denominator_exclusion",
        "denominator_exclusion": "denominator_exclusion",
        "denex": "denominator_exclusion",
        "denominator-exception": "denominator_exception",
        "denominator_exception": "denominator_exception",
        "denexcep": "denominator_exception",
        "numerator": "numerator",
        "num": "numerator",
        "measure-population": "measure_population",
        "measure_population": "measure_population",
        "measure-observation": "measure_observation",
        "measure_observation": "measure_observation",
    }

    # Some measures produce multiple detailed groups; aggregate booleans across all groups
    # so one false in an early group does not overwrite a true result in another group.
    for group in detailed_results:
        if not isinstance(group, dict):
            continue
        raw_populations = group.get("populationResults") or []
        if not isinstance(raw_populations, list):
            continue
        for item in raw_populations:
            if not isinstance(item, dict):
                continue
            raw_pop_type = item.get("populationType")
            pop_type = str(raw_pop_type).strip().lower() if raw_pop_type is not None else ""
            key = pop_key_map.get(pop_type)
            if key is None:
                continue

            raw_result = item.get("result")
            if raw_result is None:
                continue

            next_value = bool(raw_result)
            current_value = population_map.get(key)
            if current_value is None:
                population_map[key] = next_value
            else:
                population_map[key] = bool(current_value) or next_value

    denominator = population_map.get("denominator") is True
    numerator = population_map.get("numerator")

    if not denominator:
        outcome = "not_in_population"
    elif numerator is True:
        outcome = "numerator_met"
    elif numerator is False:
        outcome = "numerator_not_met"
    else:
        outcome = "unresolved_policy"

    status = "completed" if outcome != "unresolved_policy" else "unresolved"

    return {
        "member_id": patient_id,
        "measure_id": measure_id,
        "status": status,
        "outcome": outcome,
        "populations": population_map,
        "data_quality_flags": [],
        "warnings": [],
        "errors": [],
    }


def _parse_measurement_year(measurement_year: str | None) -> int | None:
    if not measurement_year:
        return None

    match = re.search(r"(\d{4})", measurement_year)
    if not match:
        return None

    year = int(match.group(1))
    if year < 1900 or year > 3000:
        return None
    return year


def _extract_root_library_reference(measure_bundle_path: Path) -> str | None:
    try:
        payload = json.loads(measure_bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    entries = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None

    measure_resource: dict | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if isinstance(resource, dict) and resource.get("resourceType") == "Measure":
            measure_resource = resource
            break

    if not measure_resource:
        return None

    libraries = measure_resource.get("library")
    if isinstance(libraries, list):
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            reference = lib.get("reference")
            if isinstance(reference, str) and reference.strip():
                raw_ref = reference.strip()
                # Handle canonical URLs and Reference strings by taking the final path segment.
                # Example: http://example.org/fhir/Library/MyLibrary|1.0.0 -> MyLibrary
                tail = raw_ref.rsplit("/", 1)[-1]
                lib_ref = tail.split("|", 1)[0].strip()
                if lib_ref:
                    return lib_ref

    measure_id = measure_resource.get("id")
    if isinstance(measure_id, str) and measure_id.strip():
        return measure_id.strip()

    stem = measure_bundle_path.stem
    if stem.endswith("-bundle"):
        stem = stem[: -len("-bundle")]
    return stem or None


@router.get("/health")
def api_health() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Service reachable",
        "version": "1.0.0",
    }


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
async def upload_dataset(files: list[UploadFile] = File(...)) -> dict:
    started_at = time.time()
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


@router.post("/run-request-file")
async def run_request_file(
    file: UploadFile = File(...),
    measure_id: str = Form(...),
    measurement_year: str | None = Form(None),
    adapter: str | None = Form(None),
    measure_bundle_path: str | None = Form(None),
    include_measure_report: str | None = Form(None),
    runtime_enabled: str | None = Form(None),
) -> dict:
    del adapter, include_measure_report, runtime_enabled

    bundle_path = _resolve_measure_bundle_path(measure_bundle_path)
    repo_root = Path(__file__).resolve().parents[3]
    engine_root = repo_root / "services" / "measure_calc" / "engine"
    cli_path = engine_root / "build" / "cli.js"

    if not cli_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Measure engine CLI is not built. Run `npm run backend:build`.",
        )

    suffix = Path(file.filename or "patient_bundle.json").suffix or ".json"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_input:
        temp_path = Path(temp_input.name)
        temp_input.write(await file.read())

    try:
        root_lib_ref = _extract_root_library_reference(bundle_path)
        parsed_year = _parse_measurement_year(measurement_year)
        if parsed_year is None:
            parsed_year = datetime.utcnow().year

        try:
            original_payload = json.loads(temp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            original_payload = None

        if not isinstance(original_payload, dict):
            return {
                "member_id": Path(file.filename or "patient").stem,
                "measure_id": measure_id,
                "status": "failed",
                "outcome": "failed",
                "populations": {},
                "data_quality_flags": ["invalid_input_bundle"],
                "warnings": [],
                "errors": [{"message": "Input patient bundle is not valid JSON Bundle content."}],
            }

        cmd_base = [
            "node",
            str(cli_path),
            "detailed",
            "--measure-bundle",
            str(bundle_path),
            "--measurement-period-start",
            f"{parsed_year}-01-01",
            "--measurement-period-end",
            f"{parsed_year}-12-31",
            "--trust-meta-profile",
            "false",
        ]
        if root_lib_ref:
            cmd_base.extend(["--root-lib-ref", root_lib_ref])

        execution_errors: list[str] = []
        candidates = _build_execution_payload_candidates(original_payload, measure_id, bundle_path)

        for label, candidate_payload, candidate_count, removed in candidates:
            try:
                temp_path.write_text(json.dumps(candidate_payload), encoding="utf-8")
            except OSError:
                execution_errors.append(f"{label}: unable to write temporary candidate bundle")
                continue

            cmd = [*cmd_base, "--patient-bundles", str(temp_path)]
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=engine_root,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=_ENGINE_EXEC_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                execution_errors.append(
                    f"{label}: timeout after {_ENGINE_EXEC_TIMEOUT_SECONDS}s (entries={candidate_count})"
                )
                continue

            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or "Engine execution failed"
                execution_errors.append(f"{label}: {message[-1500:]}")
                continue

            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                execution_errors.append(f"{label}: invalid engine payload: {exc}")
                continue

            if not isinstance(payload, list) or not payload:
                execution_errors.append(f"{label}: engine returned empty patient result")
                continue

            result = _to_member_result(payload[0], measure_id)
            if label != "original":
                # Pruned payloads are performance fallbacks and may omit evidence.
                # Do not report definitive gap closure/opening from these runs.
                result["status"] = "unresolved"
                result["outcome"] = "unresolved_policy"
                result["data_quality_flags"] = [
                    *(result.get("data_quality_flags") or []),
                    "bundle_pruned_for_performance",
                ]
                result["warnings"] = [
                    *(result.get("warnings") or []),
                    f"Execution used {label} candidate (removed {removed} resources, remaining {candidate_count}).",
                    "Result is non-definitive because a pruned bundle was used.",
                ]
                result["errors"] = [
                    *(result.get("errors") or []),
                    {"message": "Definitive gap status requires full-bundle execution."},
                ]
            return result

        combined_error = " | ".join(execution_errors)[-4000:] if execution_errors else "Engine execution failed"
        return {
            "member_id": Path(file.filename or "patient").stem,
            "measure_id": measure_id,
            "status": "failed",
            "outcome": "failed",
            "populations": {},
            "data_quality_flags": ["engine_execution_failed"],
            "warnings": [],
            "errors": [{"message": combined_error}],
        }
    finally:
        temp_path.unlink(missing_ok=True)


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
def start_conversion_job(input_dir: str) -> dict:
    cleaned = _require_input_dir(input_dir)
    path = Path(cleaned)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="Input directory not found")

    metadata = discover_dataset(cleaned)
    if metadata.missing_required_files:
        raise HTTPException(status_code=400, detail={"missing_required_files": metadata.missing_required_files})

    with session_scope() as session:
        cleared_jobs = clear_jobs_for_input_dir(session, cleaned)

    job_id = job_manager.create_job(cleaned)
    launch_job(job_id, cleaned)
    return {
        "job_id": job_id,
        "status": "started",
        "legacy_jobs_cleared": cleared_jobs,
    }


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
def get_job_patients(
    job_id: str,
    persisted_only: bool = False,
    bundleable_only: bool = True,
) -> dict:
    with session_scope() as session:
        resources = list_resources_for_job(session, job_id)

        if persisted_only:
            resources = [item for item in resources if bool(item.persisted)]

        if bundleable_only:
            resources = [item for item in resources if isinstance(item.resource_json, dict)]

        patient_stats: dict[str, dict[str, Any]] = {}
        for item in resources:
            if not item.patient_id:
                continue

            patient_id = item.patient_id
            stats = patient_stats.setdefault(
                patient_id,
                {
                    "patient_id": patient_id,
                    "resource_count": 0,
                    "estimated_size_bytes": 0,
                    "saved_at": None,
                },
            )

            stats["resource_count"] += 1

            if isinstance(item.resource_json, dict):
                try:
                    stats["estimated_size_bytes"] += len(
                        json.dumps(item.resource_json, separators=(",", ":")).encode("utf-8")
                    )
                except (TypeError, ValueError):
                    pass

            candidate_saved_at = item.updated_at or item.created_at
            current_saved_at = stats["saved_at"]
            if candidate_saved_at and (current_saved_at is None or candidate_saved_at > current_saved_at):
                stats["saved_at"] = candidate_saved_at

        patients = sorted(patient_stats.keys())
        patient_details = [
            {
                "patient_id": details["patient_id"],
                "resource_count": details["resource_count"],
                "estimated_size_bytes": details["estimated_size_bytes"],
                "saved_at": details["saved_at"].isoformat() if details["saved_at"] else None,
            }
            for details in sorted(patient_stats.values(), key=lambda row: row["patient_id"])
        ]

    return {
        "job_id": job_id,
        "patients": patients,
        "patient_details": patient_details,
        "persisted_only": persisted_only,
        "bundleable_only": bundleable_only,
    }


@router.get("/jobs/{job_id}/patients/{patient_id}/resources")
def get_patient_resources(job_id: str, patient_id: str) -> dict:
    with session_scope() as session:
        resources = [
            as_resource_dict(item)
            for item in list_resources_for_job(session, job_id)
            if item.patient_id == patient_id
        ]
    return {"job_id": job_id, "patient_id": patient_id, "resources": resources}


@router.get("/jobs/{job_id}/patients/{patient_id}/bundle")
def get_patient_bundle(job_id: str, patient_id: str, persisted_only: bool = True) -> dict:
    """Return a FHIR Bundle built from stored resources for one patient/job."""
    with session_scope() as session:
        rows = [item for item in list_resources_for_job(session, job_id) if item.patient_id == patient_id]

    if persisted_only:
        rows = [item for item in rows if bool(item.persisted)]

    entries = [{"resource": item.resource_json} for item in rows if isinstance(item.resource_json, dict)]

    if not entries:
        detail = "No persisted resources found for this patient" if persisted_only else "No resources found for this patient"
        raise HTTPException(status_code=404, detail=detail)

    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries,
    }

    return {
        "job_id": job_id,
        "patient_id": patient_id,
        "resource_count": len(entries),
        "persisted_only": persisted_only,
        "bundle": bundle,
    }


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

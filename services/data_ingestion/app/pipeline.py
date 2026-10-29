from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any

from pydantic import ValidationError

from .adapters import registry
from .config import settings
from .db import session_scope
from .mapping import bundles_by_patient, map_dataset, map_source_record, normalize_resource, validation_issue
from .models import (
    FhirResource,
)
from .repository import (
    batch_extract_denormalized,
    batch_fetch_fhir_resources,
    bulk_upsert_resources,
    complete_job,
    create_job,
    extract_additional_resource,
    extract_condition_from_fhir,
    extract_coverage_from_fhir,
    extract_encounter_from_fhir,
    extract_immunization_from_fhir,
    extract_location_from_fhir,
    extract_medication_from_fhir,
    extract_observation_from_fhir,
    extract_patient_from_fhir,
    extract_procedure_from_fhir,
    mark_validated_resources_persisted,
    persist_row_issues,
    persist_validation_messages,
    start_job,
    upsert_resource,
)
from .validator import validate_resources

STAGES = ["csv_input", "mapping", "fhir_generation", "validation", "postgresql_storage"]
RESOURCE_BATCH_SIZE = 1000

# CSV file → FHIR resource type mapping (for Synthea CSV adapter)
FILE_RESOURCE_TYPES = {
    "patients.csv": "Patient",
    "encounters.csv": "Encounter",
    "conditions.csv": "Condition",
    "observations.csv": "Observation",
    "procedures.csv": "Procedure",
    "medications.csv": "MedicationRequest",
    "immunizations.csv": "Immunization",
    "careplans.csv": "CarePlan",
    "claims.csv": "Claim",
    "coverages.csv": "Coverage",
    "allergies.csv": "AllergyIntolerance",
    "claims_transactions.csv": "Basic",
    "devices.csv": "Device",
    "imaging_studies.csv": "ImagingStudy",
    "organizations.csv": "Organization",
    "payer_transitions.csv": "Coverage",
    "payers.csv": "Organization",
    "providers.csv": "Practitioner",
    "supplies.csv": "SupplyDelivery",
}

# Adapter name → resource types mapping (for non-CSV adapters like claims837, ncpdp_pharmacy)
# Keys are the source_file prefixes (before the colon), not the adapter_name
ADAPTER_RESOURCE_TYPES = {
    "claims837": ["Claim"],
    "ncpdp": ["MedicationDispense"],
    "fhir": ["Patient", "Encounter", "Condition", "Observation", "Procedure", "MedicationRequest"],
    "ccda": ["Patient", "Encounter", "Condition", "Observation", "Procedure", "MedicationRequest"],
}


def resolve_resource_type(source_file: str, record_data: dict[str, Any] | None = None) -> str | None:
    """Resolve FHIR resource type from source file identifier or record.
    
    Handles both CSV files and adapter-prefixed source identifiers.
    
    Args:
        source_file: File identifier (e.g., "patients.csv", "claims837:file.837", "ncpdp:rx.txt")
        record_data: Optional record data dict containing "resourceType" field
    
    Returns:
        FHIR resource type string or None if unresolvable
    """
    # Check if record explicitly specifies resource type
    if record_data and isinstance(record_data, dict):
        if explicit_type := record_data.get("resourceType"):
            return explicit_type
    
    # Check if it's a known CSV file
    if resource_type := FILE_RESOURCE_TYPES.get(source_file):
        return resource_type
    
    # Check for adapter-prefixed identifiers (e.g., "claims837:", "ncpdp:", "fhir:", "ccda:")
    if ":" in source_file:
        prefix = source_file.split(":", 1)[0]
        # Map adapter prefix to resource types (prefer primary type)
        adapter_types = ADAPTER_RESOURCE_TYPES.get(prefix, [])
        return adapter_types[0] if adapter_types else None
    
    return None

logger = logging.getLogger("uvicorn.error")


def _emit(message: str) -> None:
    logger.info(message)


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
    batch_id: str | None = None
    status: str = "pending"
    stages: dict[str, StageState] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    resource_progress: dict[str, dict[str, int]] = field(default_factory=dict)


class JobManager:
    def __init__(self) -> None:
        self._states: dict[str, RuntimeState] = {}
        self._lock = threading.Lock()

    def create_job(self, input_dir: str, adapter_name: str = "synthea_csv") -> str:
        with session_scope() as session:
            job = create_job(session, input_dir, adapter_name)
            job_id = job.id
            batch_id = job.batch_id

        state = RuntimeState(
            job_id=job_id,
            batch_id=batch_id,
            status="pending",
            stages={stage: StageState(name=stage) for stage in STAGES},
        )
        with self._lock:
            self._states[job_id] = state
        return job_id

    def get_state(self, job_id: str) -> RuntimeState | None:
        with self._lock:
            return self._states.get(job_id)

    def ensure_state(self, job_id: str, batch_id: str | None = None) -> RuntimeState:
        with self._lock:
            state = self._states.get(job_id)
            if state is None:
                state = RuntimeState(
                    job_id=job_id,
                    batch_id=batch_id,
                    status="pending",
                    stages={stage: StageState(name=stage) for stage in STAGES},
                )
                self._states[job_id] = state
            elif batch_id is not None and state.batch_id is None:
                state.batch_id = batch_id
            return state

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

    def initialize_resource_progress(self, job_id: str, resources: list[Any] | dict[str, int]) -> None:
        with self._lock:
            state = self._states[job_id]
            state.resource_progress = {}
            if isinstance(resources, dict):
                for resource_type, total in resources.items():
                    state.resource_progress[resource_type] = {
                        "total": total,
                        "processed": 0,
                        "validation_processed": 0,
                        "warnings": 0,
                        "rejected": 0,
                    }
            else:
                for resource in resources:
                    entry = state.resource_progress.setdefault(
                        resource.resource_type,
                        {"total": 0, "processed": 0, "validation_processed": 0, "warnings": 0, "rejected": 0},
                    )
                    entry["total"] += 1

    def record_resource_progress(self, job_id: str, resource_type: str, status: str, processed: bool = True) -> None:
        with self._lock:
            entry = self._states[job_id].resource_progress.setdefault(
                resource_type,
                {"total": 0, "processed": 0, "validation_processed": 0, "warnings": 0, "rejected": 0},
            )
            entry["status"] = status
            if processed:
                entry["processed"] += 1
            elif status in {"validated", "unavailable", "partial", "failed"}:
                entry["validation_processed"] += 1
            if status in {"unavailable", "partial"}:
                entry["warnings"] += 1
            if status == "failed":
                entry["rejected"] += 1

    def count_resource(self, job_id: str, resource_type: str) -> None:
        with self._lock:
            entry = self._states[job_id].resource_progress.setdefault(
                resource_type,
                {"total": 0, "processed": 0, "validation_processed": 0, "warnings": 0, "rejected": 0},
            )
            entry["total"] += 1


job_manager = JobManager()


def _extract_denormalized(session: Any, row: Any, resource: Any) -> None:
    extractors = {
        "Patient": extract_patient_from_fhir,
        "Encounter": extract_encounter_from_fhir,
        "Condition": extract_condition_from_fhir,
        "Observation": extract_observation_from_fhir,
        "Procedure": extract_procedure_from_fhir,
        "MedicationRequest": extract_medication_from_fhir,
        "Immunization": extract_immunization_from_fhir,
        "Location": extract_location_from_fhir,
        "Coverage": extract_coverage_from_fhir,
        "AllergyIntolerance": extract_additional_resource,
        "Basic": extract_additional_resource,
        "Claim": extract_additional_resource,
        "Device": extract_additional_resource,
        "ImagingStudy": extract_additional_resource,
        "Organization": extract_additional_resource,
        "Practitioner": extract_additional_resource,
        "SupplyDelivery": extract_additional_resource,
    }
    extractor = extractors.get(resource.resource_type)
    if extractor:
        extractor(session, row, resource.fhir)


def _run_streaming_conversion(job_id: str, input_dir: str, adapter: Any) -> None:
    stage = job_manager.set_stage
    started_at = time.time()
    issue_buffer: list[Any] = []
    warning_count = 0
    error_count = 0
    mapping_seconds = 0.0
    validation_seconds = 0.0
    persistence_seconds = 0.0

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
        "adapter": adapter.adapter_name,
        "streaming": True,
        "batch_size": RESOURCE_BATCH_SIZE,
    }

    def collect_issue(issue: Any) -> None:
        nonlocal warning_count, error_count
        issue_buffer.append(issue)
        # Safely resolve resource type from issue's source file
        issue_resource_type = resolve_resource_type(issue.source_file)
        if issue_resource_type:
            job_manager.record_resource_progress(job_id, issue_resource_type, "failed")
        if issue.severity == "warning":
            warning_count += 1
        elif issue.severity == "error":
            error_count += 1

    try:
        stage(job_id, "csv_input", "running", f"Streaming input using '{adapter.adapter_name}' adapter")
        metadata, records = adapter.stream(input_dir, collect_issue)
        summary["total_source_records"] = sum(metadata.row_counts.values())
        summary["patients"] = metadata.row_counts.get("patients.csv", 0)
        summary["files"] = {
            "found": metadata.found_files,
            "missing_required": metadata.missing_required_files,
            "row_counts": metadata.row_counts,
        }
        stage(job_id, "csv_input", "completed", f"Discovered {len(metadata.found_files)} supported files")

        resource_types: dict[str, int] = {}
        for filename, count in metadata.row_counts.items():
            # Safely resolve resource type from filename
            resource_type = resolve_resource_type(filename)
            if resource_type:
                resource_types[resource_type] = resource_types.get(resource_type, 0) + count
        job_manager.initialize_resource_progress(
            job_id,
            {resource_type: total for resource_type, total in resource_types.items() if total},
        )
        stage(job_id, "mapping", "running", "Mapping and persisting records in bounded batches")
        stage(job_id, "validation", "running", "Validating resources during streaming")

        coverage_records: list[Any] = []
        batch: list[Any] = []
        mapped_count = 0
        validated_count = 0

        def persist_batch(session: Any) -> None:
            nonlocal batch, validated_count, error_count, validation_seconds, persistence_seconds
            if not batch and not issue_buffer:
                return
            persist_row_issues(session, job_id, issue_buffer)
            issue_buffer.clear()
            valid_batch: list[Any] = []
            validation_start = time.perf_counter()
            for resource in batch:
                try:
                    normalize_resource(resource)
                    valid_batch.append(resource)
                    validated_count += 1
                    job_manager.record_resource_progress(job_id, resource.resource_type, "validated", processed=False)
                except ValidationError as exc:
                    issue = validation_issue(resource, exc)
                    persist_row_issues(session, job_id, [issue])
                    error_count += 1
                    job_manager.record_resource_progress(job_id, resource.resource_type, "failed", processed=False)
            validation_seconds += time.perf_counter() - validation_start
            persistence_start = time.perf_counter()
            if valid_batch:
                # Upsert validated resources with correct validation status
                bulk_upsert_resources(
                    session,
                    job_id=job_id,
                    batch_id=job_manager.ensure_state(job_id).batch_id,
                    resources=valid_batch,
                    validation_status="validated",
                    lifecycle_status="validated",
                    persisted=True,
                )
                session.flush()
            session.flush()
            session.commit()
            persistence_seconds += time.perf_counter() - persistence_start
            batch = []

        with session_scope() as session:
            for record in records:
                if record.source_file == "coverages.csv":
                    coverage_records.append(record)
                    continue
                mapping_start = time.perf_counter()
                resource, issues = map_source_record(record, validate=False)
                mapping_seconds += time.perf_counter() - mapping_start
                issue_buffer.extend(issues)
                for issue in issues:
                    if issue.severity == "warning":
                        warning_count += 1
                    elif issue.severity == "error":
                        error_count += 1
                # Safely resolve resource type from source file or record data
                resource_type = resolve_resource_type(record.source_file, record.data)
                if not resource_type:
                    # Unable to determine resource type - record as failed
                    continue
                # Count resources from adapter sources (non-CSV files)
                if record.source_file.startswith(("fhir:", "ccda:", "claims837:", "ncpdp:")):
                    job_manager.count_resource(job_id, resource_type)
                if resource:
                    job_manager.record_resource_progress(job_id, resource_type, "mapped")
                    batch.append(resource)
                    mapped_count += 1
                else:
                    job_manager.record_resource_progress(job_id, resource_type, "failed")
                if len(batch) >= RESOURCE_BATCH_SIZE:
                    persist_batch(session)

            # Process coverage records grouped by patient to avoid loading all in memory
            # Sort by patient_id so groupby can group contiguous records efficiently
            coverage_records_sorted = sorted(coverage_records, key=lambda r: r.patient_id or "")
            for patient_id, patient_coverage_iter in groupby(coverage_records_sorted, key=lambda r: r.patient_id):
                # Convert iterator to list only for current patient's records (not all records)
                patient_coverage_list = list(patient_coverage_iter)
                for record in patient_coverage_list:
                    mapping_start = time.perf_counter()
                    # Pass only current patient's coverage records, not all coverage records
                    resource, issues = map_source_record(record, patient_coverage_list, validate=False)
                    mapping_seconds += time.perf_counter() - mapping_start
                    issue_buffer.extend(issues)
                    for issue in issues:
                        if issue.severity == "warning":
                            warning_count += 1
                        elif issue.severity == "error":
                            error_count += 1
                    if resource:
                        batch.append(resource)
                        mapped_count += 1
                    else:
                        job_manager.record_resource_progress(job_id, "Coverage", "failed")
                    if len(batch) >= RESOURCE_BATCH_SIZE:
                        persist_batch(session)
            persist_batch(session)

        summary["total_source_records"] = sum(metadata.row_counts.values())
        summary["resources_generated"] = mapped_count
        summary["resources_validated"] = validated_count
        summary["resources_persisted"] = validated_count
        summary["warning_count"] = warning_count
        summary["error_count"] = error_count
        summary["timings"] = {
            "mapping_seconds": round(mapping_seconds, 3),
            "validation_seconds": round(validation_seconds, 3),
            "database_insertion_seconds": round(persistence_seconds, 3),
            "csv_reading_seconds": round(time.time() - started_at - mapping_seconds - validation_seconds - persistence_seconds, 3),
        }
        stage(job_id, "mapping", "completed", f"Mapped {mapped_count} resources")
        stage(job_id, "validation", "completed", f"Validated {validated_count} resources during streaming")
        stage(job_id, "fhir_generation", "completed", f"Generated {mapped_count} resources without retaining the full dataset")
        stage(job_id, "postgresql_storage", "completed", f"Persisted {validated_count} resources during ingestion")

        final_status = "partially_completed" if error_count or validated_count < mapped_count else "completed"
        summary["timelogs"] = {
            "conversion_started_at": started_at,
            "conversion_completed_at": time.time(),
            "conversion_elapsed_seconds": round(time.time() - started_at, 3),
        }
        with session_scope() as session:
            complete_job(session, job_id, final_status, summary)
        job_manager.set_job_status(job_id, final_status)
        _emit(f"[pipeline] job={job_id} streaming_conversion_finished status={final_status} resources={mapped_count}")
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error("[pipeline] job=%s streaming_conversion_failed error_type=%s", job_id, error_type)
        with session_scope() as session:
            summary["error_count"] = error_count + 1
            summary["error"] = "Streaming conversion failed"
            summary["error_type"] = error_type
            complete_job(session, job_id, "failed", summary)
        job_manager.set_job_status(job_id, "failed")


def run_conversion(job_id: str, input_dir: str, adapter_name: str = "synthea_csv") -> None:
    stage = job_manager.set_stage
    job_manager.set_job_status(job_id, "running")
    conversion_started_at = time.time()
    _emit(f"[pipeline] job={job_id} conversion_started adapter={adapter_name}")

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
        "adapter": adapter_name,
    }

    try:
        with session_scope() as session:
            start_job(session, job_id)

        stage(job_id, "csv_input", "running", f"Parsing input source using '{adapter_name}' adapter")
        
        # Get adapter from registry
        adapter = registry.get_adapter(adapter_name)
        if adapter is None:
            stage(job_id, "csv_input", "failed", f"Adapter '{adapter_name}' not found. Available: {registry.list_adapters()}")
            job_manager.set_job_status(job_id, "failed")
            with session_scope() as session:
                complete_job(session, job_id, "failed", {
                    **summary,
                    "error": f"Adapter '{adapter_name}' not registered"
                })
            return

        if not settings.validation_require_external:
            _run_streaming_conversion(job_id, input_dir, adapter)
            return
        
        # Parse using selected adapter
        parsed = adapter.parse(input_dir)
        summary["total_source_records"] = parsed.total_rows
        summary["files"] = {
            "found": parsed.metadata.found_files,
            "missing_required": parsed.metadata.missing_required_files,
            "row_counts": parsed.metadata.row_counts,
        }
        summary["patients"] = len(parsed.records_by_file.get("patients.csv", []))
        stage(job_id, "csv_input", "completed", f"Discovered {len(parsed.metadata.found_files)} supported files")

        stage(job_id, "mapping", "running", "Applying CSV-to-FHIR mapping rules")
        resource_types = {
            "Patient": len(parsed.records_by_file.get("patients.csv", [])),
            "Encounter": len(parsed.records_by_file.get("encounters.csv", [])),
            "Condition": len(parsed.records_by_file.get("conditions.csv", [])),
            "Observation": len(parsed.records_by_file.get("observations.csv", [])),
            "Procedure": len(parsed.records_by_file.get("procedures.csv", [])),
            "MedicationRequest": len(parsed.records_by_file.get("medications.csv", [])),
            "Immunization": len(parsed.records_by_file.get("immunizations.csv", [])),
            "CarePlan": len(parsed.records_by_file.get("careplans.csv", [])),
            "Claim": len(parsed.records_by_file.get("claims.csv", [])),
            "Coverage": len(parsed.records_by_file.get("coverages.csv", [])),
        }
        job_manager.initialize_resource_progress(
            job_id,
            {resource_type: total for resource_type, total in resource_types.items() if total},
        )
        if not settings.validation_require_external:
            stage(job_id, "validation", "running", "Validating resources during mapping")

        streamed_validated_count = 0

        def persist_local_resource(resource: Any) -> None:
            nonlocal streamed_validated_count
            streamed_validated_count += 1
            job_manager.record_resource_progress(job_id, resource.resource_type, "validated", processed=False)

        mapped = map_dataset(
            parsed,
            lambda resource_type: job_manager.record_resource_progress(job_id, resource_type, "mapped"),
            resource_callback=persist_local_resource,
            validate=not settings.validation_require_external,
        )

        stage(job_id, "mapping", "completed", f"Mapped {len(mapped.resources)} resources")

        all_issues = [*parsed.issues, *mapped.issues]
        if not settings.validation_require_external:
            summary["resources_validated"] = streamed_validated_count
            stage(job_id, "validation", "completed", f"Validated {streamed_validated_count} resources during mapping")

        stage(job_id, "fhir_generation", "running", "Generating FHIR resources and bundles")
        bundles = bundles_by_patient(mapped.resources)
        summary["bundles"] = bundles
        summary["resources_generated"] = len(mapped.resources)
        stage(job_id, "fhir_generation", "completed", f"Generated {len(bundles)} patient bundles")

        validated_count = streamed_validated_count
        if settings.validation_require_external:
            stage(job_id, "validation", "running", "Validating FHIR resources")
            validated_count = 0
            validation_results = validate_resources(mapped.resources)
            try:
                with session_scope() as session:
                    persist_row_issues(session, job_id, all_issues)
                    resource_count = 0
                    _emit(f"[pipeline] job={job_id} validation_external: starting to upsert {len(mapped.resources)} resources")
                    # Collect resources by type for batch extraction
                    resources_by_type_for_extraction: dict[str, list[tuple[FhirResource, dict[str, Any]]]] = {}
                    
                    for resource, result in zip(mapped.resources, validation_results, strict=True):
                        validation_status = result.status
                        lifecycle_status = "validated" if result.status == "validated" else "quarantined"
                        if any(msg.severity == "error" for msg in result.messages):
                            lifecycle_status = "rejected"

                        row = upsert_resource(
                            session,
                            job_id=job_id,
                            batch_id=job_manager.ensure_state(job_id).batch_id,
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

                        # Collect for batch extraction
                        resources_by_type_for_extraction.setdefault(resource.resource_type, []).append((row, resource.fhir))

                        persist_validation_messages(
                            session,
                            job_id=job_id,
                            batch_id=job_manager.ensure_state(job_id).batch_id,
                            resource_pk=row.id,
                            resource_type=resource.resource_type,
                            resource_id=resource.resource_id,
                            source_file=resource.source_file,
                            source_record_id=resource.source_record_id,
                            validation=result,
                        )

                        if result.status == "validated":
                            validated_count += 1
                        job_manager.record_resource_progress(job_id, resource.resource_type, result.status, processed=False)
                        resource_count += 1
                        if resource_count % 10000 == 0:
                            _emit(f"[pipeline] job={job_id} validation_external: upserted {resource_count}/{len(mapped.resources)}")
                        if resource_count % RESOURCE_BATCH_SIZE == 0:
                            session.flush()
                            session.commit()
                            _emit(f"[pipeline] job={job_id} validation_external: committed {resource_count} resources")
                    
                    _emit(f"[pipeline] job={job_id} validation_external: batch-extracting denormalized data")
                    batch_extract_denormalized(session, resources_by_type_for_extraction)
                    session.flush()
                    _emit(f"[pipeline] job={job_id} validation_external: batch extraction completed")
                    
                    _emit(f"[pipeline] job={job_id} validation_external: completed upsert of {resource_count} resources")
            except Exception as e:
                _emit(f"[pipeline] job={job_id} ERROR in validation_external: {type(e).__name__}: {e}")
                raise
        else:
            try:
                with session_scope() as session:
                    persist_row_issues(session, job_id, all_issues)
                    resource_count = 0
                    _emit(f"[pipeline] job={job_id} validation_no_external: starting to upsert {len(mapped.resources)} resources")
                    for resource in mapped.resources:
                        upsert_resource(
                            session,
                            job_id=job_id,
                            batch_id=job_manager.ensure_state(job_id).batch_id,
                            resource_type=resource.resource_type,
                            resource_id=resource.resource_id,
                            patient_id=resource.patient_id,
                            source_file=resource.source_file,
                            source_record_id=resource.source_record_id,
                            source_fingerprint=resource.source_fingerprint,
                            resource_json=resource.fhir,
                            validation_status="validated",
                            lifecycle_status="validated",
                            metadata_json={"references": resource.references},
                            flush=False,
                        )
                        
                        # Extract denormalized data from FHIR resources (deferred flush)
                        # Note: We pass None for FhirResource since flush=False means it's not in DB yet
                        # Extraction will happen after session.flush()
                        
                        resource_count += 1
                        if resource_count % 10000 == 0:
                            _emit(f"[pipeline] job={job_id} validation_no_external: upserted {resource_count}/{len(mapped.resources)}")
                        if resource_count % RESOURCE_BATCH_SIZE == 0:
                            session.flush()
                            session.commit()
                            _emit(f"[pipeline] job={job_id} validation_no_external: committed {resource_count} resources")
                    
                    _emit(f"[pipeline] job={job_id} validation_no_external: flushing {resource_count} resources to database")
                    session.flush()
                    _emit(f"[pipeline] job={job_id} validation_no_external: flush completed successfully")
                    
                    # Batch-fetch all FhirResource rows to avoid N+1 queries
                    _emit(f"[pipeline] job={job_id} validation_no_external: batch-fetching {len(mapped.resources)} FHIR resources")
                    resource_keys = [(resource.resource_type, resource.resource_id) for resource in mapped.resources]
                    fhir_resources_by_key = batch_fetch_fhir_resources(session, resource_keys)
                    _emit(f"[pipeline] job={job_id} validation_no_external: fetched {len(fhir_resources_by_key)} FHIR resources")
                    
                    # Now perform batch extraction on flushed resources
                    _emit(f"[pipeline] job={job_id} validation_no_external: batch-extracting denormalized data")
                    # Collect resources by type for batch extraction
                    resources_by_type_for_extraction: dict[str, list[tuple[FhirResource, dict[str, Any]]]] = {}
                    extraction_count = 0
                    for resource in mapped.resources:
                        fhir_res = fhir_resources_by_key.get((resource.resource_type, resource.resource_id))
                        if fhir_res:
                            resources_by_type_for_extraction.setdefault(resource.resource_type, []).append((fhir_res, resource.fhir))
                        extraction_count += 1
                    
                    batch_extract_denormalized(session, resources_by_type_for_extraction)
                    
                    # Flush in batches to avoid memory issues
                    session.flush()
                    if extraction_count % RESOURCE_BATCH_SIZE == 0:
                        session.commit()
                        _emit(f"[pipeline] job={job_id} validation_no_external: committed extraction {extraction_count}/{len(mapped.resources)}")
                    
                    session.flush()
                    _emit(f"[pipeline] job={job_id} validation_no_external: extraction completed")
            except Exception as e:
                _emit(f"[pipeline] job={job_id} ERROR in validation_no_external: {type(e).__name__}: {e}")
                raise

        summary["resources_validated"] = validated_count
        if settings.validation_require_external:
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
        error_type = type(exc).__name__
        stage(job_id, "csv_input", "failed", "Conversion failed")
        logger.error("[pipeline] job=%s conversion_failed error_type=%s", job_id, error_type)
        with session_scope() as session:
            summary["error_count"] = summary.get("error_count", 0) + 1
            summary["error"] = "Conversion failed"
            summary["error_type"] = error_type
            summary["timelogs"] = {
                "conversion_started_at": conversion_started_at,
                "conversion_completed_at": time.time(),
                "conversion_elapsed_seconds": round(time.time() - conversion_started_at, 3),
            }
            complete_job(session, job_id, "failed", summary)
        job_manager.set_job_status(job_id, "failed")


def run_persist_stage(job_id: str) -> int:
    job_manager.ensure_state(job_id)
    stage = job_manager.set_stage
    stage(job_id, "postgresql_storage", "running", "Marking validated resources as persisted")
    _emit(f"[pipeline] job={job_id} persist_stage_started")
    with session_scope() as session:
        persisted = mark_validated_resources_persisted(session, job_id)

        from sqlalchemy import func, select
        from .models import FhirResource

        total_persisted = (
            session.execute(
                select(func.count())
                .select_from(FhirResource)
                .where((FhirResource.job_id == job_id) & (FhirResource.persisted.is_(True)))
            ).scalar()
            or 0
        )

        from .models import ConversionJob

        job = session.get(ConversionJob, job_id)
        if job:
            summary = job.summary or {}
            summary["resources_persisted"] = total_persisted
            job.summary = summary
            job.resources_persisted = total_persisted
            if job.status == "completed" and persisted == 0:
                job.status = "partially_completed"

    stage(
        job_id,
        "postgresql_storage",
        "completed",
        f"Persisted {persisted} resources (total persisted: {total_persisted})",
    )
    _emit(
        f"[pipeline] job={job_id} persist_stage_finished "
        f"persisted_delta={persisted} persisted_total={total_persisted}"
    )
    return total_persisted


def launch_job(job_id: str, input_dir: str, adapter_name: str = "synthea_csv") -> None:
    raise RuntimeError(
        "launch_job is retired. Start ingestion by enqueueing RabbitMQ messages via /jobs/start queued mode."
    )

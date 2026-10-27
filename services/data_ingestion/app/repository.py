from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import ConversionJob, FhirResource, SourceBatch, ValidationIssue
from .synthea import RowIssue
from .validator import ResourceValidationResult


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _touch(row: Any) -> None:
    row.updated_at = dt.datetime.now(dt.timezone.utc)


def extract_reference_id(reference_dict: dict[str, Any] | None) -> str | None:
    """Extract resource ID from FHIR reference dict.
    
    Safely parses FHIR references to extract stable resource identifiers.
    Never uses display names as identifiers.
    
    Handles:
    - Relative refs: {"reference": "Patient/patient-123"} → "patient-123"
    - Absolute refs: {"reference": "http://example.com/Patient/patient-123"} → "patient-123"
    - Version refs: {"reference": "Patient/patient-123/_history/2"} → "patient-123"
    - Contained refs: {"reference": "#contained-123"} → "#contained-123" (passed through)
    - Missing refs: {} or None → None
    
    Args:
        reference_dict: FHIR reference object, may be None or empty
    
    Returns:
        Resource ID (logical ID portion) or None if reference is missing/malformed
    """
    if not reference_dict or not isinstance(reference_dict, dict):
        return None
    
    ref = reference_dict.get("reference")
    if not ref or not isinstance(ref, str):
        return None
    
    # Contained resource reference - return as-is (starts with #)
    if ref.startswith("#"):
        return ref
    
    # Remove base URL if present (absolute reference)
    if ref.startswith(("http://", "https://")):
        # Extract last path component after splitting by /
        ref = ref.split("/")[-1]
    
    # Remove version suffix if present (e.g., Patient/123/_history/2 → Patient/123)
    if "/_history/" in ref:
        ref = ref.split("/_history/")[0]
    
    # Extract ID from resource_type/ID format (e.g., Patient/123 → 123)
    if "/" in ref:
        parts = ref.split("/")
        return parts[-1] if len(parts) >= 2 and parts[-1] else None
    
    return ref if ref else None


def to_iso_date(value: str | None) -> dt.date | None:
    """Parse FHIR date or dateTime to Python date object.
    
    Handles multiple FHIR date/dateTime formats safely:
    - Date-only: "2025-04-15" → date(2025, 4, 15)
    - DateTime with UTC: "2025-04-15T10:30:00Z" → date(2025, 4, 15)
    - DateTime with offset: "2025-04-15T10:30:00-05:00" → date(2025, 4, 15)
    - Invalid or missing: None → None (no exception raised)
    
    Args:
        value: FHIR date or dateTime string
    
    Returns:
        Python date object or None if parsing fails
    """
    if not value or not isinstance(value, str):
        return None
    
    try:
        # Try full datetime parsing first (handles Z and timezone offsets)
        dt_obj = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt_obj.date()
    except (ValueError, AttributeError, TypeError):
        pass
    
    try:
        # Try date-only parsing (YYYY-MM-DD)
        return dt.date.fromisoformat(value)
    except (ValueError, AttributeError, TypeError):
        pass
    
    return None


def batch_fetch_fhir_resources(
    session: Session,
    resource_keys: list[tuple[str, str]],
) -> dict[tuple[str, str], FhirResource]:
    """
    Batch-fetch FhirResource rows by (resource_type, resource_id) composite key.
    
    Prevents collisions: Patient#123 and Encounter#123 are distinct resources.
    
    Args:
        session: SQLAlchemy session
        resource_keys: List of (resource_type, resource_id) tuples to fetch
    
    Returns:
        Dict mapping (resource_type, resource_id) -> FhirResource row
    """
    if not resource_keys:
        return {}
    
    rows = session.execute(
        select(FhirResource).where(
            tuple_(FhirResource.resource_type, FhirResource.resource_id).in_(resource_keys)
        )
    ).scalars().all()
    
    return {(row.resource_type, row.resource_id): row for row in rows}


def extract_additional_resource(session: Session, fhir_resource: FhirResource, resource_json: dict[str, Any]) -> None:
    from .models import AllergyIntolerance, BasicResource, Claim, Device, ImagingStudy, Organization, Practitioner, SupplyDelivery

    models = {
        "AllergyIntolerance": AllergyIntolerance,
        "Basic": BasicResource,
        "Claim": Claim,
        "Device": Device,
        "ImagingStudy": ImagingStudy,
        "Organization": Organization,
        "Practitioner": Practitioner,
        "SupplyDelivery": SupplyDelivery,
    }
    model = models.get(fhir_resource.resource_type)
    resource_id = resource_json.get("id")
    if model is None or not resource_id:
        return

    subject = _as_dict(resource_json.get("patient") or resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    code_data = _as_dict(resource_json.get("code"))
    coding = _as_list(code_data.get("coding"))
    code_entry = _as_dict(coding[0]) if coding else {}
    status_data = _as_dict(resource_json.get("clinicalStatus"))
    status_coding = _as_list(status_data.get("coding"))
    status = _as_dict(status_coding[0]).get("code") if status_coding else resource_json.get("status")
    values: dict[str, Any] = {
        "resource_id": resource_id,
        "patient_id": patient_id,
        "source_file": fhir_resource.source_file,
        "fhir_resource_pk": fhir_resource.id,
        "resource_json": resource_json,
    }
    if model is AllergyIntolerance:
        values.update({"clinical_status": status, "code": code_entry.get("code"), "display": code_entry.get("display") or code_data.get("text")})
    elif model is Claim:
        values["status"] = status
        created = resource_json.get("created")
        values["created_date"] = to_iso_date(created)
    elif model is Device:
        values["status"] = status
        values.update({"device_code": code_entry.get("code"), "display": code_entry.get("display") or code_data.get("text")})
    elif model is ImagingStudy:
        values["status"] = status
        started = resource_json.get("started")
        values["started_at"] = dt.datetime.fromisoformat(started.replace("Z", "+00:00")) if started else None
    elif model is Organization:
        values = {key: values[key] for key in ("resource_id", "source_file", "fhir_resource_pk", "resource_json")}
        values["name"] = resource_json.get("name")
    elif model is Practitioner:
        names = _as_list(resource_json.get("name"))
        values = {key: values[key] for key in ("resource_id", "source_file", "fhir_resource_pk", "resource_json")}
        values.update({"name": _as_dict(names[0]).get("text") if names else None, "gender": resource_json.get("gender")})
    elif model is SupplyDelivery:
        values["status"] = status
        supplied = _as_dict(resource_json.get("suppliedItem"))
        quantity = _as_dict(supplied.get("quantity"))
        item = _as_dict(supplied.get("itemCodeableConcept"))
        values.update({"quantity": quantity.get("value"), "display": item.get("text")})
    existing = session.execute(select(model).where(model.resource_id == resource_id)).scalar_one_or_none()
    if existing:
        _touch(existing)
        for key, value in values.items():
            setattr(existing, key, value)
    else:
        session.add(model(**values))


def create_job(session: Session, input_dir: str, adapter_name: str = "synthea_csv") -> ConversionJob:
    source_system = "Synthea" if adapter_name == "synthea_csv" else "EHR/HIE"
    source_format = {
        "synthea_csv": "csv",
        "ehr_fhir_r4": "fhir_r4",
        "ccda_hie": "c_cda",
        "claims_837": "x12_837",
        "ncpdp_pharmacy": "ncpdp",
    }.get(adapter_name, "unknown")
    batch = SourceBatch(
        source_system=source_system,
        source_format=source_format,
        adapter_name=adapter_name,
        input_source=input_dir,
        status="pending",
    )
    session.add(batch)
    session.flush()
    job = ConversionJob(input_dir=input_dir, status="pending", summary={}, batch_id=batch.id)
    session.add(job)
    session.flush()
    return job


def clear_jobs_for_input_dir(session: Session, input_dir: str) -> int:
    jobs = session.execute(
        select(ConversionJob).where(ConversionJob.input_dir == input_dir)
    ).scalars().all()
    for job in jobs:
        resource_ids = select(FhirResource.id).where(FhirResource.job_id == job.id)
        session.execute(delete(ValidationIssue).where(ValidationIssue.job_id == job.id))
        session.execute(delete(ValidationIssue).where(ValidationIssue.resource_pk.in_(resource_ids)))
        session.execute(delete(FhirResource).where(FhirResource.job_id == job.id))
        session.delete(job)
    return len(jobs)


def start_job(session: Session, job_id: str) -> ConversionJob:
    job = session.get(ConversionJob, job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")
    job.status = "running"
    job.started_at = dt.datetime.now(dt.timezone.utc)
    if job.source_batch:
        job.source_batch.status = "running"
        job.source_batch.started_at = job.started_at
    return job


def complete_job(session: Session, job_id: str, status: str, summary: dict[str, Any]) -> ConversionJob:
    job = session.get(ConversionJob, job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    job.status = status
    job.completed_at = dt.datetime.now(dt.timezone.utc)
    job.summary = summary
    job.total_source_records = summary.get("total_source_records", 0)
    job.resources_generated = summary.get("resources_generated", 0)
    job.resources_validated = summary.get("resources_validated", 0)
    job.resources_persisted = summary.get("resources_persisted", 0)
    job.warning_count = summary.get("warning_count", 0)
    job.error_count = summary.get("error_count", 0)
    if job.source_batch:
        job.source_batch.status = status
        job.source_batch.resource_count = job.resources_generated
        job.source_batch.resource_count_validated = job.resources_validated
        job.source_batch.completed_at = job.completed_at
    return job


def persist_row_issues(session: Session, job_id: str, issues: list[RowIssue]) -> None:
    for issue in issues:
        session.add(
            ValidationIssue(
                job_id=job_id,
                source_file=issue.source_file,
                source_record_id=issue.source_record_id,
                severity=issue.severity,
                issue_category="mapping" if issue.code not in {"duplicate_source_id", "missing_header"} else "input",
                error_code=issue.code,
                field_path=f"{issue.source_file}:{issue.row_number}",
                diagnostic=issue.message,
            )
        )


def upsert_resource(
    session: Session,
    *,
    job_id: str,
    batch_id: str | None = None,
    resource_type: str,
    resource_id: str,
    patient_id: str | None,
    source_file: str,
    source_record_id: str,
    source_fingerprint: str,
    resource_json: dict[str, Any],
    validation_status: str,
    lifecycle_status: str,
    metadata_json: dict[str, Any],
    flush: bool = True,
    persisted: bool = False,
) -> FhirResource:
    existing = session.execute(
        select(FhirResource).where(
            (FhirResource.resource_type == resource_type) & (FhirResource.resource_id == resource_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        row = FhirResource(
            job_id=job_id,
            batch_id=batch_id,
            resource_type=resource_type,
            resource_id=resource_id,
            patient_id=patient_id,
            source_file=source_file,
            source_record_id=source_record_id,
            source_fingerprint=source_fingerprint,
            resource_json=resource_json,
            validation_status=validation_status,
            lifecycle_status=lifecycle_status,
            metadata_json=metadata_json,
            persisted=persisted,
        )
        session.add(row)
        if flush:
            session.flush()
        return row

    # Safe replacement policy: never downgrade a validated/persisted resource to invalid.
    existing.job_id = job_id
    existing.batch_id = batch_id
    existing.patient_id = patient_id
    existing.source_file = source_file
    existing.source_record_id = source_record_id
    existing.source_fingerprint = source_fingerprint

    old_status = existing.lifecycle_status
    downgrade = old_status in {"validated", "persisted"} and lifecycle_status in {"generated", "rejected", "quarantined"}

    if not downgrade:
        existing.resource_json = resource_json
        existing.validation_status = validation_status
        existing.lifecycle_status = lifecycle_status
        existing.metadata_json = metadata_json
    existing.persisted = persisted or existing.persisted
    _touch(existing)

    if flush:
        session.flush()
    return existing


def bulk_upsert_resources(
    session: Session,
    *,
    job_id: str,
    batch_id: str | None,
    resources: list[Any],
    validation_status: str = "generated",
    lifecycle_status: str = "generated",
    persisted: bool = False,
) -> None:
    """Upsert a resource batch in one PostgreSQL statement when available.
    
    Args:
        session: SQLAlchemy session
        job_id: Ingestion job ID
        batch_id: Optional source batch ID
        resources: List of ResourceEnvelope objects to upsert
        validation_status: Validation state (default: "generated", not yet validated)
        lifecycle_status: Lifecycle state (default: "generated")
        persisted: Whether resources have been persisted (default: False)
    
    Note:
        Callers MUST NOT assume resources are validated simply by inserting them.
        Pass actual validation_status from validation pipeline, not hardcoded values.
        
        Deduplicates resources by (resource_type, resource_id) to handle C-CDA files
        with duplicate IDs within the same batch - keeps the last occurrence.
    """
    if not resources:
        return

    # Deduplicate resources by (resource_type, resource_id), keeping the last occurrence
    seen: dict[tuple[str, str], Any] = {}
    for resource in resources:
        key = (resource.resource_type, resource.resource_id)
        seen[key] = resource
    
    # Use deduplicated resources
    resources_dedup = list(seen.values())

    values = [
        {
            "job_id": job_id,
            "batch_id": batch_id,
            "resource_type": resource.resource_type,
            "resource_id": resource.resource_id,
            # For Patient resources, use the patient's own ID as patient_id
            # For other resources, use the extracted patient_id from subject reference
            "patient_id": resource.resource_id if resource.resource_type == "Patient" else resource.patient_id,
            "source_file": resource.source_file,
            "source_record_id": resource.source_record_id,
            "source_fingerprint": resource.source_fingerprint,
            "resource_json": resource.fhir,
            "validation_status": validation_status,
            "lifecycle_status": lifecycle_status,
            "persisted": persisted,
            "metadata_json": {"references": resource.references},
        }
        for resource in resources_dedup
    ]

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = pg_insert(FhirResource).values(values)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[FhirResource.resource_type, FhirResource.resource_id],
            set_={
                "job_id": excluded.job_id,
                "batch_id": excluded.batch_id,
                "patient_id": excluded.patient_id,
                "source_file": excluded.source_file,
                "source_record_id": excluded.source_record_id,
                "source_fingerprint": excluded.source_fingerprint,
                "resource_json": excluded.resource_json,
                "validation_status": excluded.validation_status,
                "lifecycle_status": excluded.lifecycle_status,
                "persisted": excluded.persisted,
                "metadata_json": excluded.metadata_json,
                "updated_at": func.now(),
            },
        )
        session.execute(statement)
        return
    
    # Fallback for non-PostgreSQL databases: use individual upserts
    # Note: This does NOT preserve downgrade protection; prefer PostgreSQL bulk for safety

    for resource in resources_dedup:
        # For Patient resources, use the patient's own ID as patient_id
        patient_id = resource.resource_id if resource.resource_type == "Patient" else resource.patient_id
        
        upsert_resource(
            session,
            job_id=job_id,
            batch_id=batch_id,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            patient_id=patient_id,
            source_file=resource.source_file,
            source_record_id=resource.source_record_id,
            source_fingerprint=resource.source_fingerprint,
            resource_json=resource.fhir,
            validation_status=validation_status,
            lifecycle_status=lifecycle_status,
            metadata_json={"references": resource.references},
            flush=False,
            persisted=persisted,
        )


def persist_validation_messages(
    session: Session,
    job_id: str,
    resource_pk: int | None,
    resource_type: str,
    resource_id: str,
    source_file: str | None,
    source_record_id: str | None,
    validation: ResourceValidationResult,
) -> None:
    for message in validation.messages:
        session.add(
            ValidationIssue(
                job_id=job_id,
                resource_pk=resource_pk,
                resource_type=resource_type,
                resource_id=resource_id,
                source_file=source_file,
                source_record_id=source_record_id,
                severity=message.severity,
                issue_category=message.category,
                error_code=message.code,
                field_path=message.path,
                diagnostic=message.diagnostics,
            )
        )


def mark_validated_resources_persisted(session: Session, job_id: str) -> int:
    """
    Mark all validated resources as persisted in the database.
    Uses batch updates to handle large datasets efficiently.
    """
    import logging
    logger = logging.getLogger("uvicorn.error")
    
    # First, check what resources exist for this job
    total_count = session.execute(
        select(func.count()).select_from(FhirResource).where(FhirResource.job_id == job_id)
    ).scalar() or 0
    
    # Check non-rejected resources
    non_rejected_count = session.execute(
        select(func.count()).select_from(FhirResource).where(
            (FhirResource.job_id == job_id) & 
            (FhirResource.lifecycle_status != "rejected")
        )
    ).scalar() or 0
    
    # Check already persisted
    already_persisted = session.execute(
        select(func.count()).select_from(FhirResource).where(
            (FhirResource.job_id == job_id) & 
            (FhirResource.persisted == True)
        )
    ).scalar() or 0
    
    logger.info(f"[persist] job={job_id} total={total_count} non_rejected={non_rejected_count} already_persisted={already_persisted}")
    
    # If no resources found, debug by checking validation_status values
    if total_count == 0:
        all_jobs = session.execute(select(func.count()).select_from(FhirResource)).scalar() or 0
        logger.info(f"[persist] ALERT: job={job_id} has 0 resources, but database has {all_jobs} total resources across all jobs")
        
        # Sample a few resources from ANY job to see structure
        sample = session.execute(select(FhirResource).limit(1)).scalar_one_or_none()
        if sample:
            logger.info(f"[persist] Sample resource: id={sample.id} job_id={sample.job_id} validation_status={sample.validation_status} lifecycle_status={sample.lifecycle_status} persisted={sample.persisted}")
        else:
            logger.info(f"[persist] Database is completely empty - no resources found anywhere")
        return 0
    
    # Update all non-rejected, non-persisted resources
    stmt = (
        update(FhirResource)
        .where(
            (FhirResource.job_id == job_id) &
            (FhirResource.lifecycle_status != "rejected") &
            (FhirResource.persisted == False)
        )
        .values(
            persisted=True,
            lifecycle_status=case(
                (FhirResource.validation_status == "validated", "persisted"),
                else_="persisted_with_warnings"
            )
        )
    )
    
    result = session.execute(stmt)
    newly_persisted = result.rowcount
    persisted = already_persisted + newly_persisted
    
    logger.info(
        f"[persist] job={job_id} newly_persisted={newly_persisted} total_persisted={persisted} records"
    )
    session.flush()
    return persisted


def list_jobs(session: Session) -> list[ConversionJob]:
    return session.execute(select(ConversionJob).order_by(ConversionJob.started_at.desc())).scalars().all()


def list_resources_for_job(session: Session, job_id: str) -> list[FhirResource]:
    return session.execute(select(FhirResource).where(FhirResource.job_id == job_id)).scalars().all()


def list_issues_for_job(session: Session, job_id: str) -> list[ValidationIssue]:
    return session.execute(select(ValidationIssue).where(ValidationIssue.job_id == job_id)).scalars().all()


def as_job_dict(job: ConversionJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "source_system": job.source_batch.source_system if job.source_batch else None,
        "source_format": job.source_batch.source_format if job.source_batch else None,
        "adapter_name": job.source_batch.adapter_name if job.source_batch else None,
        "input_dir": job.input_dir,
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "total_source_records": job.total_source_records,
        "resources_generated": job.resources_generated,
        "resources_validated": job.resources_validated,
        "resources_persisted": job.resources_persisted,
        "warning_count": job.warning_count,
        "error_count": job.error_count,
        "summary": job.summary or {},
    }


def as_resource_dict(resource: FhirResource) -> dict[str, Any]:
    return {
        "db_id": resource.id,
        "job_id": resource.job_id,
        "batch_id": resource.batch_id,
        "resource_type": resource.resource_type,
        "resource_id": resource.resource_id,
        "patient_id": resource.patient_id,
        "source_file": resource.source_file,
        "source_record_id": resource.source_record_id,
        "validation_status": resource.validation_status,
        "lifecycle_status": resource.lifecycle_status,
        "persisted": resource.persisted,
        "resource_json": resource.resource_json,
        "metadata_json": resource.metadata_json,
        "created_at": resource.created_at.isoformat() if resource.created_at else None,
        "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
    }


def as_issue_dict(issue: ValidationIssue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "job_id": issue.job_id,
        "resource_pk": issue.resource_pk,
        "resource_type": issue.resource_type,
        "resource_id": issue.resource_id,
        "source_file": issue.source_file,
        "source_record_id": issue.source_record_id,
        "severity": issue.severity,
        "issue_category": issue.issue_category,
        "error_code": issue.error_code,
        "field_path": issue.field_path,
        "diagnostic": issue.diagnostic,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
    }


def _get_existing_by_id(session: Session, model_class: type, id_field: str, id_values: list[str]) -> dict[str, Any]:
    """Efficiently fetch existing records by ID for any model.
    
    Helper to reduce duplicate SELECT + map logic across batch extraction functions.
    Returns empty dict if no IDs provided.
    
    Args:
        session: SQLAlchemy session
        model_class: Model class (Patient, Encounter, Location, etc.)
        id_field: Name of the ID field on the model (e.g., 'patient_id', 'encounter_id')
        id_values: List of ID values to fetch
    
    Returns:
        Dict mapping ID value to model instance for quick lookup
    """
    if not id_values:
        return {}
    
    id_attr = getattr(model_class, id_field)
    return {
        getattr(row, id_field): row 
        for row in session.execute(select(model_class).where(id_attr.in_(id_values))).scalars().all()
    }


def batch_extract_denormalized(
    session: Session,
    resources_by_type: dict[str, list[tuple[FhirResource, dict[str, Any]]]],
) -> None:
    """Batch-extract denormalized data from FHIR resources grouped by type.
    
    Processes Patient, Encounter, Condition, Observation, Procedure, MedicationRequest,
    Immunization, Location, and Coverage resources in bulk to avoid N+1 queries.
    
    Args:
        session: SQLAlchemy session
        resources_by_type: Dict mapping resource_type to list of (FhirResource, fhir_json) tuples
    """
    # Check if there's any work to do
    has_resources = any(resources for resources in resources_by_type.values())
    if not has_resources:
        return
    
    # Single flush before all batch extractions (optimization: reduces 8+ flushes to 1)
    session.flush()
    
    for resource_type, resources in resources_by_type.items():
        if not resources:
            continue
        
        if resource_type == "Patient":
            _batch_extract_patients(session, resources)
        elif resource_type == "Encounter":
            _batch_extract_encounters(session, resources)
        elif resource_type == "Condition":
            _batch_extract_conditions(session, resources)
        elif resource_type == "Observation":
            _batch_extract_observations(session, resources)
        elif resource_type == "Procedure":
            _batch_extract_procedures(session, resources)
        elif resource_type == "MedicationRequest":
            _batch_extract_medications(session, resources)
        elif resource_type == "Immunization":
            _batch_extract_immunizations(session, resources)
        elif resource_type == "Location":
            _batch_extract_locations(session, resources)
        elif resource_type == "Coverage":
            _batch_extract_coverages(session, resources)


def _batch_extract_patients(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Patient resources in one SELECT + one bulk INSERT/UPDATE."""
    from .models import Patient
    
    # Extract IDs for batch fetch
    patient_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Patient, "patient_id", patient_ids)
    
    # Process all resources
    for fhir_resource, resource_json in resources:
        patient_id = resource_json.get("id")
        if not patient_id:
            continue
        
        # Extract name
        name_parts = _as_list(resource_json.get("name"))
        name = None
        if name_parts and name_parts[0]:
            name_data = _as_dict(name_parts[0])
            name = name_data.get("text")
            if not name:
                given = _as_list(name_data.get("given"))
                family = name_data.get("family", "")
                name = f"{' '.join(given)} {family}".strip() if given else family
        
        birth_date = resource_json.get("birthDate")
        birth_date_obj = None
        if birth_date:
            try:
                birth_date_obj = dt.datetime.fromisoformat(birth_date).date()
            except (ValueError, AttributeError):
                pass
        
        gender = resource_json.get("gender")
        
        marital_status_data = _as_dict(resource_json.get("maritalStatus"))
        marital_coding = _as_list(marital_status_data.get("coding"))
        marital_status = _as_dict(marital_coding[0]).get("display") if marital_coding else None
        
        address_parts = _as_list(resource_json.get("address"))
        address = None
        if address_parts:
            addr = _as_dict(address_parts[0])
            lines = _as_list(addr.get("line"))
            city = addr.get("city", "")
            state = addr.get("state", "")
            address = f"{', '.join(lines)} {city} {state}".strip() if (lines or city) else None
        
        telecom_parts = _as_list(resource_json.get("telecom"))
        phone = None
        email = None
        for telecom in telecom_parts:
            if telecom.get("system") == "phone":
                phone = telecom.get("value")
            elif telecom.get("system") == "email":
                email = telecom.get("value")
        
        existing = existing_map.get(patient_id)
        if existing:
            _touch(existing)
            existing.name = name
            existing.birth_date = birth_date_obj
            existing.gender = gender
            existing.marital_status = marital_status
            existing.address = address
            existing.phone = phone
            existing.email = email
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Patient(
                patient_id=patient_id,
                name=name,
                birth_date=birth_date_obj,
                gender=gender,
                marital_status=marital_status,
                address=address,
                phone=phone,
                email=email,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_encounters(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Encounter resources in one SELECT + one bulk INSERT/UPDATE."""
    from .models import Encounter
    
    encounter_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Encounter, "encounter_id", encounter_ids)
    
    for fhir_resource, resource_json in resources:
        encounter_id = resource_json.get("id")
        if not encounter_id:
            continue
        
        subject = _as_dict(resource_json.get("subject"))
        patient_id = extract_reference_id(subject)
        
        encounter_type_codings = _as_list(resource_json.get("type"))
        encounter_type = None
        encounter_type_data = _as_dict(encounter_type_codings[0]) if encounter_type_codings else {}
        encounter_type_codes = _as_list(encounter_type_data.get("coding"))
        if encounter_type_codes:
            encounter_type = _as_dict(encounter_type_codes[0]).get("display")
        
        encounter_class = _as_dict(resource_json.get("class")).get("display")
        status = resource_json.get("status")
        
        period = _as_dict(resource_json.get("period"))
        start_date = period.get("start")
        end_date = period.get("end")
        
        start_datetime = None
        end_datetime = None
        if start_date:
            try:
                start_datetime = dt.datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        if end_date:
            try:
                end_datetime = dt.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        location = _as_list(resource_json.get("location"))
        facility = None
        if location:
            facility = _as_dict(location[0]).get("display")
        
        reason_coding = _as_list(resource_json.get("reasonCode"))
        reason_code = None
        reason_display = None
        if reason_coding:
            reason_data = _as_dict(reason_coding[0])
            reason_codes = _as_list(reason_data.get("coding"))
            reason_code = _as_dict(reason_codes[0]).get("code") if reason_codes else None
            reason_display = reason_data.get("text")
        
        existing = existing_map.get(encounter_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_type = encounter_type
            existing.encounter_class = encounter_class
            existing.status = status
            existing.start_date = start_datetime
            existing.end_date = end_datetime
            existing.facility = facility
            existing.reason_code = reason_code
            existing.reason_display = reason_display
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Encounter(
                encounter_id=encounter_id,
                patient_id=patient_id,
                encounter_type=encounter_type,
                encounter_class=encounter_class,
                status=status,
                start_date=start_datetime,
                end_date=end_datetime,
                facility=facility,
                reason_code=reason_code,
                reason_display=reason_display,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_conditions(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Condition resources."""
    from .models import Condition
    
    condition_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Condition, "condition_id", condition_ids)
    
    for fhir_resource, resource_json in resources:
        condition_id = resource_json.get("id")
        if not condition_id:
            continue
        
        subject = _as_dict(resource_json.get("subject"))
        patient_id = extract_reference_id(subject)
        
        encounter = _as_dict(resource_json.get("encounter"))
        encounter_id = extract_reference_id(encounter)
        
        code_data = _as_dict(resource_json.get("code"))
        code_coding = _as_list(code_data.get("coding"))
        code = None
        display = code_data.get("text")
        system = None
        if code_coding:
            code_data = _as_dict(code_coding[0])
            code = code_data.get("code")
            display = code_data.get("display") or display
            system = code_data.get("system")
        
        onset_datetime = resource_json.get("onsetDateTime")
        onset_date = None
        if onset_datetime:
            try:
                onset_date = dt.datetime.fromisoformat(onset_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        resolved_datetime = resource_json.get("abatementDateTime")
        resolved_date = None
        if resolved_datetime:
            try:
                resolved_date = dt.datetime.fromisoformat(resolved_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        clinical_status = _as_dict(resource_json.get("clinicalStatus"))
        status_coding = _as_list(clinical_status.get("coding"))
        status = _as_dict(status_coding[0]).get("display") if status_coding else None
        
        existing = existing_map.get(condition_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_id = encounter_id
            existing.code = code
            existing.display = display
            existing.system = system
            existing.onset_date = onset_date
            existing.resolved_date = resolved_date
            existing.status = status
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Condition(
                condition_id=condition_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                code=code,
                display=display,
                system=system,
                onset_date=onset_date,
                resolved_date=resolved_date,
                status=status,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_observations(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Observation resources."""
    from .models import Observation
    
    observation_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Observation, "observation_id", observation_ids)
    
    for fhir_resource, resource_json in resources:
        observation_id = resource_json.get("id")
        if not observation_id:
            continue
        
        subject = _as_dict(resource_json.get("subject"))
        patient_id = extract_reference_id(subject)
        
        encounter = _as_dict(resource_json.get("encounter"))
        encounter_id = extract_reference_id(encounter)
        
        code_data = _as_dict(resource_json.get("code"))
        code_coding = _as_list(code_data.get("coding"))
        code = None
        display = code_data.get("text")
        system = None
        if code_coding:
            code_data = _as_dict(code_coding[0])
            code = code_data.get("code")
            display = code_data.get("display") or display
            system = code_data.get("system")
        
        value = None
        unit = None
        value_quantity = _as_dict(resource_json.get("valueQuantity"))
        if value_quantity:
            value = str(value_quantity.get("value"))
            unit = value_quantity.get("unit")
        else:
            value_string = resource_json.get("valueString")
            if value_string:
                value = value_string
        
        effective_datetime = resource_json.get("effectiveDateTime")
        effective_date = None
        if effective_datetime:
            try:
                effective_date = dt.datetime.fromisoformat(effective_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        status = resource_json.get("status")
        
        existing = existing_map.get(observation_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_id = encounter_id
            existing.code = code
            existing.display = display
            existing.system = system
            existing.value = value
            existing.unit = unit
            existing.effective_date = effective_date
            existing.status = status
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Observation(
                observation_id=observation_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                code=code,
                display=display,
                system=system,
                value=value,
                unit=unit,
                effective_date=effective_date,
                status=status,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_procedures(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Procedure resources."""
    from .models import Procedure
    
    procedure_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Procedure, "procedure_id", procedure_ids)
    
    for fhir_resource, resource_json in resources:
        procedure_id = resource_json.get("id")
        if not procedure_id:
            continue
        
        subject = _as_dict(resource_json.get("subject"))
        patient_id = extract_reference_id(subject)
        
        encounter = _as_dict(resource_json.get("encounter"))
        encounter_id = extract_reference_id(encounter)
        
        code_data = _as_dict(resource_json.get("code"))
        code_coding = _as_list(code_data.get("coding"))
        code = None
        display = code_data.get("text")
        system = None
        if code_coding:
            code_data = _as_dict(code_coding[0])
            code = code_data.get("code")
            display = code_data.get("display") or display
            system = code_data.get("system")
        
        performed_datetime = resource_json.get("performedDateTime")
        performed_date = None
        if performed_datetime:
            try:
                performed_date = dt.datetime.fromisoformat(performed_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        status = resource_json.get("status")
        
        location = _as_dict(resource_json.get("location"))
        location_name = location.get("display")
        
        existing = existing_map.get(procedure_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_id = encounter_id
            existing.code = code
            existing.display = display
            existing.system = system
            existing.performed_date = performed_date
            existing.status = status
            existing.location = location_name
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Procedure(
                procedure_id=procedure_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                code=code,
                display=display,
                system=system,
                performed_date=performed_date,
                status=status,
                location=location_name,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_medications(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract MedicationRequest resources."""
    from .models import Medication
    
    medication_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Medication, "medication_request_id", medication_ids)
    
    for fhir_resource, resource_json in resources:
        medication_request_id = resource_json.get("id")
        if not medication_request_id:
            continue
        
        subject = _as_dict(resource_json.get("subject"))
        patient_id = extract_reference_id(subject)
        
        encounter = _as_dict(resource_json.get("encounter"))
        encounter_id = extract_reference_id(encounter)
        
        medication_data = _as_dict(resource_json.get("medicationCodeableConcept"))
        medication_coding = _as_list(medication_data.get("coding"))
        medication_code = None
        medication_display = medication_data.get("text")
        medication_system = None
        if medication_coding:
            medication_code_data = _as_dict(medication_coding[0])
            medication_code = medication_code_data.get("code")
            medication_display = medication_code_data.get("display") or medication_display
            medication_system = medication_code_data.get("system")
        
        authored_datetime = resource_json.get("authoredOn")
        authored_date = None
        if authored_datetime:
            try:
                authored_date = dt.datetime.fromisoformat(authored_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        status = resource_json.get("status")
        intent = resource_json.get("intent")
        
        dosage_text = None
        dosage_list = _as_list(resource_json.get("dosageInstruction"))
        if dosage_list:
            dosage_text = _as_dict(dosage_list[0]).get("text")
        
        existing = existing_map.get(medication_request_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_id = encounter_id
            existing.medication_code = medication_code
            existing.medication_display = medication_display
            existing.medication_system = medication_system
            existing.authored_date = authored_date
            existing.status = status
            existing.intent = intent
            existing.dosage_text = dosage_text
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Medication(
                medication_request_id=medication_request_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                medication_code=medication_code,
                medication_display=medication_display,
                medication_system=medication_system,
                authored_date=authored_date,
                status=status,
                intent=intent,
                dosage_text=dosage_text,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_immunizations(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Immunization resources."""
    from .models import Immunization
    
    immunization_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Immunization, "immunization_id", immunization_ids)
    
    for fhir_resource, resource_json in resources:
        immunization_id = resource_json.get("id")
        if not immunization_id:
            continue
        
        patient = _as_dict(resource_json.get("patient"))
        patient_id = extract_reference_id(patient)
        
        encounter = _as_dict(resource_json.get("encounter"))
        encounter_id = extract_reference_id(encounter)
        
        vaccine_data = _as_dict(resource_json.get("vaccineCode"))
        vaccine_coding = _as_list(vaccine_data.get("coding"))
        vaccine_code = None
        vaccine_display = vaccine_data.get("text")
        vaccine_system = None
        if vaccine_coding:
            vaccine_code_data = _as_dict(vaccine_coding[0])
            vaccine_code = vaccine_code_data.get("code")
            vaccine_display = vaccine_code_data.get("display") or vaccine_display
            vaccine_system = vaccine_code_data.get("system")
        
        occurrence_datetime = resource_json.get("occurrenceDateTime")
        occurrence_date = None
        if occurrence_datetime:
            try:
                occurrence_date = dt.datetime.fromisoformat(occurrence_datetime.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        status = resource_json.get("status")
        lot_number = resource_json.get("lotNumber")
        
        existing = existing_map.get(immunization_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_id
            existing.encounter_id = encounter_id
            existing.vaccine_code = vaccine_code
            existing.vaccine_display = vaccine_display
            existing.vaccine_system = vaccine_system
            existing.occurrence_date = occurrence_date
            existing.status = status
            existing.lot_number = lot_number
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Immunization(
                immunization_id=immunization_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                vaccine_code=vaccine_code,
                vaccine_display=vaccine_display,
                vaccine_system=vaccine_system,
                occurrence_date=occurrence_date,
                status=status,
                lot_number=lot_number,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def _batch_extract_locations(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Location resources."""
    from .models import Location
    
    location_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Location, "location_id", location_ids)
    
    for fhir_resource, resource_json in resources:
        location_id = resource_json.get("id")
        if not location_id:
            continue
        
        name = resource_json.get("name")
        status = resource_json.get("status")
        
        # Extract address
        address = _as_dict(resource_json.get("address"))
        address_line = None
        address_city = None
        address_state = None
        address_postal_code = None
        address_country = None
        
        if address:
            line_list = _as_list(address.get("line"))
            address_line = line_list[0] if line_list else None
            address_city = address.get("city")
            address_state = address.get("state")
            address_postal_code = address.get("postalCode")
            address_country = address.get("country")
        
        # Extract contact information
        phone = None
        telecom_list = _as_list(resource_json.get("telecom"))
        for telecom in telecom_list:
            telecom_dict = _as_dict(telecom)
            if telecom_dict.get("system") == "phone":
                phone = telecom_dict.get("value")
                break
        
        # Extract position (GPS coordinates)
        position = _as_dict(resource_json.get("position"))
        latitude = None
        longitude = None
        if position:
            latitude = position.get("latitude")
            longitude = position.get("longitude")
        
        existing = existing_map.get(location_id)
        if existing:
            _touch(existing)
            existing.name = name
            existing.status = status
            existing.address_line = address_line
            existing.address_city = address_city
            existing.address_state = address_state
            existing.address_postal_code = address_postal_code
            existing.address_country = address_country
            existing.phone = phone
            existing.latitude = latitude
            existing.longitude = longitude
            existing.source_file = fhir_resource.source_file
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add_all((Location(
                location_id=location_id,
                name=name,
                status=status,
                address_line=address_line,
                address_city=address_city,
                address_state=address_state,
                address_postal_code=address_postal_code,
                address_country=address_country,
                phone=phone,
                latitude=latitude,
                longitude=longitude,
                source_file=fhir_resource.source_file,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ),))


def _batch_extract_coverages(
    session: Session,
    resources: list[tuple[FhirResource, dict[str, Any]]],
) -> None:
    """Batch-extract Coverage resources."""
    from .models import Coverage
    
    coverage_ids = [resource_json.get("id") for _, resource_json in resources if resource_json.get("id")]
    
    # Use helper to fetch existing records (session already flushed in batch_extract_denormalized)
    existing_map = _get_existing_by_id(session, Coverage, "coverage_id", coverage_ids)
    
    for fhir_resource, resource_json in resources:
        coverage_id = resource_json.get("id")
        if not coverage_id:
            continue
        
        beneficiary = _as_dict(resource_json.get("beneficiary"))
        patient_ref = extract_reference_id(beneficiary)
        
        period = _as_dict(resource_json.get("period"))
        period_start = period.get("start")
        period_end = period.get("end")
        
        start_date = None
        end_date = None
        if period_start:
            try:
                start_date = dt.datetime.fromisoformat(period_start.replace('Z', '+00:00')).date()
            except (ValueError, AttributeError):
                pass
        if period_end:
            try:
                end_date = dt.datetime.fromisoformat(period_end.replace('Z', '+00:00')).date()
            except (ValueError, AttributeError):
                pass
        
        payer_list = _as_list(resource_json.get("payor"))
        payer = _as_dict(payer_list[0]).get("display") if payer_list else None
        
        gap_detected = False
        gap_days = None
        gap_start_date = None
        gap_end_date = None
        continuous_enrollment_compliant = True
        
        extensions = _as_list(resource_json.get("extension"))
        for ext in extensions:
            ext = _as_dict(ext)
            url = ext.get("url", "")
            
            if "enrollment-gap-detected" in url:
                gap_detected = ext.get("valueBoolean", False)
            
            if "gap-details" in url:
                sub_exts = _as_list(ext.get("extension"))
                for sub_ext in sub_exts:
                    sub_ext = _as_dict(sub_ext)
                    sub_url = sub_ext.get("url", "")
                    
                    if "total-gap-days" in sub_url:
                        gap_days = sub_ext.get("valueInteger")
                    
                    if "continuous-enrollment-compliant" in sub_url:
                        continuous_enrollment_compliant = sub_ext.get("valueBoolean", True)
                    
                    if sub_url.endswith("/gap"):
                        gap_sub_exts = _as_list(sub_ext.get("extension"))
                        for gap_sub_ext in gap_sub_exts:
                            gap_sub_ext = _as_dict(gap_sub_ext)
                            gap_sub_url = gap_sub_ext.get("url", "")
                            
                            if gap_sub_url.endswith("gap-start-date"):
                                gap_start_str = gap_sub_ext.get("valueDate")
                                if gap_start_str:
                                    try:
                                        gap_start_date = dt.datetime.fromisoformat(gap_start_str).date()
                                    except ValueError:
                                        pass
                            
                            if gap_sub_url.endswith("gap-end-date"):
                                gap_end_str = gap_sub_ext.get("valueDate")
                                if gap_end_str:
                                    try:
                                        gap_end_date = dt.datetime.fromisoformat(gap_end_str).date()
                                    except ValueError:
                                        pass
        
        existing = existing_map.get(coverage_id)
        if existing:
            _touch(existing)
            existing.patient_id = patient_ref
            existing.period_start = start_date
            existing.period_end = end_date
            existing.payer = payer
            existing.gap_detected = gap_detected
            existing.gap_days = gap_days
            existing.gap_start_date = gap_start_date
            existing.gap_end_date = gap_end_date
            existing.continuous_enrollment_compliant = continuous_enrollment_compliant
            existing.source_file = fhir_resource.source_file
            existing.source_record_id = fhir_resource.source_record_id
            existing.fhir_resource_pk = fhir_resource.id
            existing.batch_id = fhir_resource.batch_id
        else:
            session.add(Coverage(
                coverage_id=coverage_id,
                patient_id=patient_ref,
                period_start=start_date,
                period_end=end_date,
                payer=payer,
                gap_detected=gap_detected,
                gap_days=gap_days,
                gap_start_date=gap_start_date,
                gap_end_date=gap_end_date,
                continuous_enrollment_compliant=continuous_enrollment_compliant,
                source_file=fhir_resource.source_file,
                source_record_id=fhir_resource.source_record_id,
                fhir_resource_pk=fhir_resource.id,
                batch_id=fhir_resource.batch_id,
            ))


def extract_coverage_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """
    DEPRECATED: Use batch_extract_denormalized() for better performance.
    Individual extraction kept for backward compatibility.
    
    Extract coverage data from FHIR Coverage resource and populate denormalized Coverage table.
    Includes HEDIS gap detection data.
    """
    from .models import Coverage
    
    coverage_id = resource_json.get("id")
    if not coverage_id:
        return  # Skip if no ID
    
    # Extract basic coverage information
    beneficiary = _as_dict(resource_json.get("beneficiary"))
    patient_ref = extract_reference_id(beneficiary)  # Extract stable resource ID
    
    # Extract period dates
    period = _as_dict(resource_json.get("period"))
    period_start = period.get("start")
    period_end = period.get("end")
    
    # Convert ISO date strings to Python date objects
    start_date = None
    end_date = None
    if period_start:
        try:
            start_date = dt.datetime.fromisoformat(period_start.replace('Z', '+00:00')).date()
        except (ValueError, AttributeError):
            pass
    if period_end:
        try:
            end_date = dt.datetime.fromisoformat(period_end.replace('Z', '+00:00')).date()
        except (ValueError, AttributeError):
            pass
    
    # Extract payer information
    payer_list = _as_list(resource_json.get("payor"))
    payer = _as_dict(payer_list[0]).get("display") if payer_list else None
    
    # Extract gap detection information from FHIR extensions
    gap_detected = False
    gap_days = None
    gap_start_date = None
    gap_end_date = None
    continuous_enrollment_compliant = True
    
    extensions = _as_list(resource_json.get("extension"))
    for ext in extensions:
        ext = _as_dict(ext)
        url = ext.get("url", "")
        
        # Check for enrollment-gap-detected marker
        if "enrollment-gap-detected" in url:
            gap_detected = ext.get("valueBoolean", False)
        
        # Check for gap-details (contains specific gap information)
        if "gap-details" in url:
            sub_exts = _as_list(ext.get("extension"))
            for sub_ext in sub_exts:
                sub_ext = _as_dict(sub_ext)
                sub_url = sub_ext.get("url", "")
                
                if "total-gap-days" in sub_url:
                    gap_days = sub_ext.get("valueInteger")
                
                if "continuous-enrollment-compliant" in sub_url:
                    continuous_enrollment_compliant = sub_ext.get("valueBoolean", True)
                
                # Extract gap details (gaps are nested under "gap" sub-extensions)
                if sub_url.endswith("/gap"):
                    gap_sub_exts = _as_list(sub_ext.get("extension"))
                    for gap_sub_ext in gap_sub_exts:
                        gap_sub_ext = _as_dict(gap_sub_ext)
                        gap_sub_url = gap_sub_ext.get("url", "")
                        
                        if gap_sub_url.endswith("gap-start-date"):
                            gap_start_str = gap_sub_ext.get("valueDate")
                            if gap_start_str:
                                try:
                                    gap_start_date = dt.datetime.fromisoformat(gap_start_str).date()
                                except ValueError:
                                    pass
                        
                        if gap_sub_url.endswith("gap-end-date"):
                            gap_end_str = gap_sub_ext.get("valueDate")
                            if gap_end_str:
                                try:
                                    gap_end_date = dt.datetime.fromisoformat(gap_end_str).date()
                                except ValueError:
                                    pass
    
    # Upsert coverage record (update if exists, insert if new)
    existing = session.execute(
        select(Coverage).where(Coverage.coverage_id == coverage_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        # Update existing record
        existing.patient_id = patient_ref
        existing.period_start = start_date
        existing.period_end = end_date
        existing.payer = payer
        existing.gap_detected = gap_detected
        existing.gap_days = gap_days
        existing.gap_start_date = gap_start_date
        existing.gap_end_date = gap_end_date
        existing.continuous_enrollment_compliant = continuous_enrollment_compliant
        existing.source_file = fhir_resource.source_file
        existing.source_record_id = fhir_resource.source_record_id
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        # Create new record
        coverage_row = Coverage(
            coverage_id=coverage_id,
            patient_id=patient_ref,
            period_start=start_date,
            period_end=end_date,
            payer=payer,
            gap_detected=gap_detected,
            gap_days=gap_days,
            gap_start_date=gap_start_date,
            gap_end_date=gap_end_date,
            continuous_enrollment_compliant=continuous_enrollment_compliant,
            source_file=fhir_resource.source_file,
            source_record_id=fhir_resource.source_record_id,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(coverage_row)


def extract_patient_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract patient demographics from FHIR Patient resource."""
    from .models import Patient
    
    patient_id = resource_json.get("id")
    if not patient_id:
        return
    
    # Extract name
    name_parts = _as_list(resource_json.get("name"))
    name = None
    if name_parts and name_parts[0]:
        name_data = _as_dict(name_parts[0])
        # Try to get name from text field first (Synthea format), fallback to given/family
        name = name_data.get("text")
        if not name:
            given = _as_list(name_data.get("given"))
            family = name_data.get("family", "")
            name = f"{' '.join(given)} {family}".strip() if given else family
    
    # Extract birth date
    birth_date = resource_json.get("birthDate")
    birth_date_obj = None
    if birth_date:
        try:
            birth_date_obj = dt.datetime.fromisoformat(birth_date).date()
        except (ValueError, AttributeError):
            pass
    
    gender = resource_json.get("gender")
    
    # Extract marital status
    marital_status_data = _as_dict(resource_json.get("maritalStatus"))
    marital_coding = _as_list(marital_status_data.get("coding"))
    marital_status = _as_dict(marital_coding[0]).get("display") if marital_coding else None
    
    # Extract address
    address_parts = _as_list(resource_json.get("address"))
    address = None
    if address_parts:
        addr = _as_dict(address_parts[0])
        lines = _as_list(addr.get("line"))
        city = addr.get("city", "")
        state = addr.get("state", "")
        address = f"{', '.join(lines)} {city} {state}".strip() if (lines or city) else None
    
    # Extract telecom (phone/email)
    telecom_parts = _as_list(resource_json.get("telecom"))
    phone = None
    email = None
    for telecom in telecom_parts:
        if telecom.get("system") == "phone":
            phone = telecom.get("value")
        elif telecom.get("system") == "email":
            email = telecom.get("value")
    
    # Flush session to ensure existing patients are visible to the query
    session.flush()
    
    existing = session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.name = name
        existing.birth_date = birth_date_obj
        existing.gender = gender
        existing.marital_status = marital_status
        existing.address = address
        existing.phone = phone
        existing.email = email
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        patient_row = Patient(
            patient_id=patient_id,
            name=name,
            birth_date=birth_date_obj,
            gender=gender,
            marital_status=marital_status,
            address=address,
            phone=phone,
            email=email,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(patient_row)


def extract_encounter_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract encounter from FHIR Encounter resource."""
    from .models import Encounter
    
    encounter_id = resource_json.get("id")
    if not encounter_id:
        return
    
    subject = _as_dict(resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    
    # Extract encounter type
    encounter_type_codings = _as_list(resource_json.get("type"))
    encounter_type = None
    encounter_type_data = _as_dict(encounter_type_codings[0]) if encounter_type_codings else {}
    encounter_type_codes = _as_list(encounter_type_data.get("coding"))
    if encounter_type_codes:
        encounter_type = _as_dict(encounter_type_codes[0]).get("display")
    
    encounter_class = _as_dict(resource_json.get("class")).get("display")
    status = resource_json.get("status")
    
    # Extract period dates
    period = _as_dict(resource_json.get("period"))
    start_date = period.get("start")
    end_date = period.get("end")
    
    start_datetime = None
    end_datetime = None
    if start_date:
        try:
            start_datetime = dt.datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    if end_date:
        try:
            end_datetime = dt.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    # Extract facility
    location = _as_list(resource_json.get("location"))
    facility = None
    if location:
        facility = _as_dict(location[0]).get("display")
    
    # Extract reason
    reason_coding = _as_list(resource_json.get("reasonCode"))
    reason_code = None
    reason_display = None
    if reason_coding:
        reason_data = _as_dict(reason_coding[0])
        reason_codes = _as_list(reason_data.get("coding"))
        reason_code = _as_dict(reason_codes[0]).get("code") if reason_codes else None
        reason_display = reason_data.get("text")
    
    # Flush session to ensure existing encounters are visible to the query
    session.flush()
    
    existing = session.execute(
        select(Encounter).where(Encounter.encounter_id == encounter_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_type = encounter_type
        existing.encounter_class = encounter_class
        existing.status = status
        existing.start_date = start_datetime
        existing.end_date = end_datetime
        existing.facility = facility
        existing.reason_code = reason_code
        existing.reason_display = reason_display
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        encounter_row = Encounter(
            encounter_id=encounter_id,
            patient_id=patient_id,
            encounter_type=encounter_type,
            encounter_class=encounter_class,
            status=status,
            start_date=start_datetime,
            end_date=end_datetime,
            facility=facility,
            reason_code=reason_code,
            reason_display=reason_display,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(encounter_row)


def extract_condition_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract condition from FHIR Condition resource."""
    from .models import Condition
    
    condition_id = resource_json.get("id")
    if not condition_id:
        return
    
    subject = _as_dict(resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    
    encounter = _as_dict(resource_json.get("encounter"))
    encounter_id = extract_reference_id(encounter)
    
    # Extract code
    code_data = _as_dict(resource_json.get("code"))
    code_coding = _as_list(code_data.get("coding"))
    code = None
    display = code_data.get("text")
    system = None
    if code_coding:
        code_data = _as_dict(code_coding[0])
        code = code_data.get("code")
        display = code_data.get("display") or display
        system = code_data.get("system")
    
    # Extract onset date
    onset_datetime = resource_json.get("onsetDateTime")
    onset_date = None
    if onset_datetime:
        try:
            onset_date = dt.datetime.fromisoformat(onset_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    # Extract resolved date
    resolved_datetime = resource_json.get("abatementDateTime")
    resolved_date = None
    if resolved_datetime:
        try:
            resolved_date = dt.datetime.fromisoformat(resolved_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    clinical_status = _as_dict(resource_json.get("clinicalStatus"))
    status_coding = _as_list(clinical_status.get("coding"))
    status = _as_dict(status_coding[0]).get("display") if status_coding else None
    
    # Flush session to ensure existing conditions are visible to the query
    session.flush()
    
    existing = session.execute(
        select(Condition).where(Condition.condition_id == condition_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_id = encounter_id
        existing.code = code
        existing.display = display
        existing.system = system
        existing.onset_date = onset_date
        existing.resolved_date = resolved_date
        existing.status = status
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        condition_row = Condition(
            condition_id=condition_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            code=code,
            display=display,
            system=system,
            onset_date=onset_date,
            resolved_date=resolved_date,
            status=status,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(condition_row)


def extract_observation_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract observation (lab/vital) from FHIR Observation resource."""
    from .models import Observation
    
    observation_id = resource_json.get("id")
    if not observation_id:
        return
    
    subject = _as_dict(resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    
    encounter = _as_dict(resource_json.get("encounter"))
    encounter_id = extract_reference_id(encounter)
    
    # Extract code
    code_data = _as_dict(resource_json.get("code"))
    code_coding = _as_list(code_data.get("coding"))
    code = None
    display = code_data.get("text")
    system = None
    if code_coding:
        code_data = _as_dict(code_coding[0])
        code = code_data.get("code")
        display = code_data.get("display") or display
        system = code_data.get("system")
    
    # Extract value
    value = None
    unit = None
    value_quantity = _as_dict(resource_json.get("valueQuantity"))
    if value_quantity:
        value = str(value_quantity.get("value"))
        unit = value_quantity.get("unit")
    else:
        value_string = resource_json.get("valueString")
        if value_string:
            value = value_string
    
    effective_datetime = resource_json.get("effectiveDateTime")
    effective_date = None
    if effective_datetime:
        try:
            effective_date = dt.datetime.fromisoformat(effective_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    status = resource_json.get("status")
    
    # Flush session to ensure existing observations are visible to the query
    session.flush()
    
    existing = session.execute(
        select(Observation).where(Observation.observation_id == observation_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_id = encounter_id
        existing.code = code
        existing.display = display
        existing.system = system
        existing.value = value
        existing.unit = unit
        existing.effective_date = effective_date
        existing.status = status
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        observation_row = Observation(
            observation_id=observation_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            code=code,
            display=display,
            system=system,
            value=value,
            unit=unit,
            effective_date=effective_date,
            status=status,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(observation_row)


def extract_procedure_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract procedure from FHIR Procedure resource."""
    from .models import Procedure
    
    procedure_id = resource_json.get("id")
    if not procedure_id:
        return
    
    subject = _as_dict(resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    
    encounter = _as_dict(resource_json.get("encounter"))
    encounter_id = extract_reference_id(encounter)
    
    # Extract code
    code_data = _as_dict(resource_json.get("code"))
    code_coding = _as_list(code_data.get("coding"))
    code = None
    display = code_data.get("text")
    system = None
    if code_coding:
        code_data = _as_dict(code_coding[0])
        code = code_data.get("code")
        display = code_data.get("display") or display
        system = code_data.get("system")
    
    # Extract performed date
    performed_datetime = resource_json.get("performedDateTime")
    performed_date = None
    if performed_datetime:
        try:
            performed_date = dt.datetime.fromisoformat(performed_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    status = resource_json.get("status")
    
    # Extract location
    location = _as_dict(resource_json.get("location"))
    location_name = location.get("display")
    
    existing = session.execute(
        select(Procedure).where(Procedure.procedure_id == procedure_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_id = encounter_id
        existing.code = code
        existing.display = display
        existing.system = system
        existing.performed_date = performed_date
        existing.status = status
        existing.location = location_name
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        procedure_row = Procedure(
            procedure_id=procedure_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            code=code,
            display=display,
            system=system,
            performed_date=performed_date,
            status=status,
            location=location_name,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(procedure_row)


def extract_medication_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract medication request from FHIR MedicationRequest resource."""
    from .models import Medication
    
    medication_request_id = resource_json.get("id")
    if not medication_request_id:
        return
    
    subject = _as_dict(resource_json.get("subject"))
    patient_id = extract_reference_id(subject)
    
    encounter = _as_dict(resource_json.get("encounter"))
    encounter_id = extract_reference_id(encounter)
    
    # Extract medication code
    medication_data = _as_dict(resource_json.get("medicationCodeableConcept"))
    medication_coding = _as_list(medication_data.get("coding"))
    medication_code = None
    medication_display = medication_data.get("text")
    medication_system = None
    if medication_coding:
        medication_code_data = _as_dict(medication_coding[0])
        medication_code = medication_code_data.get("code")
        medication_display = medication_code_data.get("display") or medication_display
        medication_system = medication_code_data.get("system")
    
    authored_datetime = resource_json.get("authoredOn")
    authored_date = None
    if authored_datetime:
        try:
            authored_date = dt.datetime.fromisoformat(authored_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    status = resource_json.get("status")
    intent = resource_json.get("intent")
    
    # Extract dosage
    dosage_text = None
    dosage_list = _as_list(resource_json.get("dosageInstruction"))
    if dosage_list:
        dosage_text = _as_dict(dosage_list[0]).get("text")
    
    existing = session.execute(
        select(Medication).where(Medication.medication_request_id == medication_request_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_id = encounter_id
        existing.medication_code = medication_code
        existing.medication_display = medication_display
        existing.medication_system = medication_system
        existing.authored_date = authored_date
        existing.status = status
        existing.intent = intent
        existing.dosage_text = dosage_text
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        medication_row = Medication(
            medication_request_id=medication_request_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            medication_code=medication_code,
            medication_display=medication_display,
            medication_system=medication_system,
            authored_date=authored_date,
            status=status,
            intent=intent,
            dosage_text=dosage_text,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(medication_row)


def extract_immunization_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract immunization from FHIR Immunization resource."""
    from .models import Immunization
    
    immunization_id = resource_json.get("id")
    if not immunization_id:
        return
    
    patient = _as_dict(resource_json.get("patient"))
    patient_id = extract_reference_id(patient)
    
    encounter = _as_dict(resource_json.get("encounter"))
    encounter_id = extract_reference_id(encounter)
    
    # Extract vaccine code
    vaccine_data = _as_dict(resource_json.get("vaccineCode"))
    vaccine_coding = _as_list(vaccine_data.get("coding"))
    vaccine_code = None
    vaccine_display = vaccine_data.get("text")
    vaccine_system = None
    if vaccine_coding:
        vaccine_code_data = _as_dict(vaccine_coding[0])
        vaccine_code = vaccine_code_data.get("code")
        vaccine_display = vaccine_code_data.get("display") or vaccine_display
        vaccine_system = vaccine_code_data.get("system")
    
    # Extract occurrence date
    occurrence_datetime = resource_json.get("occurrenceDateTime")
    occurrence_date = None
    if occurrence_datetime:
        try:
            occurrence_date = dt.datetime.fromisoformat(occurrence_datetime.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pass
    
    status = resource_json.get("status")
    lot_number = resource_json.get("lotNumber")
    
    existing = session.execute(
        select(Immunization).where(Immunization.immunization_id == immunization_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.patient_id = patient_id
        existing.encounter_id = encounter_id
        existing.vaccine_code = vaccine_code
        existing.vaccine_display = vaccine_display
        existing.vaccine_system = vaccine_system
        existing.occurrence_date = occurrence_date
        existing.status = status
        existing.lot_number = lot_number
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        immunization_row = Immunization(
            immunization_id=immunization_id,
            patient_id=patient_id,
            encounter_id=encounter_id,
            vaccine_code=vaccine_code,
            vaccine_display=vaccine_display,
            vaccine_system=vaccine_system,
            occurrence_date=occurrence_date,
            status=status,
            lot_number=lot_number,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(immunization_row)


def extract_location_from_fhir(
    session: Session,
    fhir_resource: FhirResource,
    resource_json: dict[str, Any],
) -> None:
    """Extract location from FHIR Location resource."""
    from .models import Location
    
    location_id = resource_json.get("id")
    if not location_id:
        return
    
    name = resource_json.get("name")
    status = resource_json.get("status")
    
    # Extract address
    address = _as_dict(resource_json.get("address"))
    address_line = None
    address_city = None
    address_state = None
    address_postal_code = None
    address_country = None
    
    if address:
        line_list = _as_list(address.get("line"))
        address_line = line_list[0] if line_list else None
        address_city = address.get("city")
        address_state = address.get("state")
        address_postal_code = address.get("postalCode")
        address_country = address.get("country")
    
    # Extract contact information
    phone = None
    telecom_list = _as_list(resource_json.get("telecom"))
    for telecom in telecom_list:
        telecom_dict = _as_dict(telecom)
        if telecom_dict.get("system") == "phone":
            phone = telecom_dict.get("value")
            break
    
    # Extract position (GPS coordinates)
    position = _as_dict(resource_json.get("position"))
    latitude = None
    longitude = None
    if position:
        latitude = position.get("latitude")
        longitude = position.get("longitude")
    
    # Flush session to ensure existing locations are visible to the query
    session.flush()
    
    existing = session.execute(
        select(Location).where(Location.location_id == location_id)
    ).scalar_one_or_none()
    
    if existing:
        _touch(existing)
        existing.name = name
        existing.status = status
        existing.address_line = address_line
        existing.address_city = address_city
        existing.address_state = address_state
        existing.address_postal_code = address_postal_code
        existing.address_country = address_country
        existing.phone = phone
        existing.latitude = latitude
        existing.longitude = longitude
        existing.source_file = fhir_resource.source_file
        existing.fhir_resource_pk = fhir_resource.id
        existing.batch_id = fhir_resource.batch_id
    else:
        location_row = Location(
            location_id=location_id,
            name=name,
            status=status,
            address_line=address_line,
            address_city=address_city,
            address_state=address_state,
            address_postal_code=address_postal_code,
            address_country=address_country,
            phone=phone,
            latitude=latitude,
            longitude=longitude,
            source_file=fhir_resource.source_file,
            fhir_resource_pk=fhir_resource.id,
            batch_id=fhir_resource.batch_id,
        )
        session.add(location_row)

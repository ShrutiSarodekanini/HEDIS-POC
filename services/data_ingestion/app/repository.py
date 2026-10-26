from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from .models import ConversionJob, FhirResource, ValidationIssue
from .synthea import RowIssue
from .validator import ResourceValidationResult


def create_job(session: Session, input_dir: str) -> ConversionJob:
    job = ConversionJob(input_dir=input_dir, status="pending", summary={})
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
) -> FhirResource:
    existing = session.execute(
        select(FhirResource).where(
            (FhirResource.resource_type == resource_type) & (FhirResource.resource_id == resource_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        row = FhirResource(
            job_id=job_id,
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
            persisted=False,
        )
        session.add(row)
        if flush:
            session.flush()
        return row

    # Safe replacement policy: never downgrade a validated/persisted resource to invalid.
    existing.job_id = job_id
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

    if flush:
        session.flush()
    return existing


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
    persisted = result.rowcount
    
    logger.info(f"[persist] job={job_id} persisted={persisted} records")
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

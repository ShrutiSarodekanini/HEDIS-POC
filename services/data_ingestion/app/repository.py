from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any

from sqlalchemy import Text, case, delete, func, select, update
from sqlalchemy.orm import Session

from .models import (
    AllergyIntolerance,
    BasicResource,
    CareGap,
    Claim,
    Condition,
    ConversionJob,
    Coverage,
    Device,
    DocumentReference,
    DomainEvent,
    Encounter,
    FeedCompletenessReport,
    FhirResource,
    FhirResourceRaw,
    ImagingStudy,
    Immunization,
    Location,
    MeasureDefinition,
    MeasureResult,
    Medication,
    Observation,
    Organization,
    Patient,
    Practitioner,
    Procedure,
    SourceBatch,
    SupplyDelivery,
    ValidationIssue,
)
from .synthea import RowIssue
from .validator import ResourceValidationResult


def create_job(session: Session, input_dir: str) -> ConversionJob:
    job = ConversionJob(input_dir=input_dir, status="pending", summary={})
    session.add(job)
    session.flush()
    return job


def create_source_batch(
    session: Session,
    *,
    source_system: str,
    source_format: str,
    adapter_name: str,
    input_source: str,
) -> SourceBatch:
    batch = SourceBatch(
        source_system=source_system,
        source_format=source_format,
        adapter_name=adapter_name,
        input_source=input_source,
        status="running",
    )
    session.add(batch)
    session.flush()
    return batch


def complete_source_batch(
    session: Session,
    *,
    batch_id: str,
    status: str,
    resource_count: int,
) -> SourceBatch:
    batch = session.get(SourceBatch, batch_id)
    if not batch:
        raise ValueError(f"Source batch not found: {batch_id}")
    batch.status = status
    batch.resource_count = int(resource_count or 0)
    batch.resource_count_validated = int(resource_count or 0)
    batch.completed_at = dt.datetime.now(dt.timezone.utc)
    return batch


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
    batch_id: str | None = None,
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
            batch_id=batch_id,
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
    existing.batch_id = batch_id

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


def _iso_to_datetime(value: Any) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _coding_first(concept: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(concept, dict):
        return None, None, None
    codings = concept.get("coding")
    if isinstance(codings, list) and codings:
        c0 = codings[0] if isinstance(codings[0], dict) else {}
        return c0.get("code"), c0.get("display"), c0.get("system")
    return None, concept.get("text"), None


def _upsert_by_unique(
    session: Session,
    model: Any,
    unique_field: str,
    unique_value: Any,
    values: dict[str, Any],
) -> bool:
    if not unique_value:
        return False
    existing = session.execute(select(model).where(getattr(model, unique_field) == unique_value)).scalar_one_or_none()
    if existing is None:
        session.add(model(**values))
        return True
    for key, val in values.items():
        setattr(existing, key, val)
    return False


def persist_denormalized_resources_for_job(
    session: Session,
    *,
    job_id: str,
    batch_id: str | None,
) -> dict[str, int]:
    resources = session.execute(select(FhirResource).where(FhirResource.job_id == job_id)).scalars().all()
    counts: dict[str, int] = {}

    def inc(name: str, added: bool) -> None:
        counts.setdefault(name, 0)
        if added:
            counts[name] += 1

    for row in resources:
        resource = row.resource_json if isinstance(row.resource_json, dict) else {}
        rid = str(resource.get("id") or row.resource_id or "")
        rtype = str(resource.get("resourceType") or row.resource_type or "")
        patient_id = row.patient_id

        # Mirror raw resource for direct audit trail.
        added = _upsert_by_unique(
            session,
            FhirResourceRaw,
            "fhir_resource_pk",
            row.id,
            {
                "fhir_resource_pk": row.id,
                "batch_id": batch_id,
                "resource_type": rtype,
                "resource_id": rid,
                "resource_json": resource,
            },
        )
        inc("fhir_resource_raw", added)

        if rtype == "Patient":
            names = resource.get("name") or []
            n0 = names[0] if isinstance(names, list) and names else {}
            given = " ".join(n0.get("given") or []) if isinstance(n0, dict) else ""
            family = n0.get("family") if isinstance(n0, dict) else ""
            full_name = " ".join([p for p in [given, family] if p]).strip() or None
            birth_date = None
            raw_birth = resource.get("birthDate")
            if isinstance(raw_birth, str) and raw_birth:
                try:
                    birth_date = dt.date.fromisoformat(raw_birth[:10])
                except ValueError:
                    birth_date = None
            added = _upsert_by_unique(
                session,
                Patient,
                "patient_id",
                patient_id or rid,
                {
                    "patient_id": patient_id or rid,
                    "name": full_name,
                    "birth_date": birth_date,
                    "gender": resource.get("gender"),
                    "marital_status": (_coding_first(resource.get("maritalStatus"))[0]),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("patient", added)
        elif rtype == "Encounter":
            reason = (resource.get("reasonCode") or [{}])[0]
            reason_code, reason_display, _ = _coding_first(reason)
            period = resource.get("period") if isinstance(resource.get("period"), dict) else {}
            added = _upsert_by_unique(
                session,
                Encounter,
                "encounter_id",
                rid,
                {
                    "encounter_id": rid,
                    "patient_id": patient_id,
                    "encounter_type": _coding_first((resource.get("type") or [{}])[0])[0],
                    "encounter_class": _coding_first(resource.get("class"))[0],
                    "status": resource.get("status"),
                    "start_date": _iso_to_datetime(period.get("start")),
                    "end_date": _iso_to_datetime(period.get("end")),
                    "facility": ((resource.get("serviceProvider") or {}).get("display") if isinstance(resource.get("serviceProvider"), dict) else None),
                    "reason_code": reason_code,
                    "reason_display": reason_display,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("encounter", added)
        elif rtype == "Condition":
            code, display, system = _coding_first(resource.get("code"))
            clinical_status = _coding_first(resource.get("clinicalStatus"))[0]
            added = _upsert_by_unique(
                session,
                Condition,
                "condition_id",
                rid,
                {
                    "condition_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "code": code,
                    "display": display,
                    "system": system,
                    "onset_date": _iso_to_datetime(resource.get("onsetDateTime")),
                    "resolved_date": _iso_to_datetime(resource.get("abatementDateTime")),
                    "status": clinical_status,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("condition", added)
        elif rtype == "Observation":
            code, display, system = _coding_first(resource.get("code"))
            vq = resource.get("valueQuantity") if isinstance(resource.get("valueQuantity"), dict) else {}
            value = vq.get("value") if vq else resource.get("valueString")
            unit = vq.get("unit") if vq else None
            added = _upsert_by_unique(
                session,
                Observation,
                "observation_id",
                rid,
                {
                    "observation_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "code": code,
                    "display": display,
                    "system": system,
                    "value": str(value) if value is not None else None,
                    "unit": unit,
                    "effective_date": _iso_to_datetime(resource.get("effectiveDateTime")),
                    "status": resource.get("status"),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("observation", added)
        elif rtype == "Procedure":
            code, display, system = _coding_first(resource.get("code"))
            performed = resource.get("performedDateTime")
            if not performed and isinstance(resource.get("performedPeriod"), dict):
                performed = resource.get("performedPeriod", {}).get("start")
            added = _upsert_by_unique(
                session,
                Procedure,
                "procedure_id",
                rid,
                {
                    "procedure_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "code": code,
                    "display": display,
                    "system": system,
                    "performed_date": _iso_to_datetime(performed),
                    "status": resource.get("status"),
                    "location": ((resource.get("location") or {}).get("display") if isinstance(resource.get("location"), dict) else None),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("procedure", added)
        elif rtype == "MedicationRequest":
            med = resource.get("medicationCodeableConcept")
            code, display, system = _coding_first(med)
            added = _upsert_by_unique(
                session,
                Medication,
                "medication_request_id",
                rid,
                {
                    "medication_request_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "medication_code": code,
                    "medication_display": display,
                    "medication_system": system,
                    "authored_date": _iso_to_datetime(resource.get("authoredOn")),
                    "status": resource.get("status"),
                    "intent": resource.get("intent"),
                    "dosage_text": (((resource.get("dosageInstruction") or [{}])[0] or {}).get("text") if isinstance(resource.get("dosageInstruction"), list) else None),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("medication", added)
        elif rtype == "Immunization":
            code, display, system = _coding_first(resource.get("vaccineCode"))
            added = _upsert_by_unique(
                session,
                Immunization,
                "immunization_id",
                rid,
                {
                    "immunization_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "vaccine_code": code,
                    "vaccine_display": display,
                    "vaccine_system": system,
                    "occurrence_date": _iso_to_datetime(resource.get("occurrenceDateTime")),
                    "status": resource.get("status"),
                    "lot_number": resource.get("lotNumber"),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("immunization", added)
        elif rtype == "Coverage":
            payer = None
            payors = resource.get("payor")
            if isinstance(payors, list) and payors:
                p0 = payors[0] if isinstance(payors[0], dict) else {}
                payer = p0.get("display") or p0.get("reference")
            period = resource.get("period") if isinstance(resource.get("period"), dict) else {}
            start_date = None
            end_date = None
            try:
                if isinstance(period.get("start"), str):
                    start_date = dt.date.fromisoformat(period["start"][:10])
                if isinstance(period.get("end"), str):
                    end_date = dt.date.fromisoformat(period["end"][:10])
            except ValueError:
                pass
            added = _upsert_by_unique(
                session,
                Coverage,
                "coverage_id",
                rid,
                {
                    "coverage_id": rid,
                    "patient_id": patient_id,
                    "period_start": start_date,
                    "period_end": end_date,
                    "payer": payer,
                    "source_file": row.source_file,
                    "source_record_id": row.source_record_id,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                },
            )
            inc("coverage", added)
        elif rtype == "Organization":
            added = _upsert_by_unique(
                session,
                Organization,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "name": resource.get("name"),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("organization", added)
        elif rtype == "Practitioner":
            names = resource.get("name") or []
            n0 = names[0] if isinstance(names, list) and names else {}
            given = " ".join(n0.get("given") or []) if isinstance(n0, dict) else ""
            family = n0.get("family") if isinstance(n0, dict) else ""
            full_name = " ".join([p for p in [given, family] if p]).strip() or None
            added = _upsert_by_unique(
                session,
                Practitioner,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "name": full_name,
                    "gender": resource.get("gender"),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("practitioner", added)
        elif rtype == "Location":
            position = resource.get("position") if isinstance(resource.get("position"), dict) else {}
            managing = resource.get("managingOrganization") if isinstance(resource.get("managingOrganization"), dict) else {}
            added = _upsert_by_unique(
                session,
                Location,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "organization_id": managing.get("reference"),
                    "name": resource.get("name"),
                    "latitude": str(position.get("latitude")) if position.get("latitude") is not None else None,
                    "longitude": str(position.get("longitude")) if position.get("longitude") is not None else None,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("location", added)
        elif rtype == "DocumentReference":
            type_code, type_display, _ = _coding_first(resource.get("type"))
            added = _upsert_by_unique(
                session,
                DocumentReference,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "encounter_id": None,
                    "status": resource.get("status"),
                    "type_code": type_code,
                    "type_display": type_display,
                    "date": _iso_to_datetime(resource.get("date")),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("document_reference", added)
        elif rtype == "AllergyIntolerance":
            code, display, _ = _coding_first(resource.get("code"))
            added = _upsert_by_unique(
                session,
                AllergyIntolerance,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "clinical_status": _coding_first(resource.get("clinicalStatus"))[0],
                    "code": code,
                    "display": display,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("allergy_intolerance", added)
        elif rtype == "Claim":
            added = _upsert_by_unique(
                session,
                Claim,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "status": resource.get("status"),
                    "created_date": None,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("claim", added)
        elif rtype == "Device":
            code, display, _ = _coding_first(resource.get("type"))
            added = _upsert_by_unique(
                session,
                Device,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "status": resource.get("status"),
                    "device_code": code,
                    "display": display,
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("device", added)
        elif rtype == "ImagingStudy":
            added = _upsert_by_unique(
                session,
                ImagingStudy,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "status": resource.get("status"),
                    "started_at": _iso_to_datetime(resource.get("started")),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("imaging_study", added)
        elif rtype == "SupplyDelivery":
            qty = resource.get("suppliedItem", {}).get("quantity", {}) if isinstance(resource.get("suppliedItem"), dict) else {}
            added = _upsert_by_unique(
                session,
                SupplyDelivery,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "status": resource.get("status"),
                    "quantity": qty.get("value") if isinstance(qty, dict) else None,
                    "display": ((resource.get("suppliedItem") or {}).get("itemCodeableConcept", {}).get("text") if isinstance(resource.get("suppliedItem"), dict) else None),
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("supply_delivery", added)
        elif rtype == "Basic":
            added = _upsert_by_unique(
                session,
                BasicResource,
                "resource_id",
                rid,
                {
                    "resource_id": rid,
                    "patient_id": patient_id,
                    "code": _coding_first(resource.get("code"))[0],
                    "source_file": row.source_file,
                    "fhir_resource_pk": row.id,
                    "batch_id": batch_id,
                    "resource_json": resource,
                },
            )
            inc("basic_resource", added)

    # Feed completeness and domain events per resource type for this job.
    expected_by_type: dict[str, int] = {}
    for row in resources:
        expected_by_type[row.resource_type] = expected_by_type.get(row.resource_type, 0) + 1
    for resource_type, observed_count in sorted(expected_by_type.items()):
        ratio = "1.0000"
        existing = session.execute(
            select(FeedCompletenessReport).where(
                (FeedCompletenessReport.job_id == job_id) & (FeedCompletenessReport.resource_type == resource_type)
            )
        ).scalar_one_or_none()
        values = {
            "job_id": job_id,
            "batch_id": batch_id,
            "resource_type": resource_type,
            "expected_count": observed_count,
            "observed_count": observed_count,
            "completeness_ratio": ratio,
            "details_json": {"source": "persist_denormalized_resources_for_job"},
        }
        if existing is None:
            session.add(FeedCompletenessReport(**values))
            counts.setdefault("feed_completeness_report", 0)
            counts["feed_completeness_report"] += 1
        else:
            for k, v in values.items():
                setattr(existing, k, v)

    session.add(
        DomainEvent(
            event_type="denormalization_completed",
            status="completed",
            job_id=job_id,
            batch_id=batch_id,
            payload_json={"inserted_counts": counts},
        )
    )
    counts.setdefault("domain_event", 0)
    counts["domain_event"] += 1
    return counts


def upsert_measure_result_and_gap(
    session: Session,
    *,
    measure_key: str,
    measure_name: str | None,
    measure_bundle_path: str | None,
    root_lib_ref: str | None,
    patient_id: str,
    member_id: str | None,
    measurement_year: int | None,
    status: str | None,
    outcome: str | None,
    populations: dict[str, Any],
    raw_result: dict[str, Any],
    job_id: str | None,
    batch_id: str | None,
) -> dict[str, bool]:
    md_existing = session.execute(
        select(MeasureDefinition).where(MeasureDefinition.measure_key == measure_key)
    ).scalar_one_or_none()
    md_values = {
        "measure_key": measure_key,
        "measure_name": measure_name,
        "measure_bundle_path": measure_bundle_path,
        "root_lib_ref": root_lib_ref,
        "metadata_json": {},
    }
    md_inserted = False
    if md_existing is None:
        session.add(MeasureDefinition(**md_values))
        md_inserted = True
    else:
        for k, v in md_values.items():
            setattr(md_existing, k, v)

    ipp = bool(populations.get("initial_population") or populations.get("IPP")) if isinstance(populations, dict) else None
    den = bool(populations.get("denominator") or populations.get("DEN")) if isinstance(populations, dict) else None
    num = bool(populations.get("numerator") or populations.get("NUM")) if isinstance(populations, dict) else None

    mr_existing = session.execute(
        select(MeasureResult).where(
            (MeasureResult.measure_key == measure_key)
            & (MeasureResult.patient_id == patient_id)
            & (MeasureResult.measurement_year == measurement_year)
        )
    ).scalar_one_or_none()
    mr_values = {
        "measure_key": measure_key,
        "patient_id": patient_id,
        "member_id": member_id,
        "job_id": job_id,
        "batch_id": batch_id,
        "measurement_year": measurement_year,
        "outcome": outcome,
        "status": status,
        "ipp": ipp,
        "den": den,
        "num": num,
        "populations_json": populations if isinstance(populations, dict) else {},
        "raw_result_json": raw_result if isinstance(raw_result, dict) else {},
    }
    mr_inserted = False
    if mr_existing is None:
        session.add(MeasureResult(**mr_values))
        mr_inserted = True
    else:
        for k, v in mr_values.items():
            setattr(mr_existing, k, v)

    gap_status = "open_gap"
    reason = None
    if outcome == "numerator_met":
        gap_status = "closed_gap"
        reason = "numerator_met"
    elif outcome == "not_in_population":
        gap_status = "not_applicable"
        reason = "not_in_population"
    elif outcome == "numerator_not_met":
        gap_status = "open_gap"
        reason = "numerator_not_met"
    elif outcome:
        gap_status = str(outcome)

    cg_existing = session.execute(
        select(CareGap).where(
            (CareGap.measure_key == measure_key)
            & (CareGap.patient_id == patient_id)
            & (CareGap.measurement_year == measurement_year)
        )
    ).scalar_one_or_none()
    cg_values = {
        "measure_key": measure_key,
        "patient_id": patient_id,
        "member_id": member_id,
        "measurement_year": measurement_year,
        "gap_status": gap_status,
        "reason": reason,
        "result_json": raw_result if isinstance(raw_result, dict) else {},
    }
    cg_inserted = False
    if cg_existing is None:
        session.add(CareGap(**cg_values))
        cg_inserted = True
    else:
        for k, v in cg_values.items():
            setattr(cg_existing, k, v)

    return {
        "measure_definition_inserted": md_inserted,
        "measure_result_inserted": mr_inserted,
        "care_gap_inserted": cg_inserted,
    }


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


def list_patient_summaries_for_job(
    session: Session,
    job_id: str,
    *,
    persisted_only: bool = False,
    bundleable_only: bool = True,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            FhirResource.patient_id.label("patient_id"),
            func.count(FhirResource.id).label("resource_count"),
            func.max(func.coalesce(FhirResource.updated_at, FhirResource.created_at)).label("saved_at"),
            func.coalesce(
                func.sum(func.length(func.cast(FhirResource.resource_json, Text))),
                0,
            ).label("estimated_size_bytes"),
        )
        .where(FhirResource.job_id == job_id)
        .where(FhirResource.patient_id.isnot(None))
        .group_by(FhirResource.patient_id)
        .order_by(FhirResource.patient_id.asc())
    )

    if persisted_only:
        stmt = stmt.where(FhirResource.persisted.is_(True))

    if bundleable_only:
        stmt = stmt.where(FhirResource.resource_json.isnot(None))

    rows = session.execute(stmt).all()
    output: list[dict[str, Any]] = []
    for row in rows:
        patient_id = row.patient_id.strip() if isinstance(row.patient_id, str) else ""
        if not patient_id:
            continue
        output.append(
            {
                "patient_id": patient_id,
                "resource_count": int(row.resource_count or 0),
                "estimated_size_bytes": int(row.estimated_size_bytes or 0),
                "saved_at": row.saved_at,
            }
        )
    return output


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

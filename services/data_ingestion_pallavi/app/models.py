from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base


JSONType = JSON().with_variant(JSONB, "postgresql")


class SourceBatch(Base):
    __tablename__ = "source_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_format: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    input_source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_count_validated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list[ConversionJob]] = relationship("ConversionJob", back_populates="source_batch")
    resources: Mapped[list[FhirResource]] = relationship("FhirResource", back_populates="source_batch")


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    input_dir: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_source_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resources_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resources_validated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resources_persisted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)

    resources: Mapped[list[FhirResource]] = relationship("FhirResource", back_populates="job")
    issues: Mapped[list[ValidationIssue]] = relationship("ValidationIssue", back_populates="job")
    source_batch: Mapped[SourceBatch | None] = relationship("SourceBatch", back_populates="jobs")


class FhirResource(Base):
    __tablename__ = "fhir_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    source_fingerprint: Mapped[str] = mapped_column(String(180), nullable=False)

    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="generated")
    lifecycle_status: Mapped[str] = mapped_column(String(30), nullable=False, default="generated")
    persisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    job: Mapped[ConversionJob | None] = relationship("ConversionJob", back_populates="resources")
    source_batch: Mapped[SourceBatch | None] = relationship("SourceBatch", back_populates="resources")
    issues: Mapped[list[ValidationIssue]] = relationship("ValidationIssue", back_populates="resource")

    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", name="uq_resource_type_id"),
        UniqueConstraint("source_fingerprint", name="uq_source_fingerprint"),
    )


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=False)
    resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(180), nullable=True)

    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    issue_category: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(240), nullable=True)
    diagnostic: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job: Mapped[ConversionJob] = relationship("ConversionJob", back_populates="issues")
    resource: Mapped[FhirResource | None] = relationship("FhirResource", back_populates="issues")


class Coverage(Base):
    """Denormalized coverage/enrollment table for HEDIS measure calculation."""
    __tablename__ = "coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coverage_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    period_start: Mapped[dt.date | None] = mapped_column(nullable=True, index=True)
    period_end: Mapped[dt.date | None] = mapped_column(nullable=True, index=True)
    payer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Gap detection fields (HEDIS continuous enrollment)
    gap_detected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    gap_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gap_start_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    gap_end_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    continuous_enrollment_compliant: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    
    # Source tracking
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    # Audit fields
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Patient(Base):
    """Denormalized patient demographics table."""
    __tablename__ = "patient"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    birth_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    gender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Encounter(Base):
    """Denormalized encounter/visit table."""
    __tablename__ = "encounter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    encounter_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    facility: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason_display: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Condition(Base):
    """Denormalized diagnosis/condition table."""
    __tablename__ = "condition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onset_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Observation(Base):
    """Denormalized observation/lab/vital result table."""
    __tablename__ = "observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    effective_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Procedure(Base):
    """Denormalized procedure/intervention table."""
    __tablename__ = "procedure"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    procedure_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system: Mapped[str | None] = mapped_column(String(255), nullable=True)
    performed_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Medication(Base):
    """Denormalized medication/prescription table."""
    __tablename__ = "medication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medication_request_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    medication_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    medication_display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    medication_system: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authored_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dosage_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Immunization(Base):
    """Denormalized immunization/vaccination table."""
    __tablename__ = "immunization"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    immunization_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    vaccine_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vaccine_display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    vaccine_system: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurrence_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    lot_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AllergyIntolerance(Base):
    __tablename__ = "allergy_intolerance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    clinical_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Claim(Base):
    __tablename__ = "claim"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_date: Mapped[dt.date | None] = mapped_column(nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Device(Base):
    __tablename__ = "device"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    device_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ImagingStudy(Base):
    __tablename__ = "imaging_study"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Practitioner(Base):
    __tablename__ = "practitioner"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SupplyDelivery(Base):
    __tablename__ = "supply_delivery"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity: Mapped[float | None] = mapped_column(nullable=True)
    display: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class BasicResource(Base):
    __tablename__ = "basic_resource"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_resource_pk: Mapped[int | None] = mapped_column(ForeignKey("fhir_resources.id"), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    resource_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("source_batches.id"), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=True, index=True)
    payload_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DqRun(Base):
    __tablename__ = "dq_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("source_batches.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=False, index=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("domain_events.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    summary_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("batch_id", "job_id", name="uq_dq_runs_batch_job"),
    )


class DataAnomaly(Base):
    __tablename__ = "data_anomalies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dq_run_id: Mapped[int] = mapped_column(ForeignKey("dq_runs.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("source_batches.id"), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    anomaly_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    source_component: Mapped[str] = mapped_column(String(80), nullable=False, default="dq_worker")
    details_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeedCompletenessReport(Base):
    __tablename__ = "feed_completeness_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dq_run_id: Mapped[int] = mapped_column(ForeignKey("dq_runs.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("source_batches.id"), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    measure_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dq_run_id", "resource_type", name="uq_feed_completeness_run_resource"),
    )


class DqCheckResult(Base):
    __tablename__ = "dq_check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dq_run_id: Mapped[int] = mapped_column(ForeignKey("dq_runs.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("source_batches.id"), nullable=False, index=True)
    check_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="passed", index=True)
    metric_name: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    details_json: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

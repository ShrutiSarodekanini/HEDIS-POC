"""ORM models for data ingestion service.

These models map to tables created by Alembic migrations. Only ingestion-specific tables
are defined here. Team schema tables (patient, encounter, condition, etc.) are created by
migrations 0001-0007 and are accessed directly via SQL or by other services.
"""

from __future__ import annotations

import datetime as dt
import uuid
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class SourceBatch(Base):
    __tablename__ = "source_batch"

    batch_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("source_batch.batch_id"), nullable=True, index=True)

    resources: Mapped[list[FhirResource]] = relationship("FhirResource", back_populates="job")
    issues: Mapped[list[ValidationIssue]] = relationship("ValidationIssue", back_populates="job")
    source_batch: Mapped[SourceBatch | None] = relationship("SourceBatch", back_populates="jobs")




class FhirResource(Base):
    __tablename__ = "fhir_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("source_batch.batch_id"), nullable=True, index=True)
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
# ============================================================================
# TEAM SCHEMA ORM MODELS
# ============================================================================
# These models represent the team's clinical schema tables created by migrations 0001-0007.
# They are used by the pipeline to populate denormalized data during ingestion.
# The tables are created and managed by Alembic migrations, not by these ORM definitions.
# Only essential columns for ORM mapping are included; additional columns are
# accessed directly via SQL in extraction functions as needed.


class Patient(Base):
    __tablename__ = "patient"
    patient_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    mrn: Mapped[str | None] = mapped_column(String(150), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    birth_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    race: Mapped[str | None] = mapped_column(String(150), nullable=True)
    ethnicity: Mapped[str | None] = mapped_column(String(150), nullable=True)
    birth_sex: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(120), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Encounter(Base):
    __tablename__ = "encounter"
    encounter_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    class_code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    practitioner_npi: Mapped[str | None] = mapped_column(String(150), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    period_start: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Condition(Base):
    __tablename__ = "condition"
    condition_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    clinical_status: Mapped[str | None] = mapped_column(String(150), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(150), nullable=True)
    code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    onset_date: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Observation(Base):
    __tablename__ = "observation"
    observation_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    category_code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    value_quantity: Mapped[float | None] = mapped_column(nullable=True)
    effective_datetime: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Procedure(Base):
    __tablename__ = "procedure"
    procedure_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    performed_start: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Medication(Base):
    __tablename__ = "medication"
    medication_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class MedicationRequest(Base):
    __tablename__ = "medication_request"
    medication_request_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    medication_code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    authored_on: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Immunization(Base):
    __tablename__ = "immunization"
    immunization_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vaccine_code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    occurrence_datetime: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Coverage(Base):
    __tablename__ = "coverage"
    coverage_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    payer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    period_start: Mapped[dt.date | None] = mapped_column(nullable=True)
    period_end: Mapped[dt.date | None] = mapped_column(nullable=True)
    source_eob_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class AllergyIntolerance(Base):
    __tablename__ = "allergy_intolerance"
    allergy_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    clinical_status: Mapped[str | None] = mapped_column(String(150), nullable=True)
    code: Mapped[str | None] = mapped_column(String(150), nullable=True)
    criticality: Mapped[str | None] = mapped_column(String(50), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Claim(Base):
    __tablename__ = "claim"
    claim_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class Device(Base):
    __tablename__ = "device"
    device_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class ImagingStudy(Base):
    __tablename__ = "imaging_study"
    imaging_study_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class Organization(Base):
    __tablename__ = "organization"
    organization_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    city: Mapped[str | None] = mapped_column(String(150), nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Practitioner(Base):
    __tablename__ = "practitioner"
    practitioner_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    npi: Mapped[str | None] = mapped_column(String(150), nullable=True, unique=True)
    family_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    active: Mapped[bool | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Location(Base):
    __tablename__ = "location"
    location_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class SupplyDelivery(Base):
    __tablename__ = "supply_delivery"
    supply_delivery_id: Mapped[str] = mapped_column(String(150), primary_key=True)


class BasicResource(Base):
    __tablename__ = "basic_resource"
    basic_resource_id: Mapped[str] = mapped_column(String(150), primary_key=True)


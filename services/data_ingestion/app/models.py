from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base


JSONType = JSON().with_variant(JSONB, "postgresql")


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

    resources: Mapped[list[FhirResource]] = relationship("FhirResource", back_populates="job")
    issues: Mapped[list[ValidationIssue]] = relationship("ValidationIssue", back_populates="job")


class FhirResource(Base):
    __tablename__ = "fhir_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_jobs.id"), nullable=True)
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

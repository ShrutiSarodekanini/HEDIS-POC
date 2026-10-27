from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={"ix": "idx_%(table_name)s_%(column_0_name)s"}
    )


class SourceBatch(Base):
    __tablename__ = "source_batch"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="source_batch_status_chk",
        ),
        CheckConstraint(
            "resource_count >= 0",
            name="source_batch_resource_count_chk",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_system: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    resource_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Organization(Base):
    __tablename__ = "organization"

    organization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="organization_batch_fk"),
        index=True,
    )


class Practitioner(Base):
    __tablename__ = "practitioner"
    __table_args__ = (
        UniqueConstraint("npi", name="practitioner_npi_uq"),
    )

    practitioner_id: Mapped[str] = mapped_column(Text, primary_key=True)
    npi: Mapped[str] = mapped_column(Text)
    family_name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool | None] = mapped_column(Boolean)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="practitioner_batch_fk"),
        index=True,
    )


class Patient(Base):
    __tablename__ = "patient"

    patient_id: Mapped[str] = mapped_column(Text, primary_key=True)
    mrn: Mapped[str | None] = mapped_column(Text, index=True)
    gender: Mapped[str | None] = mapped_column(Text)
    birth_date: Mapped[dt.date | None] = mapped_column(Date)
    race: Mapped[str | None] = mapped_column(Text)
    ethnicity: Mapped[str | None] = mapped_column(Text)
    birth_sex: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="patient_batch_fk"),
        index=True,
    )


class Location(Base):
    __tablename__ = "location"
    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="location_latitude_chk",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="location_longitude_chk",
        ),
    )

    location_id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "organization.organization_id",
            name="location_organization_fk",
        ),
        index=True,
    )
    name: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="location_batch_fk"),
        index=True,
    )


class Coverage(Base):
    __tablename__ = "coverage"
    __table_args__ = (
        CheckConstraint(
            "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
            name="coverage_period_chk",
        ),
    )

    coverage_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="coverage_patient_fk"),
        index=True,
    )
    payer_name: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[dt.date | None] = mapped_column(Date)
    period_end: Mapped[dt.date | None] = mapped_column(Date)
    source_eob_id: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="coverage_batch_fk"),
        index=True,
    )


class Encounter(Base):
    __tablename__ = "encounter"

    encounter_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="encounter_patient_fk"),
        index=True,
    )
    status: Mapped[str | None] = mapped_column(Text)
    class_code: Mapped[str | None] = mapped_column(Text)
    practitioner_npi: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("practitioner.npi", name="encounter_practitioner_fk"),
        index=True,
    )
    organization_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "organization.organization_id",
            name="encounter_organization_fk",
        ),
        index=True,
    )
    period_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="encounter_batch_fk"),
        index=True,
    )


class Condition(Base):
    __tablename__ = "condition"

    condition_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="condition_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("encounter.encounter_id", name="condition_encounter_fk"),
        index=True,
    )
    clinical_status: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    onset_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="condition_batch_fk"),
        index=True,
    )


class Observation(Base):
    __tablename__ = "observation"
    __table_args__ = (
        CheckConstraint(
            "category_code IS NULL OR category_code IN "
            "('vital-signs', 'laboratory', 'social-history')",
            name="observation_category_chk",
        ),
        Index("idx_observation_code", "code"),
    )

    observation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="observation_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("encounter.encounter_id", name="observation_encounter_fk"),
        index=True,
    )
    category_code: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    value_quantity: Mapped[Decimal | None] = mapped_column(Numeric)
    effective_datetime: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="observation_batch_fk"),
        index=True,
    )


class Procedure(Base):
    __tablename__ = "procedure"

    procedure_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="procedure_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("encounter.encounter_id", name="procedure_encounter_fk"),
        index=True,
    )
    status: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    performed_start: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="procedure_batch_fk"),
        index=True,
    )


class MedicationRequest(Base):
    __tablename__ = "medication_request"

    medication_request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="medication_request_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "encounter.encounter_id",
            name="medication_request_encounter_fk",
        ),
        index=True,
    )
    status: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    medication_code: Mapped[str | None] = mapped_column(Text)
    authored_on: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="medication_request_batch_fk"),
        index=True,
    )


class Immunization(Base):
    __tablename__ = "immunization"

    immunization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="immunization_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("encounter.encounter_id", name="immunization_encounter_fk"),
        index=True,
    )
    status: Mapped[str | None] = mapped_column(Text)
    vaccine_code: Mapped[str | None] = mapped_column(Text)
    occurrence_datetime: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="immunization_batch_fk"),
        index=True,
    )


class AllergyIntolerance(Base):
    __tablename__ = "allergy_intolerance"

    allergy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="allergy_intolerance_patient_fk"),
        index=True,
    )
    clinical_status: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    criticality: Mapped[str | None] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="allergy_intolerance_batch_fk"),
        index=True,
    )


class DocumentReference(Base):
    __tablename__ = "document_reference"

    document_reference_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("patient.patient_id", name="document_reference_patient_fk"),
        index=True,
    )
    encounter_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "encounter.encounter_id",
            name="document_reference_encounter_fk",
        ),
        index=True,
    )
    status: Mapped[str | None] = mapped_column(Text)
    type_code: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    doc_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="document_reference_batch_fk"),
        index=True,
    )


class FhirResourceRaw(Base):
    __tablename__ = "fhir_resource_raw"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            name="fhir_resource_raw_resource_uq",
        ),
        Index("idx_fhir_resource_raw_patient_id", "patient_id"),
        Index("idx_fhir_resource_raw_type", "resource_type"),
    )

    resource_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[str] = mapped_column(Text)
    patient_id: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="fhir_resource_raw_batch_fk"),
        index=True,
    )


class FeedCompletenessReport(Base):
    __tablename__ = "feed_completeness_report"
    __table_args__ = (
        CheckConstraint(
            "record_count >= 0",
            name="feed_completeness_report_record_count_chk",
        ),
        Index("idx_feed_completeness_report_resource_type", "resource_type"),
    )

    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "source_batch.batch_id",
            name="feed_completeness_report_batch_fk",
        ),
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(Text)
    record_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    measure_relevant: Mapped[bool] = mapped_column(Boolean)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class DomainEvent(Base):
    __tablename__ = "domain_event"
    __table_args__ = (
        CheckConstraint(
            "event_type IN "
            "('MemberResourceNormalized', 'FeedCompletenessReported')",
            name="domain_event_type_chk",
        ),
        Index("idx_domain_event_type", "event_type"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_batch.batch_id", name="domain_event_batch_fk"),
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class Member(Base):
    __tablename__ = "members"

    member_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(Text)


class Measure(Base):
    __tablename__ = "measures"

    measure_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    measurement_year: Mapped[int] = mapped_column(Integer)


class CareGap(Base):
    __tablename__ = "care_gaps"

    care_gap_id: Mapped[str] = mapped_column(Text, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        Text, ForeignKey("members.member_id"), index=True
    )
    measure_id: Mapped[str] = mapped_column(
        Text, ForeignKey("measures.measure_id"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    date_identified: Mapped[dt.date | None] = mapped_column(Date)
    measurement_year: Mapped[int] = mapped_column(Integer)


class SyntheaRecord(Base):
    __tablename__ = "synthea_records"

    record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    member_id: Mapped[str] = mapped_column(
        Text, ForeignKey("members.member_id"), index=True
    )
    care_gap_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("care_gaps.care_gap_id"), index=True
    )
    source_type: Mapped[str] = mapped_column(Text)
    source_date: Mapped[dt.date | None] = mapped_column(Date)
    fact_key: Mapped[str] = mapped_column(Text)
    fact_value: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(Text)

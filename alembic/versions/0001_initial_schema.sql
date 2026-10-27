-- HEDIS source-normalization schema
-- Generated from: Schema design.drawio.html
--
-- Execution order:
--   1. SOURCE_BATCH
--   2. ORGANIZATION, PRACTITIONER, PATIENT
--   3. LOCATION, COVERAGE, ENCOUNTER
--   4. CONDITION, OBSERVATION, PROCEDURE, MEDICATION_REQUEST,
--      IMMUNIZATION, ALLERGY_INTOLERANCE, DOCUMENT_REFERENCE
--   5. FHIR_RESOURCE_RAW, FEED_COMPLETENESS_REPORT, DOMAIN_EVENT
--   6. Application evidence tables (members, measures, care_gaps,
--      synthea_records)
--   7. Indexes
--
-- This script is additive only. It contains no DROP, DELETE, or TRUNCATE
-- statements and does not execute any data-changing operations.
--
-- Assumptions:
--   * Diagram relationships without mandatory cardinality are nullable.
--   * PostgreSQL unquoted identifiers are folded to lowercase; the names below
--     therefore preserve the diagram's names in PostgreSQL's normal form.
--   * FHIR_RESOURCE_RAW.patient_id is intentionally not an FK because the
--     diagram declares it as a raw-resource attribute, not a relationship.
--   * SOURCE_BATCH.batch_id is applied as an FK to batch-scoped entities,
--     consistent with the diagram's "every table above" relationship.

CREATE TABLE IF NOT EXISTS source_batch (
    batch_id UUID PRIMARY KEY,
    source_system TEXT NOT NULL,
    status TEXT NOT NULL,
    resource_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT source_batch_status_chk
        CHECK (status IN ('running', 'completed', 'failed')),
    CONSTRAINT source_batch_resource_count_chk
        CHECK (resource_count >= 0)
);

CREATE TABLE IF NOT EXISTS organization (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    batch_id UUID NOT NULL,
    CONSTRAINT organization_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS practitioner (
    practitioner_id TEXT PRIMARY KEY,
    npi TEXT NOT NULL,
    family_name TEXT,
    active BOOLEAN,
    batch_id UUID NOT NULL,
    CONSTRAINT practitioner_npi_uq UNIQUE (npi),
    CONSTRAINT practitioner_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS patient (
    patient_id TEXT PRIMARY KEY,
    mrn TEXT,
    gender TEXT,
    birth_date DATE,
    race TEXT,
    ethnicity TEXT,
    birth_sex TEXT,
    source_file TEXT,
    batch_id UUID NOT NULL,
    CONSTRAINT patient_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS location (
    location_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT,
    latitude NUMERIC,
    longitude NUMERIC,
    batch_id UUID NOT NULL,
    CONSTRAINT location_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organization (organization_id),
    CONSTRAINT location_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id),
    CONSTRAINT location_latitude_chk
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT location_longitude_chk
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE TABLE IF NOT EXISTS coverage (
    coverage_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    payer_name TEXT,
    period_start DATE,
    period_end DATE,
    source_eob_id TEXT,
    batch_id UUID NOT NULL,
    CONSTRAINT coverage_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT coverage_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id),
    CONSTRAINT coverage_period_chk
        CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS encounter (
    encounter_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    status TEXT,
    class_code TEXT,
    practitioner_npi TEXT,
    organization_id TEXT,
    period_start TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT encounter_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT encounter_practitioner_fk
        FOREIGN KEY (practitioner_npi) REFERENCES practitioner (npi),
    CONSTRAINT encounter_organization_fk
        FOREIGN KEY (organization_id) REFERENCES organization (organization_id),
    CONSTRAINT encounter_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS condition (
    condition_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    clinical_status TEXT,
    verification_status TEXT,
    code TEXT,
    onset_date TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT condition_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT condition_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT condition_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS observation (
    observation_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    category_code TEXT,
    code TEXT,
    value_quantity NUMERIC,
    effective_datetime TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT observation_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT observation_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT observation_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id),
    CONSTRAINT observation_category_chk
        CHECK (
            category_code IS NULL
            OR category_code IN ('vital-signs', 'laboratory', 'social-history')
        )
);

CREATE TABLE IF NOT EXISTS procedure (
    procedure_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    status TEXT,
    code TEXT,
    performed_start TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT procedure_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT procedure_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT procedure_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS medication_request (
    medication_request_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    status TEXT,
    intent TEXT,
    medication_code TEXT,
    authored_on TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT medication_request_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT medication_request_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT medication_request_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS immunization (
    immunization_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    status TEXT,
    vaccine_code TEXT,
    occurrence_datetime TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT immunization_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT immunization_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT immunization_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS allergy_intolerance (
    allergy_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    clinical_status TEXT,
    code TEXT,
    criticality TEXT,
    batch_id UUID NOT NULL,
    CONSTRAINT allergy_intolerance_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT allergy_intolerance_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS document_reference (
    document_reference_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    status TEXT,
    type_code TEXT,
    content_type TEXT,
    doc_date TIMESTAMPTZ,
    batch_id UUID NOT NULL,
    CONSTRAINT document_reference_patient_fk
        FOREIGN KEY (patient_id) REFERENCES patient (patient_id),
    CONSTRAINT document_reference_encounter_fk
        FOREIGN KEY (encounter_id) REFERENCES encounter (encounter_id),
    CONSTRAINT document_reference_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS fhir_resource_raw (
    resource_pk BIGSERIAL PRIMARY KEY,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    patient_id TEXT,
    raw JSONB NOT NULL,
    batch_id UUID NOT NULL,
    CONSTRAINT fhir_resource_raw_resource_uq
        UNIQUE (resource_type, resource_id),
    CONSTRAINT fhir_resource_raw_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id)
);

CREATE TABLE IF NOT EXISTS feed_completeness_report (
    report_id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL,
    resource_type TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    measure_relevant BOOLEAN NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT feed_completeness_report_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id),
    CONSTRAINT feed_completeness_report_record_count_chk
        CHECK (record_count >= 0)
);

CREATE TABLE IF NOT EXISTS domain_event (
    event_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    batch_id UUID NOT NULL,
    payload JSONB NOT NULL,
    published BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT domain_event_batch_fk
        FOREIGN KEY (batch_id) REFERENCES source_batch (batch_id),
    CONSTRAINT domain_event_type_chk
        CHECK (
            event_type IN (
                'MemberResourceNormalized',
                'FeedCompletenessReported'
            )
        )
);

-- Application tables used by the existing evidence-generation API. These are
-- intentionally additive to the ER-diagram tables so the current API can use
-- the same PostgreSQL database without changing its request contract.
CREATE TABLE IF NOT EXISTS members (
member_id TEXT PRIMARY KEY,
first_name TEXT,
last_name TEXT,
date_of_birth DATE,
gender TEXT
);

CREATE TABLE IF NOT EXISTS measures (
measure_id TEXT PRIMARY KEY,
name TEXT NOT NULL,
description TEXT,
measurement_year INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS care_gaps (
care_gap_id TEXT PRIMARY KEY,
member_id TEXT NOT NULL REFERENCES members (member_id),
measure_id TEXT NOT NULL REFERENCES measures (measure_id),
description TEXT NOT NULL,
status TEXT NOT NULL,
date_identified DATE,
measurement_year INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS synthea_records (
record_id TEXT PRIMARY KEY,
member_id TEXT NOT NULL REFERENCES members (member_id),
care_gap_id TEXT REFERENCES care_gaps (care_gap_id),
source_type TEXT NOT NULL,
source_date DATE,
fact_key TEXT NOT NULL,
fact_value TEXT,
provenance TEXT NOT NULL
);

-- Frequently queried relationship and lookup indexes.
CREATE INDEX IF NOT EXISTS idx_organization_batch_id
    ON organization (batch_id);
CREATE INDEX IF NOT EXISTS idx_location_organization_id
    ON location (organization_id);
CREATE INDEX IF NOT EXISTS idx_location_batch_id
    ON location (batch_id);
CREATE INDEX IF NOT EXISTS idx_practitioner_batch_id
    ON practitioner (batch_id);
CREATE INDEX IF NOT EXISTS idx_patient_batch_id
    ON patient (batch_id);
CREATE INDEX IF NOT EXISTS idx_patient_mrn
    ON patient (mrn);
CREATE INDEX IF NOT EXISTS idx_coverage_patient_id
    ON coverage (patient_id);
CREATE INDEX IF NOT EXISTS idx_coverage_batch_id
    ON coverage (batch_id);
CREATE INDEX IF NOT EXISTS idx_encounter_patient_id
    ON encounter (patient_id);
CREATE INDEX IF NOT EXISTS idx_encounter_practitioner_npi
    ON encounter (practitioner_npi);
CREATE INDEX IF NOT EXISTS idx_encounter_organization_id
    ON encounter (organization_id);
CREATE INDEX IF NOT EXISTS idx_encounter_batch_id
    ON encounter (batch_id);
CREATE INDEX IF NOT EXISTS idx_condition_patient_id
    ON condition (patient_id);
CREATE INDEX IF NOT EXISTS idx_condition_encounter_id
    ON condition (encounter_id);
CREATE INDEX IF NOT EXISTS idx_condition_batch_id
    ON condition (batch_id);
CREATE INDEX IF NOT EXISTS idx_observation_patient_id
    ON observation (patient_id);
CREATE INDEX IF NOT EXISTS idx_observation_encounter_id
    ON observation (encounter_id);
CREATE INDEX IF NOT EXISTS idx_observation_code
    ON observation (code);
CREATE INDEX IF NOT EXISTS idx_observation_batch_id
    ON observation (batch_id);
CREATE INDEX IF NOT EXISTS idx_procedure_patient_id
    ON procedure (patient_id);
CREATE INDEX IF NOT EXISTS idx_procedure_encounter_id
    ON procedure (encounter_id);
CREATE INDEX IF NOT EXISTS idx_procedure_batch_id
    ON procedure (batch_id);
CREATE INDEX IF NOT EXISTS idx_medication_request_patient_id
    ON medication_request (patient_id);
CREATE INDEX IF NOT EXISTS idx_medication_request_encounter_id
    ON medication_request (encounter_id);
CREATE INDEX IF NOT EXISTS idx_medication_request_batch_id
    ON medication_request (batch_id);
CREATE INDEX IF NOT EXISTS idx_immunization_patient_id
    ON immunization (patient_id);
CREATE INDEX IF NOT EXISTS idx_immunization_encounter_id
    ON immunization (encounter_id);
CREATE INDEX IF NOT EXISTS idx_immunization_batch_id
    ON immunization (batch_id);
CREATE INDEX IF NOT EXISTS idx_allergy_intolerance_patient_id
    ON allergy_intolerance (patient_id);
CREATE INDEX IF NOT EXISTS idx_allergy_intolerance_batch_id
    ON allergy_intolerance (batch_id);
CREATE INDEX IF NOT EXISTS idx_document_reference_patient_id
    ON document_reference (patient_id);
CREATE INDEX IF NOT EXISTS idx_document_reference_encounter_id
    ON document_reference (encounter_id);
CREATE INDEX IF NOT EXISTS idx_document_reference_batch_id
    ON document_reference (batch_id);
CREATE INDEX IF NOT EXISTS idx_fhir_resource_raw_batch_id
    ON fhir_resource_raw (batch_id);
CREATE INDEX IF NOT EXISTS idx_fhir_resource_raw_patient_id
    ON fhir_resource_raw (patient_id);
CREATE INDEX IF NOT EXISTS idx_fhir_resource_raw_type
    ON fhir_resource_raw (resource_type);
CREATE INDEX IF NOT EXISTS idx_feed_completeness_report_batch_id
    ON feed_completeness_report (batch_id);
CREATE INDEX IF NOT EXISTS idx_feed_completeness_report_resource_type
    ON feed_completeness_report (resource_type);
CREATE INDEX IF NOT EXISTS idx_domain_event_batch_id
    ON domain_event (batch_id);
CREATE INDEX IF NOT EXISTS idx_domain_event_type
    ON domain_event (event_type);
CREATE INDEX IF NOT EXISTS idx_care_gaps_member_id
    ON care_gaps (member_id);
CREATE INDEX IF NOT EXISTS idx_care_gaps_measure_id
    ON care_gaps (measure_id);
CREATE INDEX IF NOT EXISTS idx_synthea_records_member_id
    ON synthea_records (member_id);
CREATE INDEX IF NOT EXISTS idx_synthea_records_care_gap_id
    ON synthea_records (care_gap_id);

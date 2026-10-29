"""Copy one ingestion job's persisted FHIR resources into the team's clinical tables.

Reads only the rows the ingestion service's persist stage approved (fhir_resources with persisted = true and
lifecycle_status other than 'rejected'), and writes, in the caller's transaction: source_batch (one row),
fhir_resource_raw (every approved resource), patient, coverage, encounter, condition, observation, procedure,
medication_request and immunization. The batch's feed_completeness_report is written by the data quality checks
(services/integration/pallavi.py), which run next. All of it is set-based SQL over the stored JSON, so no resource
passes through Python.

The ingestion mapping produces no Organization or Practitioner resources and no organization on encounters, so
organization and practitioner stay empty and no patient has an attributed practice. Nothing is invented for them.

Safe to run twice for the same job: every insert skips rows that already exist, and existing patients keep their MRN.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

import psycopg

log = logging.getLogger(__name__)

SOURCE_SYSTEM = "synthea-csv-upload"

# The rows the persist stage approved for this job; every read below uses it.
_APPROVED = "r.job_id = %(job)s AND r.persisted AND r.lifecycle_status <> 'rejected'"

# FHIR id of the patient a resource belongs to: Patient itself, or its subject, beneficiary or patient reference.
_PATIENT_REF = """coalesce(CASE WHEN r.resource_type = 'Patient' THEN r.resource_id END,
    nullif(split_part(coalesce(r.resource_json->'subject'->>'reference', r.resource_json->'beneficiary'->>'reference',
                               r.resource_json->'patient'->>'reference'), '/', 2), ''))"""
_ENCOUNTER_REF = "nullif(split_part(r.resource_json->'encounter'->>'reference', '/', 2), '')"
_CODE = "r.resource_json->'code'->'coding'->0->>'code'"
_MEDICATION_CODE = """coalesce(r.resource_json->'medicationCodeableConcept'->'coding'->0->>'code',
  r.resource_json->'medication'->'concept'->'coding'->0->>'code')"""

_SOURCE_BATCH = """
INSERT INTO source_batch (batch_id, source_system, status, resource_count, started_at)
VALUES (%(batch)s, %(system)s, 'running', 0, now())
ON CONFLICT (batch_id) DO NOTHING
"""

_RAW = f"""
INSERT INTO fhir_resource_raw (resource_type, resource_id, patient_id, raw, batch_id)
SELECT r.resource_type, r.resource_id, {_PATIENT_REF}, r.resource_json, %(batch)s
  FROM fhir_resources r
 WHERE {_APPROVED}
ON CONFLICT (resource_type, resource_id) DO NOTHING
"""

# New patients get the next member IDs (S-000001, ...) in patient_id order; existing patients keep theirs.
_PATIENTS = f"""
WITH new AS (
    SELECT r.resource_id, r.resource_json AS j, r.source_file
      FROM fhir_resources r
     WHERE {_APPROVED} AND r.resource_type = 'Patient'
       AND NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = r.resource_id)
), base AS (
    SELECT coalesce(max(substring(mrn FROM '^S-([0-9]+)$')::int), 0) AS n FROM patient
)
INSERT INTO patient (patient_id, mrn, gender, birth_date, birth_sex, race, ethnicity, source_file, batch_id)
SELECT new.resource_id, 'S-' || lpad((base.n + row_number() OVER (ORDER BY new.resource_id))::text, 6, '0'),
       new.j->>'gender', (new.j->>'birthDate')::date,
       CASE new.j->>'gender' WHEN 'male' THEN 'M' WHEN 'female' THEN 'F' END,
       -- Extract race from US Core Race extension
       (
         SELECT (elem->>'valueString')
         FROM (
           SELECT jsonb_array_elements(elem->'extension') as elem
           FROM jsonb_array_elements(new.j->'extension') as ext(elem)
           WHERE ext.elem->>'url' = 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-race'
         ) race_ext
         WHERE elem->>'url' = 'text'
         LIMIT 1
       ),
       -- Extract ethnicity from US Core Ethnicity extension
       (
         SELECT (elem->>'valueString')
         FROM (
           SELECT jsonb_array_elements(elem->'extension') as elem
           FROM jsonb_array_elements(new.j->'extension') as ext(elem)
           WHERE ext.elem->>'url' = 'http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity'
         ) ethn_ext
         WHERE elem->>'url' = 'text'
         LIMIT 1
       ),
       new.source_file, %(batch)s
  FROM new, base
"""

_COVERAGE = f"""
INSERT INTO coverage (coverage_id, patient_id, payer_name, period_start, period_end, batch_id)
SELECT r.resource_id, p.patient_id, r.resource_json->'payor'->0->>'display',
       (r.resource_json->'period'->>'start')::timestamptz::date, (r.resource_json->'period'->>'end')::timestamptz::date,
       %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'beneficiary'->>'reference', '/', 2)
 WHERE {_APPROVED} AND r.resource_type = 'Coverage'
ON CONFLICT (coverage_id) DO NOTHING
"""

_ENCOUNTERS = f"""
INSERT INTO encounter (encounter_id, patient_id, status, class_code, period_start, batch_id)
SELECT r.resource_id, p.patient_id, r.resource_json->>'status', r.resource_json->'class'->>'code',
       (r.resource_json->'period'->>'start')::timestamptz, %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'subject'->>'reference', '/', 2)
 WHERE {_APPROVED} AND r.resource_type = 'Encounter'
ON CONFLICT (encounter_id) DO NOTHING
"""

# An encounter reference to an encounter that was not mapped is left empty rather than failing the foreign key.
_CONDITIONS = f"""
INSERT INTO condition (condition_id, patient_id, encounter_id, clinical_status, verification_status, code, onset_date,
                       batch_id)
SELECT r.resource_id, p.patient_id, e.encounter_id,
       r.resource_json->'clinicalStatus'->'coding'->0->>'code',
       r.resource_json->'verificationStatus'->'coding'->0->>'code', {_CODE},
       (r.resource_json->>'onsetDateTime')::timestamptz, %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'subject'->>'reference', '/', 2)
  LEFT JOIN encounter e ON e.encounter_id = {_ENCOUNTER_REF}
 WHERE {_APPROVED} AND r.resource_type = 'Condition'
ON CONFLICT (condition_id) DO NOTHING
"""

_OBSERVATIONS = f"""
INSERT INTO observation (observation_id, patient_id, encounter_id, category_code, code, value_quantity,
                         effective_datetime, batch_id)
SELECT r.resource_id, p.patient_id, e.encounter_id, r.resource_json->'category'->0->'coding'->0->>'code', {_CODE},
       (r.resource_json->'valueQuantity'->>'value')::numeric, (r.resource_json->>'effectiveDateTime')::timestamptz,
       %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'subject'->>'reference', '/', 2)
  LEFT JOIN encounter e ON e.encounter_id = {_ENCOUNTER_REF}
 WHERE {_APPROVED} AND r.resource_type = 'Observation'
ON CONFLICT (observation_id) DO NOTHING
"""

_PROCEDURES = f"""
INSERT INTO procedure (procedure_id, patient_id, encounter_id, status, code, performed_start, batch_id)
SELECT r.resource_id, p.patient_id, e.encounter_id, r.resource_json->>'status', {_CODE},
       coalesce(r.resource_json->>'performedDateTime', r.resource_json->'performedPeriod'->>'start')::timestamptz,
       %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'subject'->>'reference', '/', 2)
  LEFT JOIN encounter e ON e.encounter_id = {_ENCOUNTER_REF}
 WHERE {_APPROVED} AND r.resource_type = 'Procedure'
ON CONFLICT (procedure_id) DO NOTHING
"""

_MEDICATIONS = f"""
INSERT INTO medication_request (medication_request_id, patient_id, encounter_id, status, intent, medication_code,
                                authored_on, batch_id)
SELECT r.resource_id, p.patient_id, e.encounter_id, r.resource_json->>'status', r.resource_json->>'intent',
  {_MEDICATION_CODE},
       (r.resource_json->>'authoredOn')::timestamptz, %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'subject'->>'reference', '/', 2)
  LEFT JOIN encounter e ON e.encounter_id = {_ENCOUNTER_REF}
 WHERE {_APPROVED} AND r.resource_type = 'MedicationRequest'
ON CONFLICT (medication_request_id) DO NOTHING
"""

# Immunization points to its patient with "patient", not "subject"; the mapping gives it no encounter.
_IMMUNIZATIONS = f"""
INSERT INTO immunization (immunization_id, patient_id, encounter_id, status, vaccine_code, occurrence_datetime,
                          batch_id)
SELECT r.resource_id, p.patient_id, e.encounter_id, r.resource_json->>'status',
       r.resource_json->'vaccineCode'->'coding'->0->>'code', (r.resource_json->>'occurrenceDateTime')::timestamptz,
       %(batch)s
  FROM fhir_resources r
  JOIN patient p ON p.patient_id = split_part(r.resource_json->'patient'->>'reference', '/', 2)
  LEFT JOIN encounter e ON e.encounter_id = {_ENCOUNTER_REF}
 WHERE {_APPROVED} AND r.resource_type = 'Immunization'
ON CONFLICT (immunization_id) DO NOTHING
"""

# Attributed practice: the organization on most of the patient's encounters (none while the mapping has none).
_ATTRIBUTION = """
SELECT e.patient_id, mode() WITHIN GROUP (ORDER BY e.organization_id)
  FROM encounter e
 WHERE e.organization_id IS NOT NULL AND e.patient_id = ANY(%(patients)s)
 GROUP BY e.patient_id
"""

_STEPS = (("fhir_resource_raw", _RAW), ("patient", _PATIENTS), ("coverage", _COVERAGE), ("encounter", _ENCOUNTERS),
          ("condition", _CONDITIONS), ("observation", _OBSERVATIONS), ("procedure", _PROCEDURES),
          ("medication_request", _MEDICATIONS), ("immunization", _IMMUNIZATIONS))


@dataclass(frozen=True)
class LoadResult:
    batch_id: UUID
    patient_ids: tuple[str, ...]                 # every approved patient of the job, new or already loaded
    attributed_org: dict[str, str | None]        # patient_id -> organization on most encounters
    inserted: dict[str, int]                     # table -> rows added by this run
    resource_types: dict[str, int]               # resource type -> approved resources in the job


def load_job(conn: psycopg.Connection, job_id: str, batch_id: UUID) -> LoadResult:
    """Copy one ingestion job's approved resources into the clinical tables, in the caller's transaction."""
    params = {"job": job_id, "batch": batch_id, "system": SOURCE_SYSTEM}
    types = dict(conn.execute(
        f"SELECT r.resource_type, count(*) FROM fhir_resources r WHERE {_APPROVED} GROUP BY 1 ORDER BY 1", params
    ).fetchall())
    if not types.get("Patient"):
        raise LookupError(f"ingestion job {job_id} has no persisted Patient resources")
    conn.execute(_SOURCE_BATCH, params)
    inserted: dict[str, int] = {}
    for table, sql in _STEPS:
        inserted[table] = conn.execute(sql, params).rowcount
    patient_ids, attributed = job_patients(conn, job_id)
    conn.execute(
        """UPDATE source_batch SET resource_count = (SELECT count(*) FROM fhir_resource_raw WHERE batch_id = %(batch)s)
            WHERE batch_id = %(batch)s""", params)
    log.info("job %s loaded into batch %s: %s", job_id, batch_id, inserted)
    return LoadResult(batch_id, patient_ids, attributed, inserted, types)


def job_patients(conn: psycopg.Connection, job_id: str) -> tuple[tuple[str, ...], dict[str, str | None]]:
    """The job's approved patients already in the patient table, and each one's attributed practice."""
    patient_ids = tuple(r[0] for r in conn.execute(
        f"""SELECT p.patient_id FROM patient p
             WHERE p.patient_id IN (SELECT r.resource_id FROM fhir_resources r
                                     WHERE {_APPROVED} AND r.resource_type = 'Patient')
             ORDER BY p.patient_id""", {"job": job_id}).fetchall())
    attributed: dict[str, str | None] = dict.fromkeys(patient_ids)
    attributed.update(dict(conn.execute(_ATTRIBUTION, {"patients": list(patient_ids)}).fetchall()))
    return patient_ids, attributed

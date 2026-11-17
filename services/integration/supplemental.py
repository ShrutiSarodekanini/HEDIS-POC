"""Confirmed supplemental data as FHIR resources the measure engine reads.

A gap closes only through the engine (UC-HC-02): a reviewer confirms evidence, the decision writes a
supplemental_records row, and the next engine run for that patient adds the row to the patient's bundle as the FHIR
resource the measure's CQL looks for. The codes below are ones the measure bundles in
services/measure_calc/engine/examples test for. A record goes only into its own measure's bundle, so it never changes
another measure's populations.

A record the engine cannot read is not sent and comes back as unusable with a reason: an evidence type the measure's
CQL has no logic for, no service date, or a value that does not parse. Reasons name the record and evidence type,
never the value, because the value is text from a patient's document.
"""

import re
from dataclasses import dataclass
from datetime import date

import psycopg

SNOMED = "http://snomed.info/sct"
CPT = "http://www.ama-assn.org/go/cpt"
LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SYSTOLIC, DIASTOLIC, BP_PANEL = "8480-6", "8462-4", "85354-9"
US_CORE_BP = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-blood-pressure"
RECORD_TAG = "urn:hedis-poc:supplemental-record"

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")  # "HbA1c 7.2 %": the number before %, not the 1 in A1c
_BARE_NUMBER = re.compile(r"\s*(\d+(?:\.\d+)?)\s*")
_BP = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")


@dataclass(frozen=True)
class Target:
    """The FHIR shape one evidence type becomes. shape: procedure, observation, lab_result, percent, blood_pressure."""
    shape: str
    system: str
    code: str
    display: str


# Hospice Care Ambulatory and Palliative Care Intervention, read by the CMS Hospice and PalliativeCare libraries that
# CBP (CMS165) and CCS-E (CMS124) include. The other measures' CQL has no exclusion logic, so exclusions there have
# no engine input and stay unusable.
_HOSPICE = Target("procedure", SNOMED, "385763009", "Hospice care")
_PALLIATIVE = Target("procedure", SNOMED, "103735009", "Palliative care")
_RETINAL = Target("observation", LOINC, "71490-7", "Diabetic retinopathy evaluation")

TARGETS: dict[tuple[str, str], Target] = {
    ("BCS-E", "mammogram"): Target("procedure", SNOMED, "168749009", "Screening mammography"),
    ("COL-E", "colonoscopy"): Target("procedure", SNOMED, "73761001", "Colonoscopy"),
    ("COL-E", "fobt_or_fit"): Target("procedure", CPT, "82274", "Fecal immunochemical test"),
    ("COL-E", "stool_dna_with_fit"): Target("procedure", CPT, "81528", "Stool DNA with FIT"),
    ("EED", "retinal_or_dilated_eye_exam"): _RETINAL,
    ("EED", "retinal_exam_negative_for_retinopathy_prior_year"): _RETINAL,
    ("EED", "retinal_imaging_read_by_reading_center"): _RETINAL,
    ("EED", "autonomous_ai_retinal_exam"): _RETINAL,
    ("GSD", "hba1c"): Target("percent", LOINC, "4548-4", "Hemoglobin A1c"),
    ("KED", "egfr"): Target("observation", LOINC, "62238-1", "eGFR (CKD-EPI)"),
    ("KED", "uacr_ratio"): Target("observation", LOINC, "9318-7", "Albumin/creatinine ratio in urine"),
    ("CCS-E", "cervical_cytology"): Target("lab_result", LOINC, "10524-7", "Cervical cytology"),
    ("CCS-E", "hrhpv_test_age_30_plus"): Target("lab_result", LOINC, "21440-3", "HPV high risk DNA"),
    ("CCS-E", "hospice"): _HOSPICE,
    ("CCS-E", "palliative_care"): _PALLIATIVE,
    ("CBP", "blood_pressure_reading"): Target("blood_pressure", LOINC, BP_PANEL, "Blood pressure panel"),
    ("CBP", "hospice"): _HOSPICE,
    ("CBP", "palliative_care"): _PALLIATIVE,
}


@dataclass(frozen=True)
class Record:
    record_id: int
    patient_id: str
    measure_id: str
    evidence_type: str
    service_date: date | None
    value: str | None


@dataclass(frozen=True)
class Unusable:
    record_id: int
    measure_id: str
    evidence_type: str
    reason: str


@dataclass
class EngineInputs:
    """Resources per (patient_id, measure_id), and the records that could not become one."""
    resources: dict[tuple[str, str], list[dict]]
    used: list[int]
    unusable: list[Unusable]


_LOAD = """
SELECT id, patient_id, measure_id, evidence_type, service_date_from, value
  FROM supplemental_records
 WHERE patient_id = ANY(%s) AND measurement_year = %s
 ORDER BY id
"""


def load(conn: psycopg.Connection, patient_ids: list[str], year: int) -> EngineInputs:
    """Every supplemental record for these patients and year, as engine resources."""
    records = [Record(*row) for row in conn.execute(_LOAD, (list(patient_ids), year)).fetchall()]
    return build(records)


def build(records: list[Record]) -> EngineInputs:
    """Turn records into resources; a record that cannot be turned goes to unusable with its reason."""
    inputs = EngineInputs(resources={}, used=[], unusable=[])
    for record in records:
        try:
            made = to_resources(record)
        except ValueError as exc:
            inputs.unusable.append(Unusable(record.record_id, record.measure_id, record.evidence_type, str(exc)))
            continue
        inputs.resources.setdefault((record.patient_id, record.measure_id), []).extend(made)
        inputs.used.append(record.record_id)
    return inputs


def to_resources(record: Record) -> list[dict]:
    """The FHIR resources for one record. Raises ValueError, with a reason that holds no patient data."""
    target = TARGETS.get((record.measure_id, record.evidence_type))
    if target is None:
        raise ValueError(f"the {record.measure_id} engine logic has no input for {record.evidence_type}")
    if record.service_date is None:
        raise ValueError(f"supplemental record {record.record_id} has no service date")
    rid = f"supp-{record.record_id}"
    when = record.service_date.isoformat()
    subject = {"reference": f"Patient/{record.patient_id}"}
    meta = {"tag": [{"system": RECORD_TAG, "code": str(record.record_id)}]}
    code = {"coding": [{"system": target.system, "code": target.code, "display": target.display}]}
    if target.shape == "procedure":
        return [{"resourceType": "Procedure", "id": rid, "meta": meta, "status": "completed", "code": code,
                 "subject": subject, "performedDateTime": when}]
    observation = {"resourceType": "Observation", "id": rid, "meta": meta, "status": "final", "code": code,
                   "subject": subject, "effectiveDateTime": when}
    if target.shape == "observation":
        return [observation]
    if target.shape == "lab_result":
        category = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "laboratory"}]}
        return [{**observation, "category": [category], "valueString": "result recorded in the chart"}]
    if target.shape == "percent":
        return [{**observation, "valueQuantity": {"value": _percent(record), "unit": "%", "system": UCUM,
                                                   "code": "%"}}]
    return _blood_pressure(record, observation, meta, subject, when)


def _percent(record: Record) -> float:
    value = record.value or ""
    match = _PERCENT.search(value) or _BARE_NUMBER.fullmatch(value)
    number = float(match[1]) if match else None
    if number is None or not 2 <= number <= 25:
        raise ValueError(f"supplemental record {record.record_id}: the {record.evidence_type} value is not a "
                         "percentage between 2 and 25")
    return number


def _blood_pressure(record: Record, observation: dict, meta: dict, subject: dict, when: str) -> list[dict]:
    """A combined reading (US Core blood pressure profile) and the outpatient encounter it was taken at: CMS165
    drops readings whose encounter it cannot resolve, so the reading needs one."""
    match = _BP.search(record.value or "")
    if match is None:
        raise ValueError(f"supplemental record {record.record_id}: the blood pressure value is not systolic/diastolic")
    systolic, diastolic = int(match[1]), int(match[2])
    if not (50 <= systolic <= 300 and 30 <= diastolic <= 200 and diastolic < systolic):
        raise ValueError(f"supplemental record {record.record_id}: the blood pressure value is out of range")
    encounter_id = f"supp-{record.record_id}-enc"
    encounter = {"resourceType": "Encounter", "id": encounter_id, "meta": meta, "status": "finished",
                 "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
                 "subject": subject, "period": {"start": when, "end": when}}
    reading = {**observation, "meta": {**meta, "profile": [US_CORE_BP]},
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": "vital-signs"}]}],
               "encounter": {"reference": f"Encounter/{encounter_id}"},
               "component": [_component(SYSTOLIC, "Systolic blood pressure", systolic),
                             _component(DIASTOLIC, "Diastolic blood pressure", diastolic)]}
    return [encounter, reading]


def _component(code: str, display: str, value: int) -> dict:
    return {"code": {"coding": [{"system": LOINC, "code": code, "display": display}]},
            "valueQuantity": {"value": value, "unit": "mm[Hg]", "system": UCUM, "code": "mm[Hg]"}}

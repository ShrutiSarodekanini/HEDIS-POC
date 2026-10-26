from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from pydantic import ValidationError

from .synthea import ParsedDataset, RowIssue, SourceRecord


FHIR_NAMESPACE = uuid.UUID("44f46614-c2f4-42d0-8d47-f4f57a0535e0")

FHIR_MODEL_MAP = {
    "Patient": "fhir.resources.patient.Patient",
    "Encounter": "fhir.resources.encounter.Encounter",
    "Condition": "fhir.resources.condition.Condition",
    "Observation": "fhir.resources.observation.Observation",
    "Procedure": "fhir.resources.procedure.Procedure",
    "MedicationRequest": "fhir.resources.medicationrequest.MedicationRequest",
    "Immunization": "fhir.resources.immunization.Immunization",
    "CarePlan": "fhir.resources.careplan.CarePlan",
    "Claim": "fhir.resources.claim.Claim",
    "Coverage": "fhir.resources.coverage.Coverage",
}

# Cache for loaded FHIR models to avoid repeated expensive imports
_MODEL_CACHE: dict[str, type] = {}


@dataclass
class ResourceEnvelope:
    resource_type: str
    resource_id: str
    patient_id: str | None
    source_file: str
    source_record_id: str
    fhir: dict[str, Any]
    references: list[str] = field(default_factory=list)

    @property
    def source_fingerprint(self) -> str:
        payload = f"{self.source_file}:{self.source_record_id}:{self.resource_type}:{self.resource_id}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass
class MappingResult:
    resources: list[ResourceEnvelope]
    issues: list[RowIssue]


def deterministic_id(resource_type: str, source_file: str, source_record_id: str) -> str:
    key = f"{resource_type}:{source_file}:{source_record_id}"
    return str(uuid.uuid5(FHIR_NAMESPACE, key))


def _load_model(resource_type: str):
    # Check cache first to avoid expensive repeated imports
    if resource_type in _MODEL_CACHE:
        return _MODEL_CACHE[resource_type]
    
    dotted = FHIR_MODEL_MAP.get(resource_type)
    if dotted is None:
        raise ValueError(f"No FHIR model mapped for resource type {resource_type}.")
    module_name, class_name = dotted.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    model = getattr(module, class_name)
    _MODEL_CACHE[resource_type] = model
    return model


def to_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def to_iso_datetime(value: str | None) -> str | None:
    if not value:
        return None

    trimmed = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = dt.datetime.strptime(trimmed, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=0, minute=0, second=0)
            return parsed.isoformat() + "Z"
        except ValueError:
            continue

    # Support common ISO-8601 forms emitted by Synthea variants, including timezone offsets and milliseconds.
    try:
        normalized = trimmed.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed.isoformat() + "Z"
    except ValueError:
        return None


def to_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def codeable_concept(code: str | None, display: str | None, system: str = "http://snomed.info/sct") -> dict[str, Any] | None:
    if not code and not display:
        return None
    coding = {"system": system}
    if code:
        coding["code"] = code
    if display:
        coding["display"] = display
    return {"coding": [coding], "text": display or code}


def patient_ref(patient_id: str | None) -> dict[str, str] | None:
    if not patient_id:
        return None
    fhir_id = deterministic_id("Patient", "patients.csv", patient_id)
    return {"reference": f"Patient/{fhir_id}", "display": patient_id}


def encounter_ref(encounter_id: str | None) -> dict[str, str] | None:
    if not encounter_id:
        return None
    fhir_id = deterministic_id("Encounter", "encounters.csv", encounter_id)
    return {"reference": f"Encounter/{fhir_id}", "display": encounter_id}


def map_dataset(
    parsed: ParsedDataset,
    progress_callback: Callable[[str], None] | None = None,
    resource_callback: Callable[[ResourceEnvelope], None] | None = None,
) -> MappingResult:
    resources: list[ResourceEnvelope] = []
    issues: list[RowIssue] = []

    records = parsed.records_by_file

    for record in records.get("patients.csv", []):
        item, item_issues = _map_record(record, map_patient)
        issues.extend(item_issues)
        if item:
            resources.append(item)
            if resource_callback:
                resource_callback(item)
        if progress_callback:
            progress_callback("Patient")

    for filename, mapper in (
        ("encounters.csv", map_encounter),
        ("conditions.csv", map_condition),
        ("observations.csv", map_observation),
        ("procedures.csv", map_procedure),
        ("medications.csv", map_medication_request),
        ("immunizations.csv", map_immunization),
        ("careplans.csv", map_careplan),
        ("claims.csv", map_claim),
        ("coverages.csv", map_coverage),
        ("payer_transitions.csv", map_coverage),
    ):
        for record in records.get(filename, []):
            item, item_issues = _map_record(record, mapper)
            issues.extend(item_issues)
            if item:
                resources.append(item)
                if resource_callback:
                    resource_callback(item)
            if progress_callback:
                progress_callback(
                    {
                        "encounters.csv": "Encounter",
                        "conditions.csv": "Condition",
                        "observations.csv": "Observation",
                        "procedures.csv": "Procedure",
                        "medications.csv": "MedicationRequest",
                        "immunizations.csv": "Immunization",
                        "careplans.csv": "CarePlan",
                        "claims.csv": "Claim",
                        "coverages.csv": "Coverage",
                        "payer_transitions.csv": "Coverage",
                    }[filename]
                )

    return MappingResult(resources=resources, issues=issues)


def map_patient(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    name_text = " ".join(filter(None, [record.data.get("FIRST"), record.data.get("LAST")])).strip()
    birth_date = to_iso_date(record.data.get("BIRTHDATE"))
    if not birth_date:
        return None, [
            RowIssue(
                source_file=record.source_file,
                row_number=record.row_number,
                severity="error",
                code="invalid_birthdate",
                message="Patient row is missing a valid BIRTHDATE.",
                source_record_id=record.source_record_id,
            )
        ]

    resource_id = deterministic_id("Patient", record.source_file, record.source_record_id)
    raw_gender = (record.data.get("GENDER") or "unknown").strip().lower()
    if raw_gender in {"m", "male"}:
        gender = "male"
    elif raw_gender in {"f", "female"}:
        gender = "female"
    elif raw_gender in {"other", "unknown"}:
        gender = raw_gender
    else:
        gender = "unknown"
    if gender not in {"male", "female", "other", "unknown"}:
        gender = "unknown"

    fhir = {
        "resourceType": "Patient",
        "id": resource_id,
        "identifier": [{"system": "urn:synthea:patients", "value": record.source_record_id}],
        "name": [{"text": name_text or record.source_record_id}],
        "gender": gender,
        "birthDate": birth_date,
    }

    return (
        ResourceEnvelope(
            resource_type="Patient",
            resource_id=resource_id,
            patient_id=record.source_record_id,
            source_file=record.source_file,
            source_record_id=record.source_record_id,
            fhir=fhir,
        ),
        [],
    )


def map_encounter(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    resource_id = deterministic_id("Encounter", record.source_file, record.source_record_id)
    started = to_iso_datetime(record.data.get("START"))
    if not started:
        return None, [issue_invalid(record, "invalid_start", "Encounter START is required and must be a supported datetime.")]

    encounter_class_raw = (record.data.get("ENCOUNTERCLASS") or "").strip().lower()
    class_map = {
        "ambulatory": "AMB",
        "outpatient": "AMB",
        "inpatient": "IMP",
        "emergency": "EMER",
        "urgentcare": "AMB",
        "wellness": "AMB",
    }
    class_coding = {
        "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
        "code": class_map.get(encounter_class_raw, "AMB"),
    }

    period_payload = {"start": started}
    if end := to_iso_datetime(record.data.get("STOP")):
        period_payload["end"] = end

    fhir = {
        "resourceType": "Encounter",
        "id": resource_id,
        "status": "finished",
        "subject": patient_ref(record.patient_id),
        "identifier": [{"system": "urn:synthea:encounters", "value": record.source_record_id}],
        "class": class_coding,
        "period": period_payload,
    }

    encounter_type = codeable_concept(record.data.get("CODE"), record.data.get("DESCRIPTION"))
    if encounter_type:
        fhir["type"] = [encounter_type]

    if reason := codeable_concept(record.data.get("REASONCODE"), record.data.get("REASONDESCRIPTION")):
        fhir["reasonCode"] = [reason]

    return envelope(record, "Encounter", resource_id, fhir), []


def map_condition(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")
    if not code and not display:
        return None, [issue_invalid(record, "missing_code", "Condition row requires CODE or DESCRIPTION.")]

    onset = to_iso_datetime(record.data.get("START")) or to_iso_datetime(record.data.get("DATE"))
    fhir = {
        "resourceType": "Condition",
        "id": deterministic_id("Condition", record.source_file, record.source_record_id),
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "code": codeable_concept(code, display),
        "identifier": [{"system": "urn:synthea:conditions", "value": record.source_record_id}],
    }
    if onset:
        fhir["onsetDateTime"] = onset

    return envelope(record, "Condition", fhir["id"], fhir), []


def map_observation(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")
    if not code and not display:
        return None, [issue_invalid(record, "missing_code", "Observation row requires CODE or DESCRIPTION.")]

    effective = to_iso_datetime(record.data.get("DATE")) or to_iso_datetime(record.data.get("START"))
    value = record.data.get("VALUE")
    unit = record.data.get("UNITS")

    fhir = {
        "resourceType": "Observation",
        "id": deterministic_id("Observation", record.source_file, record.source_record_id),
        "status": "final",
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "code": codeable_concept(code, display, system="http://loinc.org"),
        "identifier": [{"system": "urn:synthea:observations", "value": record.source_record_id}],
    }

    if effective:
        fhir["effectiveDateTime"] = effective

    decimal_value = to_decimal(value)
    if decimal_value is not None:
        fhir["valueQuantity"] = {"value": float(decimal_value), "unit": unit or "1"}
    elif value:
        fhir["valueString"] = value

    return envelope(record, "Observation", fhir["id"], fhir), []


def map_procedure(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")
    if not code and not display:
        return None, [issue_invalid(record, "missing_code", "Procedure row requires CODE or DESCRIPTION.")]

    performed = to_iso_datetime(record.data.get("DATE")) or to_iso_datetime(record.data.get("START"))
    fhir = {
        "resourceType": "Procedure",
        "id": deterministic_id("Procedure", record.source_file, record.source_record_id),
        "status": "completed",
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "code": codeable_concept(code, display),
        "identifier": [{"system": "urn:synthea:procedures", "value": record.source_record_id}],
    }
    if performed:
        fhir["performedDateTime"] = performed

    return envelope(record, "Procedure", fhir["id"], fhir), []


def map_medication_request(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")
    if not code and not display:
        return None, [issue_invalid(record, "missing_code", "Medication row requires CODE or DESCRIPTION.")]

    authored = to_iso_datetime(record.data.get("START"))
    medication_concept = codeable_concept(code, display, system="http://www.nlm.nih.gov/research/umls/rxnorm")

    fhir = {
        "resourceType": "MedicationRequest",
        "id": deterministic_id("MedicationRequest", record.source_file, record.source_record_id),
        "status": "active",
        "intent": "order",
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "identifier": [{"system": "urn:synthea:medications", "value": record.source_record_id}],
        "medicationCodeableConcept": medication_concept,
    }

    if authored:
        fhir["authoredOn"] = authored

    return envelope(record, "MedicationRequest", fhir["id"], fhir), []


def map_immunization(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")
    occurrence = to_iso_datetime(record.data.get("DATE"))
    if not code and not display:
        return None, [issue_invalid(record, "missing_code", "Immunization row requires CODE or DESCRIPTION.")]

    fhir = {
        "resourceType": "Immunization",
        "id": deterministic_id("Immunization", record.source_file, record.source_record_id),
        "status": "completed",
        "patient": patient_ref(record.patient_id),
        "vaccineCode": codeable_concept(code, display, system="http://hl7.org/fhir/sid/cvx"),
        "identifier": [{"system": "urn:synthea:immunizations", "value": record.source_record_id}],
        "occurrenceDateTime": occurrence or dt.datetime.utcnow().isoformat() + "Z",
    }

    return envelope(record, "Immunization", fhir["id"], fhir), []


def map_careplan(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    code = record.data.get("CODE")
    display = record.data.get("DESCRIPTION")

    fhir = {
        "resourceType": "CarePlan",
        "id": deterministic_id("CarePlan", record.source_file, record.source_record_id),
        "status": "active",
        "intent": "plan",
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "identifier": [{"system": "urn:synthea:careplans", "value": record.source_record_id}],
    }

    if concept := codeable_concept(code, display):
        fhir["category"] = [concept]

    if start := to_iso_datetime(record.data.get("START")):
        fhir["period"] = {"start": start}

    return envelope(record, "CarePlan", fhir["id"], fhir), []


def map_claim(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    created = (
        to_iso_datetime(record.data.get("START"))
        or to_iso_datetime(record.data.get("DATE"))
        or to_iso_datetime(record.data.get("SERVICEDATE"))
        or to_iso_datetime(record.data.get("SERVICE_DATE"))
        or to_iso_datetime(record.data.get("CURRENTILLNESSDATE"))
        or dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    )

    fhir = {
        "resourceType": "Claim",
        "id": deterministic_id("Claim", record.source_file, record.source_record_id),
        "status": "active",
        "use": "claim",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
        "patient": patient_ref(record.patient_id),
        "provider": {"display": "Synthea Generated Provider"},
        "created": created,
        "priority": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority", "code": "normal"}]},
        "identifier": [{"system": "urn:synthea:claims", "value": record.source_record_id}],
    }

    if encounter := encounter_ref(record.encounter_id):
        fhir["encounter"] = [encounter]

    return envelope(record, "Claim", fhir["id"], fhir), []


def map_coverage(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    payer_display = record.data.get("PAYER", "Unknown") or "Unknown"
    subscriber_id = record.data.get("MEMBERID") or record.data.get("SUBSCRIBER")
    start = (
        to_iso_datetime(record.data.get("START_DATE"))
        or to_iso_datetime(record.data.get("START"))
        or to_iso_datetime(record.data.get("DATE"))
    )
    end = (
        to_iso_datetime(record.data.get("END_DATE"))
        or to_iso_datetime(record.data.get("END"))
        or to_iso_datetime(record.data.get("STOP"))
    )
    ownership = record.data.get("PLAN_OWNERSHIP") or record.data.get("OWNER_NAME")

    fhir = {
        "resourceType": "Coverage",
        "id": deterministic_id("Coverage", record.source_file, record.source_record_id),
        "status": "active",
        "beneficiary": patient_ref(record.patient_id),
        "identifier": [{"system": "urn:synthea:coverages", "value": record.source_record_id}],
        "payor": [{"display": payer_display}],
    }

    if subscriber_id:
        fhir["subscriberId"] = subscriber_id

    if start or end:
        period = {}
        if start:
            period["start"] = start
        if end:
            period["end"] = end
        fhir["period"] = period

    if ownership:
        fhir["class"] = [{"type": {"text": "ownership"}, "value": ownership}]

    if concept := codeable_concept(record.data.get("PLAN"), record.data.get("PLAN"), system="urn:synthea:plan"):
        fhir["type"] = concept
    elif plan_ownership := record.data.get("PLAN_OWNERSHIP"):
        fhir["type"] = {"text": plan_ownership}

    return envelope(record, "Coverage", fhir["id"], fhir), []


def issue_invalid(record: SourceRecord, code: str, message: str) -> RowIssue:
    return RowIssue(
        source_file=record.source_file,
        row_number=record.row_number,
        severity="error",
        code=code,
        message=message,
        source_record_id=record.source_record_id,
    )


def _map_record(
    record: SourceRecord,
    mapper: Callable[[SourceRecord], tuple[ResourceEnvelope | None, list[RowIssue]]],
) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    try:
        return mapper(record)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', []))}: {error.get('msg', 'FHIR validation error')}"
            for error in exc.errors()
        )
        return None, [
            issue_invalid(
                record,
                "fhir_model_validation_error",
                f"Mapped {record.source_file} row failed FHIR model validation: {details}",
            )
        ]


def envelope(record: SourceRecord, resource_type: str, resource_id: str, fhir: dict[str, Any]) -> ResourceEnvelope:
    normalized_fhir = _strip_none(fhir)

    refs = []
    if record.patient_id:
        refs.append(f"Patient/{deterministic_id('Patient', 'patients.csv', record.patient_id)}")
    if record.encounter_id:
        refs.append(f"Encounter/{deterministic_id('Encounter', 'encounters.csv', record.encounter_id)}")

    return ResourceEnvelope(
        resource_type=resource_type,
        resource_id=resource_id,
        patient_id=record.patient_id,
        source_file=record.source_file,
        source_record_id=record.source_record_id,
        fhir=normalized_fhir,
        references=refs,
    )


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value if v is not None]
    return value


def bundles_by_patient(resources: list[ResourceEnvelope]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ResourceEnvelope]] = {}
    for item in resources:
        if not item.patient_id:
            continue
        grouped.setdefault(item.patient_id, []).append(item)

    bundles: dict[str, dict[str, Any]] = {}
    for patient_id, items in grouped.items():
        entries = []
        for item in items:
            entries.append(
                {
                    "fullUrl": f"urn:uuid:{item.resource_id}",
                    "resource": item.fhir,
                }
            )
        bundles[patient_id] = {
            "resourceType": "Bundle",
            "id": deterministic_id("Bundle", "patient", patient_id),
            "type": "collection",
            "entry": entries,
        }

    return bundles

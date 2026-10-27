from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import groupby
from collections.abc import Iterable
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
    "MedicationDispense": "fhir.resources.medicationdispense.MedicationDispense",
    "Immunization": "fhir.resources.immunization.Immunization",
    "CarePlan": "fhir.resources.careplan.CarePlan",
    "Claim": "fhir.resources.claim.Claim",
    "Coverage": "fhir.resources.coverage.Coverage",
    "Location": "fhir.resources.location.Location",
    "AllergyIntolerance": "fhir.resources.allergyintolerance.AllergyIntolerance",
    "Device": "fhir.resources.device.Device",
    "ImagingStudy": "fhir.resources.imagingstudy.ImagingStudy",
    "Organization": "fhir.resources.organization.Organization",
    "Practitioner": "fhir.resources.practitioner.Practitioner",
    "PractitionerRole": "fhir.resources.practitionerrole.PractitionerRole",
    "SupplyDelivery": "fhir.resources.supplydelivery.SupplyDelivery",
    "Basic": "fhir.resources.basic.Basic",
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
    row_number: int = 0

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
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=0, minute=0, second=0)
            return parsed.isoformat() + "Z"
        except ValueError:
            continue
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
    validate: bool = True,
) -> MappingResult:
    """Map source records to FHIR resources.
    
    Args:
        parsed: Parsed dataset from adapters
        progress_callback: Optional callback for progress reporting
        resource_callback: Optional callback per resource
        validate: Whether to validate resources during mapping (default True).
                 Set to False if external validation will be performed later
                 to avoid duplicate validation.
    
    Returns:
        MappingResult with mapped resources and issues
    """
    resources: list[ResourceEnvelope] = []
    issues: list[RowIssue] = []

    records = parsed.records_by_file

    for record in records.get("patients.csv", []):
        item, item_issues = _map_record(record, map_patient, validate=validate)
        issues.extend(item_issues)
        if item:
            resources.append(item)
            if resource_callback:
                resource_callback(item)
        if progress_callback:
            progress_callback("Patient")

    # Process coverage records first so all coverage data is available for gap detection
    # Sort by patient_id to group contiguous records - enables processing one patient at a time
    coverage_records_raw = records.get("coverages.csv", [])
    coverage_records_sorted = sorted(coverage_records_raw, key=lambda r: r.patient_id or "")
    for patient_id, patient_coverage_iter in groupby(coverage_records_sorted, key=lambda r: r.patient_id):
        # For each patient, keep only their coverage records in memory for gap detection
        patient_coverage_list = list(patient_coverage_iter)
        for record in patient_coverage_list:
            item, item_issues = _map_record(record, lambda rec: map_coverage(rec, patient_coverage_list), validate=validate)
            issues.extend(item_issues)
            if item:
                resources.append(item)
                if resource_callback:
                    resource_callback(item)
        if progress_callback:
            progress_callback("Coverage")

    # Process other resource types (encounters, conditions, etc.)
    for filename, mapper in (
        ("encounters.csv", map_encounter),
        ("conditions.csv", map_condition),
        ("observations.csv", map_observation),
        ("procedures.csv", map_procedure),
        ("medications.csv", map_medication_request),
        ("immunizations.csv", map_immunization),
        ("careplans.csv", map_careplan),
        ("claims.csv", map_claim),
    ):
        for record in records.get(filename, []):
            item, item_issues = _map_record(record, mapper, validate=validate)
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
                    }[filename]
                )

    return MappingResult(resources=resources, issues=issues)


def map_source_record(
    record: SourceRecord,
    coverage_by_patient: dict[str | None, list[SourceRecord]] | list[SourceRecord] | None = None,
    validate: bool = True,
) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    """Map a single source record to FHIR.
    
    Args:
        record: Source record to map
        coverage_by_patient: For coverage records, either:
                           - dict[patient_id -> list of coverage records] (for full dataset)
                           - list of coverage records (for single patient)
                           - None (if not mapping coverage)
        validate: Whether to validate during mapping
    
    Returns:
        Tuple of (ResourceEnvelope or None, list of validation issues)
    """
    if record.source_file.startswith(("fhir:", "ccda:", "claims837:", "ncpdp:")):
        try:
            raw_resource = record.data["resource_json"]
            resource_json = json.loads(raw_resource) if isinstance(raw_resource, str) else raw_resource
            if not isinstance(resource_json, dict):
                raise TypeError("resource_json must be a JSON object")
            resource = ResourceEnvelope(
                resource_type=resource_json["resourceType"],
                resource_id=resource_json["id"],
                patient_id=record.patient_id,
                source_file=record.source_file,
                source_record_id=record.source_record_id,
                fhir=resource_json,
                references=[],
                row_number=record.row_number,
            )
            if validate:
                normalize_resource(resource)
            return resource, []
        except ValidationError as exc:
            return None, [validation_issue_for_record(record, exc)]
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            return None, [issue_invalid(record, "invalid_fhir_resource", str(exc))]

    mappers: dict[str, Callable[[SourceRecord], tuple[ResourceEnvelope | None, list[RowIssue]]]] = {
        "patients.csv": map_patient,
        "encounters.csv": map_encounter,
        "conditions.csv": map_condition,
        "observations.csv": map_observation,
        "procedures.csv": map_procedure,
        "medications.csv": map_medication_request,
        "immunizations.csv": map_immunization,
        "careplans.csv": map_careplan,
        "claims.csv": map_claim,
        "allergies.csv": map_allergy,
        "claims_transactions.csv": map_claim_transaction,
        "devices.csv": map_device,
        "imaging_studies.csv": map_imaging_study,
        "organizations.csv": map_organization,
        "payer_transitions.csv": map_payer_transition,
        "payers.csv": map_payer,
        "providers.csv": map_provider,
        "supplies.csv": map_supply,
    }
    if record.source_file == "coverages.csv":
        # Handle both dict (legacy/multi-patient) and list (new/single-patient) formats
        coverage_list: list[SourceRecord] = []
        if isinstance(coverage_by_patient, dict):
            coverage_list = coverage_by_patient.get(record.patient_id, [])
        elif isinstance(coverage_by_patient, list):
            coverage_list = coverage_by_patient
        
        return _map_record(
            record,
            lambda current: map_coverage(current, coverage_list),
            validate=validate,
        )
    mapper = mappers.get(record.source_file)
    if mapper is None:
        return None, []
    return _map_record(record, mapper, validate=validate)


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
    
    # Extract and normalize gender
    gender_raw = record.data.get("GENDER") or ""
    gender_raw = str(gender_raw).strip() if gender_raw else ""
    gender = "unknown"  # Default
    
    if gender_raw:
        gender_lower = gender_raw.lower()
        # Handle full words (male, female, other)
        if gender_lower in {"male", "female", "other"}:
            gender = gender_lower
        # Handle single letter codes (M, F, O)
        elif gender_lower in {"m"}:
            gender = "male"
        elif gender_lower in {"f"}:
            gender = "female"
        elif gender_lower in {"o"}:
            gender = "other"
        # Fallback: if it's not recognized, keep "unknown"

    # Build marital status if available (Synthea stores as "M", "S", "D", "W", etc.)
    marital_status = None
    marital_raw = record.data.get("MARITAL", "").strip()
    if marital_raw:
        # Map Synthea marital codes to FHIR display names
        marital_map = {"M": "Married", "S": "Single", "D": "Divorced", "W": "Widowed"}
        marital_display = marital_map.get(marital_raw, marital_raw)
        marital_status = {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-MaritalStatus", "code": marital_raw, "display": marital_display}]
        }

    # Build address if available
    address = None
    address_line = record.data.get("ADDRESS", "").strip()
    city = record.data.get("CITY", "").strip()
    state = record.data.get("STATE", "").strip()
    zip_code = record.data.get("ZIP", "").strip()
    
    if address_line or city or state:
        address = {
            "line": [address_line] if address_line else [],
            "city": city,
            "state": state,
            "postalCode": zip_code,
        }

    fhir = {
        "resourceType": "Patient",
        "id": resource_id,
        "identifier": [{"system": "urn:synthea:patients", "value": record.source_record_id}],
        "name": [{"text": name_text or record.source_record_id}],
        "gender": gender,
        "birthDate": birth_date,
    }

    # Add optional fields if present
    if marital_status:
        fhir["maritalStatus"] = marital_status
    if address:
        fhir["address"] = [address]

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

    fhir = {
        "resourceType": "Encounter",
        "id": resource_id,
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        "subject": patient_ref(record.patient_id),
        "period": {"start": started},
        "identifier": [{"system": "urn:synthea:encounters", "value": record.source_record_id}],
    }

    if end := to_iso_datetime(record.data.get("STOP")):
        fhir["period"]["end"] = end

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
    fhir = {
        "resourceType": "MedicationRequest",
        "id": deterministic_id("MedicationRequest", record.source_file, record.source_record_id),
        "status": "active",
        "intent": "order",
        "subject": patient_ref(record.patient_id),
        "encounter": encounter_ref(record.encounter_id),
        "medicationCodeableConcept": codeable_concept(code, display, system="http://www.nlm.nih.gov/research/umls/rxnorm"),
        "identifier": [{"system": "urn:synthea:medications", "value": record.source_record_id}],
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


def _map_additional_resource(
    record: SourceRecord,
    resource_type: str,
    fhir: dict[str, Any],
) -> tuple[ResourceEnvelope, list[RowIssue]]:
    return envelope(record, resource_type, fhir["id"], fhir), []


def map_allergy(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "AllergyIntolerance",
        "id": deterministic_id("AllergyIntolerance", record.source_file, record.source_record_id),
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "patient": patient_ref(record.patient_id),
        "code": codeable_concept(record.data.get("CODE"), record.data.get("DESCRIPTION")) or {"text": "Unknown allergy"},
        "type": record.data.get("TYPE") or "allergy",
    }
    if record.data.get("CATEGORY"):
        fhir["category"] = [record.data["CATEGORY"].lower()]
    if record.data.get("START"):
        fhir["onsetDateTime"] = to_iso_datetime(record.data["START"])
    return _map_additional_resource(record, "AllergyIntolerance", fhir)


def map_claim_transaction(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "Basic",
        "id": deterministic_id("Basic", record.source_file, record.source_record_id),
        "code": {"text": record.data.get("TYPE") or "Claim transaction"},
        "subject": patient_ref(record.patient_id),
        "created": to_iso_date(record.data.get("DATE")) or dt.datetime.utcnow().date().isoformat(),
    }
    return _map_additional_resource(record, "Basic", fhir)


def map_device(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "Device",
        "id": deterministic_id("Device", record.source_file, record.source_record_id),
        "status": "active",
        "type": codeable_concept(record.data.get("CODE"), record.data.get("DESCRIPTION")) or {"text": "Unknown device"},
        "patient": patient_ref(record.patient_id),
        "udiCarrier": [{"deviceIdentifier": record.data.get("UDI") or record.source_record_id}],
    }
    return _map_additional_resource(record, "Device", fhir)


def map_imaging_study(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    modality = record.data.get("MODALITY_CODE") or "OT"
    fhir = {
        "resourceType": "ImagingStudy",
        "id": deterministic_id("ImagingStudy", record.source_file, record.source_record_id),
        "status": "available",
        "subject": patient_ref(record.patient_id),
        "started": to_iso_datetime(record.data.get("DATE")) or dt.datetime.utcnow().isoformat() + "Z",
        "numberOfSeries": 0,
        "numberOfInstances": 0,
        "modality": [{"system": "http://dicom.nema.org/resources/ontology/DCM", "code": modality}],
    }
    return _map_additional_resource(record, "ImagingStudy", fhir)


def map_organization(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {"resourceType": "Organization", "id": deterministic_id("Organization", record.source_file, record.source_record_id), "name": record.data.get("NAME") or record.source_record_id}
    return _map_additional_resource(record, "Organization", fhir)


def map_payer_transition(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "Coverage",
        "id": deterministic_id("Coverage", record.source_file, record.source_record_id),
        "status": "active",
        "beneficiary": patient_ref(record.patient_id),
        "payor": [{"display": record.data.get("PAYER") or "Unknown payer"}],
    }
    start = to_iso_date(record.data.get("START"))
    end = to_iso_date(record.data.get("END"))
    if start or end:
        fhir["period"] = {key: value for key, value in (("start", start), ("end", end)) if value}
    return _map_additional_resource(record, "Coverage", fhir)


def map_payer(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {"resourceType": "Organization", "id": deterministic_id("Organization", record.source_file, record.source_record_id), "name": record.data.get("NAME") or record.source_record_id}
    return _map_additional_resource(record, "Organization", fhir)


def map_provider(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "Practitioner",
        "id": deterministic_id("Practitioner", record.source_file, record.source_record_id),
        "name": [{"text": record.data.get("NAME") or record.source_record_id}],
    }
    if record.data.get("GENDER"):
        fhir["gender"] = record.data["GENDER"].lower()
    return _map_additional_resource(record, "Practitioner", fhir)


def map_supply(record: SourceRecord) -> tuple[ResourceEnvelope, list[RowIssue]]:
    fhir = {
        "resourceType": "SupplyDelivery",
        "id": deterministic_id("SupplyDelivery", record.source_file, record.source_record_id),
        "status": "completed",
        "patient": patient_ref(record.patient_id),
        "suppliedItem": {
            "quantity": {"value": float(record.data.get("QUANTITY") or 1)},
            "itemCodeableConcept": codeable_concept(record.data.get("CODE"), record.data.get("DESCRIPTION")) or {"text": "Supply"},
        },
    }
    return _map_additional_resource(record, "SupplyDelivery", fhir)


def map_claim(record: SourceRecord) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    created = (
        to_iso_date(record.data.get("DATE"))
        or to_iso_date(record.data.get("START"))
        or dt.datetime.utcnow().date().isoformat()
    )
    fhir = {
        "resourceType": "Claim",
        "id": deterministic_id("Claim", record.source_file, record.source_record_id),
        "status": record.data.get("STATUS") or "active",
        "use": "claim",
        "created": created,
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
        "patient": patient_ref(record.patient_id),
        "provider": {"display": "Synthea Generated Provider"},
        "priority": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority", "code": "normal"}]},
        "insurance": [{
            "sequence": 1,
            "focal": True,
            "coverage": {"display": record.data.get("PAYER") or "Synthea coverage"},
        }],
        "identifier": [{"system": "urn:synthea:claims", "value": record.source_record_id}],
    }

    if encounter := encounter_ref(record.encounter_id):
        fhir["encounter"] = [encounter]

    return envelope(record, "Claim", fhir["id"], fhir), []


@dataclass
class EnrollmentGap:
    """Represents a gap in insurance coverage between periods."""
    gap_number: int
    gap_start_date: str  # ISO date
    gap_end_date: str    # ISO date
    gap_days: int
    hedis_compliant: bool  # True if gap <= 45 days


@dataclass
class EnrollmentAnalysis:
    """Analysis of coverage periods for a patient."""
    patient_id: str
    total_periods: int
    total_gap_days: int
    continuous_enrollment_compliant: bool
    gaps: list[EnrollmentGap]


def detect_enrollment_gaps(coverage_records: list[SourceRecord]) -> EnrollmentAnalysis:
    """
    Detect gaps in coverage for a patient.
    HEDIS requires continuous enrollment with max 45-day gap.
    
    Args:
        coverage_records: All coverage records for a single patient, from any source
        
    Returns:
        EnrollmentAnalysis with gap details for HEDIS compliance checking
    """
    if not coverage_records:
        return EnrollmentAnalysis(
            patient_id="",
            total_periods=0,
            total_gap_days=0,
            continuous_enrollment_compliant=True,
            gaps=[]
        )
    
    patient_id = coverage_records[0].patient_id or ""
    
    # Parse all coverage periods
    periods = []
    for record in coverage_records:
        start = to_iso_date(record.data.get("START"))
        end = to_iso_date(record.data.get("END"))
        
        if start and end:
            try:
                start_dt = dt.datetime.fromisoformat(start).date()
                end_dt = dt.datetime.fromisoformat(end).date()
                if start_dt <= end_dt:  # Validate period is valid
                    periods.append({
                        "start": start_dt,
                        "end": end_dt,
                        "start_iso": start,
                        "end_iso": end,
                        "payer": record.data.get("PAYER", "Unknown"),
                        "member_id": record.data.get("MEMBER_ID"),
                        "source_record_id": record.source_record_id
                    })
            except ValueError:
                # Invalid date format, skip this record
                continue
    
    if len(periods) <= 1:
        # No gaps possible with 0 or 1 period
        return EnrollmentAnalysis(
            patient_id=patient_id,
            total_periods=len(periods),
            total_gap_days=0,
            continuous_enrollment_compliant=True,
            gaps=[]
        )
    
    # Sort periods by start date
    periods.sort(key=lambda p: p["start"])
    
    # Detect gaps between consecutive periods
    gaps: list[EnrollmentGap] = []
    total_gap_days = 0
    
    for i in range(len(periods) - 1):
        current_end = periods[i]["end"]
        next_start = periods[i + 1]["start"]
        
        # Calculate gap: days between end of current and start of next
        # If end = 2024-06-30 and next start = 2024-09-01, gap = 62 days
        gap_delta = next_start - current_end
        gap_days = gap_delta.days - 1  # -1 because we count the days between, not including boundary dates
        
        if gap_days > 0:  # Only record positive gaps
            gap_start = current_end + dt.timedelta(days=1)
            gap_end = next_start - dt.timedelta(days=1)
            
            # HEDIS threshold: 45 days max continuous enrollment gap
            hedis_compliant = gap_days <= 45
            
            gaps.append(EnrollmentGap(
                gap_number=len(gaps) + 1,
                gap_start_date=gap_start.isoformat(),
                gap_end_date=gap_end.isoformat(),
                gap_days=gap_days,
                hedis_compliant=hedis_compliant
            ))
            
            total_gap_days += gap_days
    
    # Overall enrollment compliance: ALL gaps must be <= 45 days
    continuous_enrollment_compliant = all(g.hedis_compliant for g in gaps)
    
    return EnrollmentAnalysis(
        patient_id=patient_id,
        total_periods=len(periods),
        total_gap_days=total_gap_days,
        continuous_enrollment_compliant=continuous_enrollment_compliant,
        gaps=gaps
    )


def map_coverage(record: SourceRecord, all_coverage_records: list[SourceRecord] | None = None) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    """
    Map coverage CSV to FHIR Coverage resource with HEDIS gap detection.
    
    Args:
        record: Current coverage record to map
        all_coverage_records: Optional - ALL coverage records for this patient to enable gap detection.
                             If provided, gaps will be detected and added as FHIR extensions.
    """
    fhir = {
        "resourceType": "Coverage",
        "id": deterministic_id("Coverage", record.source_file, record.source_record_id),
        "status": "active",
        "beneficiary": patient_ref(record.patient_id),
        "payor": [{"display": record.data.get("PAYER", "Unknown") or "Unknown"}],
        "identifier": [{"system": "urn:synthea:coverages", "value": record.source_record_id}],
    }

    # Add coverage period if available
    if start := to_iso_date(record.data.get("START")):
        fhir["period"] = {"start": start}
        if end := to_iso_date(record.data.get("END")):
            fhir["period"]["end"] = end

    if concept := codeable_concept(record.data.get("PLAN"), record.data.get("PLAN"), system="urn:synthea:plan"):
        fhir["type"] = concept

    # ★ NEW: Add HEDIS gap detection extensions
    if all_coverage_records:
        analysis = detect_enrollment_gaps(all_coverage_records)
        
        if analysis.gaps:  # Has gaps (could be compliant or non-compliant)
            fhir["extension"] = []
            
            # Add overall gap detection flag
            fhir["extension"].append({
                "url": "http://hedis.ncqa.org/StructureDefinition/enrollment-gap-detected",
                "valueBoolean": True
            })
            
            # Add detailed gap information
            gap_details_ext = {
                "url": "http://hedis.ncqa.org/StructureDefinition/gap-details",
                "extension": [
                    {
                        "url": "http://hedis.ncqa.org/total-periods",
                        "valueInteger": analysis.total_periods
                    },
                    {
                        "url": "http://hedis.ncqa.org/total-gap-days",
                        "valueInteger": analysis.total_gap_days
                    },
                    {
                        "url": "http://hedis.ncqa.org/continuous-enrollment-compliant",
                        "valueBoolean": analysis.continuous_enrollment_compliant
                    }
                ]
            }
            
            # Add individual gap details
            for gap in analysis.gaps:
                gap_details_ext["extension"].append({
                    "url": "http://hedis.ncqa.org/gap",
                    "extension": [
                        {
                            "url": "gap-number",
                            "valueInteger": gap.gap_number
                        },
                        {
                            "url": "gap-start-date",
                            "valueDate": gap.gap_start_date
                        },
                        {
                            "url": "gap-end-date",
                            "valueDate": gap.gap_end_date
                        },
                        {
                            "url": "gap-days",
                            "valueInteger": gap.gap_days
                        },
                        {
                            "url": "hedis-45day-compliant",
                            "valueBoolean": gap.hedis_compliant
                        },
                        {
                            "url": "gap-severity",
                            "valueCode": "critical" if not gap.hedis_compliant else "info"
                        }
                    ]
                })
            
            fhir["extension"].append(gap_details_ext)
        else:
            # No gaps - add marker for clean enrollment
            fhir["extension"] = [{
                "url": "http://hedis.ncqa.org/StructureDefinition/continuous-enrollment-status",
                "extension": [
                    {
                        "url": "http://hedis.ncqa.org/continuous-enrollment-compliant",
                        "valueBoolean": True
                    },
                    {
                        "url": "http://hedis.ncqa.org/total-periods",
                        "valueInteger": analysis.total_periods
                    }
                ]
            }]

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
    validate: bool = True,
) -> tuple[ResourceEnvelope | None, list[RowIssue]]:
    try:
        resource, issues = mapper(record)
        if resource is not None and validate:
            normalize_resource(resource)
        return resource, issues
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
        fhir=fhir,
        references=refs,
        row_number=record.row_number,
    )


def normalize_resource(resource: ResourceEnvelope) -> ResourceEnvelope:
    model = _load_model(resource.resource_type)
    if hasattr(model, "model_validate"):
        validated = model.model_validate(resource.fhir)
        resource.fhir = validated.model_dump(exclude_none=True, by_alias=True, mode="json")
    else:
        validated = model.parse_obj(resource.fhir)
        resource.fhir = json.loads(validated.json(exclude_none=True, by_alias=True))
    return resource


def validation_issue(resource: ResourceEnvelope, exc: ValidationError) -> RowIssue:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', []))}: {error.get('msg', 'FHIR validation error')}"
        for error in exc.errors()
    )
    return issue_invalid(
        SourceRecord(
            source_file=resource.source_file,
            row_number=resource.row_number,
            source_record_id=resource.source_record_id,
            patient_id=resource.patient_id,
            encounter_id=None,
            data={},
        ),
        "fhir_model_validation_error",
        f"Mapped {resource.source_file} row failed FHIR model validation: {details}",
    )


def validation_issue_for_record(record: SourceRecord, exc: ValidationError) -> RowIssue:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', []))}: {error.get('msg', 'FHIR validation error')}"
        for error in exc.errors()
    )
    return issue_invalid(
        record,
        "fhir_model_validation_error",
        f"Mapped {record.source_file} row failed FHIR model validation: {details}",
    )


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

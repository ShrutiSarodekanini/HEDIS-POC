import json
from pathlib import Path

from services.data_ingestion.app.adapters.ncpdp_pharmacy_adapter import NCPDPPharmacyAdapter
from services.data_ingestion.app.mapping import map_dataset, map_source_record


def test_seed_pharmacy_batch_streams_mapper_compatible_resources():
    source = Path("data/seed/ncpdp/pharmacy_claims.ncpdp.txt")
    issues = []

    _, records_iter = NCPDPPharmacyAdapter().stream(str(source), issues.append)
    records = list(records_iter)
    mapped = [map_source_record(record, validate=True)[0] for record in records]

    assert issues == []
    assert len(records) == 21
    assert [record.data["resourceType"] for record in records].count("Patient") == 5
    assert [record.data["resourceType"] for record in records].count("MedicationRequest") == 8
    assert [record.data["resourceType"] for record in records].count("MedicationDispense") == 8
    assert all(resource is not None for resource in mapped)

    payloads = [json.loads(record.data["resource_json"]) for record in records]
    patient_references = {
        f"Patient/{payload['id']}" for payload in payloads if payload["resourceType"] == "Patient"
    }
    medication_references = {
        payload["subject"]["reference"] for payload in payloads if payload["resourceType"].startswith("Medication")
    }
    assert medication_references == patient_references

    parsed_mapping = map_dataset(NCPDPPharmacyAdapter().parse(str(source)), validate=True)
    assert len(parsed_mapping.resources) == 21


def test_pharmacy_transaction_without_patient_reports_controlled_issue(tmp_path):
    source = tmp_path / "D-INVALID.ncpdp"
    source.write_text("prescription_id|patient_id|medication_code\nRX-1||0001\n", encoding="utf-8")
    issues = []

    _, records = NCPDPPharmacyAdapter().stream(str(source), issues.append)

    assert list(records) == []
    assert [(issue.code, issue.message) for issue in issues] == [
        ("missing_patient_id", "Pharmacy transaction has no member or patient identifier."),
    ]
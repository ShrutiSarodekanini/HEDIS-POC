import json

from services.data_ingestion.app.adapters import FhirR4Adapter, registry

def test_fhir_adapter_is_registered_under_detected_name():
    assert registry.get_adapter("ehr_fhir_r4") is not None
    assert registry.get_adapter("ehr_fhir") is None


def test_fhir_extension_is_discovered_and_streamed(tmp_path):
    source = tmp_path / "resource.fhir"
    source.write_text(
        json.dumps({"resourceType": "Patient", "id": "patient-test"}),
        encoding="utf-8",
    )

    metadata, records = FhirR4Adapter().stream(str(tmp_path))

    assert metadata.found_files == ["resource.fhir"]
    assert metadata.row_counts == {"resource.fhir": 1}
    assert len(records) == 1
    assert json.loads(records[0].data["resource_json"])["resourceType"] == "Patient"

    parsed = FhirR4Adapter().parse(str(tmp_path))
    assert parsed.total_rows == 1
    assert len(parsed.records_by_file["resource.fhir"]) == 1


def test_invalid_fhir_json_reports_controlled_issue(tmp_path):
    source = tmp_path / "D-INVALID.json"
    source.write_text("{invalid", encoding="utf-8")
    issues = []

    _, records = FhirR4Adapter().stream(str(tmp_path), issues.append)

    assert list(records) == []
    assert [(issue.code, issue.message) for issue in issues] == [
        ("JSON_PARSE_ERROR", "FHIR JSON could not be parsed."),
    ]

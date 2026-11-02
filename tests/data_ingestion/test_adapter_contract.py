from services.data_ingestion.app.adapters import registry
from services.data_ingestion.app.mapping import _map_record, map_encounter
from services.data_ingestion.app.synthea import SourceRecord


def test_synthea_parse_preserves_existing_dataset_contract():
    adapter = registry.get_adapter("synthea_csv")

    parsed = adapter.parse("data/test_uploads/cohort_4_patients")

    assert parsed.total_rows == 6176
    assert len(parsed.records_by_file["patients.csv"]) == 4


def test_synthea_encounter_validates_as_r4_fhir():
    record = SourceRecord(
        source_file="encounters.csv",
        row_number=1,
        source_record_id="synthetic-encounter",
        patient_id="synthetic-patient",
        encounter_id=None,
        data={"START": "2026-01-01T10:00:00Z", "STOP": "2026-01-01T11:00:00Z"},
    )

    resource, issues = _map_record(record, map_encounter)

    assert issues == []
    assert resource is not None
    assert resource.fhir["period"]["start"] == "2026-01-01T10:00:00Z"
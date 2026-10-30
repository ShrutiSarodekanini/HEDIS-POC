from services.data_ingestion.app.adapters import registry


def test_synthea_parse_preserves_existing_dataset_contract():
    adapter = registry.get_adapter("synthea_csv")

    parsed = adapter.parse("data/test_uploads/cohort_4_patients")

    assert parsed.total_rows == 6176
    assert len(parsed.records_by_file["patients.csv"]) == 4
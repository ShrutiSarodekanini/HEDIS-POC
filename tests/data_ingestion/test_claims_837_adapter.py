from pathlib import Path

from services.data_ingestion.app.adapters.claims_837_adapter import Claims837Adapter
from services.data_ingestion.app.mapping import map_dataset, map_source_record


def test_seed_claim_streams_mapper_compatible_resources():
    source = Path("data/seed/claims/healthcare_claims.837")
    issues = []

    _, records_iter = Claims837Adapter().stream(str(source), issues.append)
    records = list(records_iter)
    mapped = [map_source_record(record, validate=True)[0] for record in records]

    assert issues == []
    assert {record.data["resourceType"] for record in records} == {
        "Claim",
        "Condition",
        "Encounter",
        "Patient",
        "Procedure",
    }
    assert all(resource is not None for resource in mapped)

    parsed_mapping = map_dataset(Claims837Adapter().parse(str(source)), validate=True)
    assert len(parsed_mapping.resources) == 5


def test_claim_without_claim_segment_reports_controlled_issue(tmp_path):
    source = tmp_path / "D-TEST.837"
    source.write_text("ISA*00*~GS*HC*~", encoding="utf-8")
    issues = []

    _, records = Claims837Adapter().stream(str(source), issues.append)

    assert list(records) == []
    assert [(issue.code, issue.message) for issue in issues] == [
        ("invalid_claim", "837 transaction contains no claim segments."),
    ]
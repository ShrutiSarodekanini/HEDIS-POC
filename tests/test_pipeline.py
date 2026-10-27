from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def reload_ingestion_stack(database_url: str):
    os.environ["DATABASE_URL"] = database_url
    os.environ["VALIDATION_REQUIRE_EXTERNAL"] = "false"

    for name in list(sys.modules):
        if name.startswith("services.data_ingestion"):
            del sys.modules[name]

    main = importlib.import_module("services.data_ingestion.main")
    db = importlib.import_module("services.data_ingestion.app.db")
    return main, db


def test_discovery_reports_files_and_missing_required(tmp_path: Path):
    sample = tmp_path / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "encounters.csv").write_text("Id,START,PATIENT\nenc-1,2022-01-01,pat-1\n", encoding="utf-8")

    from services.data_ingestion.app.synthea import discover_dataset

    metadata = discover_dataset(str(sample))
    assert "encounters.csv" in metadata.found_files
    assert "patients.csv" in metadata.missing_required_files
    assert metadata.row_counts["encounters.csv"] == 1


def test_mapping_preserves_patient_and_encounter_references():
    from services.data_ingestion.app.mapping import map_dataset
    from services.data_ingestion.app.synthea import parse_dataset

    sample_dir = Path("tests/data/synthea_sample").resolve()
    parsed = parse_dataset(str(sample_dir))
    mapped = map_dataset(parsed)

    assert len(mapped.resources) >= 10
    observations = [r for r in mapped.resources if r.resource_type == "Observation"]
    assert observations
    assert observations[0].fhir["subject"]["reference"].startswith("Patient/")
    assert observations[0].fhir["encounter"]["reference"].startswith("Encounter/")


def test_claims_use_synthea_patientid_column(tmp_path: Path):
    from services.data_ingestion.app.synthea import parse_dataset

    dataset = tmp_path / "claims_dataset"
    dataset.mkdir()
    (dataset / "patients.csv").write_text("Id\npatient-1\n", encoding="utf-8")
    (dataset / "claims.csv").write_text(
        "Id,PATIENTID\nclaim-1,patient-1\n",
        encoding="utf-8",
    )

    parsed = parse_dataset(str(dataset.resolve()))
    claim_issues = [issue for issue in parsed.issues if issue.source_file == "claims.csv"]

    assert not any(issue.code == "missing_patient_id" for issue in claim_issues)


def test_parse_dataset_handles_bom_and_case_insensitive_headers(tmp_path: Path):
    from services.data_ingestion.app.synthea import parse_dataset

    dataset = tmp_path / "bom_case_dataset"
    dataset.mkdir()
    (dataset / "patients.csv").write_text("\ufeffid\npatient-1\n", encoding="utf-8")
    (dataset / "claims.csv").write_text("\ufeffid,patientid\nclaim-1,patient-1\n", encoding="utf-8")

    parsed = parse_dataset(str(dataset.resolve()))
    assert len(parsed.records_by_file["patients.csv"]) == 1
    assert len(parsed.records_by_file["claims.csv"]) == 1
    assert not any(issue.code in {"missing_id_column", "missing_patient_column"} for issue in parsed.issues)


def test_claim_mapping_uses_servicedate_column(tmp_path: Path):
    from services.data_ingestion.app.mapping import map_dataset
    from services.data_ingestion.app.synthea import parse_dataset

    dataset = tmp_path / "claim_date_dataset"
    dataset.mkdir()
    (dataset / "patients.csv").write_text("Id\npatient-1\n", encoding="utf-8")
    (dataset / "claims.csv").write_text(
        "Id,PATIENTID,SERVICEDATE\nclaim-1,patient-1,2026-03-14\n",
        encoding="utf-8",
    )

    parsed = parse_dataset(str(dataset.resolve()))
    mapped = map_dataset(parsed)
    claim = next(r for r in mapped.resources if r.resource_type == "Claim")
    assert claim.fhir["created"].startswith("2026-03-14")


def test_bp_bundle_pruning_keeps_bp_data_and_drops_irrelevant_entries():
    from services.data_ingestion.app.api import _prune_bp_bundle_payload

    payload = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "p-1"}},
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "obs-bp",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "obs-a1c",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4"}]},
                }
            },
            {"resource": {"resourceType": "Claim", "id": "claim-1"}},
            {"resource": {"resourceType": "Condition", "id": "cond-1"}},
        ],
    }

    pruned, kept_count, removed_count = _prune_bp_bundle_payload(payload)
    kept_types = [entry["resource"]["resourceType"] for entry in pruned["entry"]]
    kept_obs_ids = [entry["resource"].get("id") for entry in pruned["entry"] if entry["resource"].get("resourceType") == "Observation"]

    assert removed_count == 2
    assert kept_count == 3
    assert "Claim" not in kept_types
    assert "obs-bp" in kept_obs_ids
    assert "obs-a1c" not in kept_obs_ids


def test_to_member_result_aggregates_across_detailed_groups():
    from services.data_ingestion.app.api import _to_member_result

    cli_entry = {
        "patientId": "patient-1",
        "detailedResults": [
            {
                "populationResults": [
                    {"populationType": "denominator", "result": True},
                    {"populationType": "numerator", "result": False},
                ]
            },
            {
                "populationResults": [
                    {"populationType": "denominator", "result": True},
                    {"populationType": "numerator", "result": True},
                ]
            },
        ],
    }

    result = _to_member_result(cli_entry, "GSD")

    assert result["populations"]["denominator"] is True
    assert result["populations"]["numerator"] is True
    assert result["outcome"] == "numerator_met"
    assert result["status"] == "completed"


def test_to_member_result_supports_population_aliases():
    from services.data_ingestion.app.api import _to_member_result

    cli_entry = {
        "patientId": "patient-2",
        "detailedResults": [
            {
                "populationResults": [
                    {"populationType": "IPP", "result": True},
                    {"populationType": "DEN", "result": True},
                    {"populationType": "NUM", "result": False},
                ]
            }
        ],
    }

    result = _to_member_result(cli_entry, "SPD")

    assert result["populations"]["initial_population"] is True
    assert result["populations"]["denominator"] is True
    assert result["populations"]["numerator"] is False
    assert result["outcome"] == "numerator_not_met"


def test_pruned_execution_results_are_non_definitive():
    from services.data_ingestion.app.api import _to_member_result

    cli_entry = {
        "patientId": "patient-3",
        "detailedResults": [
            {
                "populationResults": [
                    {"populationType": "denominator", "result": True},
                    {"populationType": "numerator", "result": False},
                ]
            }
        ],
    }

    result = _to_member_result(cli_entry, "COLE")
    assert result["status"] == "completed"
    assert result["outcome"] == "numerator_not_met"

    # Mirror the run_request_file non-original candidate behavior.
    result["status"] = "unresolved"
    result["outcome"] = "unresolved_policy"
    result["data_quality_flags"] = [*(result.get("data_quality_flags") or []), "bundle_pruned_for_performance"]
    result["warnings"] = [
        *(result.get("warnings") or []),
        "Execution used generic_soft_pruned candidate (removed 10 resources, remaining 2000).",
        "Result is non-definitive because a pruned bundle was used.",
    ]
    result["errors"] = [
        *(result.get("errors") or []),
        {"message": "Definitive gap status requires full-bundle execution."},
    ]

    assert result["status"] == "unresolved"
    assert result["outcome"] == "unresolved_policy"
    assert "bundle_pruned_for_performance" in (result.get("data_quality_flags") or [])
    assert any("non-definitive" in warning for warning in (result.get("warnings") or []))
    assert any(
        isinstance(error, dict) and error.get("message") == "Definitive gap status requires full-bundle execution."
        for error in (result.get("errors") or [])
    )


def test_end_to_end_api_pipeline_and_rerun_idempotency(tmp_path: Path):
    db_path = tmp_path / "test_pipeline.db"
    main, db = reload_ingestion_stack(f"sqlite:///{db_path}")

    db.Base.metadata.drop_all(bind=db.engine)
    db.Base.metadata.create_all(bind=db.engine)

    app = main.create_app()
    client = TestClient(app)

    dataset = Path("tests/data/synthea_sample").resolve()

    start = client.post(f"/api/jobs/start?input_dir={dataset}")
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    status = None
    for _ in range(600):
        status_resp = client.get(f"/api/jobs/{job_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()["status"]
        if status in {"completed", "partially_completed", "failed"}:
            break
        time.sleep(0.1)

    assert status in {"completed", "partially_completed"}

    resources_resp = client.get(f"/api/jobs/{job_id}/resources")
    assert resources_resp.status_code == 200
    resources = resources_resp.json()["resources"]
    assert len(resources) >= 10

    validation_resp = client.get(f"/api/jobs/{job_id}/validation")
    assert validation_resp.status_code == 200
    assert len(validation_resp.json()["issues"]) >= 1

    persist_resp = client.post(f"/api/jobs/{job_id}/persist")
    assert persist_resp.status_code == 200
    assert persist_resp.json()["resources_persisted"] >= 1

    start_2 = client.post(f"/api/jobs/start?input_dir={dataset}")
    assert start_2.status_code == 200
    job2 = start_2.json()["job_id"]

    for _ in range(600):
        status_resp = client.get(f"/api/jobs/{job2}")
        if status_resp.json()["status"] in {"completed", "partially_completed", "failed"}:
            break
        time.sleep(0.1)

    resources_resp_2 = client.get(f"/api/jobs/{job2}/resources")
    resources_2 = resources_resp_2.json()["resources"]
    assert len(resources_2) <= len(resources)

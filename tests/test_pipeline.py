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
    for _ in range(50):
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
    assert len(validation_resp.json()["issues"]) >= len(resources)

    persist_resp = client.post(f"/api/jobs/{job_id}/persist")
    assert persist_resp.status_code == 200
    assert persist_resp.json()["resources_persisted"] >= 1

    start_2 = client.post(f"/api/jobs/start?input_dir={dataset}")
    assert start_2.status_code == 200
    job2 = start_2.json()["job_id"]

    for _ in range(50):
        status_resp = client.get(f"/api/jobs/{job2}")
        if status_resp.json()["status"] in {"completed", "partially_completed", "failed"}:
            break
        time.sleep(0.1)

    resources_resp_2 = client.get(f"/api/jobs/{job2}/resources")
    resources_2 = resources_resp_2.json()["resources"]
    assert len(resources_2) <= len(resources)

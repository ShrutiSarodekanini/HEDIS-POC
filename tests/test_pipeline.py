from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def test_encounter_mapping_uses_validated_source_fields():
    from services.data_ingestion.app.mapping import map_encounter
    from services.data_ingestion.app.synthea import SourceRecord

    encounter, issues = map_encounter(
        SourceRecord(
            source_file="encounters.csv",
            row_number=1,
            source_record_id="encounter-1",
            patient_id="patient-1",
            encounter_id=None,
            data={"START": "2026-01-01T10:00:00Z", "STOP": "2026-01-01T11:00:00Z"},
        )
    )

    assert issues == []
    assert encounter is not None
    assert encounter.fhir["actualPeriod"]["start"]
    assert "period" not in encounter.fhir


def test_procedure_and_medication_mapping_use_validated_source_fields():
    from services.data_ingestion.app.mapping import (
        map_medication_request,
        map_procedure,
    )
    from services.data_ingestion.app.synthea import SourceRecord

    procedure, procedure_issues = map_procedure(
        SourceRecord(
            source_file="procedures.csv",
            row_number=1,
            source_record_id="procedure-1",
            patient_id="patient-1",
            encounter_id="encounter-1",
            data={"CODE": "123", "DESCRIPTION": "Test procedure", "DATE": "2026-01-01T10:00:00Z"},
        )
    )
    medication, medication_issues = map_medication_request(
        SourceRecord(
            source_file="medications.csv",
            row_number=1,
            source_record_id="medication-1",
            patient_id="patient-1",
            encounter_id="encounter-1",
            data={"CODE": "456", "DESCRIPTION": "Test medication", "START": "2026-01-01T10:00:00Z"},
        )
    )

    assert procedure_issues == []
    assert procedure is not None
    assert procedure.fhir["occurrenceDateTime"]
    assert "performedDateTime" not in procedure.fhir
    assert medication_issues == []
    assert medication is not None
    assert medication.fhir["medication"]["concept"]["coding"][0]["code"] == "456"
    assert "medicationCodeableConcept" not in medication.fhir


def test_engine_bundle_normalizes_r5_fields_for_measure_evaluation():
    from services.integration.measures import _normalize_measure_bundle_fields

    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Encounter",
                    "actualPeriod": {"start": "2026-01-01T00:00:00Z"},
                }
            },
            {
                "resource": {
                    "resourceType": "Procedure",
                    "occurrenceDateTime": "2026-01-01T00:00:00Z",
                }
            },
            {
                "resource": {
                    "resourceType": "MedicationRequest",
                    "medication": {"concept": {"coding": [{"code": "456"}]}},
                }
            },
        ],
    }

    normalized = json.loads(_normalize_measure_bundle_fields(json.dumps(bundle)))
    encounter, procedure, medication = (entry["resource"] for entry in normalized["entry"])

    assert encounter["period"] == {"start": "2026-01-01T00:00:00Z"}
    assert "actualPeriod" not in encounter
    assert procedure["performedDateTime"] == "2026-01-01T00:00:00Z"
    assert "occurrenceDateTime" not in procedure
    assert medication["medicationCodeableConcept"]["coding"][0]["code"] == "456"
    assert "medication" not in medication


def test_extract_root_library_reference_uses_measure_library_relationship(tmp_path: Path):
    from services.data_ingestion.app.api import _extract_root_library_reference

    bundle_path = tmp_path / "renamed-measure-bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Measure",
                            "id": "measure-id",
                            "library": ["https://example.test/Library/root-library|1.0.0"],
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "Library",
                            "id": "dependency-library",
                            "url": "https://example.test/Library/dependency-library",
                        }
                    },
                    {
                        "resource": {
                            "resourceType": "Library",
                            "id": "root-library",
                            "url": "https://example.test/Library/root-library",
                        }
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _extract_root_library_reference(bundle_path) == "root-library"


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


def test_to_member_result_coerces_population_result_types():
    from services.data_ingestion.app.api import _to_member_result

    cli_entry = {
        "patientId": "patient-2b",
        "detailedResults": [
            {
                "populationResults": [
                    {"populationType": "IPP", "result": "true"},
                    {"populationType": "DEN", "result": 1},
                    {"populationType": "NUM", "result": "0"},
                ]
            }
        ],
    }

    result = _to_member_result(cli_entry, "SPD")

    assert result["status"] == "completed"
    assert result["populations"]["initial_population"] is True
    assert result["populations"]["denominator"] is True
    assert result["populations"]["numerator"] is False
    assert result["outcome"] == "numerator_not_met"


def test_measure_evaluator_consumes_normalized_populations(monkeypatch, tmp_path: Path):
    from services.integration import measures

    patient_path = tmp_path / "patient-1.json"
    patient_path.write_text('{"resourceType": "Bundle", "entry": []}', encoding="utf-8")
    measure_path = (
        Path("services/measure_calc/engine/examples/independent_public_bcse")
        / "PublicBreastCancerScreeningTechnicalDemo-bundle.json"
    )
    bundle = measures.MeasureBundle("GSD", measure_path, "PublicBreastCancerScreeningTechnicalDemo", None)
    monkeypatch.setattr(
        measures,
        "_call_engine",
        lambda *_: [
            {
                "patientId": "patient-1",
                "detailedResults": [
                    {
                        "populationResults": [
                            {"populationType": "DEN", "result": True},
                            {"populationType": "NUM", "result": False},
                        ]
                    }
                ],
            }
        ],
    )

    results = measures._evaluate(
        bundle,
        {"patient-1": patient_path},
        tmp_path / "work",
        SimpleNamespace(engine_chunk=1),
    )

    assert results == {
        "patient-1": {
            "initial_population": None,
            "denominator": True,
            "denominator_exclusion": None,
            "denominator_exception": None,
            "numerator": False,
        }
    }


def test_measure_evaluator_rejects_unresolved_population_results(monkeypatch, tmp_path: Path):
    from services.integration import measures

    patient_path = tmp_path / "patient-1.json"
    patient_path.write_text('{"resourceType": "Bundle", "entry": []}', encoding="utf-8")
    measure_path = (
        Path("services/measure_calc/engine/examples/independent_public_bcse")
        / "PublicBreastCancerScreeningTechnicalDemo-bundle.json"
    )
    bundle = measures.MeasureBundle("BCS-E", measure_path, "PublicBreastCancerScreeningTechnicalDemo", None)
    monkeypatch.setattr(
        measures,
        "_call_engine",
        lambda *_: [
            {
                "patientId": "patient-1",
                "detailedResults": [{"populationResults": [{"populationType": "denominator", "result": "unknown"}]}],
            }
        ],
    )

    with pytest.raises(RuntimeError, match="unresolved populations"):
        measures._evaluate(
            bundle,
            {"patient-1": patient_path},
            tmp_path / "work",
            SimpleNamespace(engine_chunk=1),
        )


def test_measure_evaluator_rejects_duplicate_patient_results(monkeypatch, tmp_path: Path):
    from services.integration import measures

    patient_path = tmp_path / "patient-1.json"
    patient_path.write_text('{"resourceType": "Bundle", "entry": []}', encoding="utf-8")
    measure_path = (
        Path("services/measure_calc/engine/examples/independent_public_bcse")
        / "PublicBreastCancerScreeningTechnicalDemo-bundle.json"
    )
    bundle = measures.MeasureBundle("GSD", measure_path, "PublicBreastCancerScreeningTechnicalDemo", None)
    duplicate_result = {
        "patientId": "patient-1",
        "detailedResults": [
            {"populationResults": [{"populationType": "DEN", "result": True}, {"populationType": "NUM", "result": False}]}
        ],
    }
    monkeypatch.setattr(measures, "_call_engine", lambda *_: [duplicate_result, duplicate_result])

    with pytest.raises(RuntimeError, match="duplicate patient results"):
        measures._evaluate(
            bundle,
            {"patient-1": patient_path},
            tmp_path / "work",
            SimpleNamespace(engine_chunk=1),
        )


def test_recalculation_defaults_to_patients_in_latest_ready_batch(monkeypatch):
    from services.integration import pipeline

    batch_id = "11111111-1111-1111-1111-111111111111"
    started: dict[str, object] = {}

    class Result:
        def fetchone(self):
            return (batch_id, "job-1")

    class Connection:
        def execute(self, *_):
            return Result()

    @contextmanager
    def fake_connect():
        yield Connection()

    class Thread:
        def __init__(self, *, args, **_):
            started["args"] = args

        def start(self):
            started["started"] = True

    monkeypatch.setattr(pipeline.db, "connect", fake_connect)
    monkeypatch.setattr(
        pipeline.team_schema_loader,
        "job_patients",
        lambda *_: (("patient-current",), {"patient-current": None}),
    )
    monkeypatch.setattr(pipeline, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline.threading, "Thread", Thread)

    try:
        assert pipeline.start_recalculation(["BCS-E"]) == batch_id
    finally:
        if pipeline._ONE_AT_A_TIME.locked():
            pipeline._ONE_AT_A_TIME.release()

    assert started["started"] is True
    assert started["args"] == (
        uuid.UUID(batch_id),
        "job-1",
        ["BCS-E"],
        ["patient-current"],
    )


def test_recalculation_uses_the_batch_patient_attributes(monkeypatch):
    from services.integration import pipeline

    captured: dict[str, object] = {}

    @contextmanager
    def fake_connect():
        yield object()

    class Run:
        run_id = "engine-run"
        patients = 1

        def counts(self):
            return {"measures": {}}

    monkeypatch.setattr(pipeline.db, "connect", fake_connect)
    monkeypatch.setattr(
        pipeline.team_schema_loader,
        "job_patients",
        lambda *_: (("patient-current", "patient-other"), {"patient-current": "org-current", "patient-other": None}),
    )
    monkeypatch.setattr(
        pipeline.measures,
        "run_measures",
        lambda _conn, patient_ids, attributed, _batch_label, ingest_job_id, **_kwargs: (
            captured.update(patient_ids=patient_ids, attributed=attributed, ingest_job_id=ingest_job_id) or Run()
        ),
    )
    monkeypatch.setattr(pipeline.measures, "gap_counts", lambda *_: {})
    monkeypatch.setattr(pipeline, "_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "load_rules", lambda: object())
    monkeypatch.setattr(
        pipeline,
        "score_all",
        lambda *_: SimpleNamespace(gaps_scored=0, tiers={}, channels={}, rules_version="test", as_of=date(2026, 1, 1)),
    )
    monkeypatch.setattr(pipeline, "audit", lambda *_args, **_kwargs: None)

    pipeline._ONE_AT_A_TIME.acquire()
    pipeline._recalculate(
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "job-1",
        ["BCS-E"],
        ["patient-current"],
    )

    assert captured == {
        "patient_ids": ["patient-current"],
        "attributed": {"patient-current": "org-current"},
        "ingest_job_id": "job-1",
    }


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


def test_root_app_starts_integration_watcher(monkeypatch):
    root_app = importlib.import_module("app")
    watcher_started = threading.Event()
    watcher_stopped = threading.Event()
    lifecycle_calls: list[str] = []
    expected_flow = {"batch": {"status": "measuring"}, "ingest_job": None, "tables": {}}

    @contextmanager
    def fake_connect():
        yield object()

    def fake_watch_ingestion(stop: threading.Event) -> None:
        watcher_started.set()
        stop.wait(timeout=1)
        watcher_stopped.set()

    monkeypatch.setattr(root_app.intake_db, "connect", fake_connect)
    monkeypatch.setattr(root_app.integration_auth, "ensure_default_users", lambda _: None)
    monkeypatch.setattr(
        root_app.integration_pipeline,
        "recover_interrupted_batches",
        lambda: lifecycle_calls.append("recover"),
    )
    monkeypatch.setattr(
        root_app.integration_pipeline,
        "record_missing_upload_sizes",
        lambda: lifecycle_calls.append("record_sizes"),
    )
    monkeypatch.setattr(root_app.integration_pipeline, "watch_ingestion", fake_watch_ingestion)
    monkeypatch.setattr(root_app.integration_pipeline, "flow", lambda _: expected_flow)

    with TestClient(root_app.create_app(validate_database=False)):
        assert watcher_started.wait(timeout=1)

    assert watcher_stopped.wait(timeout=1)
    assert lifecycle_calls == ["recover", "record_sizes"]
    assert root_app.ingestion_flow_snapshot() == expected_flow

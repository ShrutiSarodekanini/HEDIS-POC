# HEDIS-POC

End-to-end CSV-to-FHIR R4 conversion workflow for Synthea exports, including:

- CSV dataset inspection and row counting
- Reusable CSV-to-FHIR mapping by resource type
- FHIR R4 resource generation and patient bundles
- Validation (structural using `fhir.resources`; optional external HL7 validator jar)
- PostgreSQL-ready persistence using SQLAlchemy with JSONB support
- Job tracking, validation issue tracking, and idempotent reruns
- Simple web UI for monitoring and resource exploration

## Architecture

Main backend service: `services/data_ingestion/main.py`

Core modules:

- `services/data_ingestion/app/synthea.py`: file discovery, parsing, row-level issues
- `services/data_ingestion/app/mapping.py`: deterministic mapping rules per FHIR resource
- `services/data_ingestion/app/validator.py`: structural + optional external validator
- `services/data_ingestion/app/models.py`: `ConversionJob`, `FhirResource`, `ValidationIssue`
- `services/data_ingestion/app/pipeline.py`: async job orchestration and stage tracking
- `services/data_ingestion/app/api.py`: API endpoints used by the UI
- `ui/frontend`: active frontend workspace (Vite + React)

Pipeline stages:

1. CSV Input
2. Mapping
3. FHIR Generation
4. Validation
5. PostgreSQL Storage

## Prerequisites

- Python 3.11+
- PostgreSQL 14+ (recommended for production)
- Java 11+ only if using external HL7 validator jar

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Environment Variables

Set these before starting the app:

Use `.env` in the project root for local development. A starter file is included as `.env.example`.

- `DATABASE_URL`
	- PostgreSQL example: `postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/fhir`
	- Local quickstart default (if unset): `sqlite:///./hedis_poc.db`
- `DATASETS_BASE_DIR`
	- Base directory containing dataset subfolders for `/api/datasets`
- `FHIR_VALIDATOR_JAR` (optional)
	- Absolute path to the official HL7 validator jar for external validation
- `VALIDATION_REQUIRE_EXTERNAL`
	- `true` or `false` (default `false`)

## Database Setup

Create DB (PostgreSQL example):

```sql
CREATE DATABASE fhir;
```

Initialize schema:

```bash
python scripts/init_db.py
```

Tables created:

- `conversion_jobs`
- `fhir_resources`
- `validation_issues`
- `domain_events` (ingestion completion events for downstream workflows)
- `dq_runs` (DQ workflow execution tracking per batch/job)
- `data_anomalies` (anomaly findings produced by DQ workflows)
- `feed_completeness_reports` (resource-level record counts per DQ run)
- `dq_check_results` (structured check outcomes and metrics)

`fhir_resources.resource_json` uses JSONB on PostgreSQL.

## DQ/AD Worker and Checks

This branch includes event-driven Data Quality/Anomaly Detection execution after ingestion completes.

- Ingestion emits `ingestion.completed` events into `domain_events`.
- DQ worker consumes pending events and creates/updates a `dq_runs` record.
- Worker writes:
  - feed completeness snapshots to `feed_completeness_reports`
  - check metrics to `dq_check_results`
  - detected anomalies to `data_anomalies`
- Measure-wise thresholds and rule metadata are loaded from:
  - `services/data_quality/config/dq_rules.json`
- Current built-in checks:
  - feed volume drop/spike/dropout against historical baseline
  - required resource presence (`Patient`, `Encounter`, `Condition`, `Observation`, `Coverage`)
  - mapping error-rate threshold from `validation_issues`
  - coverage gap violations (`gap_days > 45`)
  - measure-specific observation presence, unit, and value-range checks for configured observation codes
  - measure clinical-evidence checks across configured `Condition`, `Procedure`, `MedicationRequest`, and `Immunization` code sets
  - measure minimum-patient-coverage checks (`min_distinct_patients_with_evidence`)

### Phased HEDIS-like Rule Rollout

Rules are implemented as phased measure profiles in `services/data_quality/config/dq_rules.json`.

- **Phase 1 (baseline go-live)**
  - `CBP`, `GSD`, `WCV`, `CIS`
- **Phase 2 (expanded quality controls)**
  - `AAB`, `URI`, `BPD`
- **Phase 3 (advanced/placeholder expansion)**
  - `COA`, `COL`, `BCS`

Each measure profile supports:

- `phase`, `description`
- `required_resource_types`
- `observation_codes` + `requires_unit` + `value_range`
- `required_condition_codes`
- `required_procedure_codes`
- `required_medication_codes`
- `required_immunization_codes`
- `clinical_evidence_mode` (`any`/`all`)
- `min_distinct_patients_with_evidence`

> Note: This repository currently uses open-source/synthetic datasets, so Phase 3 includes placeholder HEDIS-like profiles intended for incremental value-set hardening as official clinical value sets are incorporated.

Run worker continuously:

```bash
python -m services.data_quality.main
```

Process one event and exit:

```bash
python -m services.data_quality.main --once
```

Start both backend processes (API + DQ worker) in one step on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start\start-backend-services.ps1
```

### DQ/AD End-to-End Execution (Windows)

Use this sequence for a clean and repeatable DQ/AD cycle.

1. Start services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start\start-backend-services.ps1
```

Notes:

- The startup script now performs prelaunch cleanup by default:
  - stops existing project-scoped Python/Uvicorn processes
  - clears `__pycache__` directories under the repo
- To skip cleanup once, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start\start-backend-services.ps1 -SkipPrelaunchCleanup
```

2. Run ingestion from UI (`Run Ingestion`) and wait for job status `completed` or `partially_completed`.

3. Run DQ checks:

- Auto trigger: happens after ingestion completion event.
- Manual trigger (single pass):

```powershell
python -m services.data_quality.main --once
```

4. Verify what UI should show with API:

```powershell
curl "http://localhost:8000/api/batches?limit=20"
curl "http://localhost:8000/api/dq/runs?batch_id=<BATCH_ID>"
curl "http://localhost:8000/api/summary?dq_run_id=<DQ_RUN_ID>"
curl "http://localhost:8000/api/dq/runs/<DQ_RUN_ID>/checks?limit=1000"
curl "http://localhost:8000/api/dq/runs/<DQ_RUN_ID>/anomalies?limit=500"
```

5. Understand rerun behavior:

- Rerun in DQ/AD page can update the same `dq_run_id` for the same `(batch_id, job_id)` context.
- This is expected: checks/anomalies are refreshed for that run context.

## Run Backend + UI

Terminal 1 - Backend (API + DQ worker):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start\start-backend-services.ps1
```

Terminal 2 - UI (Vite dev server):

```powershell
Set-Location .\ui
npm install
npm run dev
```

UI URL:

- `http://localhost:3000`

API base URL:

- `http://localhost:8000`

Health endpoint:

- `GET http://localhost:8000/health`

## API Endpoints

- `GET /api/datasets?base_dir=...`
- `GET /api/datasets/inspect?input_dir=...`
- `POST /api/jobs/start?input_dir=...`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/status`
- `GET /api/jobs/{job_id}/resources`
- `GET /api/jobs/{job_id}/patients`
- `GET /api/jobs/{job_id}/patients/{patient_id}/resources`
- `GET /api/jobs/{job_id}/validation`
- `POST /api/jobs/{job_id}/persist`
- `GET /api/resources/{resource_pk}`
- `GET /api/dq/runs?job_id=...&batch_id=...`
- `GET /api/dq/runs/{dq_run_id}/anomalies`
- `GET /api/dq/runs/{dq_run_id}/checks`
- `GET /api/batches`
- `GET /api/config/measures`
- `GET /api/measures`
- `POST /api/run-checks?job_id=...&batch_id=...&measure_id=...`
- `GET /api/summary`
- `GET /api/anomalies`
- `GET /api/trends/anomalies-by-batch`

## Validation Policy

- Structural validation uses `fhir.resources` classes for R4-compatible models.
- If external validator is not configured, validation is recorded as `unavailable` (not `validated`).
- Resources with hard validation errors are marked `rejected`.
- Persist stage can persist `validated` and `unavailable` resources, with clear lifecycle flags:
	- `persisted`
	- `persisted_with_warnings`

## Example Workflow

1. Start app.
2. Open UI at `http://localhost:8000/ui/`.
3. Enter Synthea export folder path.
4. Click **Inspect CSV Files**.
5. Click **Start Conversion**.
6. Wait until status is `completed` or `partially_completed`.
7. Review validation issues.
8. Click **Store Validated Resources**.
9. Explore patient resources and persisted status.

## Tests

Run all tests:

```bash
pytest -q
```

Included test dataset:

- `tests/data/synthea_sample`

Coverage includes:

- CSV discovery and missing required file detection
- Mapping and reference preservation
- End-to-end API job run
- Validation issue recording
- Persistence stage execution
- Rerun idempotency behavior

## Known Limitations

- Terminology and profile-level validation is limited unless `FHIR_VALIDATOR_JAR` is configured.
- Mapping covers common Synthea CSV files but does not infer ambiguous clinical semantics.
- Unsupported/malformed rows are reported as issues and skipped from resource creation.

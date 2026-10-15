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
- `ui/index.html`: simple monitoring/explorer UI

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

`fhir_resources.resource_json` uses JSONB on PostgreSQL.

## Run Backend + UI

```bash
uvicorn services.data_ingestion.main:app --host 0.0.0.0 --port 8000 --reload
```

UI URL:

- `http://localhost:8000/ui/`

Health endpoint:

- `GET /health`

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

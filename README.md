# HEDIS-POC

AI-enabled Healthcare Quality Measurement Platform that ingests Synthea data in the background, calculates HEDIS measure performance and CMS Star Ratings-related metrics, identifies missing care, extracts supporting clinical evidence, and generates reports for quality teams.

## Run the app

Needs Python 3.11+, Node 20, PostgreSQL, and Tesseract OCR for scanned test documents.

### Single environment file for backend and frontend

Use one shared `.env` at the repo root for both services:

1. Copy `.env.example` to `.env`.
2. Set required backend values (`DATABASE_URL`, model settings, etc.).
3. Keep frontend `VITE_*` values in the same root `.env`.

Do not create a separate `ui/.env`; Vite is configured to read environment variables from the repo root.

### Install dependencies

```powershell
# backend deps
python -m pip install -r requirements.txt

# frontend deps
cd ui
npm install
cd ..
```

### Run database migrations

```powershell
python -m alembic -c alembic.ini upgrade head
```

### Start backend

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

### Start frontend

```powershell
cd ui
npm run dev
```

Open the UI URL shown by Vite (default `http://127.0.0.1:5173`).

Test files (synthetic) are in `data/test_uploads`: upload `synthea_20_patients.zip` (20 patients), or the 12 CSV
files in `cohort_4_patients/`, on Data Ingestion. The stored evidence documents for those patients are in
`data/evidence_store` and attach on their own once a batch is prioritized.

### Observability (optional)

```
powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration -Observability
```

The app serves Prometheus metrics at http://localhost:8780/metrics (or the port set in `INTEGRATION_PORT`). With
`-Observability` the script also starts Prometheus at http://localhost:9090: in Docker when Rancher Desktop or Docker
Desktop is running, otherwise from `PROMETHEUS_EXE` or `prometheus.exe` on PATH. Nothing is downloaded. Prometheus
scrapes every 15s and keeps 15 days. Stop the Docker one with
`docker compose -f observability/docker-compose.yml down`. The metrics are defined in
`services/integration/metrics.py`.

In Docker the script also starts Grafana at http://localhost:3000. It opens straight on the "HEDIS pipeline"
dashboard and needs no login to view. Editing needs the admin login: user `admin`, with the password from
`GRAFANA_ADMIN_PASSWORD` in `.env` or the shell (default `admin`). Grafana listens on this machine only, and the same
`down` command stops it with Prometheus.

A second dashboard, "HEDIS run history" (link "Run history" at the top of the first), reads the app's database and
lists every batch, AI call, quality check, engine run and review with its status, timing and exact error text. It
uses a read-only login: a superuser runs `observability/grafana/readonly_role.sql` once (after the lead's OK) and sets
its password, and the same value goes in `.env` as `GRAFANA_DB_PASSWORD`. Until then its panels show a login error.

Logs: the app writes its log to `logs/hedis-integration.log` (folder set by `LOG_DIR` in `.env`, rotated at 10 MB,
5 files kept) as well as the console. In Docker, Alloy sends the lines to Loki, which keeps them for 15 days; document
file names become `<file>` and OpenRouter user ids become `<user>` on the way (the file itself is not changed). Search
them in Grafana > Explore > HEDIS logs, for example `{job="hedis-integration"} |= "error"` or
`{job="hedis-integration", level="ERROR"}`. The overview has a Logs row, and run history shows the ERROR lines.

Useful queries (Prometheus, Graph tab):

```
histogram_quantile(0.95, sum by (le, step) (rate(hedis_batch_step_seconds_bucket[1h])))
sum by (step) (increase(hedis_batch_failures_total[24h]))
sum by (measure) (rate(hedis_engine_seconds_sum[1h])) / sum by (measure) (rate(hedis_engine_seconds_count[1h]))
histogram_quantile(0.95, sum by (le, model) (rate(hedis_ai_call_seconds_bucket[1h])))
sum by (model, kind) (increase(hedis_ai_tokens_total[24h]))
```

## Integration branch

This branch brings three lines of work together so they can be joined into one flow
(upload, FHIR, patients, measures, gaps, prioritization, AI evidence). Each part still runs
the way it did on its own branch; the sections below are copied from each branch's README.

| Part | Where |
|---|---|
| Schema, document intake, AI evidence extraction, gap prioritization, demo page | `db/`, `services/document_intake/`, `services/evidence_extraction/`, `services/gap_priority/`, `scripts/demo_priority/` (see `scripts/run_demo.ps1`) |
| Data ingestion, measure engine, React UI | `services/data_ingestion/`, `services/measure_calc/engine/`, `ui/` |
| Star rating, measure comparison, mock evidence documents | `app.py`, `common/`, `alembic/`, `database/`, `ui/` (React app at `ui/index.html`) |

Current migration path is `db/migrations` and can be run from the repo root with:
`python -m alembic -c alembic.ini upgrade head`.

## Data ingestion and measure engine

This workspace is aligned to the team repository structure.

Top-level layout:
- common/
- scripts/
- services/
- tests/
- ui/

## Where the working implementation lives

- Backend API: app.py
- Frontend app: ui/

## Quick commands (from workspace root)

```bash
python -m pip install -r requirements.txt
python -m alembic -c alembic.ini upgrade head
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
cd ui && npm install && npm run dev
```

## Notes

- The referenced GitHub repository is a skeleton structure.
- This local workspace keeps your full implementation while matching that structure for easier merge and collaboration.

## Star rating and measure comparison

## Measure comparison POC

### Database configuration and migrations

This application uses PostgreSQL, SQLAlchemy 2.x, and Alembic. SQLite is not
supported. The database connection is declared through `DATABASE_URL`;
application code does not create or alter tables during startup.

Install dependencies:

```powershell
cd D:\Git\HEDIS-POC
python -m pip install -r requirements.txt
```

Set the database URL in the process environment. URL-encode special characters
in the password and never commit a real URL:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:YourPassword@localhost:5432/hedis"
```

For persistent local development settings, copy `.env.example` to `.env` and
replace the placeholders. `.env` is ignored by Git and loaded automatically.

Create or update all tables through Alembic:

```powershell
python -m alembic upgrade head
```

The application checks the database's Alembic revision during startup and
fails clearly when migrations are missing or outdated.

For future model changes:

```powershell
python -m alembic revision --autogenerate -m "describe schema change"
python -m alembic upgrade head
```

Review every autogenerated migration before applying it. SQLAlchemy models are
defined in [`common/models.py`](./common/models.py), the engine/session factory
is in [`common/database.py`](./common/database.py), and versioned migrations
are under [`alembic/versions`](./alembic/versions).

Demo data is intentionally separate from migrations. After migrating, load the
repeatable demo seed when needed:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" `
  --host localhost --username postgres --password `
  --dbname hedis --file database\seed.sql
```

For first-time local setup, the helper securely prompts for the PostgreSQL
password, creates `hedis` and `hedis_test`, runs Alembic for both databases,
and loads demo data into `hedis`:

```powershell
.\scripts\setup_postgres.ps1
```

The helper does not save or print the password.

### Application startup

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:YourPassword@localhost:5432/hedis"
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000/> in a browser.

### PostgreSQL test database

Integration tests require a separate database whose name ends in `_test`.
Alembic migrates that database before the fixture inserts test data. This guard
prevents tests from truncating application data.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:YourPassword@localhost:5432/hedis_test"
pytest -q
```

When `TEST_DATABASE_URL` is absent, PostgreSQL integration tests are skipped
while database-independent measure and Star tests still run.

### Demo workflow

1. Select `GSD - Glycemic Status Assessment for Patients With Diabetes`.
2. Select the five gaps labelled `approved`.
3. Click **Calculate comparison**.
4. Confirm the comparison shows baseline `65.00%`, recalculated `70.00%`,
   a `5.00 percentage points` change, five closed gaps, and CMS 2027 Stars `2` to `2`.
5. Select `GSD-GAP-06 (pending)` or `GSD-GAP-07 (rejected)` and calculate again.
   The rate and numerator do not change; a validation warning is displayed.
6. Select `EED - Eye Exam for Patients With Diabetes`, then select
   `EED-GAP-05 (approved)` (outside the measurement period). It remains open and
   does not change the result.
7. Select `CBP - Controlling High Blood Pressure`, then select
   `CBP-GAP-09 (approved)`. It is identified as a duplicate and does not close.
8. Select `COL-E - Colorectal Cancer Screening` and calculate without selecting a
   gap. Its CMS 2027 Star mapping is available and the rate is shown with the
   corresponding CMS Star assignment.
9. Select `CCS-E - Cervical Cancer Screening` and calculate without selecting a
   gap. The valid HEDIS rate is shown, while Stars display `N/A` with an
   explanation that the measure is not included in the verified CMS 2027 Medicare
   Advantage Star Ratings measure set.

The page supports the ten MY 2026 measures from the BRD, mock numerator/denominator
inputs, and simulated care-gap closure. Only unique, approved, in-period mock gaps
change the recalculated result. Pending, rejected, duplicate, and out-of-period
gaps remain open and are shown as validation warnings.

Performance inputs and care-gap scenarios remain mock POC data. Star assignments
are calculated only from the official CMS 2027 measure-specific cut points. The
2027 mappings configured here are:

| BRD measure | CMS 2027 measure | Cut points for 2/3/4/5 Stars |
|---|---|---|
| BCS-E | C01 Breast Cancer Screening | 63% / 70% / 77% / 83% |
| COL-E | C02 Colorectal Cancer Screening | 59% / 68% / 75% / 82% |
| EED | C11 Diabetes Care - Eye Exam | 61% / 72% / 79% / 86% |
| GSD | C12 Diabetes Care - Blood Sugar Controlled | 59% / 82% / 89% / 92% |
| KED | C13 Kidney Health Evaluation for Patients with Diabetes | 46% / 63% / 72% / 83% |
| CBP | C14 Controlling Blood Pressure | 72% / 79% / 84% / 90% |

Rates below the 2-Star cut point receive 1 Star. Cut points are inclusive at
the listed threshold and exclusive below the next threshold. GSD is mapped using
the public-reporting, reverse-scored higher-is-better rate described by CMS.
BPD, CCS-E, SPD, and AAB do not have a verified CMS 2027 Medicare Advantage
measure-level mapping in the official measure set and therefore remain unrated.

Official sources:

- [CMS 2027 Star Ratings Technical Notes](https://www.cms.gov/files/document/2027-tech-notes-2026-09-30.pdf)
- [CMS 2027 Star Ratings Measures and Weights](https://www.cms.gov/files/document/2027-star-ratings-measures.pdf)

These are measure-level assignments, not overall contract Star ratings. The
mock performance data does not represent official CMS results, plan submissions,
or clinical determinations. This feature does not change document generation or
persist clinical data.

## Star calculation integration contract

The reusable Star component is in
[`common/star_rating.py`](./common/star_rating.py). It accepts finalized output
from a future HEDIS engine and does not calculate or change numerator, denominator,
or rate values.

Required engine input:

```json
{
  "measure_code": "CBP",
  "measurement_year": 2026,
  "scoring_value": 84.0,
  "denominator": 100,
  "result_status": "completed",
  "suppression_status": "not_suppressed",
  "reliability_status": "met",
  "calculation_version": "hedis-engine-version",
  "specification_version": "MY2026",
  "scoring_value_kind": "cms_scoring_value"
}
```

`scoring_value` must already be the CMS-required scoring value. The Star
component never reverse-scores GSD or transforms another measure's input.
`scoring_value_kind` must be `cms_scoring_value`; invalid, suppressed,
unreliable, incomplete, zero-denominator, unsupported, or missing-year inputs
return `rating_status: "unavailable"` with an explanation.

For integration testing, post the finalized result to:

```text
POST /api/star-rating/calculate
```

with:

```json
{
  "cms_rating_year": 2027,
  "result": {
    "measure_code": "CBP",
    "measurement_year": 2026,
    "scoring_value": 84.0,
    "denominator": 100,
    "result_status": "completed",
    "calculation_version": "hedis-engine-version",
    "specification_version": "MY2026",
    "scoring_value_kind": "cms_scoring_value"
  }
}
```

Use `POST /api/star-rating/compare` with `before`, `after`, and
`cms_rating_year` to compare two finalized results. The response includes the
applied configuration, source URL, methodology version, rating status, and
whether the assigned Star changed.

## Multi-source ingestion and data quality

Kept beside the main ingestion so the integrated flow is unchanged: `services/multi_source_ingestion/` (multi-source adapters), `services/data_quality_rules/` (data quality and anomaly checks) and `ui/ingestion-dashboard/` (its React dashboard). Their imports point at these folders. Its README follows.


End-to-end CSV-to-FHIR R4 conversion workflow for Synthea exports, including:

- CSV dataset inspection and row counting
- Reusable CSV-to-FHIR mapping by resource type
- FHIR R4 resource generation and patient bundles
- Validation (structural using `fhir.resources`; optional external HL7 validator jar)
- PostgreSQL-ready persistence using SQLAlchemy with JSONB support
- Job tracking, validation issue tracking, and idempotent reruns
- Simple web UI for monitoring and resource exploration

## Architecture

Main backend service: `services/multi_source_ingestion/main.py`

Core modules:

- `services/multi_source_ingestion/app/synthea.py`: file discovery, parsing, row-level issues
- `services/multi_source_ingestion/app/mapping.py`: deterministic mapping rules per FHIR resource
- `services/multi_source_ingestion/app/validator.py`: structural + optional external validator
- `services/multi_source_ingestion/app/models.py`: `ConversionJob`, `FhirResource`, `ValidationIssue`
- `services/multi_source_ingestion/app/pipeline.py`: async job orchestration and stage tracking
- `services/multi_source_ingestion/app/api.py`: API endpoints used by the UI
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

Start the backend API, DQ worker, and latest HEDIS Ops Console in one step on Windows:

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
- It always starts the latest HEDIS Ops Console UI at `http://localhost:8780`.
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

## Alternative Data Quality Dashboard

This is a separate, legacy Vite dashboard. It is not the HEDIS Ops Console and uses port 5173.

Terminal 1 - Backend (API + DQ worker):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\local-start\start-backend-services.ps1
```

Terminal 2 - UI (Vite dev server):

```powershell
npm run legacy:ui:dev
```

UI URL:

- `http://localhost:5173`

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

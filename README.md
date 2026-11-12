# HEDIS-POC

Healthcare quality measurement platform. It ingests Synthea data, calculates HEDIS measures and CMS Star ratings,
finds open care gaps, extracts supporting evidence from clinical documents with AI, and reports the results.

One backend (`app.py`, port 8000) serves every API. The React UI is in `ui/`. Long-running pipeline steps run as
RabbitMQ workers.

## Prerequisites

- Python 3.11+ and Node 20+
- PostgreSQL, reachable at the `DATABASE_URL` in `.env`
- Rancher Desktop or Docker Desktop (for RabbitMQ and the monitoring stack)
- Tesseract OCR for scanned documents, and a local Ollama vision model for handwriting
- An OpenRouter key for AI evidence

## Configuration

Copy `.env.example` to `.env` at the repo root and fill it in. The backend, workers and UI all read this one file.
Do not create `ui/.env`, and never commit `.env`.

Values that matter most:

| Key | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection, with the schema in `options=-csearch_path%3D<schema>` |
| `OPENROUTER_API_KEY`, `EXTRACTION_MODEL` | AI evidence extraction (the model must be serving on OpenRouter) |
| `HEDIS_BOOTSTRAP_ADMIN_EMAIL` | Email of the first admin, created at startup if no account has it (lowercase) |
| `HEDIS_BOOTSTRAP_ADMIN_PASSWORD` | Password for that first admin, at least 8 characters; never changes an existing account |
| `INTEGRATION_EVIDENCE_DIR` | Stored evidence documents, normally `data/evidence_store` |
| `RABBITMQ_*` | Broker connection (defaults match `docker-compose.yml`) |
| `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_DB_PASSWORD` | Optional, for Grafana |

## Run everything (one command)

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1
```

The script checks `.env`, Docker and Postgres, installs missing Python and npm packages, builds the measure engine,
runs migrations, starts RabbitMQ and the monitoring stack, then opens one window each for the backend, the outbox
dispatcher, the workers and the UI.

| Option | Use |
|---|---|
| `-Setup` | First run on an empty schema: also loads the reference data (measures, rules, star cut points) |
| `-Install` | Reinstall packages and rebuild the measure engine, for example after a pull |
| `-NoObservability` | Skip Prometheus, Grafana, Loki and Alloy |
| `-Stop` | Stop everything the script started. Database and container data are kept |

### URLs

| Service | URL |
|---|---|
| React UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Pipeline monitor | http://localhost:8000/ingestion |
| RabbitMQ | http://localhost:15672 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090/targets |

## Run manually

Each long-running command needs its own terminal, from the repo root.

```powershell
python -m pip install -r requirements.txt
cd services/measure_calc/engine; npm install; npm run build; cd ../../..
cd ui; npm install; cd ..

python -m alembic -c db/alembic.ini upgrade head
python scripts/db/load_seed.py --reference-only      # only once, on an empty schema

docker compose up -d rabbitmq
docker compose -f observability/docker-compose.yml up -d   # optional

python -m uvicorn app:app --host 127.0.0.1 --port 8000
python -m services.outbox_dispatcher
python scripts/run_workers.py
cd ui; npm run dev -- --host 127.0.0.1 --port 5173
```

`scripts/run_workers.py` runs all ten workers in one terminal, with each line prefixed by the worker's name. Pass
names to run only some, for example `python scripts/run_workers.py measures evidence`.

The backend and the workers do not reload on code changes. Restart them after editing backend code.

## Run as containers

`docker-compose.deploy.yml` runs each component in its own container: ingestion, quality, measures, gap identifier,
prioritization, AI evidence and stars, plus the API, the outbox dispatcher, the UI (nginx) and RabbitMQ. Postgres
stays outside and is reached through `DATABASE_URL`.

1. Copy `.env.deploy.example` to `.env.deploy` and fill in `DATABASE_URL` (use its own schema), `RABBITMQ_USER` and
   `RABBITMQ_PASSWORD`. The containers read `.env` first, then `.env.deploy`.
2. Build and start. The `migrate` job creates the schema and runs the migrations:

   ```powershell
   docker compose -p hedis-deploy -f docker-compose.deploy.yml up -d --build
   ```

3. Load the reference data, once per schema (a second run fails on a duplicate key, which is harmless):

   ```powershell
   docker compose -p hedis-deploy -f docker-compose.deploy.yml --profile setup run --rm seed
   ```

4. Open http://127.0.0.1:8080. RabbitMQ management is on http://localhost:15673.

`scripts/run_containers.ps1` does steps 2 to 4 in one command (`-Setup` also loads the reference data, `-Stop` stops,
`-Reset` removes the containers and volumes):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Setup
```

After changing `.env` or `.env.deploy`, run `up -d` again. `start` and `restart` keep the old values. `stop` keeps
the data; `down -v` removes the containers and their volumes. Details are in `docs/deployment/CONTAINERS.md`.

## Pipeline

An upload goes through these steps. Each step is a RabbitMQ queue, fed through the transactional outbox
(`message_outbox`) by `services.outbox_dispatcher`.

| Step | Queue | Worker |
|---|---|---|
| Convert CSV to FHIR | `ingestion.convert` | `conversion` |
| Load into the database | `ingestion.load` | `load` |
| Data quality checks | `quality.check` | `quality` |
| Resolve value sets | `valuesets.resolve` | `valuesets` |
| Run measures | `measures.run` | `measures` |
| Identify gaps | `gaps.identify` | `gap_identifier` |
| Prioritize gaps | `gaps.prioritize` | `gaps` |
| Attach documents (OCR) | `documents.attach` | `documents` |
| Extract AI evidence | `evidence.extract` | `evidence` |
| Calculate stars | `stars.calculate` | `stars` |

The batch ends as `prioritized`. The documents step is the slowest because of OCR. **Run Calculation** in the UI
runs inside the backend, not through RabbitMQ, and only starts once no batch is still running.

Failed messages go to `<queue>.dead`. To inspect or replay them:

```powershell
python scripts/rabbitmq_dlq.py status --queue all
python scripts/rabbitmq_dlq.py replay --queue measures.run --limit 10
```

Synthetic test files are in `data/test_uploads`: upload `synthea_20_patients.zip`, or the CSV files in
`cohort_4_patients/`, on the Data Ingestion page. Their evidence documents are in `data/evidence_store`.

## Database

Migrations live in `db/migrations` and only add or change schema objects. They run with:

```powershell
python -m alembic -c db/alembic.ini upgrade head
```

The script creates the schema named in `DATABASE_URL` when it does not exist (`scripts/db/ensure_schema.py`). A
schema that exists but belongs to another user is left alone; the script stops and prints the `ALTER SCHEMA ... OWNER
TO` command a superuser runs.

To start from scratch, drop the schema (`DROP SCHEMA <schema> CASCADE;`) and run the script with `-Setup`. The
backend creates the bootstrap admin from `.env` at startup.

## Monitoring

The backend serves Prometheus metrics at http://localhost:8000/metrics. Prometheus scrapes them as job `hedis-api`.
Grafana opens on the "HEDIS pipeline" dashboard. Viewing needs no login, but editing needs user `admin` and
`GRAFANA_ADMIN_PASSWORD`.

The "HEDIS run history" dashboard reads the database through a read-only login. A superuser runs
`observability/grafana/readonly_role.sql` once and puts that login's password in `.env` as `GRAFANA_DB_PASSWORD`.

The backend writes its log to `logs/hedis-app.log`. Alloy ships it to Loki with emails, file names and user IDs
masked. Search it in Grafana under Explore > HEDIS logs, for example `{job="hedis-api", level="ERROR"}`.

Metric definitions are in `services/integration/metrics.py` (pipeline, engine, AI) and `common/app_metrics.py` (UI
routes).

## Star rating API

`common/star_rating.py` turns a finalized measure result into a CMS Star rating. It never changes the numerator,
denominator or rate.

- `POST /api/star-rating/calculate` takes `cms_rating_year` and a `result` (`measure_code`, `measurement_year`,
  `scoring_value`, `denominator`, `result_status`, `calculation_version`, `specification_version`,
  `scoring_value_kind: "cms_scoring_value"`).
- `POST /api/star-rating/compare` takes `before`, `after` and `cms_rating_year`.

Results that are invalid, suppressed, unreliable or unmapped come back as `rating_status: "unavailable"` with a
reason. Cut points follow the CMS 2027 Star Ratings Technical Notes.

## Project layout

| Path | Contents |
|---|---|
| `app.py` | The backend: every API route, the UI's backend and the pipeline monitor |
| `services/integration/` | Pipeline orchestration, measures, evidence, work list and export routes |
| `services/workers/` | The RabbitMQ workers |
| `services/data_ingestion/` | CSV to FHIR conversion and loading |
| `services/measure_calc/engine/` | The CQL measure engine (Node) |
| `services/document_intake/`, `services/evidence_extraction/` | Document OCR and AI evidence extraction |
| `services/gap_identifier/`, `services/gap_priority/` | Gap detection and prioritization |
| `common/` | Shared models, database access, star rating and metrics |
| `db/` | Alembic migrations |
| `ui/` | React UI |
| `observability/` | Prometheus, Grafana, Loki and Alloy |
| `scripts/` | Start, worker, seed and maintenance scripts |
| `Dockerfile`, `ui/Dockerfile`, `docker-compose.deploy.yml` | Container images and the per-component deployment |

## Tests

```powershell
pytest tests -q
```

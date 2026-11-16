# Running the app as containers

`docker-compose.deploy.yml` runs each component in its own container, built from the current code. Each Python
component has its own image (Dockerfiles in `docker/`) with only the system tools it needs. Postgres is not part of
the compose: the containers reach the existing local Postgres, or a managed one, through `DATABASE_URL`.

| Container | Start command |
|---|---|
| ingestion | `python scripts/run_workers.py conversion load` |
| quality | `python scripts/run_workers.py quality` |
| measures | `python scripts/run_workers.py valuesets measures` (runs the measure engine with Node) |
| gap-identifier | `python scripts/run_workers.py gap_identifier` |
| prioritization | `python scripts/run_workers.py gaps` |
| ai-evidence | `python scripts/run_workers.py documents evidence` |
| stars | `python scripts/run_workers.py stars` |
| api | `python -m uvicorn app:app --host 0.0.0.0 --port 8000` |
| outbox | `python -m services.outbox_dispatcher` |
| migrate | one-shot: `python scripts/db/ensure_schema.py && python -m alembic -c db/alembic.ini upgrade head` |
| seed | one-shot, profile `setup`: `python scripts/db/load_seed.py --reference-only` |
| ui | nginx serving the React build; proxies `/api/` and `/openapi.json` to the api container |
| rabbitmq | `rabbitmq:3.13-management`, its own instance for this compose |

Only two ports are published: the UI on 8080 and the RabbitMQ management page on 15673. The dev stack (5173, 8000,
5672, 15672) can run next to it. Uploads and generated evidence live in the `hedis-data` volume, shared by api,
ingestion and ai-evidence.

## Images

Two build-only images come first. `hedis-base` (`docker/base.Dockerfile`) is Python 3.11 with the packages from
`requirements.txt`, the fastapi pin, the user `app` (uid 10001), the `/data` folders and the code. `hedis-engine`
(`docker/engine.Dockerfile`) compiles the measure engine (fqm-execution) with Node 22. Every component image starts
FROM `hedis-base` and adds only what it needs; api and measures copy the engine build, its runtime packages and the
Node binary.

| Image | Dockerfile | Adds to hedis-base | Why | Size |
|---|---|---|---|---|
| hedis-deploy-api | `docker/api.Dockerfile` | Tesseract, Node, engine | Document upload runs intake (OCR); Run Calculation runs the engine | 1.37 GB |
| hedis-deploy-measures | `docker/measures.Dockerfile` | Node, engine | The measures worker runs the engine | 1.22 GB |
| hedis-deploy-ai-evidence | `docker/ai-evidence.Dockerfile` | Tesseract | The documents worker attaches stored documents through intake | 1.05 GB |
| hedis-deploy-ingestion | `docker/ingestion.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-quality | `docker/quality.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-gap-identifier | `docker/gap-identifier.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-prioritization | `docker/prioritization.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-stars | `docker/stars.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-outbox | `docker/outbox.Dockerfile` | nothing | | 901 MB |
| hedis-deploy-tools | `docker/tools.Dockerfile` | nothing | migrate and seed set their command in compose | 901 MB |
| hedis-deploy-ui | `ui/Dockerfile` | (nginx, not Python) | | 94 MB |

Sizes are as `docker images` reports them; `hedis-base` itself is 901 MB. The images share the base layers, so on
disk the ones without additions cost little beyond `hedis-base`. Before the split, every Python container used one
1.38 GB image.

Compose cannot order builds between services, so `base` and `engine` are services in the `build` profile (never
started) and are built first:

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml --profile build build base engine
```

`scripts/run_containers.ps1` does this before `up -d --build` (not with `-NoBuild`). Rebuild them after changing
`requirements.txt`, the code (the base image holds it) or the engine.

## Settings

The Python containers read `.env` and then `.env.deploy`, so `.env.deploy` wins. Copy `.env.deploy.example` to
`.env.deploy` and fill in `DATABASE_URL` (use its own schema, for example `hedis_deploy`), `RABBITMQ_USER` and
`RABBITMQ_PASSWORD`. The RabbitMQ container creates that user on first start. `.env.deploy` is ignored by git.

## Build and start

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml --profile build build base engine
docker compose -p hedis-deploy -f docker-compose.deploy.yml up -d --build
```

`migrate` runs first and must exit 0; the other Python containers wait for it and for RabbitMQ. The first time on
an empty schema, load the reference data (measures, cut points and so on):

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml --profile setup run --rm seed
```

Then open http://127.0.0.1:8080. RabbitMQ management: http://localhost:15673.

## Stop

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml down
```

This keeps the volumes (uploads, generated evidence, RabbitMQ data). `down -v` also deletes them.

## Scale a component

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml up -d --scale ai-evidence=2
```

Each copy adds one consumer to its queues. The API stays one container: all routes live in `app.py`, so it cannot
be split by component without changing the code.

## Logs

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml logs -f measures
```

The gap identifier, evidence and stars workers write nothing to stdout when a message succeeds, so their container
logs stay quiet; RabbitMQ management shows their consumers and acknowledgements.

## Monitoring

Prometheus, Grafana, Loki and Alloy run in one container, `monitoring`, built from `docker/monitoring/`, with the
dashboards, alerts and log masking of the local run:

| File in `docker/monitoring/` | What it is |
|---|---|
| `Dockerfile` | Grafana 12.1.0 (Ubuntu, because Alloy needs glibc) plus the Prometheus v3.5.0, Loki 3.5.0 and Alloy v1.10.0 binaries copied from their pinned images; runs as uid 472 |
| `start.sh` | Starts Loki, Prometheus, Alloy and Grafana, prefixes their output, stops them all on a stop signal |
| `prometheus.yml` | Scrape config for the containers (`api:8000` and the `hedis-workers` targets) |
| `docker-compose.yml` | The `monitoring` service, its volumes, and `ALLOY_URL` on the Python services |

The shared configs stay in `observability/` (`alerts.yml`, `loki/loki-config.yml` with 15 days of logs,
`alloy/config.alloy`, `grafana/provisioning`, `grafana/dashboards`) because the local run uses them too; the image
copies them in. Nothing is bind-mounted, so a change to a dashboard, alert or config needs a rebuild: rerun the
script (it builds). On the compose network the container answers to `prometheus`, `loki`, `alloy` and `grafana`, so
every existing URL works unchanged.

| Tool | Address |
|---|---|
| Grafana | http://127.0.0.1:3001 |
| Prometheus | http://127.0.0.1:9091/targets |
| Loki, Alloy | network only (Loki 3100, Alloy 3500 push and 12345 UI) |

The ports differ from the local run (3000, 9090), so both can run at once. The local run (`run_all.ps1`,
`observability/docker-compose.yml`) is unchanged and still starts its own four containers.

The container's log holds all four tools, each line prefixed with `[loki]`, `[prometheus]`, `[alloy]` or `[grafana]`
(and `[monitoring]` for the start script):

```
docker logs hedis-deploy-monitoring-1
```

If one tool stops by itself, the start script stops the other three and exits 1, and Docker restarts the container.
On `docker stop` the tools get 20 seconds to stop before they are killed. The data volumes (`prometheus-data`,
`loki-data`, `alloy-data`, `grafana-data`) keep metrics, logs and Grafana settings across restarts; the script hands
them to uid 472 before starting, which also fixes volumes left by the earlier four monitoring containers.

`docker-compose.deploy.yml` must come first: Compose resolves every relative path from the first file's folder (the
repo root), and the network `hedis` is declared there.

```
docker compose -p hedis-deploy -f docker-compose.deploy.yml -f docker/monitoring/docker-compose.yml up -d
```

Or with the script:

```
powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -NoObservability
powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Stop
```

The script starts monitoring with the app unless `-NoObservability` is given. `-Stop` and `-Reset` always include
the monitoring container (`-Reset` also removes its volumes), so nothing is left running.
`docker-compose.deploy.yml` alone is the app without monitoring.

What is collected:

- API metrics (requests, pipeline steps, engine, AI calls) from `/metrics` (job `hedis-api`).
- Worker metrics: every worker process and the outbox serve their own `/metrics` on a fixed port (job
  `hedis-workers`, label `worker`), so AI calls, engine runs and steps recorded in the workers reach Prometheus too.
  The ports are in `services/rabbitmq_messages.py` (`METRICS_PORT`) and are not published; Prometheus reaches them on
  the compose network (the local run scrapes them on `host.docker.internal`). A port that cannot be opened logs one
  warning and the worker carries on.

  | Worker | Target | Worker | Target |
  |---|---|---|---|
  | outbox | `outbox:9100` | gap_identifier | `gap-identifier:9106` |
  | conversion | `ingestion:9101` | gaps | `prioritization:9107` |
  | load | `ingestion:9102` | documents | `ai-evidence:9108` |
  | quality | `quality:9103` | evidence | `ai-evidence:9109` |
  | valuesets | `measures:9104` | stars | `stars:9110` |
  | measures | `measures:9105` | | |

  A stopped worker shows as a down target; the panels add up whatever is up.
- The logs of every Python process: the overlay sets `ALLOY_URL=http://alloy:3500` on api, outbox and the seven
  worker containers, and `common/app_logging.py` (`ship_logs`) sends their records at INFO and above to Alloy in
  batches (labels `job="hedis"`, `service`, `level`; `batch_id` and `job_id` as structured metadata). Alloy masks
  emails, file names and OpenRouter user ids before Loki stores them. If Alloy is down, lines are dropped and counted
  (one warning a minute on stderr); the app does not wait. Without `ALLOY_URL` nothing is shipped, as in the local run.

To follow one batch: open the HEDIS failures dashboard, paste the batch id into `batch_id`. "One batch across every
service" lists its lines from every step in time order, so the last lines show where it stopped; "Latest errors and
warnings" and "Errors per service" narrow it down by service. In Explore, `{job="hedis"}` shows everything,
`{job="hedis", service="measures"} | batch_id="<id>"` one step of one batch.

Grafana opens without sign-in (viewer) by default. For anything reachable by others, set `GRAFANA_ANONYMOUS=false` and
a real `GRAFANA_ADMIN_PASSWORD` in `.env.deploy`; both are read when the container starts, never built into the image
(the admin password only takes effect on Grafana's first start with an empty `grafana-data` volume).

On Azure Container Apps, the monitoring image runs as one app; its Loki and Grafana data (`loki-data`, `grafana-data`)
need persistent storage (Azure Files), or logs and Grafana settings are lost on restart. The services push their
logs over HTTP, so no shared files or Log Analytics are needed.

The run history dashboard and the database panels of the new UI dashboard read Postgres as the read-only login
`hedis_grafana_ro` at `host.docker.internal:5432`, database `HEDISPOCDB`. Their `schema` dropdown lists every schema
the login may read that holds the app's tables (newest name first); pick the one in `.env.deploy`.

The login is created once, by a superuser, with `observability/grafana/readonly_role.sql`, which also sets its
per-column grants on `hedis`; set its password and put the same value in `GRAFANA_DB_PASSWORD` (`.env` or
`.env.deploy`). After that, `scripts/db/grant_grafana_readonly.py` gives it the same columns in the schema of
`DATABASE_URL` after every migration (the migrate job, `run_all.ps1`), so each new schema shows up in the dropdown
without a superuser. Without the login the script prints a one-line hint, the database panels show a datasource
error, and the overview dashboard and the Prometheus and Loki panels still work.

## Notes

- The base image pins fastapi to the version the dev environment runs (`FASTAPI_VERSION` in
  `docker/base.Dockerfile`):
  requirements.txt has no upper bound, and fastapi 0.16x breaks the Prometheus middleware (500 on every route).
- Dataset uploads are saved under `./.uploaded_datasets` by the code; in the image that path links into the shared
  volume (`/data/uploaded_datasets`) so the ingestion container can read what the api container received.
- Document intake files go to `/data/intake_store` (`INTAKE_STORE_DIR`) in the same volume.
- Handwriting OCR uses a local Ollama model at `OLLAMA_URL` (default `http://localhost:11434`), which inside a
  container is the container itself. To use the host's Ollama, set `OLLAMA_URL=http://host.docker.internal:11434`
  in `.env.deploy`. Printed text is read by Tesseract, which is in the api and ai-evidence images.

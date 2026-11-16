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

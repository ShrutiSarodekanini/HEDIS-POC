# Pipeline observability with Prometheus

The integration app exposes metrics at `/metrics` on its own port (`INTEGRATION_PORT`, default 8780). Prometheus
scrapes them every 15 seconds, keeps 15 days of history and evaluates four alert rules. Grafana shows them on one
provisioned dashboard. The app's log lines go to Loki through Alloy (see Logs below). There is no OpenTelemetry or
RabbitMQ in this story. Every metric is defined in
`services/integration/metrics.py`.

## Label rules

- Allowed labels: `step`, `measure`, `model`, `status`, `outcome`, `decision` and `kind`, plus the instrumentator's
  `handler`, `method` and `status`. Each label has a short, fixed set of values.
- Never a label: a patient id, batch id, job id, gap or evidence id, name, date of birth, file name, or any document,
  prompt or answer text. A batch id is used only as an in-memory dictionary key to time steps
  (`metrics._STEPS`). It is removed when the batch ends and is never exported.
- `handler` is the route template (`/api/gaps/{gap_id}`), so ids in URLs never appear. Paths with no matching route
  are grouped as `none`.
- `model` is the configured `EXTRACTION_MODEL` string, never the model name the provider reports.

## Metric catalogue

| Metric | Type | Labels | Buckets (s) | Recorded in |
|---|---|---|---|---|
| `hedis_batch_step_seconds` | histogram | step | 1, 2, 5, 10, 20, 30, 60, 120, 300, 600 | `pipeline._progress` (previous step ends when the next starts), `pipeline._fail` (the last step) |
| `hedis_batches_total` | counter | status = prioritized, failed | | `_progress` with status prioritized; `_fail` |
| `hedis_batch_failures_total` | counter | step | | `_fail` |
| `hedis_batch_running` | gauge | | | 1 at the start of `run_batch`, `_run_from_job` and `_recalculate`; 0 in their `finally` |
| `hedis_patients_processed_total` | counter | | | end of `_after_persist`, after the batch is prioritized |
| `hedis_engine_seconds` | histogram | measure | 1, 5, 10, 20, 40, 60, 120, 300 | `measures._call_engine`, one observation per engine call (a retry is included in the same call) |
| `hedis_engine_retries_total` | counter | measure | | `measures._run_engine`, when the crash retry happens |
| `hedis_gaps_open` | gauge | measure | | `measures.run_measures`, set to that run's `gaps_open` for each measure |
| `hedis_ai_call_seconds` | histogram | model | 1, 2, 5, 10, 20, 30, 60, 120 | `evidence._complete`, around `client.complete` |
| `hedis_ai_tokens_total` | counter | model, kind = prompt, output | | `evidence._complete`, from `ModelResult` when the count is not None |
| `hedis_ai_calls_total` | counter | model, outcome = ok, error, empty, cut_off | | `evidence._complete` |
| `hedis_ai_review_decisions_total` | counter | decision = approved, rejected | | `evidence.decide`, after the decision is committed |
| `http_request*`, `http_response*` | instrumentator defaults | handler, method, status | defaults | every request |

Notes:

- Step timing covers `run_batch`, `_run_from_job` and `_recalculate`, because every step change goes through
  `_progress`. The first step of a recalculation (`measuring`) starts in the request thread, in
  `start_recalculation`.
- `hedis_gaps_open` shows the open gaps that the latest engine run found for its batch's patients, per measure. It
  is not a count of every open gap in the database. No new SQL was added for it.
- AI outcomes: `cut_off` is the model client's error for the output-token limit ("Answer was cut off ..."). `empty`
  is its error for an empty or whitespace answer. `error` is any other exception, which is re-raised unchanged.
  An answer that arrives but is not usable JSON still counts as `ok` for the call; the attempt row records it as
  failed.
- A reviewer's "confirm" is counted as `approved`.

## Alert rules (`observability/alerts.yml`)

| Alert | Expression | For | Severity |
|---|---|---|---|
| HedisBatchFailed | `increase(hedis_batches_total{status="failed"}[15m]) > 0` | | critical |
| HedisAiErrorRateHigh | `sum(rate(hedis_ai_calls_total{outcome!="ok"}[15m])) / sum(rate(hedis_ai_calls_total[15m])) > 0.10` | 5m | warning |
| HedisEngineRetried | `increase(hedis_engine_retries_total[30m]) > 0` | | warning |
| HedisAppDown | `up{job="hedis-integration"} == 0` | 1m | critical |

The alerts appear on Prometheus's Alerts page. Nothing sends them anywhere yet, because there is no Alertmanager.

## How Prometheus runs

`scripts/run_demo.ps1 -Integration -Observability` writes `observability/prometheus.yml.template` to
`.observability/prometheus.yml` (git-ignored) with the real target, and then:

- **Docker** (Rancher Desktop or Docker Desktop is running, so `docker info` succeeds): it runs
  `docker compose -f observability/docker-compose.yml up -d`. The `prom/prometheus:v3.5.0` container
  `hedis-prometheus` listens on port 9090 and stores its data in the named volume `prometheus-data`, so history
  survives a container restart. The target is `host.docker.internal:<port>`.
- **Exe** (`PROMETHEUS_EXE`, or `prometheus.exe` on PATH): it runs Prometheus in the background with
  `--storage.tsdb.path=.observability/data`. The target is `localhost:<port>`.
- **Neither**: it prints one line saying how to get Prometheus, then starts the app. Nothing is downloaded.

Both ways use a 15s scrape interval and 15d retention (`--storage.tsdb.retention.time=15d`).

Prometheus in a container reaches the app through `host.docker.internal`. On Rancher Desktop this name already points
at an address (192.168.127.254) that reaches Windows localhost, so the app's `127.0.0.1` listener is scraped without
any change to the app. The compose file used to set `extra_hosts: host.docker.internal:host-gateway`; that replaced
the address with 172.17.0.1 and the target went DOWN, so it was removed.

## Grafana

In the Docker setup, `observability/docker-compose.yml` also runs the `grafana/grafana:12.1.0` container
`hedis-grafana`, with its data in the named volume `grafana-data`.

- **Port**: `127.0.0.1:3000` only, so other machines cannot open it.
- **Access**: anonymous users get the Viewer role, so the dashboard opens without a login. Sign-up is off. Editing
  needs the admin login (password from `GRAFANA_ADMIN_PASSWORD` in `.env` or the shell, default `admin`; the run
  script passes it to docker compose, which does not read the root `.env` itself). The password
  is set only when the `grafana-data` volume is first created; changing the variable later does not change it.
- **Provisioning** (`observability/grafana/provisioning`): one Prometheus data source (uid `hedis-prometheus`,
  `http://prometheus:9090`, 15s interval, not editable), and one file provider that loads
  `observability/grafana/dashboards` into the HEDIS folder with UI changes switched off. To change a dashboard, edit
  its JSON file.
- **Dashboard** "HEDIS pipeline" (`hedis-overview.json`) is also the home page. It refreshes every 30s and shows
  the last 24 hours by default. Its rows are:
  - Health: app up, batches, patients, open gaps, AI error rate, alerts firing, batch running
  - Batches: step time p95, average per step, finished batches, failures by step
  - Measure engine: open gaps and engine time per measure, retries
  - AI evidence: calls by outcome, call time, tokens, reviewer decisions
  - API: requests by status, 5xx by route, request time by route
  - Alerts: a table of the firing or pending Prometheus alerts

  Health, Batches and AI evidence are open by default; Measure engine, API and Alerts start collapsed. The tiles
  that count things (batches, patients, open gaps, engine retries, alerts firing) show 0 when nothing has happened,
  not "No data". The AI error rate shows "No calls" when there were no calls.
  Panels group only by the fixed labels (step, measure, model, status, outcome, decision, kind, handler). No
  template variable lists ids.
- **Exe setup**: Grafana is not started. It needs Docker.

## Run history (Postgres)

Prometheus holds counts and timings over time. The per-run detail and the exact error text stay in the app's
database, so a second dashboard, "HEDIS run history" (`hedis-run-history.json`), reads them from Postgres. The two
dashboards link to each other, keeping the time range, and the overview's "Batches failed" and "AI error rate"
tiles link to it.

- **Login**: `hedis_grafana_ro`, created by `observability/grafana/readonly_role.sql`. A superuser runs it once
  after the lead's OK, then sets the password with `ALTER ROLE` and puts the same value in `.env` as
  `GRAFANA_DB_PASSWORD`. `run_demo.ps1 -Observability` passes it to the Grafana container for that run only and
  never prints it. The login is read-only by default (`default_transaction_read_only`), queries stop after 10s, and
  it can read only the columns below (column-level `GRANT SELECT`). It is a login and grants only: no table, column
  or migration change.
- **Data source**: "HEDIS database" (uid `hedis-postgres`, `host.docker.internal:5432`, database `HEDISPOCDB`), not
  editable. Prometheus stays the default. Every query names its tables as `hedis.<table>`.
- **Rows** (all open, newest first, at most 500 rows each):

| Row | Table | Columns shown |
|---|---|---|
| Failures | batch_run, extraction_attempts, extraction_runs, quality_run | time, area, ref (batch id, attempt id, run id), step (or model, measure), error; plus counts of failed batches, AI calls and quality checks, and the AI call success rate |
| Batches | batch_run | started_at, updated_at, duration, status, step, error, batch_id |
| AI calls | extraction_attempts joined to extraction_runs | created_at, run id, attempt id, gap_id, model_name, status, duration, prompt and output tokens, prompt_version, error |
| Quality checks | quality_run | started_at, completed_at, measure_id, status, actor, error, batch_id |
| Measure engine | engine_runs | run_at, run_id, engine, engine_version, measure_spec_version, measurement_year |
| Reviews | review_decisions | decided_at, reviewer, decision (confirmed, rejected), reject_reason_code |

- **Data rules**: operational columns only. Never selected or granted: patient ids or names, `raw_response`,
  `pages_sent`, `dropped`, `scope`, `summary`, `review_decisions.reason` (free text), audit payloads, and any
  document or prompt text. `batch_run.counts` is left out entirely: a column grant cannot be limited to JSON keys,
  and besides its numbers it holds the error text of each document that failed to attach. `engine_runs.source_file`
  is left out because it is the same engine folder on every row.

## Logs (Loki and Alloy)

- **File**: the app writes every log line, uvicorn's included, to `logs/hedis-integration.log` as well as the console
  (`LOG_DIR` in `.env`, default `logs`; 10 MB per file, 5 rotated files kept). Alloy only reads the repo's `logs/`.
- **Shipping**: in the Docker setup, Alloy (`grafana/alloy:v1.10.0`, `observability/alloy/config.alloy`) tails the
  file and pushes to Loki (`grafana/loki:3.5.0`, `observability/loki/loki-config.yml`). A traceback is joined to its
  log line, so it is one entry. `level` (INFO, WARNING, ERROR) is a label; the logger name is structured metadata.
  Prometheus's `GET /metrics` access lines stay in the file but are not sent. Alloy keeps its read position in the
  `alloy-data` volume, so a restart does not send lines twice.
- **Masking** before the lines leave Alloy: tokens ending in .pdf, .png, .jpg, .jpeg, .tif, .tiff, .csv, .json, .txt
  or .docx become `<file>` (`.py` paths stay, so tracebacks remain readable); the value of `user_id` (JSON or
  key=value) and `user_` tokens of 8 or more characters become `<user>`. The file on disk is not changed, and like
  every log it must not hold patient details in the first place.
- **Retention**: 15 days (compactor retention), stored in the `loki-data` volume. Loki and the Alloy UI listen on
  `127.0.0.1` only (3100, 12345).
- **Grafana**: data source "HEDIS logs" (uid `hedis-loki`). The overview has a Logs row (errors and warnings per
  minute, and the log newest first); run history has an "App errors" panel with the ERROR lines. In Explore, search
  with `{job="hedis-integration"} |= "error"` or `{job="hedis-integration", level="ERROR"}`.

## Later

- **RabbitMQ**: enable the `rabbitmq_prometheus` plugin and uncomment the `rabbitmq` job (`rabbitmq:15692`) in the
  template. Queue depth, publish and ack rates then sit next to the pipeline metrics. Labels on any new
  message-handling metrics follow the same rules: the queue name is fine, a message id or body is not.
- **OpenTelemetry**: the metric names and labels stay the same. The OpenTelemetry SDK with its Prometheus exporter
  (or the collector scraping `/metrics`) can replace `prometheus-client` later. Traces would add one span per batch
  step and per model call, keyed by internal ids in span attributes, never by patient details.

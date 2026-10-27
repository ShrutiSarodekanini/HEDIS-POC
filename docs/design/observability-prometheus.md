# Pipeline observability with Prometheus

The integration app exposes metrics at `/metrics` on its own port (`INTEGRATION_PORT`, default 8780). Prometheus
scrapes them every 15 seconds, keeps 15 days of history and evaluates three alert rules. There is no Grafana,
OpenTelemetry or RabbitMQ in this story. Every metric is defined in `services/integration/metrics.py`.

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

Known risk, still to check end to end: the app listens on `127.0.0.1` only. Prometheus in a container reaches the
host through `host.docker.internal`, and that may not connect to a listener bound only to the loopback address. If
the target shows DOWN, the choices are to run `prometheus.exe` on the host, or to add an opt-in setting for the bind
address. The second choice exposes the app beyond this machine and needs a team decision.

## Later

- **RabbitMQ**: enable the `rabbitmq_prometheus` plugin and uncomment the `rabbitmq` job (`rabbitmq:15692`) in the
  template. Queue depth, publish and ack rates then sit next to the pipeline metrics. Labels on any new
  message-handling metrics follow the same rules: the queue name is fine, a message id or body is not.
- **OpenTelemetry**: the metric names and labels stay the same. The OpenTelemetry SDK with its Prometheus exporter
  (or the collector scraping `/metrics`) can replace `prometheus-client` later. Traces would add one span per batch
  step and per model call, keyed by internal ids in span attributes, never by patient details.

-- Read-only login for the Grafana "HEDIS run history" dashboard. A login and grants only: no table, column or
-- migration changes. Run as a superuser after the lead's OK:
--   psql -d HEDISPOCDB -f observability/grafana/readonly_role.sql
-- Then set the password yourself (never in this file):
--   ALTER ROLE hedis_grafana_ro PASSWORD '...';
-- and put the same value in the repo-root .env as GRAFANA_DB_PASSWORD. Safe to run again.
--
-- SELECT is granted per column, so this login cannot read model answers, prompts, page lists, free-text reasons or
-- run summaries (raw_response, pages_sent, dropped, scope, summary, reason), even from Grafana's query editor.
-- batch_run.counts is left out too: besides its numbers it holds per-document error text.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hedis_grafana_ro') THEN
        CREATE ROLE hedis_grafana_ro LOGIN NOINHERIT;
    END IF;
END
$$;

ALTER ROLE hedis_grafana_ro SET default_transaction_read_only = on;
ALTER ROLE hedis_grafana_ro SET statement_timeout = '10s';

GRANT CONNECT ON DATABASE "HEDISPOCDB" TO hedis_grafana_ro;
GRANT USAGE ON SCHEMA hedis TO hedis_grafana_ro;

GRANT SELECT (batch_id, status, step, error, started_at, updated_at)
    ON hedis.batch_run TO hedis_grafana_ro;
GRANT SELECT (id, started_at, finished_at, status, provider, model_name)
    ON hedis.extraction_runs TO hedis_grafana_ro;
GRANT SELECT (id, run_id, gap_id, status, error, prompt_version, duration_ms, prompt_tokens, output_tokens, created_at)
    ON hedis.extraction_attempts TO hedis_grafana_ro;
GRANT SELECT (run_id, batch_id, measure_id, status, actor, error, started_at, completed_at)
    ON hedis.quality_run TO hedis_grafana_ro;
GRANT SELECT (run_id, engine, engine_version, measure_spec_version, measurement_year, run_at)
    ON hedis.engine_runs TO hedis_grafana_ro;
GRANT SELECT (id, reviewer, decision, reject_reason_code, decided_at)
    ON hedis.review_decisions TO hedis_grafana_ro;

"""Data quality on the team tables: our run and check tables, and a one to one detail for data_anomaly.

The data quality checks (services/integration/pallavi.py, rule set services/data_quality_pallavi/config/dq_rules.json)
write their anomalies to the lead's data_anomaly and each batch's record counts to feed_completeness_report. What the
team schema has no place for lives in our own tables:

- quality_run: one run of the checks on one batch (every measure in the rule set, or one), its status and summary.
- quality_check_result: every check of a run (passed, failed or not available) with its metric and threshold.
- data_anomaly_detail: one row per data_anomaly the checks raise: the run, the check's own anomaly type (data_anomaly
  keeps the lead's list and says 'other' when the check's type is not on it) and the patient, when there is one.

No team table changes.

Revision ID: 0007
Revises: 0005
Create Date: 2026-10-26
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE quality_run (
    run_id        bigserial PRIMARY KEY,
    batch_id      uuid NOT NULL REFERENCES source_batch(batch_id),
    measure_id    text,
    status        text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    actor         text NOT NULL,
    summary       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error         text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
CREATE INDEX ix_quality_run_batch ON quality_run (batch_id, started_at DESC);

CREATE TABLE quality_check_result (
    check_pk         bigserial PRIMARY KEY,
    run_id           bigint NOT NULL REFERENCES quality_run(run_id),
    check_name       text NOT NULL,
    resource_type    text,
    measure_id       text,
    status           text NOT NULL CHECK (status IN ('passed', 'failed', 'not_available')),
    metric_name      text,
    metric_value     numeric,
    threshold_value  numeric,
    details          jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_quality_check_result_run ON quality_check_result (run_id);

CREATE TABLE data_anomaly_detail (
    anomaly_id  uuid PRIMARY KEY REFERENCES data_anomaly(anomaly_id),
    run_id      bigint NOT NULL REFERENCES quality_run(run_id),
    check_type  text NOT NULL,
    patient_id  text REFERENCES patient(patient_id)
);
CREATE INDEX ix_data_anomaly_detail_run ON data_anomaly_detail (run_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE data_anomaly_detail, quality_check_result, quality_run")

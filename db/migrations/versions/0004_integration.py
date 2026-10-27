"""Integration tables: the ingestion service's tables, one row per uploaded batch, and a review state for confirmed gaps.

conversion_jobs, fhir_resources and validation_issues are created exactly as the data ingestion service's
SQLAlchemy models define them (services/data_ingestion/app/models.py), so the schema is owned by migrations and the
service's create_all finds them already there.

batch_run tracks one uploaded Synthea batch through conversion, loading, the measure engine and prioritization.
Its batch_id is the text form of source_batch.batch_id (a uuid), so it carries no foreign key.

care_gap_detail.review_status gains 'closed_pending_engine' and 'excluded_pending_engine': a reviewer confirmed
evidence that the care was done, or that the patient is excluded, and the gap stays open in care_gap until the measure
engine runs again with that evidence. The team tables are not changed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

INGESTION_TABLES = r"""
CREATE TABLE conversion_jobs (
    id                    varchar(36) PRIMARY KEY,
    input_dir             text NOT NULL,
    status                varchar(40) NOT NULL,
    started_at            timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz,
    total_source_records  integer NOT NULL,
    resources_generated   integer NOT NULL,
    resources_validated   integer NOT NULL,
    resources_persisted   integer NOT NULL,
    warning_count         integer NOT NULL,
    error_count           integer NOT NULL,
    summary               jsonb NOT NULL
);

CREATE TABLE fhir_resources (
    id                  serial PRIMARY KEY,
    job_id              varchar(36) REFERENCES conversion_jobs(id),
    resource_type       varchar(100) NOT NULL,
    resource_id         varchar(150) NOT NULL,
    patient_id          varchar(150),
    source_file         varchar(120),
    source_record_id    varchar(180),
    source_fingerprint  varchar(180) NOT NULL,
    validation_status   varchar(30) NOT NULL,
    lifecycle_status    varchar(30) NOT NULL,
    persisted           boolean NOT NULL,
    resource_json       jsonb NOT NULL,
    metadata_json       jsonb NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_resource_type_id UNIQUE (resource_type, resource_id),
    CONSTRAINT uq_source_fingerprint UNIQUE (source_fingerprint)
);

CREATE TABLE validation_issues (
    id                serial PRIMARY KEY,
    job_id            varchar(36) NOT NULL REFERENCES conversion_jobs(id),
    resource_pk       integer REFERENCES fhir_resources(id),
    resource_type     varchar(100),
    resource_id       varchar(150),
    source_file       varchar(120),
    source_record_id  varchar(180),
    severity          varchar(30) NOT NULL,
    issue_category    varchar(30) NOT NULL,
    error_code        varchar(120),
    field_path        varchar(240),
    diagnostic        text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);
"""

BATCH_RUN = r"""
CREATE TABLE batch_run (
    batch_id       text PRIMARY KEY,
    ingest_job_id  varchar(36) REFERENCES conversion_jobs(id),
    status         text NOT NULL CHECK (status IN ('running', 'prioritized', 'failed')),
    step           text NOT NULL,
    counts         jsonb NOT NULL DEFAULT '{}'::jsonb,
    error          text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);
"""

REVIEW_STATUS_WITH_ENGINE = r"""
ALTER TABLE care_gap_detail DROP CONSTRAINT care_gap_detail_review_status_check;
ALTER TABLE care_gap_detail ADD CONSTRAINT care_gap_detail_review_status_check
    CHECK (review_status IN ('open', 'pending_review', 'partially_evidenced', 'closed', 'excluded',
                             'closed_pending_engine', 'excluded_pending_engine'));
"""

# A confirmed gap waiting for the engine goes back to waiting for review, the closest state 0003 knows.
REVIEW_STATUS_BEFORE = r"""
UPDATE care_gap_detail SET review_status = 'pending_review'
 WHERE review_status IN ('closed_pending_engine', 'excluded_pending_engine');
ALTER TABLE care_gap_detail DROP CONSTRAINT care_gap_detail_review_status_check;
ALTER TABLE care_gap_detail ADD CONSTRAINT care_gap_detail_review_status_check
    CHECK (review_status IN ('open', 'pending_review', 'partially_evidenced', 'closed', 'excluded'));
"""


def upgrade() -> None:
    op.execute(INGESTION_TABLES)
    op.execute(BATCH_RUN)
    op.execute(REVIEW_STATUS_WITH_ENGINE)


def downgrade() -> None:
    op.execute(REVIEW_STATUS_BEFORE)
    op.execute("DROP TABLE batch_run")
    op.execute("DROP TABLE validation_issues, fhir_resources, conversion_jobs")

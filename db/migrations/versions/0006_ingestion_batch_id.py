"""Ingestion tables follow the ingestion models: source_batches, and batch_id on conversion_jobs and fhir_resources.

The models (services/data_ingestion/app/models.py, commit cdee1be) map a nullable batch_id on ConversionJob and
FhirResource that references a new source_batches table, so every query on those two tables names batch_id. 0004
created them from the earlier models without it. This adds them exactly as the models define them; the code leaves
batch_id empty today. The other new models reuse the lead's table names with different columns and are not created.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE TABLE source_batches (
    id                        varchar(36) PRIMARY KEY,
    source_system             varchar(80) NOT NULL,
    source_format             varchar(40) NOT NULL,
    adapter_name              varchar(80) NOT NULL,
    input_source              text NOT NULL,
    status                    varchar(40) NOT NULL,
    resource_count            integer NOT NULL,
    resource_count_validated  integer NOT NULL,
    started_at                timestamptz NOT NULL DEFAULT now(),
    completed_at              timestamptz
);

ALTER TABLE conversion_jobs ADD COLUMN batch_id varchar(36) REFERENCES source_batches(id);
ALTER TABLE fhir_resources ADD COLUMN batch_id varchar(36) REFERENCES source_batches(id);
CREATE INDEX ix_conversion_jobs_batch_id ON conversion_jobs (batch_id);
CREATE INDEX ix_fhir_resources_batch_id ON fhir_resources (batch_id);
""")


def downgrade() -> None:
    op.execute(r"""
ALTER TABLE fhir_resources DROP COLUMN batch_id;
ALTER TABLE conversion_jobs DROP COLUMN batch_id;
DROP TABLE source_batches;
""")

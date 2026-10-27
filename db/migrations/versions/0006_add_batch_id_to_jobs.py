"""Ingestion tables follow Jyothi's models again: source_batches, and batch_id on conversion_jobs and fhir_resources.

Her models (services/data_ingestion/app/models.py, commit cdee1be) map a nullable batch_id on ConversionJob and
FhirResource that references a new source_batches table, so every query on those two tables names batch_id. 0004
created them from her earlier models without it. This adds them exactly as her models define them; her code leaves
batch_id empty today. Her other new models reuse the lead's table names with different columns and are not created.

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
ALTER TABLE conversion_jobs ADD COLUMN batch_id uuid REFERENCES source_batch(batch_id);
ALTER TABLE fhir_resources ADD COLUMN batch_id uuid REFERENCES source_batch(batch_id);
CREATE INDEX ix_conversion_jobs_batch_id ON conversion_jobs (batch_id);
CREATE INDEX ix_fhir_resources_batch_id ON fhir_resources (batch_id);
""")


def downgrade() -> None:
    op.execute(r"""
DROP INDEX IF EXISTS ix_fhir_resources_batch_id;
DROP INDEX IF EXISTS ix_conversion_jobs_batch_id;
ALTER TABLE fhir_resources DROP COLUMN IF EXISTS batch_id;
ALTER TABLE conversion_jobs DROP COLUMN IF EXISTS batch_id;
""")
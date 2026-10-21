"""Create the initial HEDIS PostgreSQL schema.

Revision ID: 0001_initial_schema
Revises:
"""
from pathlib import Path
from collections.abc import Iterator
from typing import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _iter_sql_statements(sql_path: Path) -> Iterator[str]:
    sql_text = sql_path.read_text(encoding="utf-8")
    sql_without_line_comments = "\n".join(
        line for line in sql_text.splitlines() if not line.lstrip().startswith("--")
    )
    for statement in sql_without_line_comments.split(";"):
        normalized = statement.strip()
        if normalized:
            yield normalized


def upgrade() -> None:
    sql_path = Path(__file__).with_suffix(".sql")
    for statement in _iter_sql_statements(sql_path):
        op.execute(statement)


def downgrade() -> None:
    statements = """
        DROP TABLE IF EXISTS synthea_records;
        DROP TABLE IF EXISTS care_gaps;
        DROP TABLE IF EXISTS measures;
        DROP TABLE IF EXISTS members;
        DROP TABLE IF EXISTS domain_event;
        DROP TABLE IF EXISTS feed_completeness_report;
        DROP TABLE IF EXISTS fhir_resource_raw;
        DROP TABLE IF EXISTS document_reference;
        DROP TABLE IF EXISTS allergy_intolerance;
        DROP TABLE IF EXISTS immunization;
        DROP TABLE IF EXISTS medication_request;
        DROP TABLE IF EXISTS procedure;
        DROP TABLE IF EXISTS observation;
        DROP TABLE IF EXISTS condition;
        DROP TABLE IF EXISTS encounter;
        DROP TABLE IF EXISTS coverage;
        DROP TABLE IF EXISTS location;
        DROP TABLE IF EXISTS patient;
        DROP TABLE IF EXISTS practitioner;
        DROP TABLE IF EXISTS organization;
        DROP TABLE IF EXISTS source_batch;
        """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)

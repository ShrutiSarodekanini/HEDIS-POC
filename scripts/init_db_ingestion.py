from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.multi_source_ingestion.app import db  # noqa: E402
from services.multi_source_ingestion.app import models  # noqa: F401, E402


def ensure_validation_issue_columns() -> None:
    with db.engine.begin() as conn:
        inspector = inspect(conn)
        cols = {c["name"] for c in inspector.get_columns("validation_issues")}
        if "source_file" not in cols:
            conn.execute(text("ALTER TABLE validation_issues ADD COLUMN source_file VARCHAR(120)"))
        if "source_record_id" not in cols:
            conn.execute(text("ALTER TABLE validation_issues ADD COLUMN source_record_id VARCHAR(180)"))


def ensure_provenance_columns() -> None:
    with db.engine.begin() as conn:
        inspector = inspect(conn)
        fhir_columns = {c["name"] for c in inspector.get_columns("fhir_resources")}
        if "batch_id" not in fhir_columns:
            conn.execute(text("ALTER TABLE fhir_resources ADD COLUMN batch_id VARCHAR(36)"))
        job_columns = {c["name"] for c in inspector.get_columns("conversion_jobs")}
        if "batch_id" not in job_columns:
            conn.execute(text("ALTER TABLE conversion_jobs ADD COLUMN batch_id VARCHAR(36)"))
        for table in (
            "coverage", "patient", "encounter", "condition", "observation",
            "procedure", "medication", "immunization", "allergy_intolerance",
            "claim", "device", "imaging_study", "organization", "practitioner",
            "supply_delivery", "basic_resource",
        ):
            columns = {c["name"] for c in inspector.get_columns(table)}
            if "batch_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN batch_id VARCHAR(36)"))


def main() -> None:
    db.Base.metadata.create_all(bind=db.engine)
    ensure_validation_issue_columns()
    ensure_provenance_columns()
    print("Database schema initialized")


if __name__ == "__main__":
    main()

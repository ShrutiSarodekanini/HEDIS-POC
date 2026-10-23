from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.data_ingestion.app.db import Base, engine  # noqa: E402
import services.data_ingestion.app.models  # noqa: F401,E402

TARGET_TABLES = [
    "conversion_jobs",
    "fhir_resources",
    "coverage",
    "patient",
    "encounter",
    "condition",
    "observation",
    "procedure",
    "medication",
    "immunization",
    "allergy_intolerance",
    "claim",
    "device",
    "imaging_study",
    "organization",
    "practitioner",
    "supply_delivery",
    "basic_resource",
]


def main() -> int:
    # Create any missing tables from models first.
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        inspector = inspect(conn)
        existing = set(inspector.get_table_names(schema="public"))
        if "source_batches" not in existing:
            raise SystemExit("source_batches table was not created; migration cannot continue.")

        migrated: list[str] = []
        for table in TARGET_TABLES:
            if table not in existing:
                continue

            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS batch_id VARCHAR(36)'))
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{table}_batch_id ON "{table}"(batch_id)'))

            constraint_name = f"{table}_batch_id_fkey"
            exists = conn.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = :constraint_name LIMIT 1"),
                {"constraint_name": constraint_name},
            ).scalar_one_or_none()
            if exists is None:
                conn.execute(
                    text(
                        f'ALTER TABLE "{table}" '
                        f'ADD CONSTRAINT {constraint_name} '
                        f'FOREIGN KEY (batch_id) REFERENCES source_batches(id)'
                    )
                )
            migrated.append(table)

    print("MIGRATED_TABLES=" + ",".join(migrated))
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

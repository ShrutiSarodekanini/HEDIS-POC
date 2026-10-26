from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.data_ingestion.app import db


def ensure_validation_issue_columns() -> None:
    with db.engine.begin() as conn:
        inspector = inspect(conn)
        cols = {c["name"] for c in inspector.get_columns("validation_issues")}
        if "source_file" not in cols:
            conn.execute(text("ALTER TABLE validation_issues ADD COLUMN source_file VARCHAR(120)"))
        if "source_record_id" not in cols:
            conn.execute(text("ALTER TABLE validation_issues ADD COLUMN source_record_id VARCHAR(180)"))


def main() -> None:
    db.Base.metadata.create_all(bind=db.engine)
    ensure_validation_issue_columns()
    print("Database schema initialized")


if __name__ == "__main__":
    main()

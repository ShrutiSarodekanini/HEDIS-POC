from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    evidence_storage_dir: Path
    database_url: str | None = None

    @staticmethod
    def from_env() -> "AppConfig":
        database_url = os.getenv("DATABASE_URL")
        evidence_storage_dir = Path(
            os.getenv("EVIDENCE_STORAGE_DIR", "generated_evidence")
        )
        return AppConfig(
            evidence_storage_dir=evidence_storage_dir,
            database_url=database_url,
        )

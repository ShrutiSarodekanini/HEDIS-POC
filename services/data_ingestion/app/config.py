from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./hedis_poc.db")
    datasets_base_dir: str = os.getenv("DATASETS_BASE_DIR", "./datasets")
    fhir_validator_jar: str | None = os.getenv("FHIR_VALIDATOR_JAR")
    validation_require_external: bool = os.getenv("VALIDATION_REQUIRE_EXTERNAL", "false").lower() == "true"


settings = Settings()

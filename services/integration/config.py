"""Settings for the integration services, read from the repo-root .env and environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    actor: str                     # recorded in audit events and on engine runs
    reviewer: str                  # recorded on review decisions; the POC has no sign-in yet
    measurement_year: int
    engine_root: Path              # the fqm-execution engine (services/measure_calc/engine)
    engine_chunk: int              # patient bundles per engine call, to keep Node memory bounded
    work_dir: Path                 # temporary bundles and uploads, removed after each step
    evidence_dir: Path | None      # stored documents with manifest.json; None skips the attach step
    ui_dir: Path | None            # screen set served at "/" (index.html plus /assets); None serves static/
    port: int


def load_settings() -> Settings:
    return Settings(
        actor=os.environ.get("INTEGRATION_ACTOR", "integration"),
        reviewer=os.environ.get("INTEGRATION_REVIEWER", "local-reviewer"),
        measurement_year=int(os.environ.get("INTEGRATION_MEASUREMENT_YEAR", "2026")),
        engine_root=REPO_ROOT / "services" / "measure_calc" / "engine",
        engine_chunk=int(os.environ.get("INTEGRATION_ENGINE_CHUNK", "50")),
        work_dir=Path(os.environ.get("INTEGRATION_WORK_DIR", REPO_ROOT / ".integration_work")),
        evidence_dir=Path(evidence) if (evidence := os.environ.get("INTEGRATION_EVIDENCE_DIR", "").strip()) else None,
        ui_dir=Path(ui) if (ui := os.environ.get("INTEGRATION_UI_DIR", "").strip()) else None,
        port=int(os.environ.get("INTEGRATION_PORT", "8780")),
    )


SETTINGS = load_settings()

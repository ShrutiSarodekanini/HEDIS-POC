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
    engine_node_heap_mb: int       # V8 heap available to one measure-engine process
    engine_workers: int            # concurrent measure processes; kept low for stable Node memory use
    work_dir: Path                 # temporary bundles and uploads, removed after each step
    evidence_auto_limit: int       # top-priority gaps a batch sends to the AI step; 0 runs none, others run on request


def load_settings() -> Settings:
    return Settings(
        actor=os.environ.get("INTEGRATION_ACTOR", "integration"),
        reviewer=os.environ.get("INTEGRATION_REVIEWER", "local-reviewer"),
        measurement_year=int(os.environ.get("INTEGRATION_MEASUREMENT_YEAR", "2026")),
        engine_root=REPO_ROOT / "services" / "measure_calc" / "engine",
        engine_chunk=int(os.environ.get("INTEGRATION_ENGINE_CHUNK", "50")),
        engine_node_heap_mb=int(os.environ.get("INTEGRATION_NODE_HEAP_MB", "4096")),
        engine_workers=max(1, int(os.environ.get("INTEGRATION_ENGINE_WORKERS", "2"))),
        work_dir=Path(os.environ.get("INTEGRATION_WORK_DIR", REPO_ROOT / ".integration_work")),
        evidence_auto_limit=max(0, int(os.environ.get("AI_EVIDENCE_AUTO_LIMIT", "5"))),
    )


SETTINGS = load_settings()

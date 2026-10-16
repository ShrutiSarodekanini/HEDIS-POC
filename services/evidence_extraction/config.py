"""Settings for evidence extraction, read from the repo-root .env and environment variables.

The model runs on OpenRouter. The API key is read here and nowhere else, and never logged or stored.
Only synthetic data may be sent to OpenRouter.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    openrouter_url: str           # OpenAI-compatible chat completions endpoint
    openrouter_api_key: str | None
    model: str
    timeout_s: float              # one model call
    max_output_tokens: int        # reasoning models spend output tokens on thinking; 8192 avoids cut-off JSON
    token_budget: int             # prompt tokens per call before a chart is split into several calls
    min_confidence: float         # the model's own confidence below this means it does not believe the finding
    max_excerpt_chars: int        # longer quotes are narrowed to the sentence holding the value
    actor: str                    # recorded in audit events


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://", 1),
        openrouter_url=os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        model=os.environ.get("EXTRACTION_MODEL", "nvidia/nemotron-3-super-120b-a12b:free"),
        timeout_s=float(os.environ.get("EXTRACTION_TIMEOUT_S", "300")),
        max_output_tokens=int(os.environ.get("EXTRACTION_MAX_OUTPUT_TOKENS", "8192")),
        token_budget=int(os.environ.get("EXTRACTION_TOKEN_BUDGET", "6000")),
        min_confidence=float(os.environ.get("EXTRACTION_MIN_CONFIDENCE", "0.3")),
        max_excerpt_chars=int(os.environ.get("EXTRACTION_MAX_EXCERPT_CHARS", "200")),
        actor=os.environ.get("EXTRACTION_ACTOR", "evidence_extraction"),
    )


SETTINGS = load_settings()

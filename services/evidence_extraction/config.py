"""Settings for evidence extraction, read from the repo-root .env and environment variables.

The default provider is OpenRouter. The API key is read here and nowhere else, and never logged or stored.
Model calls are blocked unless the provider policy allows the configured data class.
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
    data_class: str = ""          # "synthetic" is the only class allowed for hosted providers without a BAA
    provider: str = "openrouter"
    provider_mode: str = "hosted"  # hosted | local
    provider_has_baa: bool = False
    provider_allowed_data_classes: tuple[str, ...] = ("synthetic",)
    store_raw_attempt_response: bool = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    values = tuple(v.strip().lower() for v in raw.split(",") if v.strip())
    return values or default


def provider_policy_error(settings: Settings) -> str | None:
    """Return the provider-policy failure message when this configuration is unsafe; otherwise None."""
    data_class = (settings.data_class or "").strip().lower()
    if not data_class:
        return ("AI evidence is off: set EXTRACTION_DATA_CLASS to an approved value and retry. "
                "For hosted providers without a BAA, only synthetic data is allowed.")

    provider_mode = (settings.provider_mode or "").strip().lower()
    if provider_mode not in {"hosted", "local"}:
        return "AI evidence is off: EXTRACTION_PROVIDER_MODE must be hosted or local."

    if data_class not in settings.provider_allowed_data_classes:
        allowed = ", ".join(settings.provider_allowed_data_classes)
        return (f"AI evidence is off: data class '{data_class}' is not approved for provider "
                f"'{settings.provider}'. Allowed classes: {allowed}.")

    if provider_mode == "hosted" and data_class != "synthetic" and not settings.provider_has_baa:
        return (f"AI evidence is off: provider '{settings.provider}' is hosted without a BAA, so it cannot receive "
                f"'{data_class}' data. Use a local provider or a hosted provider covered by a signed BAA.")
    return None


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
        data_class=os.environ.get("EXTRACTION_DATA_CLASS", "").strip(),
        provider=os.environ.get("EXTRACTION_PROVIDER", "openrouter").strip().lower() or "openrouter",
        provider_mode=os.environ.get("EXTRACTION_PROVIDER_MODE", "hosted").strip().lower() or "hosted",
        provider_has_baa=_env_bool("EXTRACTION_PROVIDER_HAS_BAA", False),
        provider_allowed_data_classes=_csv_env("EXTRACTION_PROVIDER_ALLOWED_DATA_CLASSES", ("synthetic",)),
        store_raw_attempt_response=_env_bool("EXTRACTION_STORE_RAW_ATTEMPT_RESPONSE", False),
    )


SETTINGS = load_settings()

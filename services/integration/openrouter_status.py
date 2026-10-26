"""OpenRouter status for GET /api/health: is the key valid and is the model in use listed. No completion is made.

Results are cached for 5 minutes. Network failures show as reachable: false with the error type; the key itself is
never returned or logged.
"""

import threading
import time
from datetime import UTC, datetime

import httpx

from services.evidence_extraction.config import SETTINGS as EXTRACTION_SETTINGS

CACHE_SECONDS = 300
_lock = threading.Lock()
_cached: tuple[float, dict] | None = None


def status() -> dict:
    global _cached
    with _lock:
        if _cached is None or time.monotonic() - _cached[0] > CACHE_SECONDS:
            _cached = (time.monotonic(), _check())
        return _cached[1]


def _check() -> dict:
    base = EXTRACTION_SETTINGS.openrouter_url.split("/chat/completions")[0]
    model, key = EXTRACTION_SETTINGS.model, EXTRACTION_SETTINGS.openrouter_api_key
    result = {"reachable": True, "key_valid": None, "model": model, "model_listed": None,
              "checked_at": datetime.now(UTC).isoformat(timespec="seconds"), "error": None}
    try:
        with httpx.Client(timeout=10) as client:
            if key:
                result["key_valid"] = client.get(f"{base}/key", headers={"Authorization": f"Bearer {key}"}).status_code == 200
            else:
                result["key_valid"], result["error"] = False, "OPENROUTER_API_KEY is not set"
            models = client.get(f"{base}/models")
            models.raise_for_status()
            result["model_listed"] = any(m.get("id") == model for m in models.json().get("data", []))
    except (httpx.HTTPError, ValueError) as exc:
        result["reachable"], result["error"] = False, type(exc).__name__
    return result

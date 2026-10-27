"""Prometheus metrics for the integration app, all in one place (see docs/design/observability-prometheus.md).

Labels are fixed vocabularies only: step, measure, model, status, outcome, decision, kind. A batch id is used as a
key in _STEPS to time steps, never as a label; no patient id, file name or text ever becomes a label value.
"""

import threading
import time

from prometheus_client import Counter, Gauge, Histogram

BATCH_STEP_SECONDS = Histogram("hedis_batch_step_seconds", "Time spent in each batch step", ["step"],
                               buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600))
BATCHES = Counter("hedis_batches_total", "Batches finished, by final status", ["status"])
BATCH_FAILURES = Counter("hedis_batch_failures_total", "Failed batches, by the step that failed", ["step"])
BATCH_RUNNING = Gauge("hedis_batch_running", "1 while a batch or recalculation thread is working")
PATIENTS_PROCESSED = Counter("hedis_patients_processed_total", "Patients in batches that finished prioritized")

ENGINE_SECONDS = Histogram("hedis_engine_seconds", "Time per measure engine call, retries included", ["measure"],
                           buckets=(1, 5, 10, 20, 40, 60, 120, 300))
ENGINE_RETRIES = Counter("hedis_engine_retries_total", "Engine calls retried after a crash", ["measure"])
GAPS_OPEN = Gauge("hedis_gaps_open", "Open gaps found by the latest engine run, per measure", ["measure"])

AI_CALL_SECONDS = Histogram("hedis_ai_call_seconds", "Time per model call", ["model"],
                            buckets=(1, 2, 5, 10, 20, 30, 60, 120))
AI_TOKENS = Counter("hedis_ai_tokens_total", "Tokens reported by the model provider", ["model", "kind"])
AI_CALLS = Counter("hedis_ai_calls_total", "Model calls, by outcome", ["model", "outcome"])
AI_REVIEW_DECISIONS = Counter("hedis_ai_review_decisions_total", "Reviewer decisions on AI evidence", ["decision"])

# The model client's own messages for these two failures (services/evidence_extraction/model_client.py).
_CUT_OFF = "Answer was cut off"
_EMPTY = "OpenRouter returned an empty answer"

_STEPS: dict[object, tuple[str, float]] = {}
_STEPS_LOCK = threading.Lock()


def step_started(batch_id: object, step: str) -> None:
    """Close the batch's previous step (if any) and start timing the next one."""
    now = time.monotonic()
    with _STEPS_LOCK:
        previous = _STEPS.get(batch_id)
        _STEPS[batch_id] = (step, now)
    if previous:
        BATCH_STEP_SECONDS.labels(previous[0]).observe(now - previous[1])


def batch_ended(batch_id: object) -> None:
    """Close the batch's last step and forget the batch."""
    with _STEPS_LOCK:
        previous = _STEPS.pop(batch_id, None)
    if previous:
        BATCH_STEP_SECONDS.labels(previous[0]).observe(time.monotonic() - previous[1])


def ai_outcome(exc: BaseException) -> str:
    """cut_off or empty for the model client's two known answers, error for anything else."""
    message = str(exc)
    if message.startswith(_CUT_OFF):
        return "cut_off"
    if message.startswith(_EMPTY):
        return "empty"
    return "error"

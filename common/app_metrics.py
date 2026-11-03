"""Prometheus metrics and route logging for the root app.py (the new React UI's backend), prefix hedis_ui_.

Decorators wrap the existing handlers: they time and count the call and log its outcome, then return or re-raise
exactly what the handler did. Labels are fixed vocabularies (endpoint, outcome, change, kind, trigger) or a measure
code that matches MEASURE_CODE; never an email, member id, file name or exception text.
"""

import functools
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from prometheus_client import Counter, Histogram

log = logging.getLogger("app")

STAR_CALCULATIONS = Counter("hedis_ui_star_calculations_total", "Star calculations, by endpoint, measure and outcome",
                            ["endpoint", "measure_id", "outcome"])
STAR_SECONDS = Histogram("hedis_ui_star_calculation_seconds", "Time per star calculation", ["endpoint", "measure_id"],
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
EVIDENCE = Counter("hedis_ui_evidence_generations_total", "Evidence document generations, by outcome", ["outcome"])
EVIDENCE_SECONDS = Histogram("hedis_ui_evidence_generation_seconds", "Time per evidence document generation",
                             buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60))
PROCESS_GAPS_RUNS = Counter("hedis_ui_process_gaps_runs_total", "Gap automation runs, by trigger and outcome",
                            ["trigger", "outcome"])
PROCESS_GAPS_GAPS = Counter("hedis_ui_process_gaps_gaps_total", "Care gaps processed by gap automation runs",
                            ["trigger"])
SEED_RUNS = Counter("hedis_ui_seed_runs_total", "Star dashboard sample data seed runs, by outcome", ["outcome"])
SIGNINS = Counter("hedis_ui_signins_total", "Sign-in attempts, by outcome", ["outcome"])
ADMIN_CHANGES = Counter("hedis_ui_admin_changes_total", "Administrator access and role changes, by outcome",
                        ["change", "outcome"])
CLIENT_ERRORS = Counter("hedis_ui_client_errors_total", "Errors reported by the React UI, by kind", ["kind"])
CLIENT_ERRORS_DROPPED = Counter("hedis_ui_client_errors_dropped_total", "Browser error reports over the rate limit")

MEASURE_CODE = re.compile(r"^[A-Z][A-Z0-9-]{0,9}$")
_MEASURES_SEEN: set[str] = set()
_MEASURES_MAX = 50


def measure_label(code: object) -> str:
    """A measure code as a label: upper-cased, pattern-checked and capped, so request bodies cannot add series."""
    value = str(code or "").strip().upper()
    if not value:
        return "all"
    if not MEASURE_CODE.match(value):
        return "other"
    if value not in _MEASURES_SEEN and len(_MEASURES_SEEN) >= _MEASURES_MAX:
        return "other"
    _MEASURES_SEEN.add(value)
    return value


def _failure(route: str, exc: BaseException) -> str:
    """Log a failed route call (status and exception types only) and return its outcome label."""
    if isinstance(exc, HTTPException):
        cause = type(exc.__cause__).__name__ if exc.__cause__ else "none"
        level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
        log.log(level, "%s failed: HTTP %s (%s)", route, exc.status_code, cause)
        return {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                409: "conflict", 422: "unprocessable"}.get(exc.status_code, "error")
    log.error("%s failed: %s", route, type(exc).__name__)
    return "error"


def track_route(route: str) -> Callable:
    """Log the failures of a route that has no metrics of its own."""
    def decorate(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return handler(*args, **kwargs)
            except Exception as exc:
                _failure(route, exc)
                raise
        return wrapper
    return decorate


def track_star(endpoint: str, measure_of: Callable[[dict], object]) -> Callable:
    """Count and time a star calculation; measure_of picks the measure code from the handler's kwargs."""
    def decorate(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            measure = measure_label(measure_of(kwargs))
            started = time.perf_counter()
            try:
                result = handler(*args, **kwargs)
            except Exception as exc:
                STAR_CALCULATIONS.labels(endpoint, measure, _failure(f"star {endpoint}", exc)).inc()
                raise
            finally:
                STAR_SECONDS.labels(endpoint, measure).observe(time.perf_counter() - started)
            STAR_CALCULATIONS.labels(endpoint, measure, "ok").inc()
            log.info("star %s finished: measure=%s seconds=%.2f", endpoint, measure, time.perf_counter() - started)
            return result
        return wrapper
    return decorate


def track_evidence(handler: Callable) -> Callable:
    """Count and time an evidence document generation; logs the evidence id and measure only."""
    @functools.wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = handler(*args, **kwargs)
        except Exception as exc:
            EVIDENCE.labels(_failure("evidence generation", exc)).inc()
            raise
        finally:
            EVIDENCE_SECONDS.observe(time.perf_counter() - started)
        EVIDENCE.labels("ok").inc()
        log.info("evidence generated: evidence_id=%s measure=%s format=%s seconds=%.2f", result.get("evidence_id"),
                 measure_label(result.get("measure_id")), result.get("format"), time.perf_counter() - started)
        return result
    return wrapper


def record_process_gaps(trigger: str, summary: dict | None, exc: BaseException | None = None) -> None:
    """Count one gap automation run; the summary's error list is counted, never logged (it quotes member ids)."""
    if exc is not None:
        PROCESS_GAPS_RUNS.labels(trigger, "error").inc()
        log.error("process-gaps (%s) failed: %s", trigger, type(exc).__name__)
        return
    summary = summary or {}
    errors = len(summary.get("errors") or [])
    outcome = "skipped" if summary.get("skipped") else ("partial" if errors else "ok")
    processed = int(summary.get("processed") or 0)
    PROCESS_GAPS_RUNS.labels(trigger, outcome).inc()
    # A skipped run still reports every listed gap as "processed", but nothing was done to them.
    if outcome != "skipped":
        PROCESS_GAPS_GAPS.labels(trigger).inc(processed)
    if trigger == "api" or errors or summary.get("evidence_created"):
        log.log(logging.WARNING if errors else logging.INFO,
                "process-gaps (%s) %s: processed=%d evidence_created=%s star_baseline_created=%s errors=%d",
                trigger, outcome, processed, summary.get("evidence_created", 0),
                summary.get("star_baseline_created", 0), errors)


def track_process_gaps(handler: Callable) -> Callable:
    """The process-gaps route: one counted run per call."""
    @functools.wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = handler(*args, **kwargs)
        except Exception as exc:
            record_process_gaps("api", None, exc)
            raise
        record_process_gaps("api", result)
        return result
    return wrapper


def track_seed(handler: Callable) -> Callable:
    """Count a sample data seed run; ok when rows were inserted, unchanged when everything already existed."""
    @functools.wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = handler(*args, **kwargs)
        except Exception as exc:
            SEED_RUNS.labels(_failure("sample data seed", exc)).inc()
            raise
        counts = {k: v for k, v in result.items() if k.startswith("inserted_")}
        SEED_RUNS.labels("ok" if any(counts.values()) else "unchanged").inc()
        log.info("sample data seed finished: %s", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        return result
    return wrapper


def track_signin(handler: Callable) -> Callable:
    """Count sign-ins by outcome only; the email is never logged or labelled."""
    @functools.wraps(handler)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = handler(*args, **kwargs)
        except HTTPException as exc:
            outcome = {401: "bad_credentials", 403: "not_approved", 400: "invalid_request"}.get(exc.status_code, "error")
            SIGNINS.labels(outcome).inc()
            log.warning("sign-in refused: %s", outcome)
            raise
        except Exception as exc:
            SIGNINS.labels("error").inc()
            log.error("sign-in failed: %s", type(exc).__name__)
            raise
        SIGNINS.labels("ok").inc()
        return result
    return wrapper


def track_admin(change: str) -> Callable:
    """Count an administrator access or role change; logs the target user id and new value only."""
    def decorate(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = handler(*args, **kwargs)
            except Exception as exc:
                ADMIN_CHANGES.labels(change, _failure(f"admin {change} change", exc)).inc()
                raise
            ADMIN_CHANGES.labels(change, "ok").inc()
            body = kwargs.get("body")
            new_value = getattr(body, "access_status", None) or getattr(body, "role", None)
            log.info("admin %s change: user_id=%s value=%s", change, kwargs.get("user_id"), new_value)
            return result
        return wrapper
    return decorate


class RateLimit:
    """At most `limit` events per `seconds`, per process."""

    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > self.seconds:
                self._times.popleft()
            if len(self._times) >= self.limit:
                return False
            self._times.append(now)
            return True

"""File logging for the root app.py (the one backend), set up at import because uvicorn --reload has no __main__, and
log shipping to Alloy for every process (ship_logs, only when ALLOY_URL is set).

The line format is the one the Alloy pipeline parses (observability/alloy/config.alloy). Tracebacks keep their frames but
lose the exception message, and access-log paths lose ids and query strings: neither may carry patient details.
"""

import atexit
import contextvars
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_hedis_log_file"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_UUID_ANYWHERE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def safe_text(value: str | None, limit: int) -> str:
    """Untrusted text for one log line: emails and UUIDs masked, control characters (new lines) removed, cut."""
    text = _CONTROL.sub(" ", value or "")
    text = _UUID_ANYWHERE.sub("{id}", _EMAIL.sub("<email>", text))
    return text.strip()[:limit]


def path_template(path: str) -> str:
    """Drop the query string and replace digits-only and UUID segments with {id}."""
    parts = path.split("?", 1)[0].split("#", 1)[0].split("/")
    return "/".join("{id}" if part.isdigit() or _UUID.match(part) else part for part in parts)


class SafeTracebackFormatter(logging.Formatter):
    """Tracebacks show file, line and function, and the exception type only (its text can quote data)."""

    def format(self, record: logging.LogRecord) -> str:
        # logging caches the first handler's full traceback on the record (exc_text); never reuse it here.
        cached = record.exc_text
        record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_text = cached

    def formatException(self, ei) -> str:  # noqa: N802 - logging.Formatter's method name
        lines = []
        exc = ei[1]
        while exc is not None:
            frames = traceback.extract_tb(exc.__traceback__)
            lines.append("Traceback (most recent call last):")
            lines += [f'  File "{f.filename}", line {f.lineno}, in {f.name}' for f in frames]
            lines.append(f"{type(exc).__name__} (message not logged)")
            exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        return "\n".join(lines)


class _AccessPathFilter(logging.Filter):
    """uvicorn access lines: (client, method, path, http_version, status); the path becomes its template."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            record.args = (client, method, path_template(str(path)), version, status)
        return True


def setup_file_logging(file_name: str) -> None:
    """Add one rotating file handler (10 MB x 5) under LOG_DIR to root and uvicorn's loggers; safe to call twice."""
    root = logging.getLogger()
    if any(getattr(h, _MARK, None) == file_name for h in root.handlers):
        return
    log_dir = REPO_ROOT / os.environ.get("LOG_DIR", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / file_name, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(SafeTracebackFormatter(LOG_FORMAT))
    setattr(handler, _MARK, file_name)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    # Under --reload, watchfiles logs every change at INFO, including writes to this log file: an endless loop.
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        if not _reaches(logging.getLogger(name), handler):
            logging.getLogger(name).addHandler(handler)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _AccessPathFilter) for f in access.filters):
        access.addFilter(_AccessPathFilter())


def _reaches(logger: logging.Logger, handler: logging.Handler) -> bool:
    """True when a record on this logger already ends up at handler (uvicorn's loggers do not all propagate)."""
    current: logging.Logger | None = logger
    while current is not None:
        if handler in current.handlers:
            return True
        current = current.parent if current.propagate else None
    return False


# ---------------------------------------------------------------- log shipping (Loki push API, through Alloy)

SHIP_QUEUE_SIZE = 10000
SHIP_BATCH_LINES = 500
SHIP_INTERVAL_S = 2.0
SHIP_TIMEOUT_S = 5
SHIP_FLUSH_AT_EXIT_S = 3.0
SHIP_WARN_EVERY_S = 60.0
# Ids that let one batch be followed across services; sent as structured metadata, never as labels.
CONTEXT_KEYS = ("batch_id", "job_id")

_log_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hedis_log_context", default=None)


def bind_log_context(**ids: object) -> contextvars.Token:
    """Attach batch_id / job_id to the log records of the current thread until reset_log_context(token)."""
    return _log_context.set({k: str(v) for k, v in ids.items() if k in CONTEXT_KEYS and v not in (None, "")})


def reset_log_context(token: contextvars.Token) -> None:
    _log_context.reset(token)


class _LokiPushHandler(logging.Handler):
    """Queues formatted records and sends them in batches from a daemon thread; never blocks or raises."""

    def __init__(self, push_url: str, service: str) -> None:
        super().__init__(level=logging.INFO)
        self._url = push_url
        self._service = service
        self._queue: queue.Queue = queue.Queue(maxsize=SHIP_QUEUE_SIZE)
        self._stopping = threading.Event()
        self._dropped = 0
        self._last_warning = 0.0
        self._thread = threading.Thread(target=self._run, name="hedis-log-shipper", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        if threading.current_thread() is self._thread:
            return
        try:
            context = _log_context.get() or {}
            metadata = {key: str(getattr(record, key, None) or context.get(key) or "") for key in CONTEXT_KEYS}
            entry = (record.levelname, str(int(record.created * 1e9)), self.format(record),
                     {k: v for k, v in metadata.items() if v})
            self._queue.put_nowait(entry)
        except queue.Full:
            self._count_dropped(1)
        except Exception:  # noqa: BLE001 - a record that cannot be formatted is dropped, never raised to the app
            self._count_dropped(1)

    def close(self) -> None:
        """Send what is queued, for at most SHIP_FLUSH_AT_EXIT_S seconds."""
        if not self._stopping.is_set():
            self._stopping.set()
            self._thread.join(SHIP_FLUSH_AT_EXIT_S)
        super().close()

    def _run(self) -> None:
        while True:
            batch = []
            deadline = time.monotonic() + SHIP_INTERVAL_S
            while len(batch) < SHIP_BATCH_LINES:
                wait = deadline - time.monotonic()
                if self._stopping.is_set():
                    wait = 0
                try:
                    batch.append(self._queue.get(timeout=max(wait, 0)) if wait > 0 else self._queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._send(batch)
            elif self._stopping.is_set():
                return

    def _send(self, batch: list[tuple]) -> None:
        streams: dict[str, list] = {}
        for level, timestamp, line, metadata in batch:
            streams.setdefault(level, []).append([timestamp, line, metadata] if metadata else [timestamp, line])
        body = {"streams": [{"stream": {"job": "hedis", "service": self._service, "level": level}, "values": values}
                            for level, values in streams.items()]}
        request = urllib.request.Request(self._url, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=SHIP_TIMEOUT_S):
                pass
        except Exception:  # noqa: BLE001 - Alloy down or slow: the lines are dropped and counted
            self._count_dropped(len(batch))

    def _count_dropped(self, count: int) -> None:
        """Count lost lines; at most one warning a minute, straight to stderr (logging here would recurse)."""
        self._dropped += count
        now = time.monotonic()
        if now - self._last_warning >= SHIP_WARN_EVERY_S:
            self._last_warning = now
            sys.stderr.write(f"hedis log shipping ({self._service}): {self._dropped} line(s) dropped so far; "
                             f"Alloy at {self._url} is unreachable or the queue is full\n")


class _QuietLibraries(logging.Filter):
    """pika logs every connection step at INFO; only its warnings and errors are worth shipping."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or not record.name.startswith("pika")


def ship_logs(service: str) -> None:
    """Send this process's log records at INFO and above to Alloy (ALLOY_URL); does nothing when it is not set."""
    url = os.environ.get("ALLOY_URL", "").strip().rstrip("/")
    root = logging.getLogger()
    if not url or any(isinstance(h, _LokiPushHandler) for h in root.handlers):
        return
    if not root.handlers:
        # Without any handler, logging printed WARNING and above to stderr; keep that console output.
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        root.addHandler(console)
    handler = _LokiPushHandler(f"{url}/loki/api/v1/push", service)
    handler.setFormatter(SafeTracebackFormatter(LOG_FORMAT))
    handler.addFilter(_QuietLibraries())
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        if not _reaches(logging.getLogger(name), handler):
            logging.getLogger(name).addHandler(handler)

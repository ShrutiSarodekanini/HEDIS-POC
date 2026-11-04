"""File logging for the root app.py (the one backend), set up at import because uvicorn --reload has no __main__.

The line format is the one the Alloy pipeline parses (observability/alloy/config.alloy). Tracebacks keep their frames but
lose the exception message, and access-log paths lose ids and query strings: neither may carry patient details.
"""

import logging
import os
import re
import traceback
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

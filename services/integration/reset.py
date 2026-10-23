"""Start over: empty this app's schema and load the reference data again, so a batch can be uploaded from scratch.

Development only. It runs only when the app was started with INTEGRATION_ALLOW_RESET=1 (run_demo.ps1 -AllowReset),
never on the public schema, never while a batch is running, and only when the caller repeats the schema name.

The schema is dropped and created again, then the same two steps as `run_demo.ps1 -Integration -Setup` run:
the team migrations (db/alembic.ini) and the reference data (load_seed.py --reference-only). Measures, rules, star
cut points and weights come back; patients, results, gaps, scores, documents, findings, decisions and their audit
events are gone. Encrypted document originals stay in the intake store folder, which other schemas share.
"""

import logging
import os
import shutil
import subprocess
import sys
import time

import psycopg
from psycopg import sql

from . import pipeline
from .config import REPO_ROOT, SETTINGS

log = logging.getLogger(__name__)

STEP_TIMEOUT_SECONDS = 300


class ResetRefused(ValueError):
    """The reset is not allowed in this app or in this state."""


def allowed() -> bool:
    return os.environ.get("INTEGRATION_ALLOW_RESET", "").strip() == "1"


def reset(conn: psycopg.Connection, confirm: str) -> dict:
    """Empty the current schema and reload reference data. Raises ResetRefused when not allowed."""
    if not allowed():
        raise ResetRefused("Start over is switched off in this app. Start it with -AllowReset to use it.")
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.rollback()
    if schema == "public":
        raise ResetRefused("Start over never runs on the public schema.")
    if (confirm or "").strip() != schema:
        raise ResetRefused(f"Type the schema name ({schema}) to confirm.")
    if not pipeline._ONE_AT_A_TIME.acquire(blocking=False):
        raise ResetRefused("A batch is running. Wait for it to finish, then start over.")
    started = time.perf_counter()
    try:
        conn.autocommit = True
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.autocommit = False
        _run("migrations", [sys.executable, "-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head"])
        _run("reference data", [sys.executable, "scripts/db/load_seed.py", "--reference-only"])
        shutil.rmtree(SETTINGS.work_dir / "uploads", ignore_errors=True)
    finally:
        pipeline._ONE_AT_A_TIME.release()
    seconds = round(time.perf_counter() - started, 1)
    log.info("schema %s reset in %ss", schema, seconds)
    return {"schema": schema, "status": "empty", "seconds": seconds}


def _run(title: str, cmd: list[str]) -> None:
    """One setup step in a child process with this app's environment (same DATABASE_URL and schema)."""
    done = subprocess.run(cmd, cwd=REPO_ROOT, env=os.environ.copy(), capture_output=True, text=True,
                          encoding="utf-8", timeout=STEP_TIMEOUT_SECONDS, check=False)
    if done.returncode != 0:
        first = (done.stderr.strip() or done.stdout.strip() or "no output").splitlines()[-1][:300]
        raise RuntimeError(f"Start over failed at {title}: {first}")

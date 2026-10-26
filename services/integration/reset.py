"""Start over: empty every data table of the app's schema and keep the reference data, so a batch can be uploaded
from scratch.

Development only. It runs only when the app was started with INTEGRATION_ALLOW_RESET=1 (run_demo.ps1 -AllowReset),
never while a batch is running, and only when the caller repeats the schema name. It works on whatever schema the
app uses, public included: the schema, its tables, views, triggers and migration version stay; only rows go.

Kept: REFERENCE_TABLES, and the source_batch rows they point to (the reference load's batch). Every other table is
emptied by one TRUNCATE ... RESTART IDENTITY CASCADE, after checking that the cascade cannot reach a kept table, in
one transaction with an audit event for the reset. Encrypted document originals stay in the intake store folder.
"""

import logging
import os
import shutil
import time

import psycopg
from psycopg import sql

from services.document_intake.db import audit

from . import pipeline
from .config import SETTINGS

log = logging.getLogger(__name__)

REFERENCE_TABLES = ("measure_definition", "measure_rule", "measure_star_weight", "star_cutpoint",
                    "organization_quality", "organization", "practitioner", "alembic_version")
LOCK_TIMEOUT = "30s"   # TRUNCATE waits for open readers; give up instead of hanging the request

_TABLES = """
SELECT c.relname FROM pg_class c
 WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind IN ('r', 'p') AND NOT c.relispartition
"""

# Foreign keys inside the schema: (from table, its column, to table, its column); multi-column keys give no column.
_FOREIGN_KEYS = """
SELECT src.relname, CASE WHEN cardinality(k.conkey) = 1 THEN a.attname END,
       dst.relname, CASE WHEN cardinality(k.confkey) = 1 THEN b.attname END
  FROM pg_constraint k
  JOIN pg_class src ON src.oid = k.conrelid
  JOIN pg_class dst ON dst.oid = k.confrelid
  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
  JOIN pg_attribute b ON b.attrelid = k.confrelid AND b.attnum = k.confkey[1]
 WHERE k.contype = 'f' AND src.relnamespace = current_schema()::regnamespace
   AND dst.relnamespace = current_schema()::regnamespace
"""


class ResetRefused(ValueError):
    """The reset is not allowed in this app or in this state."""


def allowed() -> bool:
    return os.environ.get("INTEGRATION_ALLOW_RESET", "").strip() == "1"


def reset(conn: psycopg.Connection, confirm: str) -> dict:
    """Empty the data tables of the current schema. Raises ResetRefused when not allowed."""
    if not allowed():
        raise ResetRefused("Start over is switched off in this app. Start it with -AllowReset to use it.")
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.rollback()
    if (confirm or "").strip() != schema:
        raise ResetRefused(f"Type the schema name ({schema}) to confirm.")
    if not pipeline._ONE_AT_A_TIME.acquire(blocking=False):
        raise ResetRefused("A batch is running. Wait for it to finish, then start over.")
    started = time.perf_counter()
    try:
        emptied = _empty_data_tables(conn)
        audit(conn, "schema", schema, "data.reset", {"tables_emptied": len(emptied),
                                                     "reference_tables_kept": len(REFERENCE_TABLES)},
              actor=SETTINGS.actor)
        conn.commit()
        shutil.rmtree(SETTINGS.work_dir / "uploads", ignore_errors=True)
    finally:
        pipeline._ONE_AT_A_TIME.release()
    seconds = round(time.perf_counter() - started, 1)
    log.info("schema %s: %d data tables emptied in %ss", schema, len(emptied), seconds)
    return {"schema": schema, "status": "empty", "tables_emptied": len(emptied), "seconds": seconds}


def _empty_data_tables(conn: psycopg.Connection) -> list[str]:
    """TRUNCATE every table but the reference tables, in the caller's transaction. Data tables a reference table
    points to (source_batch) are not truncated; their rows that no reference row uses are deleted instead."""
    tables = {r[0] for r in conn.execute(_TABLES).fetchall()}
    missing = set(REFERENCE_TABLES) - tables
    if missing:
        raise ResetRefused(f"This schema has no {', '.join(sorted(missing))}. Run run_demo.ps1 -Integration -Setup.")
    keys = conn.execute(_FOREIGN_KEYS).fetchall()
    pinned = {dst for src, _, dst, _ in keys if src in REFERENCE_TABLES and dst not in REFERENCE_TABLES}
    emptied = sorted(tables - set(REFERENCE_TABLES) - pinned)
    _refuse_if_cascade_reaches(keys, set(emptied), set(REFERENCE_TABLES) | pinned)
    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT)))
    conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in emptied)))
    for table in sorted(pinned):
        users = [(src, col, dst_col) for src, col, dst, dst_col in keys if dst == table and src in REFERENCE_TABLES]
        if any(col is None for _, col, _ in users):
            raise ResetRefused(f"{table} is referenced by a multi-column key; Start over cannot keep it consistent.")
        conn.execute(sql.SQL("DELETE FROM {} t WHERE NOT ({})").format(sql.Identifier(table), sql.SQL(" OR ").join(
            sql.SQL("t.{} IN (SELECT {} FROM {} WHERE {} IS NOT NULL)").format(
                sql.Identifier(dst_col), sql.Identifier(col), sql.Identifier(src), sql.Identifier(col))
            for src, col, dst_col in users)))
    return emptied + sorted(pinned)


def _refuse_if_cascade_reaches(keys: list[tuple], emptied: set[str], kept: set[str]) -> None:
    """TRUNCATE ... CASCADE also empties every table that references an emptied one; none of them may be kept."""
    reached, frontier = set(emptied), set(emptied)
    while frontier:
        frontier = {src for src, _, dst, _ in keys if dst in frontier and src not in reached}
        reached |= frontier
    if reached & kept:
        raise ResetRefused(f"Start over would also empty {', '.join(sorted(reached & kept))}; nothing was changed.")

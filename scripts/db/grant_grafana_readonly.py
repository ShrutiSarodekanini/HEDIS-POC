"""Grant the Grafana read-only login (hedis_grafana_ro) its columns in the schema named in DATABASE_URL.

    python scripts/db/grant_grafana_readonly.py

Runs after migrations (docker-compose.deploy.yml migrate, scripts/run_all.ps1) as the app's database user, which owns
the schema. The grants are the per-column ones in observability/grafana/readonly_role.sql, applied to this schema
instead of hedis, so the login still cannot read model answers, prompts, emails, password or token hashes. The login
itself is created once by a superuser with readonly_role.sql; without it this script prints a hint and exits 0.
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import psycopg
from dotenv import load_dotenv
from psycopg import sql

ROLE = "hedis_grafana_ro"
ROLE_SQL = Path(__file__).resolve().parents[2] / "observability" / "grafana" / "readonly_role.sql"
_GRANT = re.compile(r"GRANT SELECT \(([^)]*)\)\s+ON hedis\.(\w+) TO hedis_grafana_ro;", re.S)


def _schema(url: str) -> str | None:
    """The first schema in the URL's search_path option, if one is set."""
    match = re.search(r"search_path(?:%3D|=)([^&%\s,]+)", url)
    return unquote(match.group(1)) if match else None


def _column_grants() -> list[tuple[str, list[str]]]:
    """(table, columns) for every per-column grant in readonly_role.sql."""
    return [(table, [c.strip() for c in columns.split(",") if c.strip()])
            for columns, table in _GRANT.findall(ROLE_SQL.read_text(encoding="utf-8"))]


def main() -> None:
    load_dotenv(".env")
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, connect_timeout=8, autocommit=True) as conn:
        schema = _schema(url) or conn.execute("SELECT current_schema()").fetchone()[0]
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone():
            print(f"Grafana read-only login {ROLE} does not exist; run observability/grafana/readonly_role.sql as a "
                  "superuser first. Skipping its grants.")
            return
        granted = 0
        with conn.transaction():
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(ROLE)))
            for table, columns in _column_grants():
                if conn.execute("SELECT to_regclass(%s)", (f'"{schema}"."{table}"',)).fetchone()[0] is None:
                    continue
                conn.execute(sql.SQL("GRANT SELECT ({}) ON {}.{} TO {}").format(
                    sql.SQL(", ").join(sql.Identifier(c) for c in columns), sql.Identifier(schema),
                    sql.Identifier(table), sql.Identifier(ROLE)))
                granted += 1
        print(f"Grafana read-only login: USAGE on schema {schema} and its columns on {granted} tables.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - monitoring grants must never stop the app from starting
        print(f"Warning: Grafana read-only grants not applied ({type(exc).__name__}). The app starts anyway.",
              file=sys.stderr)

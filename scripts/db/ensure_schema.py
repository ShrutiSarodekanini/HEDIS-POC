"""Create the schema named in DATABASE_URL's search_path when it does not exist yet.

    python scripts/db/ensure_schema.py

Run before migrations: Alembic creates tables inside the schema but not the schema itself. A schema that exists but
belongs to another user is not changed; the script exits with the command a superuser runs to hand it over.
"""

import os
import re
import sys
from urllib.parse import unquote

import psycopg
from dotenv import load_dotenv
from psycopg import sql


def _schema(url: str) -> str | None:
    """The first schema in the URL's search_path option, if one is set."""
    match = re.search(r"search_path(?:%3D|=)([^&%\s,]+)", url)
    return unquote(match.group(1)) if match else None


def main() -> None:
    load_dotenv(".env")
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    schema = _schema(url)
    if not schema:
        print("No search_path in DATABASE_URL; using the database's default schema.")
        return
    with psycopg.connect(url, connect_timeout=8, autocommit=True) as conn:
        user = conn.execute("SELECT current_user").fetchone()[0]
        exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
        if not exists:
            if not conn.execute("SELECT has_database_privilege(current_database(), 'CREATE')").fetchone()[0]:
                sys.exit(f"Schema {schema} does not exist and {user} may not create schemas. As a superuser run:\n"
                         f"  CREATE SCHEMA {schema} AUTHORIZATION {user};")
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            print(f"Created schema {schema}. Run with -Setup to load the reference data into it.")
            return
        usage, create = conn.execute("SELECT has_schema_privilege(%s, 'USAGE'), has_schema_privilege(%s, 'CREATE')",
                                     (schema, schema)).fetchone()
        if not (usage and create):
            sys.exit(f"Schema {schema} exists but {user} cannot use it. As a superuser run:\n"
                     f"  ALTER SCHEMA {schema} OWNER TO {user};")
        print(f"Schema {schema} is ready.")


if __name__ == "__main__":
    main()

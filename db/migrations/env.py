"""Alembic environment: reads DATABASE_URL from the repo-root .env file."""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

# Add repo root to path so we can import services
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.data_ingestion.app.db import Base

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (alembic upgrade head --sql)."""
    context.configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"}, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(DATABASE_URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

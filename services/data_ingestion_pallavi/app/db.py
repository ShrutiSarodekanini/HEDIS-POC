from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import settings


# Configure connection pool for concurrent ingestion
# pool_size=20: Keep 20 connections open (ready for immediate use)
# max_overflow=10: Allow up to 30 total (20 + 10 overflow) connections
# pool_recycle=3600: Recycle connections after 1 hour (prevents idle connection timeout)
# pool_pre_ping=True: Test connection before using (prevents "connection lost" errors)
engine = create_engine(
    settings.database_url,
    future=True,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False, future=True)
SessionLocal.configure(bind=engine)
Base = declarative_base()


def set_database_url(database_url: str) -> None:
    global engine
    # Recreate engine with the same pool configuration as the main engine
    engine = create_engine(
        database_url,
        future=True,
        poolclass=QueuePool,
        pool_size=20,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
    )
    SessionLocal.configure(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

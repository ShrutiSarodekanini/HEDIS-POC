from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False, future=True)
SessionLocal.configure(bind=engine)
Base = declarative_base()


def set_database_url(database_url: str) -> None:
    global engine
    engine = create_engine(database_url, future=True)
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

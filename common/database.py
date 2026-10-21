from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is required. Configure a PostgreSQL connection before "
                "starting the application."
            )
        if self._engine is None:
            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
            )
        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                autoflush=False,
                expire_on_commit=False,
            )
        return self._session_factory

    def get_session(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def validate(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            migration_context = MigrationContext.configure(connection)
            current_heads = set(migration_context.get_current_heads())
            config = Config(
                str(Path(__file__).resolve().parents[1] / "alembic.ini")
            )
            expected_heads = set(ScriptDirectory.from_config(config).get_heads())
            if current_heads != expected_heads:
                raise RuntimeError(
                    "Database migrations are not current. Run: alembic upgrade head. "
                    f"Current revisions: {sorted(current_heads)}; "
                    f"expected: {sorted(expected_heads)}"
                )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

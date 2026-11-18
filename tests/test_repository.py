from __future__ import annotations

from pathlib import Path

import pytest

from common.config import AppConfig
from common.database import Database


def test_config_uses_postgresql_url_without_sqlite_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/hedis")
    monkeypatch.setenv("EVIDENCE_STORAGE_DIR", "test-evidence")

    config = AppConfig.from_env()

    assert config.database_url == "postgresql://user:password@localhost/hedis"
    assert config.evidence_storage_dir == Path("test-evidence")
    assert not hasattr(config, "db_path")


def test_database_requires_database_url():
    database = Database(None)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        database.validate()

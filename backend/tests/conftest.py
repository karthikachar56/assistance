"""Shared pytest fixtures creating an isolated encrypted SQLite database per test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import AppContainer, build_container, set_container
from app.main import create_app


@pytest.fixture()
def isolated_container(tmp_path) -> AppContainer:
    db_file = tmp_path / "test_khata.db"
    settings = Settings(
        gemini_api_key="",
        gnani_api_key="",
        ledger_encryption_key="test-secret-key-for-aes-256-gcm-unit-tests",
        database_path=str(db_file),
        mongodb_url="",
    )
    container = build_container(settings=settings, db_path=str(db_file))
    set_container(container)
    yield container
    set_container(None)


@pytest.fixture()
def api_client(isolated_container: AppContainer) -> TestClient:
    app = create_app()
    with TestClient(app) as client:
        yield client


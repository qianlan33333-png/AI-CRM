from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from aicrm_next.main import create_app
from aicrm_next.platform.shared.postgres_test_guard import validate_postgres_test_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_client() -> TestClient:
    return TestClient(create_app())


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres_integration: explicit PostgreSQL integration tests; skipped unless AICRM_NEXT_TEST_DATABASE_URL is set",
    )


@pytest.fixture(scope="session")
def safe_postgres_database_url() -> str:
    postgres_url = os.getenv("AICRM_NEXT_TEST_DATABASE_URL", "").strip()
    if not postgres_url:
        pytest.skip("AICRM_NEXT_TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests.")
    return validate_postgres_test_database_url(postgres_url).raw_url


@pytest.fixture()
def alembic_config(safe_postgres_database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", safe_postgres_database_url)
    return config


@pytest.fixture()
def migrated_postgres_engine(alembic_config: Config, safe_postgres_database_url: str) -> Engine:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    engine = create_engine(safe_postgres_database_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(alembic_config, "base")

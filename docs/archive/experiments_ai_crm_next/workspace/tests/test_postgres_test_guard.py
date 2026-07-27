from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.platform.shared.postgres_test_guard import (
    UnsafePostgresTestDatabaseError,
    redact_database_url,
    validate_postgres_test_database_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_guard_accepts_localhost_test_database_url() -> None:
    safe = validate_postgres_test_database_url(
        "postgresql+psycopg://user:pass@127.0.0.1:5432/aicrm_next_test"
    )
    assert safe.host == "127.0.0.1"
    assert safe.database == "aicrm_next_test"


def test_postgres_guard_rejects_empty_url() -> None:
    with pytest.raises(UnsafePostgresTestDatabaseError, match="AICRM_NEXT_TEST_DATABASE_URL"):
        validate_postgres_test_database_url("")


def test_postgres_guard_rejects_non_test_database_name() -> None:
    with pytest.raises(UnsafePostgresTestDatabaseError, match="without a test marker"):
        validate_postgres_test_database_url("postgresql+psycopg://user:pass@localhost:5432/aicrm_next")


def test_postgres_guard_rejects_non_local_host() -> None:
    with pytest.raises(UnsafePostgresTestDatabaseError, match="non-local host"):
        validate_postgres_test_database_url("postgresql+psycopg://user:pass@db.example.com:5432/aicrm_next_test")


def test_postgres_guard_redacts_password() -> None:
    redacted = redact_database_url("postgresql+psycopg://user:secret@localhost:5432/aicrm_next_test")
    assert "secret" not in redacted
    assert "***" in redacted


def test_postgres_integration_tests_are_marked_and_skip_without_env() -> None:
    for path in (PROJECT_ROOT / "tests" / "integration").glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        assert "pytest.mark.postgres_integration" in text
    conftest_text = (PROJECT_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "AICRM_NEXT_TEST_DATABASE_URL is not set" in conftest_text

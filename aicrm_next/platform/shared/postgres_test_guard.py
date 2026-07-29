from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import make_url


class UnsafePostgresTestDatabaseError(ValueError):
    pass


@dataclass(frozen=True)
class SafePostgresTestDatabase:
    raw_url: str
    redacted_url: str
    host: str
    database: str


LOCAL_TEST_HOSTS = {"localhost", "127.0.0.1", "::1"}
TEST_NAME_TOKENS = ("test", "aicrm_next_test")


def validate_postgres_test_database_url(raw_url: str | None) -> SafePostgresTestDatabase:
    url_text = str(raw_url or "").strip()
    if not url_text:
        raise UnsafePostgresTestDatabaseError("AICRM_NEXT_TEST_DATABASE_URL is required for PostgreSQL integration tests.")

    url = make_url(url_text)
    if not url.drivername.startswith("postgresql"):
        raise UnsafePostgresTestDatabaseError("PostgreSQL integration tests require a postgresql+psycopg URL.")

    host = str(url.host or "")
    database = str(url.database or "")
    if host not in LOCAL_TEST_HOSTS:
        raise UnsafePostgresTestDatabaseError(
            f"Refusing PostgreSQL integration test database on non-local host: {host or '<missing>'}."
        )
    if not any(token in database.lower() for token in TEST_NAME_TOKENS):
        raise UnsafePostgresTestDatabaseError(
            f"Refusing PostgreSQL integration test database without a test marker in the database name: {database or '<missing>'}."
        )

    return SafePostgresTestDatabase(
        raw_url=url_text,
        redacted_url=redact_database_url(url_text),
        host=host,
        database=database,
    )


def redact_database_url(raw_url: str | None) -> str:
    url_text = str(raw_url or "").strip()
    if not url_text:
        return ""
    try:
        return make_url(url_text).render_as_string(hide_password=True)
    except Exception:
        return "<invalid database url>"

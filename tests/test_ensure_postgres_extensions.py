from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest

from scripts.ops.ensure_postgres_extensions import (
    PostgresExtensionProvisionRefused,
    _redact_sensitive_text,
    ensure_pg_trgm,
)


DATABASE_URL = "postgresql://app:secret@127.0.0.1:5432/siyuan"


class _Result:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...]:
        return self._row


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = iter(rows)

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, parameters: tuple[object, ...]) -> _Result:
        assert "host(inet_server_addr())" in query
        assert parameters == ("pg_trgm",)
        return _Result(next(self._rows))


def _connect_with(rows: list[tuple[object, ...]]):
    return patch(
        "scripts.ops.ensure_postgres_extensions.psycopg.connect",
        return_value=_Connection(rows),
    )


def test_existing_extension_needs_no_privileged_command() -> None:
    runner = Mock()
    with _connect_with([("siyuan", 5432, "127.0.0.1", True)]) as connect:
        result = ensure_pg_trgm(DATABASE_URL, runner=runner)

    connect.assert_called_once_with(DATABASE_URL, autocommit=True)
    runner.assert_not_called()
    assert result.already_installed is True
    assert result.installed is False


def test_existing_remote_extension_needs_no_privileged_command() -> None:
    runner = Mock()
    with _connect_with([("siyuan", 5432, "10.0.0.8", True)]):
        result = ensure_pg_trgm(DATABASE_URL, runner=runner)

    runner.assert_not_called()
    assert result.server_address == "10.0.0.8"
    assert result.already_installed is True


def test_missing_local_extension_is_installed_and_verified() -> None:
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    with _connect_with(
        [
            ("siyuan", 5432, "127.0.0.1", False),
            ("siyuan", 5432, "127.0.0.1", True),
        ]
    ):
        result = ensure_pg_trgm(DATABASE_URL, runner=runner)

    assert result.already_installed is False
    assert result.installed is True
    runner.assert_called_once_with(
        [
            "sudo",
            "--non-interactive",
            "-u",
            "postgres",
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--port",
            "5432",
            "--dbname",
            "siyuan",
            "--command",
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("server_address", ["10.0.0.8", "203.0.113.10"])
def test_missing_remote_extension_fails_closed(server_address: str) -> None:
    runner = Mock()
    with _connect_with([("siyuan", 5432, server_address, False)]):
        with pytest.raises(PostgresExtensionProvisionRefused, match="not local"):
            ensure_pg_trgm(DATABASE_URL, runner=runner)

    runner.assert_not_called()


@pytest.mark.parametrize(
    ("database", "port", "message"),
    [
        ("-unsafe", 5432, "database name is unsafe"),
        ("host=elsewhere", 5432, "database name is unsafe"),
        ("siyuan", 70000, "port is outside"),
    ],
)
def test_unsafe_administrator_target_fails_closed(database: str, port: int, message: str) -> None:
    runner = Mock()
    with _connect_with([(database, port, "127.0.0.1", False)]):
        with pytest.raises(PostgresExtensionProvisionRefused, match=message):
            ensure_pg_trgm(DATABASE_URL, runner=runner)

    runner.assert_not_called()


def test_install_must_be_visible_to_the_application_connection() -> None:
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    with _connect_with(
        [
            ("siyuan", 5432, "127.0.0.1", False),
            ("siyuan", 5432, "127.0.0.1", False),
        ]
    ):
        with pytest.raises(RuntimeError, match="still unavailable"):
            ensure_pg_trgm(DATABASE_URL, runner=runner)


def test_privileged_command_failure_does_not_expose_command_output() -> None:
    runner = Mock(
        side_effect=subprocess.CalledProcessError(
            returncode=1,
            cmd=["sudo", "psql"],
            stderr="sensitive database output",
        )
    )
    with _connect_with([("siyuan", 5432, "127.0.0.1", False)]):
        with pytest.raises(RuntimeError, match="exit code 1") as error:
            ensure_pg_trgm(DATABASE_URL, runner=runner)

    assert "sensitive" not in str(error.value)


def test_error_redaction_removes_url_and_password() -> None:
    message = f"connection failed for {DATABASE_URL}; password=secret"

    redacted = _redact_sensitive_text(message, DATABASE_URL)

    assert DATABASE_URL not in redacted
    assert "secret" not in redacted

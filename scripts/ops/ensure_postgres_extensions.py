from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit

import psycopg


EXTENSION_NAME = "pg_trgm"
SAFE_DATABASE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")


class PostgresExtensionProvisionRefused(RuntimeError):
    """Raised when the deploy host cannot safely provision the required extension."""


@dataclass(frozen=True)
class PostgresTarget:
    database: str
    port: int
    server_address: str
    extension_installed: bool


@dataclass(frozen=True)
class PostgresExtensionProvisionResult:
    extension: str
    database: str
    port: int
    server_address: str
    already_installed: bool
    installed: bool


Runner = Callable[..., subprocess.CompletedProcess[str]]


def ensure_pg_trgm(
    database_url: str,
    *,
    runner: Runner | None = None,
) -> PostgresExtensionProvisionResult:
    psycopg_url = _psycopg_url(database_url)
    command_runner = runner or subprocess.run

    with psycopg.connect(psycopg_url, autocommit=True) as connection:
        target = _inspect_target(connection)
        _validate_target(target)
        if target.extension_installed:
            return _result(target, already_installed=True, installed=False)

        command = _admin_install_command(target)
        try:
            command_runner(
                command,
                check=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"PostgreSQL administrator failed to install {EXTENSION_NAME} "
                f"(exit code {exc.returncode})"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"could not execute PostgreSQL administrator command: {exc}") from exc

        verified = _inspect_target(connection)
        if (
            verified.database != target.database
            or verified.port != target.port
            or verified.server_address != target.server_address
        ):
            raise RuntimeError("PostgreSQL target changed while provisioning the required extension")
        if not verified.extension_installed:
            raise RuntimeError(f"{EXTENSION_NAME} is still unavailable after administrator provisioning")
        return _result(verified, already_installed=False, installed=True)


def _inspect_target(connection: psycopg.Connection[object]) -> PostgresTarget:
    row = connection.execute(
        """
        SELECT current_database(),
               current_setting('port')::integer,
               COALESCE(host(inet_server_addr()), ''),
               EXISTS (SELECT 1 FROM pg_extension WHERE extname = %s)
        """,
        (EXTENSION_NAME,),
    ).fetchone()
    if not row or len(row) != 4:
        raise RuntimeError("could not inspect the PostgreSQL deployment target")
    return PostgresTarget(
        database=str(row[0] or ""),
        port=int(row[1]),
        server_address=str(row[2] or ""),
        extension_installed=bool(row[3]),
    )


def _validate_target(target: PostgresTarget) -> None:
    database = target.database
    if not SAFE_DATABASE_NAME.fullmatch(database):
        raise PostgresExtensionProvisionRefused("PostgreSQL database name is unsafe for administrator tooling")
    if not 1 <= target.port <= 65535:
        raise PostgresExtensionProvisionRefused("PostgreSQL server port is outside the valid range")
    if target.server_address:
        try:
            server_ip = ipaddress.ip_address(target.server_address)
        except ValueError as exc:
            raise PostgresExtensionProvisionRefused(
                "PostgreSQL server address is not a valid IP address"
            ) from exc
        if not server_ip.is_loopback and not target.extension_installed:
            raise PostgresExtensionProvisionRefused(
                "required extension is absent and PostgreSQL is not local to the deploy host"
            )


def _admin_install_command(target: PostgresTarget) -> list[str]:
    return [
        "sudo",
        "--non-interactive",
        "-u",
        "postgres",
        "psql",
        "--no-psqlrc",
        "--set",
        "ON_ERROR_STOP=1",
        "--port",
        str(target.port),
        "--dbname",
        target.database,
        "--command",
        f"CREATE EXTENSION IF NOT EXISTS {EXTENSION_NAME}",
    ]


def _result(
    target: PostgresTarget,
    *,
    already_installed: bool,
    installed: bool,
) -> PostgresExtensionProvisionResult:
    return PostgresExtensionProvisionResult(
        extension=EXTENSION_NAME,
        database=target.database,
        port=target.port,
        server_address=target.server_address or "local_socket",
        already_installed=already_installed,
        installed=installed,
    )


def _psycopg_url(database_url: str) -> str:
    value = str(database_url or "").strip()
    if not value:
        raise ValueError("DATABASE_URL is required")
    if value.startswith("postgresql+psycopg://"):
        value = "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if not value.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must use PostgreSQL")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ensure production-local PostgreSQL has the pg_trgm extension before migrations."
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = ensure_pg_trgm(args.database_url)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": _redact_sensitive_text(str(exc), args.database_url),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"ok": True, **asdict(result)}, ensure_ascii=False, sort_keys=True))
    return 0


def _redact_sensitive_text(message: str, database_url: str) -> str:
    raw_url = str(database_url or "")
    redacted = message.replace(raw_url, "[database-url-redacted]") if raw_url else message
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return redacted
    if parsed.password:
        redacted = redacted.replace(parsed.password, "***")
    return redacted


if __name__ == "__main__":
    sys.exit(main())

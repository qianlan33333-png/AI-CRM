#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.admin_auth.capabilities import ALL_CAPABILITIES  # noqa: E402
from aicrm_next.platform.admin_auth.service import SESSION_COOKIE  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.credentials import CredentialHasher  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.models import SessionSubject  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.sessions import AuthSessionService  # noqa: E402
from aicrm_next.platform.shared.db_session import get_session_factory  # noqa: E402
from aicrm_next.platform.shared.runtime_settings import runtime_setting  # noqa: E402


DEFAULT_TTL_SECONDS = 300
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 900


def _service(*, database_url: str, pepper: str) -> AuthSessionService:
    return AuthSessionService(
        PostgresAuthRepository(database_url=database_url or None),
        CredentialHasher(pepper),
    )


def _eligible_super_admin(*, database_url: str) -> dict[str, Any]:
    session_factory = get_session_factory(database_url or None)
    with session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                    SELECT u.id, u.wecom_corpid, u.session_version
                    FROM admin_users u
                    WHERE u.is_active = TRUE
                      AND u.login_enabled = TRUE
                      AND (
                        u.admin_level = 'super_admin'
                        OR EXISTS (
                          SELECT 1
                          FROM admin_user_roles r
                          WHERE r.admin_user_id = u.id
                            AND r.role_code = 'super_admin'
                        )
                      )
                    ORDER BY u.id
                    LIMIT 1
                    """
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise RuntimeError("eligible_super_admin_missing")
    return dict(row)


def _shorten_session_expiry(*, database_url: str, session_id: str, expires_at: datetime) -> None:
    session_factory = get_session_factory(database_url or None)
    with session_factory.begin() as session:
        result = session.execute(
            text(
                """
                UPDATE auth_sessions
                SET expires_at = :expires_at
                WHERE session_id = :session_id
                  AND revoked_at IS NULL
                """
            ),
            {"session_id": session_id, "expires_at": expires_at},
        )
        if result.rowcount != 1:
            raise RuntimeError("deploy_smoke_session_expiry_update_failed")


def _write_private_cookie_file(path: Path, cookie_header: str) -> None:
    target = Path(path)
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        fd = os.open(target, flags | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("cookie file must be a regular file")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("cookie file must be owned by the current user with mode 0600")
        encoded = cookie_header.encode("utf-8")
        if os.write(fd, encoded) != len(encoded):
            raise OSError("cookie file write was incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_session_cookie(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(Path(path), flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("cookie file must be a regular file")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("cookie file must be owned by the current user with mode 0600")
        raw_header = os.read(fd, 4097)
        if len(raw_header) > 4096:
            raise ValueError("cookie file is invalid")
        header = raw_header.decode("utf-8")
    finally:
        os.close(fd)
    prefix = f"{SESSION_COOKIE}="
    if "\n" in header or "\r" in header or ";" in header or not header.startswith(prefix):
        raise ValueError("cookie file is invalid")
    session_cookie = header.removeprefix(prefix).strip()
    if not session_cookie.startswith("ss_"):
        raise ValueError("cookie file is invalid")
    return session_cookie


def issue_deploy_smoke_session(
    *,
    database_url: str,
    pepper: str,
    output_file: Path,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    ttl = int(ttl_seconds)
    if ttl < MIN_TTL_SECONDS or ttl > MAX_TTL_SECONDS:
        raise ValueError("deploy smoke session TTL is outside the allowed range")
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    admin = _eligible_super_admin(database_url=database_url)
    service = _service(database_url=database_url, pepper=pepper)
    issued = service.issue(
        subject=SessionSubject(
            principal_id=f"admin-user:{int(admin['id'])}",
            admin_user_id=str(int(admin["id"])),
            corp_id=str(admin.get("wecom_corpid") or ""),
        ),
        session_version=int(admin["session_version"]),
        scopes=("admin.read", "admin.write"),
        capabilities=tuple(sorted(ALL_CAPABILITIES)),
        owner_scope={"deployment_smoke": True},
        now=issued_at,
    )
    short_expiry = issued_at + timedelta(seconds=ttl)
    try:
        _shorten_session_expiry(
            database_url=database_url,
            session_id=issued.session_id,
            expires_at=short_expiry,
        )
        introspection = service.introspect(issued.session_cookie, now=issued_at)
        if not introspection.active or introspection.record is None:
            raise RuntimeError("deploy_smoke_session_introspection_failed")
        if introspection.record.expires_at > short_expiry:
            raise RuntimeError("deploy_smoke_session_ttl_not_enforced")
        _write_private_cookie_file(output_file, f"{SESSION_COOKIE}={issued.session_cookie}")
    except Exception:
        service.revoke(issued.session_cookie, reason="deploy_smoke_issue_failed")
        Path(output_file).unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "action": "issue",
        "session_id": issued.session_id,
        "expires_at": short_expiry.isoformat(),
        "ttl_seconds": ttl,
        "credential_printed": False,
    }


def revoke_deploy_smoke_session(*, database_url: str, pepper: str, cookie_file: Path) -> dict[str, Any]:
    session_cookie = _read_session_cookie(cookie_file)
    service = _service(database_url=database_url, pepper=pepper)
    if not service.revoke(session_cookie, reason="deploy_smoke_complete"):
        raise RuntimeError("deploy_smoke_session_revoke_failed")
    Path(cookie_file).unlink()
    return {
        "ok": True,
        "action": "revoke",
        "revoked": True,
        "credential_printed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue or revoke a short-lived deploy smoke admin session.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    subcommands = parser.add_subparsers(dest="action", required=True)
    issue = subcommands.add_parser("issue")
    issue.add_argument("--output-file", type=Path, required=True)
    issue.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    revoke = subcommands.add_parser("revoke")
    revoke.add_argument("--cookie-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        database_url = str(args.database_url or "").strip()
        if not database_url:
            raise ValueError("DATABASE_URL is required")
        pepper = runtime_setting("AICRM_AUTH_SESSION_HASH_PEPPER")
        if not pepper:
            raise ValueError("session pepper is required")
        if args.action == "issue":
            report = issue_deploy_smoke_session(
                database_url=database_url,
                pepper=pepper,
                output_file=args.output_file,
                ttl_seconds=args.ttl_seconds,
            )
        else:
            report = revoke_deploy_smoke_session(
                database_url=database_url,
                pepper=pepper,
                cookie_file=args.cookie_file,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "credential_printed": False}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

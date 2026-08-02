#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.platform_foundation.auth_platform.credentials import (  # noqa: E402
    hash_client_secret,
    issue_client_secret,
    verify_client_secret,
)
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.profiles import (  # noqa: E402
    API_CLIENT_PROFILE_BY_PURPOSE,
    API_CLIENT_PROFILES,
)
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository  # noqa: E402
from aicrm_next.platform.shared.secret_store import FileSecretStore, is_secret_reference  # noqa: E402
from scripts.ops.bootstrap_auth_clients import _read_environment_file  # noqa: E402
from scripts.ops.migrate_app_setting_secrets import _persist_environment_values  # noqa: E402


class AuthClientRepository(Protocol):
    def api_client(self, client_id: str) -> ApiClientRecord | None: ...

    def rotate_api_client_secret(
        self,
        client_id: str,
        secret_hash: str,
        credential_hint: str | None = None,
    ) -> int | None: ...

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None: ...


def client_status(*, repository: AuthClientRepository, environment: Mapping[str, str]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for profile in API_CLIENT_PROFILES:
        client_id = str(environment.get(profile.client_id_setting) or profile.client_id).strip()
        current = repository.api_client(client_id)
        items.append(
            {
                "purpose": profile.purpose,
                "client_id": client_id,
                "registered": current is not None,
                "enabled": bool(current.enabled) if current else False,
                "auth_version": int(current.auth_version) if current else 0,
                "audiences": list(current.audiences) if current else list(profile.audiences),
                "scopes": list(current.scopes) if current else list(profile.scopes),
            }
        )
    return {"ok": True, "action": "status", "items": items, "secrets_printed": False}


def set_client_enabled(
    *,
    repository: AuthClientRepository,
    environment: Mapping[str, str],
    purpose: str,
    enabled: bool,
) -> dict[str, Any]:
    profile = _profile(purpose)
    client_id = str(environment.get(profile.client_id_setting) or profile.client_id).strip()
    if repository.api_client(client_id) is None:
        raise ValueError("client_not_found")
    auth_version = repository.set_api_client_enabled(client_id, enabled)
    if auth_version is None:
        raise ValueError("client_not_found")
    return {
        "ok": True,
        "action": "enable" if enabled else "disable",
        "purpose": profile.purpose,
        "client_id": client_id,
        "enabled": enabled,
        "auth_version": auth_version,
        "secrets_printed": False,
    }


def rotate_client_secret(
    *,
    repository: AuthClientRepository,
    store: FileSecretStore,
    environment: Mapping[str, str],
    environment_file: Path,
    purpose: str,
) -> dict[str, Any]:
    profile = _profile(purpose)
    client_id = str(environment.get(profile.client_id_setting) or profile.client_id).strip()
    if repository.api_client(client_id) is None:
        raise ValueError("client_not_found")
    setting = profile.client_secret_reference_setting
    current_reference = str(environment.get(setting) or "").strip()
    secret = issue_client_secret()
    reference = store.write(
        setting.removesuffix("_REF"),
        secret,
        current_reference=current_reference if is_secret_reference(current_reference) else "",
    )
    auth_version = repository.rotate_api_client_secret(client_id, hash_client_secret(secret))
    if auth_version is None:
        raise ValueError("client_not_found")
    _persist_environment_values(environment_file, {setting: reference})
    current = repository.api_client(client_id)
    if current is None or not verify_client_secret(store.read(reference), current.secret_hash):
        raise RuntimeError("rotated_secret_verification_failed")
    return {
        "ok": True,
        "action": "rotate",
        "purpose": profile.purpose,
        "client_id": client_id,
        "auth_version": auth_version,
        "secret_reference_updated": True,
        "secrets_printed": False,
    }


def _profile(purpose: str):
    profile = API_CLIENT_PROFILE_BY_PURPOSE.get(str(purpose or "").strip())
    if profile is None:
        raise ValueError("unknown_client_purpose")
    return profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect, revoke, enable, or rotate registered AI-CRM API clients.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--secret-store-dir", default=os.getenv("AICRM_SECRET_STORE_DIR", ""))
    parser.add_argument("--environment-file", type=Path)
    subcommands = parser.add_subparsers(dest="action", required=True)
    subcommands.add_parser("status")
    for action in ("enable", "disable", "rotate"):
        command = subcommands.add_parser(action)
        command.add_argument("--purpose", required=True, choices=sorted(API_CLIENT_PROFILE_BY_PURPOSE))
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        file_environment = _read_environment_file(args.environment_file)
        environment = {**os.environ, **file_environment}
        repository = PostgresAuthRepository(database_url=args.database_url or None)
        if args.action == "status":
            report = client_status(repository=repository, environment=environment)
        elif args.action == "rotate":
            if args.environment_file is None:
                raise ValueError("--environment-file is required for rotate")
            report = rotate_client_secret(
                repository=repository,
                store=FileSecretStore(args.secret_store_dir),
                environment=environment,
                environment_file=args.environment_file,
                purpose=args.purpose,
            )
        else:
            report = set_client_enabled(
                repository=repository,
                environment=environment,
                purpose=args.purpose,
                enabled=args.action == "enable",
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

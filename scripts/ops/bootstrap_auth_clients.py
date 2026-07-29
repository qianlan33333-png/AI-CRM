#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import shlex
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.platform_foundation.auth_platform.credentials import (  # noqa: E402
    hash_client_secret,
    issue_client_secret,
    verify_client_secret,
)
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord, WebhookClientRecord  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.profiles import (  # noqa: E402
    API_CLIENT_PROFILES,
    WEBHOOK_CLIENT_PROFILES,
)
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthServiceConfig  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.webhook_hmac import issue_webhook_secret  # noqa: E402
from aicrm_next.platform.shared.secret_store import FileSecretStore, SecretStoreError, is_secret_reference  # noqa: E402
from scripts.ops.migrate_app_setting_secrets import _persist_environment_values  # noqa: E402


JWT_KEY_SETTING = "AICRM_AUTH_JWT_SIGNING_KEY"
SESSION_PEPPER_SETTING = "AICRM_AUTH_SESSION_HASH_PEPPER"
OUTBOUND_WEBHOOK_CLIENT_ID_SETTING = "AICRM_AUTH_OUTBOUND_WEBHOOK_CLIENT_ID"


def _read_environment_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        source = line.strip()
        if not source or source.startswith("#") or "=" not in source:
            continue
        key, raw_value = source.removeprefix("export ").split("=", 1)
        key = key.strip()
        try:
            parsed = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            continue
        if key and len(parsed) <= 1:
            values[key] = parsed[0] if parsed else ""
    return values


def _resolves(store: FileSecretStore, reference: str, *, minimum_bytes: int = 1) -> bool:
    try:
        return bool(is_secret_reference(reference) and len(store.read(reference).encode("utf-8")) >= minimum_bytes)
    except (SecretStoreError, OSError, UnicodeError):
        return False


def _ensure_server_secret(
    *,
    setting: str,
    environment: Mapping[str, str],
    store: FileSecretStore,
    apply: bool,
) -> tuple[str, str]:
    current = str(environment.get(setting) or "").strip()
    if _resolves(store, current, minimum_bytes=32):
        return current, "unchanged"
    if not apply:
        return "", "create"
    reference = store.write(setting, secrets.token_urlsafe(64))
    return reference, "created"


def _api_record(profile, *, client_id: str, corp_id: str, secret_hash: str) -> ApiClientRecord:
    return ApiClientRecord(
        client_id=client_id,
        principal_id=profile.principal_id,
        principal_type=profile.principal_type,
        purpose=profile.purpose,
        display_name=profile.display_name,
        secret_hash=secret_hash,
        audiences=profile.audiences,
        scopes=profile.scopes,
        capabilities=profile.capabilities,
        allowed_cidrs=profile.allowed_cidrs,
        corp_id=corp_id,
        owner_scope={},
        auth_version=1,
        token_ttl_seconds=profile.token_ttl_seconds,
        enabled=True,
    )


def bootstrap_auth_clients(
    *,
    repository: PostgresAuthRepository,
    store: FileSecretStore,
    environment: Mapping[str, str],
    issuer: str,
    apply: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    normalized_issuer = str(issuer or "").strip().rstrip("/")
    if not normalized_issuer:
        raise ValueError("AICRM auth issuer is required")
    corp_id = str(environment.get("WECOM_CORP_ID") or "").strip()
    environment_updates: dict[str, str] = {"AICRM_AUTH_ISSUER": normalized_issuer}
    items: list[dict[str, Any]] = []

    jwt_reference, jwt_action = _ensure_server_secret(
        setting=JWT_KEY_SETTING,
        environment=environment,
        store=store,
        apply=apply,
    )
    session_reference, session_action = _ensure_server_secret(
        setting=SESSION_PEPPER_SETTING,
        environment=environment,
        store=store,
        apply=apply,
    )
    if jwt_reference:
        environment_updates[JWT_KEY_SETTING] = jwt_reference
    if session_reference:
        environment_updates[SESSION_PEPPER_SETTING] = session_reference
    items.extend(
        [
            {"kind": "server_secret", "name": JWT_KEY_SETTING, "action": jwt_action},
            {"kind": "server_secret", "name": SESSION_PEPPER_SETTING, "action": session_action},
        ]
    )

    signing_key = store.read(jwt_reference) if jwt_reference else "dry-run-signing-key-contains-at-least-32-bytes"
    service = ApiClientService(
        repository,
        AuthServiceConfig(issuer=normalized_issuer, signing_key=signing_key),
    )
    for profile in API_CLIENT_PROFILES:
        client_id = str(environment.get(profile.client_id_setting) or profile.client_id).strip()
        secret_reference = str(environment.get(profile.client_secret_reference_setting) or "").strip()
        current = repository.api_client(client_id)
        action = "unchanged"
        if current is None:
            action = "create"
            if apply:
                client_secret = issue_client_secret()
                secret_reference = store.write(
                    profile.client_secret_reference_setting.removesuffix("_REF"),
                    client_secret,
                )
                repository.insert_api_client(
                    _api_record(
                        profile,
                        client_id=client_id,
                        corp_id=corp_id,
                        secret_hash=hash_client_secret(client_secret),
                    )
                )
                action = "created"
        else:
            desired = replace(
                current,
                principal_id=profile.principal_id,
                principal_type=profile.principal_type,
                purpose=profile.purpose,
                display_name=profile.display_name,
                audiences=profile.audiences,
                scopes=profile.scopes,
                capabilities=profile.capabilities,
                allowed_cidrs=profile.allowed_cidrs,
                corp_id=corp_id,
                owner_scope={},
                token_ttl_seconds=profile.token_ttl_seconds,
                enabled=True,
            )
            definition_drift = desired != current
            secret_matches = False
            if _resolves(store, secret_reference):
                secret_matches = verify_client_secret(store.read(secret_reference), current.secret_hash)
            if not secret_matches:
                action = "rotate_secret"
                if apply:
                    client_secret = issue_client_secret()
                    secret_reference = store.write(
                        profile.client_secret_reference_setting.removesuffix("_REF"),
                        client_secret,
                    )
                    repository.rotate_api_client_secret(client_id, hash_client_secret(client_secret))
                    action = "rotated_secret"
            if definition_drift:
                if apply:
                    service.reconcile_client(desired)
                action = f"{action}+reconciled" if action != "unchanged" else ("reconciled" if apply else "reconcile")
        environment_updates[profile.client_id_setting] = client_id
        if secret_reference:
            environment_updates[profile.client_secret_reference_setting] = secret_reference
        items.append(
            {
                "kind": "api_client",
                "purpose": profile.purpose,
                "client_id": client_id,
                "action": action,
                "secret_reference_present": bool(secret_reference),
            }
        )

    for profile in WEBHOOK_CLIENT_PROFILES:
        current = repository.webhook_client(profile.client_id)
        secret_reference = current.secret_reference if current else ""
        secret_valid = _resolves(store, secret_reference, minimum_bytes=32)
        desired = WebhookClientRecord(
            client_id=profile.client_id,
            principal_id=profile.principal_id,
            display_name=profile.display_name,
            secret_reference=secret_reference,
            capabilities=profile.capabilities,
            allowed_cidrs=profile.allowed_cidrs,
            corp_id=corp_id,
            owner_scope={},
            auth_version=current.auth_version if current else 1,
            enabled=True,
        )
        drift = current is None or replace(desired, secret_reference=current.secret_reference) != current
        action = "unchanged"
        if current is None or not secret_valid:
            action = "create" if current is None else "rotate_secret"
            if apply:
                secret_reference = store.write(profile.secret_store_key, issue_webhook_secret())
                desired = replace(desired, secret_reference=secret_reference)
                repository.upsert_webhook_client(desired)
                action = "created" if current is None else "rotated_secret"
        elif drift:
            action = "reconcile"
            if apply:
                repository.upsert_webhook_client(desired)
                action = "reconciled"
        items.append(
            {
                "kind": "webhook_client",
                "purpose": profile.purpose,
                "client_id": profile.client_id,
                "action": action,
                "secret_reference_present": bool(secret_reference),
            }
        )

    outbound = next(profile for profile in WEBHOOK_CLIENT_PROFILES if profile.purpose == "outbound_webhook")
    environment_updates[OUTBOUND_WEBHOOK_CLIENT_ID_SETTING] = outbound.client_id
    report = {
        "ok": True,
        "mode": "apply" if apply else "dry_run",
        "api_client_count": len(API_CLIENT_PROFILES),
        "webhook_client_count": len(WEBHOOK_CLIENT_PROFILES),
        "changed_count": sum(1 for item in items if item["action"] != "unchanged"),
        "items": items,
        "secrets_printed": False,
    }
    return report, environment_updates


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap AI-CRM API and webhook client registries without printing secrets.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--secret-store-dir", default=os.getenv("AICRM_SECRET_STORE_DIR", ""))
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--issuer", default=os.getenv("AICRM_AUTH_ISSUER", ""))
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.apply and args.environment_file is None:
            raise ValueError("--environment-file is required with --apply")
        file_environment = _read_environment_file(args.environment_file)
        environment = {**os.environ, **file_environment}
        store = FileSecretStore(args.secret_store_dir)
        report, updates = bootstrap_auth_clients(
            repository=PostgresAuthRepository(database_url=args.database_url or None),
            store=store,
            environment=environment,
            issuer=args.issuer or environment.get("AICRM_AUTH_ISSUER", ""),
            apply=bool(args.apply),
        )
        if args.apply and args.environment_file is not None:
            updates.update(
                {
                    "AICRM_SECRET_STORE_DIR": str(Path(args.secret_store_dir).expanduser()),
                    "AICRM_SECRET_REFERENCE_CUTOVER": "true",
                }
            )
            _persist_environment_values(args.environment_file, updates)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

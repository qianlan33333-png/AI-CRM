#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.platform_foundation.auth_platform.credentials import verify_client_secret  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.profiles import (  # noqa: E402
    API_CLIENT_PROFILES,
    WEBHOOK_CLIENT_PROFILES,
)
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthServiceConfig  # noqa: E402
from aicrm_next.platform.platform_foundation.auth_platform.webhook_hmac import WebhookHmacSigner  # noqa: E402
from aicrm_next.platform.shared.route_ownership import load_route_manifest  # noqa: E402
from aicrm_next.platform.shared.secret_store import FileSecretStore  # noqa: E402
from scripts.ops.bootstrap_auth_clients import (  # noqa: E402
    JWT_KEY_SETTING,
    SESSION_PEPPER_SETTING,
    _read_environment_file,
    _resolves,
)


def check_auth_readiness(
    *,
    repository: PostgresAuthRepository,
    store: FileSecretStore,
    environment: dict[str, str],
    issuer: str,
    manifest_path: Path,
) -> dict[str, Any]:
    failures: list[dict[str, str]] = []

    def fail(kind: str, name: str, reason: str) -> None:
        failures.append({"kind": kind, "name": name, "reason": reason})

    jwt_reference = str(environment.get(JWT_KEY_SETTING) or "").strip()
    session_reference = str(environment.get(SESSION_PEPPER_SETTING) or "").strip()
    if not _resolves(store, jwt_reference, minimum_bytes=32):
        fail("server_secret", JWT_KEY_SETTING, "missing_or_unresolved")
    if not _resolves(store, session_reference, minimum_bytes=32):
        fail("server_secret", SESSION_PEPPER_SETTING, "missing_or_unresolved")
    signing_key = store.read(jwt_reference) if _resolves(store, jwt_reference, minimum_bytes=32) else "x" * 32
    normalized_issuer = str(issuer or environment.get("AICRM_AUTH_ISSUER") or "").strip().rstrip("/")
    if not normalized_issuer:
        fail("server_setting", "AICRM_AUTH_ISSUER", "missing")
        normalized_issuer = "https://invalid.local/oauth"
    service = ApiClientService(repository, AuthServiceConfig(issuer=normalized_issuer, signing_key=signing_key))

    for profile in API_CLIENT_PROFILES:
        client_id = str(environment.get(profile.client_id_setting) or profile.client_id).strip()
        current = repository.api_client(client_id)
        if current is None:
            fail("api_client", profile.purpose, "not_registered")
            continue
        expected = {
            "principal_id": profile.principal_id,
            "principal_type": profile.principal_type,
            "purpose": profile.purpose,
            "audiences": profile.audiences,
            "scopes": profile.scopes,
            "capabilities": profile.capabilities,
            "allowed_cidrs": profile.allowed_cidrs,
            "token_ttl_seconds": profile.token_ttl_seconds,
            "enabled": True,
        }
        if any(getattr(current, key) != value for key, value in expected.items()):
            fail("api_client", profile.purpose, "profile_drift")
        reference = str(environment.get(profile.client_secret_reference_setting) or "").strip()
        if not _resolves(store, reference):
            fail("api_client", profile.purpose, "secret_reference_unresolved")
            continue
        secret = store.read(reference)
        if not verify_client_secret(secret, current.secret_hash):
            fail("api_client", profile.purpose, "secret_reference_mismatch")
            continue
        try:
            issued = service.issue_client_credentials_token(
                client_id=client_id,
                client_secret=secret,
                audience=profile.audiences[0],
                requested_scopes=profile.scopes,
            )
            context = service.verify_access_token(
                issued.access_token,
                audience=profile.audiences[0],
                client_purpose=profile.purpose,
            )
            if context.client_id != client_id or not set(profile.capabilities).issubset(context.capabilities):
                raise ValueError("verified context mismatch")
        except Exception:
            fail("api_client", profile.purpose, "token_probe_failed")

    for profile in WEBHOOK_CLIENT_PROFILES:
        current = repository.webhook_client(profile.client_id)
        if current is None:
            fail("webhook_client", profile.purpose, "not_registered")
            continue
        if (
            not current.enabled
            or current.principal_id != profile.principal_id
            or current.capabilities != profile.capabilities
            or current.allowed_cidrs != profile.allowed_cidrs
        ):
            fail("webhook_client", profile.purpose, "profile_drift")
        if not _resolves(store, current.secret_reference, minimum_bytes=32):
            fail("webhook_client", profile.purpose, "secret_reference_unresolved")
            continue
        try:
            WebhookHmacSigner(client_id=current.client_id, secret=store.read(current.secret_reference)).sign_headers(
                body=b"readiness",
                event_id=f"readiness-{profile.purpose}-00000001",
            )
        except Exception:
            fail("webhook_client", profile.purpose, "signing_probe_failed")

    api_profiles = tuple(API_CLIENT_PROFILES)
    webhook_capabilities = {capability for profile in WEBHOOK_CLIENT_PROFILES for capability in profile.capabilities}
    for entry in load_route_manifest(manifest_path):
        scheme = str(entry.get("auth_scheme") or "")
        capability = str(entry.get("service_capability") or entry.get("capability") or "")
        if scheme == "api_client_jwt":
            audience = str(entry.get("audience") or "")
            purpose = str(entry.get("client_purpose") or "")
            covered = any(
                audience in profile.audiences
                and capability in profile.capabilities
                and (not purpose or purpose == profile.purpose)
                for profile in api_profiles
            )
            if not covered:
                fail("route_policy", str(entry.get("path") or ""), "api_client_profile_missing")
        elif scheme == "human_or_service":
            audience = str(entry.get("service_audience") or "")
            covered = any(audience in profile.audiences and capability in profile.capabilities for profile in api_profiles)
            if not covered:
                fail("route_policy", str(entry.get("path") or ""), "service_profile_missing")
        elif scheme == "webhook_hmac" and capability not in webhook_capabilities:
            fail("route_policy", str(entry.get("path") or ""), "webhook_profile_missing")

    campaign = next(profile for profile in API_CLIENT_PROFILES if profile.purpose == "campaign_agent")
    forbidden_campaign_capabilities = {
        "direct_send_execute",
        "manage_automation",
        "campaign_approve",
        "campaign_start",
        "external_write",
    }
    if forbidden_campaign_capabilities.intersection(campaign.capabilities):
        fail("api_client", "campaign_agent", "forbidden_capability_present")

    return {
        "ok": not failures,
        "api_client_expected": len(API_CLIENT_PROFILES),
        "webhook_client_expected": len(WEBHOOK_CLIENT_PROFILES),
        "failure_count": len(failures),
        "failures": failures,
        "token_probe_count": len(API_CLIENT_PROFILES) - sum(
            1 for item in failures if item["kind"] == "api_client" and "secret" in item["reason"]
        ),
        "secrets_printed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check AI-CRM authentication cutover readiness without printing secrets.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--secret-store-dir", default=os.getenv("AICRM_SECRET_STORE_DIR", ""))
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--issuer", default=os.getenv("AICRM_AUTH_ISSUER", ""))
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/architecture/route_ownership_manifest.yml")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        environment = {**os.environ, **_read_environment_file(args.environment_file)}
        report = check_auth_readiness(
            repository=PostgresAuthRepository(database_url=args.database_url or None),
            store=FileSecretStore(args.secret_store_dir),
            environment=environment,
            issuer=args.issuer,
            manifest_path=args.manifest,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

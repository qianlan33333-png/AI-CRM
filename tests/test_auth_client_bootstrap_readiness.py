from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aicrm_next.platform.platform_foundation.auth_platform.profiles import API_CLIENT_PROFILES, WEBHOOK_CLIENT_PROFILES
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository
from aicrm_next.platform.shared.secret_store import FileSecretStore, is_secret_reference
from scripts.ops.bootstrap_auth_clients import bootstrap_auth_clients
from scripts.ops.check_auth_readiness import check_auth_readiness


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.usefixtures("next_pg_schema")
def test_bootstrap_readiness_and_idempotent_reconcile_do_not_print_secrets(tmp_path: Path) -> None:
    repository = PostgresAuthRepository(database_url=os.environ["DATABASE_URL"])
    store = FileSecretStore(tmp_path / "secrets")
    environment = {"WECOM_CORP_ID": "corp-bootstrap-test"}
    issuer = "https://crm.example.test/oauth"

    dry_run, dry_updates = bootstrap_auth_clients(
        repository=repository,
        store=store,
        environment=environment,
        issuer=issuer,
        apply=False,
    )

    assert dry_run["mode"] == "dry_run"
    assert dry_run["api_client_count"] == len(API_CLIENT_PROFILES)
    assert dry_run["webhook_client_count"] == len(WEBHOOK_CLIENT_PROFILES)
    assert repository.list_api_clients() == []
    assert dry_updates["AICRM_AUTH_ISSUER"] == issuer
    assert all(dry_updates[profile.client_id_setting] == profile.client_id for profile in API_CLIENT_PROFILES)
    assert not any(key.endswith("_CLIENT_SECRET_REF") for key in dry_updates)

    applied, updates = bootstrap_auth_clients(
        repository=repository,
        store=store,
        environment=environment,
        issuer=issuer,
        apply=True,
    )
    readiness = check_auth_readiness(
        repository=repository,
        store=store,
        environment={**environment, **updates},
        issuer=issuer,
        manifest_path=ROOT / "docs/architecture/route_ownership_manifest.yml",
    )

    assert applied["mode"] == "apply"
    assert len(repository.list_api_clients()) == len(API_CLIENT_PROFILES)
    assert readiness["ok"] is True
    assert readiness["failure_count"] == 0
    assert readiness["token_probe_count"] == len(API_CLIENT_PROFILES)
    assert readiness["secrets_printed"] is False
    for profile in API_CLIENT_PROFILES:
        assert is_secret_reference(updates[profile.client_secret_reference_setting])

    reconciled, _ = bootstrap_auth_clients(
        repository=repository,
        store=store,
        environment={**environment, **updates},
        issuer=issuer,
        apply=True,
    )
    rendered = json.dumps(
        {"applied": applied, "readiness": readiness, "reconciled": reconciled},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert reconciled["changed_count"] == 0
    assert reconciled["secrets_printed"] is False
    assert "aics_" not in rendered
    assert "scrypt$" not in rendered

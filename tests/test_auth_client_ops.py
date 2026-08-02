from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, verify_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.profiles import API_CLIENT_PROFILE_BY_PURPOSE
from aicrm_next.platform.shared.secret_store import FileSecretStore, is_secret_reference
from scripts.ops.bootstrap_auth_clients import _read_environment_file
from scripts.ops.manage_auth_clients import client_status, rotate_client_secret, set_client_enabled


PREVIOUS_CLIENT_SECRET = "aics_" + "p" * 64


class _Repository:
    def __init__(self, record: ApiClientRecord) -> None:
        self.record = record

    def api_client(self, client_id: str) -> ApiClientRecord | None:
        return self.record if client_id == self.record.client_id else None

    def rotate_api_client_secret(
        self,
        client_id: str,
        secret_hash: str,
        credential_hint: str | None = None,
    ) -> int | None:
        if client_id != self.record.client_id:
            return None
        self.record = replace(
            self.record,
            secret_hash=secret_hash,
            credential_hint=credential_hint or self.record.credential_hint,
            auth_version=self.record.auth_version + 1,
        )
        return self.record.auth_version

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None:
        if client_id != self.record.client_id:
            return None
        self.record = replace(
            self.record,
            enabled=enabled,
            auth_version=self.record.auth_version + 1,
        )
        return self.record.auth_version


def _record() -> ApiClientRecord:
    profile = API_CLIENT_PROFILE_BY_PURPOSE["automation_worker"]
    return ApiClientRecord(
        client_id=profile.client_id,
        principal_id=profile.principal_id,
        principal_type=PrincipalType.SERVICE,
        purpose=profile.purpose,
        display_name=profile.display_name,
        secret_hash=hash_client_secret(PREVIOUS_CLIENT_SECRET),
        audiences=profile.audiences,
        scopes=profile.scopes,
        capabilities=profile.capabilities,
        allowed_cidrs=(),
        corp_id="corp-test",
        owner_scope={},
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=True,
    )


def test_status_and_disable_do_not_expose_secret_material() -> None:
    repository = _Repository(_record())

    disabled = set_client_enabled(
        repository=repository,
        environment={},
        purpose="automation_worker",
        enabled=False,
    )
    status = client_status(repository=repository, environment={})
    rendered = json.dumps({"disabled": disabled, "status": status}, sort_keys=True)

    assert disabled["auth_version"] == 2
    assert disabled["enabled"] is False
    assert status["items"][0]["enabled"] is False
    assert "secret_hash" not in rendered
    assert PREVIOUS_CLIENT_SECRET not in rendered
    assert disabled["secrets_printed"] is False


def test_rotate_writes_new_secret_reference_and_increments_auth_version(tmp_path: Path) -> None:
    repository = _Repository(_record())
    store = FileSecretStore(tmp_path / "secrets")
    environment_file = tmp_path / "runtime.env"
    environment_file.write_text("EXISTING_FLAG='keep-me'\n", encoding="utf-8")
    os.chmod(environment_file, 0o600)

    report = rotate_client_secret(
        repository=repository,
        store=store,
        environment={},
        environment_file=environment_file,
        purpose="automation_worker",
    )

    environment = _read_environment_file(environment_file)
    reference = environment["AICRM_AUTH_AUTOMATION_WORKER_CLIENT_SECRET_REF"]
    rotated_secret = store.read(reference)
    assert report["auth_version"] == 2
    assert report["secrets_printed"] is False
    assert is_secret_reference(reference)
    assert rotated_secret != PREVIOUS_CLIENT_SECRET
    assert verify_client_secret(rotated_secret, repository.record.secret_hash)
    assert "EXISTING_FLAG='keep-me'" in environment_file.read_text(encoding="utf-8")
    assert rotated_secret not in json.dumps(report, sort_keys=True)

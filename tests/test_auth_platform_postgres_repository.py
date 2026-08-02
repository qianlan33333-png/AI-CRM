from datetime import datetime, timedelta, timezone
import os
from collections.abc import Iterator, Mapping
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from aicrm_next.platform.platform_foundation.admin_audit import AdminAuditRecord
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord, WebhookClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository
from aicrm_next.platform.shared.db_session import get_session_factory


class _FailingAuditMapping(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("simulated audit serialization failure")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("simulated audit serialization failure")

    def __len__(self) -> int:
        return 1


def test_postgres_api_client_round_trip_rotation_and_disable(next_pg_schema) -> None:
    suffix = uuid4().hex
    repository = PostgresAuthRepository(database_url=os.environ["DATABASE_URL"])
    client_id = f"client-{suffix}"
    secret = issue_client_secret()
    repository.insert_api_client(
        ApiClientRecord(
            client_id=client_id,
            principal_id=f"service:{suffix}",
            principal_type=PrincipalType.SERVICE,
            purpose="automation_worker",
            display_name="Repository worker",
            secret_hash=hash_client_secret(secret),
            audiences=("internal_worker",),
            scopes=("read", "write"),
            capabilities=("jobs_execute",),
            allowed_cidrs=("203.0.113.0/24",),
            corp_id="corp-test",
            owner_scope={"owner_userid": ["owner-1"]},
            auth_version=1,
            token_ttl_seconds=1800,
            enabled=True,
        )
    )

    loaded = repository.api_client(client_id)
    assert loaded is not None
    assert loaded.purpose == "automation_worker"
    assert loaded.owner_scope == {"owner_userid": ["owner-1"]}
    assert repository.rotate_api_client_secret(client_id, hash_client_secret(issue_client_secret())) == 2
    assert repository.set_api_client_enabled(client_id, False) == 3
    assert repository.api_client(client_id).enabled is False


def test_postgres_api_client_admin_audit_is_atomic_and_metadata_is_secret_free(next_pg_schema) -> None:
    suffix = uuid4().hex
    repository = PostgresAuthRepository(database_url=os.environ["DATABASE_URL"])
    client_id = f"admin-client-{suffix}"
    record = ApiClientRecord(
        client_id=client_id,
        principal_id=f"api_client:{client_id}",
        principal_type=PrincipalType.API_CLIENT,
        purpose="external_agent",
        display_name="Atomic audit client",
        secret_hash=hash_client_secret(issue_client_secret()),
        audiences=("external_integration",),
        scopes=("read", "write"),
        capabilities=("external_read", "external_write"),
        allowed_cidrs=(),
        corp_id="corp-test",
        owner_scope={},
        auth_version=1,
        token_ttl_seconds=1800,
        enabled=False,
    )
    failing_audit = AdminAuditRecord(
        operator="pytest",
        action_type="api_client_created",
        target_type="api_client",
        target_id=client_id,
        after=_FailingAuditMapping(),
    )
    with pytest.raises(RuntimeError, match="simulated audit serialization failure"):
        repository.insert_api_client(record, audit=failing_audit)
    assert repository.api_client(client_id) is None

    audit = AdminAuditRecord(
        operator="pytest",
        action_type="api_client_created",
        target_type="api_client",
        target_id=client_id,
        after={"client_type": "external_api", "auth_version": 1},
    )
    repository.insert_api_client(record, audit=audit)
    metadata = repository.api_client_metadata(client_id)
    assert metadata is not None
    assert "secret_hash" not in metadata
    with get_session_factory().begin() as session:
        logged = session.execute(
            text(
                "SELECT action_type, target_id, after_json::text FROM admin_operation_logs "
                "WHERE target_type = 'api_client' AND target_id = :client_id ORDER BY id DESC LIMIT 1"
            ),
            {"client_id": client_id},
        ).mappings().one()
    assert logged["action_type"] == "api_client_created"
    assert logged["target_id"] == client_id
    assert "secret" not in logged["after_json"].lower()


def test_postgres_webhook_registry_and_replay_are_persistent(next_pg_schema) -> None:
    suffix = uuid4().hex
    repository = PostgresAuthRepository(database_url=os.environ["DATABASE_URL"])
    client_id = f"webhook-{suffix}"
    repository.upsert_webhook_client(
        WebhookClientRecord(
            client_id=client_id,
            principal_id=f"api_client:{suffix}",
            display_name="Webhook test",
            secret_reference=f"secretref:file:AICRM_TEST_WEBHOOK:v1_{'1' * 16}_{'2' * 16}",
            capabilities=("group_ops_webhook_receive",),
            allowed_cidrs=(),
            corp_id="corp-test",
            owner_scope={"webhook_key": ["daily"]},
            auth_version=1,
            enabled=True,
        )
    )
    loaded = repository.webhook_client(client_id)
    assert loaded is not None
    assert loaded.capabilities == ("group_ops_webhook_receive",)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert repository.consume_webhook_event(client_id=client_id, event_id_hash="a" * 64, expires_at=expires_at)
    assert not repository.consume_webhook_event(client_id=client_id, event_id_hash="a" * 64, expires_at=expires_at)

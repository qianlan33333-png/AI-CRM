from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import hash_client_secret, issue_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord, WebhookClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.repository import PostgresAuthRepository


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

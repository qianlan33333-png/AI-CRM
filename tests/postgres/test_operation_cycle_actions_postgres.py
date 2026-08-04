from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from aicrm_next.extensions.hxc.operation_cycles.action_dto import OperationRunnerHeartbeatV1
from aicrm_next.extensions.hxc.operation_cycles.action_repository import (
    PostgresOperationCycleActionRepository,
)
from aicrm_next.extensions.hxc.operation_cycles.domain import OperationCycleConflictError


NOW = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
pytestmark = pytest.mark.postgres


def test_postgres_claim_lease_and_event_idempotency_state_machine(
    migrated_database_url: str,
) -> None:
    engine_url = migrated_database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(engine_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "pytest-operation-actions"
    with factory.begin() as session:
        session.execute(
            text(
                "DELETE FROM operation_cycle_action_request_events WHERE request_id IN "
                "(SELECT request_id FROM operation_cycle_action_requests WHERE tenant_id = :tenant_id)"
            ),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("DELETE FROM operation_cycle_action_requests WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("DELETE FROM operation_cycle_runners WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )

    repo = PostgresOperationCycleActionRepository(factory, tenant_id=tenant_id)
    repo.heartbeat(
        OperationRunnerHeartbeatV1(
            runner_id="mac-pg-1",
            connector_version="connector/1",
            codex_version="codex-cli 1.2.3",
            compatibility_status="ready",
            binding_keys=["excel_workspace"],
        ),
        principal_id="api_client:operation_runner",
        now=NOW,
    )
    with pytest.raises(OperationCycleConflictError, match="runner_principal_mismatch"):
        repo.heartbeat(
            OperationRunnerHeartbeatV1(
                runner_id="mac-pg-1",
                connector_version="connector/1",
                codex_version="codex-cli 1.2.3",
                compatibility_status="ready",
                binding_keys=["excel_workspace"],
            ),
            principal_id="api_client:different-operation-runner",
            now=NOW,
        )
    values = {
        "request_id": "ocact_pg_state_machine",
        "strategy_key": "hxc_monday_full_activation",
        "run_key": "hxc_monday_20260803",
        "action_key": "prepare_broadcast",
        "action_title": "启动周一群发准备",
        "strategy_version": 2,
        "context_hash": "a" * 64,
        "skill_key": "hxc_monday_broadcast.v1",
        "skill_hash": "b" * 64,
        "runner_id": "mac-pg-1",
        "parent_request_id": "",
        "created_by": "human:admin",
        "created_at": NOW,
    }
    created, reused = repo.create_request(values, idempotency_key="pg-start-1")
    assert reused is False
    same, reused = repo.create_request(
        {**values, "created_at": NOW + timedelta(seconds=15)},
        idempotency_key="pg-start-1",
    )
    assert reused is True and same.request_id == created.request_id

    claimed, first_token, expires_at = repo.claim(
        "mac-pg-1",
        principal_id="api_client:operation_runner",
        now=NOW,
        lease_seconds=60,
    )
    assert claimed is not None and claimed.status == "claimed"
    assert expires_at == NOW + timedelta(seconds=60)
    claimed_again, second_token, _ = repo.claim(
        "mac-pg-1",
        principal_id="api_client:operation_runner",
        now=NOW + timedelta(seconds=15),
        lease_seconds=60,
    )
    assert claimed_again is not None and claimed_again.request_id == created.request_id
    assert first_token != second_token
    with pytest.raises(OperationCycleConflictError, match="lease"):
        repo.apply_event(
            created.request_id,
            event_id="thread-1",
            lease_token=first_token,
            event_type="thread_bound",
            event_payload={"thread_id": "thread-pg-1"},
            now=NOW + timedelta(seconds=16),
        )

    bound, duplicate = repo.apply_event(
        created.request_id,
        event_id="thread-1",
        lease_token=second_token,
        event_type="thread_bound",
        event_payload={"thread_id": "thread-pg-1"},
        now=NOW + timedelta(seconds=16),
    )
    assert duplicate is False and bound.status == "thread_bound"
    rebound, duplicate = repo.apply_event(
        created.request_id,
        event_id="thread-1",
        lease_token=second_token,
        event_type="thread_bound",
        event_payload={"thread_id": "thread-pg-1"},
        now=NOW + timedelta(seconds=17),
    )
    assert duplicate is True and rebound.thread_id == "thread-pg-1"
    engine.dispose()

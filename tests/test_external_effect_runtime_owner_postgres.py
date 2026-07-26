from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform.platform_foundation.execution_runtime.commands import (
    QueueRuntimeCommandService,
)
from aicrm_next.platform.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
)
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def _seed_job(*, status: str = "queued") -> dict:
    suffix = uuid4().hex
    return ExternalEffectService().plan_effect(
        effect_type="test.external_effect.owner",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{suffix}",
        business_type="owner_test",
        business_id=f"business-{suffix}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"external-owner-{suffix}",
        status=status,
        lane="wecom_interactive",
        fairness_key="pytest-external-owner",
    )


def _enable_runtime(*, generation: int) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = %s,
                claim_enabled = TRUE,
                rollout_mode = 'execute',
                global_max_in_flight = 10,
                policy_version = 'queue-v2-test-loopback',
                external_claim_scope = 'test_loopback'
            WHERE singleton = TRUE
            """,
            (generation,),
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE,
                rollout_mode = 'execute',
                blocked_until = NULL,
                max_in_flight = 4,
                policy_version = 'queue-v2-test-loopback'
            WHERE lane = 'wecom_interactive'
            """
        )


def test_external_effect_owner_claim_recovery_and_heartbeat(next_pg_schema) -> None:
    _enable_runtime(generation=83)
    seeded = _seed_job()
    runtime = ExecutionRuntimeRepository(_database_url())

    first = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="external-owner-worker-1",
        generation=83,
        lease_seconds=10,
        test_only=True,
    )
    assert first is not None and first.item_id == int(seeded["id"])
    assert runtime.renew_lease(
        queue_kind="external_effect",
        item_id=first.item_id,
        lease_token=first.lease_token,
        generation=83,
        lease_seconds=20,
    )

    with _connect() as connection:
        heartbeat = connection.execute(
            "SELECT heartbeat_at FROM external_effect_job WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        recovery_count_before = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'external_effect' AND queue_row_id = %s
                """,
                (first.item_id,),
            ).fetchone()["count"]
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE id = %s
            """,
            (first.item_id,),
        )
    assert heartbeat["heartbeat_at"] is not None

    recovered = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="external-owner-worker-2",
        generation=83,
        test_only=True,
    )
    assert recovered is not None and recovered.item_id == first.item_id
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT status, locked_by, last_error_code
            FROM external_effect_job
            WHERE id = %s
            """,
            (first.item_id,),
        ).fetchone()
        recovery_count_after = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'external_effect' AND queue_row_id = %s
                """,
                (first.item_id,),
            ).fetchone()["count"]
        )
    assert row == {
        "status": "dispatching",
        "locked_by": "external-owner-worker-2",
        "last_error_code": "lease_expired_before_dispatch",
    }
    assert recovery_count_after == recovery_count_before + 1


def test_external_effect_owner_manual_cancel_keeps_audit_transaction(next_pg_schema) -> None:
    seeded = _seed_job()
    service = QueueRuntimeCommandService()
    target = service.read_target("external_effect", int(seeded["id"]))
    assert target is not None

    result = service.request_manual_action(
        "external_effect",
        int(seeded["id"]),
        action="cancel",
        expected_status=target.status,
        expected_version=target.version_token,
        actor="pytest",
        reason="cancel through external-effect owner port",
    )

    with _connect() as connection:
        job = connection.execute(
            """
            SELECT status, cancel_requested_by, cancel_reason
            FROM external_effect_job
            WHERE id = %s
            """,
            (int(seeded["id"]),),
        ).fetchone()
        audit = connection.execute(
            "SELECT status FROM internal_event_outbox WHERE outbox_id = %s",
            (result.intent_id,),
        ).fetchone()
    assert job == {
        "status": "cancelled",
        "cancel_requested_by": "pytest",
        "cancel_reason": "cancel through external-effect owner port",
    }
    assert audit is not None and audit["status"] == "pending"

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.execution_runtime.commands import QueueRuntimeCommandService
from aicrm_next.platform_foundation.execution_runtime.repository import ExecutionRuntimeRepository
from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_transactional_internal_event_outbox


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def _seed_outbox() -> dict:
    suffix = uuid4().hex
    with _connect() as connection:
        return enqueue_transactional_internal_event_outbox(
            connection,
            InternalEventCreateRequest(
                event_type="test.internal.outbox.owner",
                aggregate_type="test",
                aggregate_id=suffix,
                idempotency_key=f"internal-outbox-owner-{suffix}",
                execution_id=f"exe-outbox-owner-{suffix}",
                payload={"test": True},
                payload_summary={"test": True},
            ),
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
                policy_version = 'queue-v2-test-loopback'
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
            WHERE lane = 'internal_general'
            """
        )


def test_internal_event_outbox_owner_runtime_claim_recovery_and_heartbeat(next_pg_schema) -> None:
    _enable_runtime(generation=81)
    seeded = _seed_outbox()
    runtime = ExecutionRuntimeRepository(_database_url())

    first = runtime.claim_internal_outbox_one(
        lane="internal_general",
        worker_id="outbox-owner-worker-1",
        generation=81,
        lease_seconds=10,
    )
    assert first is not None and first.item_id == int(seeded["id"])
    assert runtime.renew_lease(
        queue_kind="internal_outbox",
        item_id=first.item_id,
        lease_token=first.lease_token,
        generation=81,
        lease_seconds=20,
    )

    with _connect() as connection:
        heartbeat = connection.execute(
            "SELECT heartbeat_at FROM internal_event_outbox WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        connection.execute(
            "UPDATE internal_event_outbox SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (first.item_id,),
        )
    assert heartbeat["heartbeat_at"] is not None

    recovered = runtime.claim_internal_outbox_one(
        lane="internal_general",
        worker_id="outbox-owner-worker-2",
        generation=81,
    )
    assert recovered is not None and recovered.item_id == first.item_id
    with _connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count, locked_by FROM internal_event_outbox WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        recovery_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM queue_runtime_lease_recovery_event
            WHERE queue_kind = 'internal_outbox' AND queue_row_id = %s
            """,
            (first.item_id,),
        ).fetchone()
    assert row == {"status": "running", "attempt_count": 2, "locked_by": "outbox-owner-worker-2"}
    assert recovery_count["count"] == 1


def test_internal_event_outbox_owner_immediate_command_keeps_audit_transaction(next_pg_schema) -> None:
    seeded = _seed_outbox()
    with _connect() as connection:
        connection.execute(
            "UPDATE internal_event_outbox SET available_at = CURRENT_TIMESTAMP + INTERVAL '1 hour' WHERE id = %s",
            (int(seeded["id"]),),
        )
    service = QueueRuntimeCommandService()
    target = service.read_target("internal_outbox", int(seeded["id"]))
    assert target is not None

    result = service.request_immediate_execution(
        "internal_outbox",
        int(seeded["id"]),
        expected_status=target.status,
        expected_version=target.version_token,
        actor="pytest",
        reason="wake through internal-event outbox owner port",
    )

    with _connect() as connection:
        row = connection.execute(
            "SELECT status, available_at FROM internal_event_outbox WHERE id = %s",
            (int(seeded["id"]),),
        ).fetchone()
        audit_count = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE outbox_id = %s",
            (result.intent_id,),
        ).fetchone()
    assert row["status"] == "pending"
    assert audit_count["count"] == 1

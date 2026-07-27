from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from aicrm_next.platform.platform_foundation.execution_runtime.commands import QueueRuntimeCommandService
from aicrm_next.platform.platform_foundation.execution_runtime.repository import ExecutionRuntimeRepository


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def _seed_run(*, status: str = "pending") -> dict:
    suffix = uuid4().hex
    event_id = f"iev_consumer_owner_{suffix}"
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO internal_event (
                event_id, event_type, aggregate_type, aggregate_id,
                idempotency_key, execution_id
            ) VALUES (%s, 'test.consumer.owner', 'test', %s, %s, %s)
            """,
            (event_id, suffix, f"event-owner-{suffix}", f"exe-event-owner-{suffix}"),
        )
        row = connection.execute(
            """
            INSERT INTO internal_event_consumer_run (
                event_id, consumer_name, status, execution_id,
                parent_execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version,
                attempt_count, max_attempts
            ) VALUES (
                %s, %s, %s, %s, %s, 'internal_general',
                CURRENT_TIMESTAMP, %s, 'pytest-owner', 'queue-v2-test-loopback', 0, 5
            )
            RETURNING id, event_id, consumer_name, execution_id
            """,
            (
                event_id,
                f"pytest_consumer_owner_{suffix}",
                status,
                f"exe-run-owner-{suffix}",
                f"exe-event-owner-{suffix}",
                f"order-owner-{suffix}",
            ),
        ).fetchone()
    return dict(row)


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


def test_internal_event_consumer_owner_runtime_claim_recovery_and_heartbeat(next_pg_schema) -> None:
    _enable_runtime(generation=79)
    seeded = _seed_run()
    runtime = ExecutionRuntimeRepository(_database_url())

    first = runtime.claim_internal_event_one(
        lane="internal_general",
        worker_id="internal-owner-worker-1",
        generation=79,
        lease_seconds=10,
    )
    assert first is not None and first.item_id == int(seeded["id"])
    assert runtime.renew_lease(
        queue_kind="internal_event",
        item_id=first.item_id,
        lease_token=first.lease_token,
        generation=79,
        lease_seconds=20,
    )

    with _connect() as connection:
        heartbeat = connection.execute(
            "SELECT heartbeat_at FROM internal_event_consumer_run WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        connection.execute(
            "UPDATE internal_event_consumer_run SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (first.item_id,),
        )
    assert heartbeat["heartbeat_at"] is not None

    recovered = runtime.claim_internal_event_one(
        lane="internal_general",
        worker_id="internal-owner-worker-2",
        generation=79,
    )
    assert recovered is not None and recovered.item_id == first.item_id
    with _connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count, locked_by FROM internal_event_consumer_run WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        recovery_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM queue_runtime_lease_recovery_event
            WHERE queue_kind = 'internal_event' AND queue_row_id = %s
            """,
            (first.item_id,),
        ).fetchone()
    assert row == {"status": "running", "attempt_count": 1, "locked_by": "internal-owner-worker-2"}
    assert recovery_count["count"] == 1


def test_internal_event_consumer_owner_operator_retry_and_skip_are_atomic(next_pg_schema) -> None:
    retry_run = _seed_run(status="failed_terminal")
    skip_run = _seed_run(status="pending")
    service = QueueRuntimeCommandService()

    retry_target = service.read_target("internal_event", int(retry_run["id"]))
    skip_target = service.read_target("internal_event", int(skip_run["id"]))
    assert retry_target is not None and skip_target is not None
    retry_result = service.request_manual_action(
        "internal_event",
        int(retry_run["id"]),
        action="retry",
        expected_status=retry_target.status,
        expected_version=retry_target.version_token,
        actor="pytest",
        reason="retry through internal-event owner port",
    )
    skip_result = service.request_manual_action(
        "internal_event",
        int(skip_run["id"]),
        action="skip",
        expected_status=skip_target.status,
        expected_version=skip_target.version_token,
        actor="pytest",
        reason="skip through internal-event owner port",
    )

    with _connect() as connection:
        statuses = connection.execute(
            "SELECT id, status FROM internal_event_consumer_run WHERE id = ANY(%s) ORDER BY id",
            ([int(retry_run["id"]), int(skip_run["id"])],),
        ).fetchall()
        attempts = connection.execute(
            """
            SELECT consumer_run_id, status
            FROM internal_event_consumer_attempt
            WHERE consumer_run_id = ANY(%s)
            """,
            ([int(retry_run["id"]), int(skip_run["id"])],),
        ).fetchall()
        audit_count = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE outbox_id = ANY(%s)",
            ([retry_result.intent_id, skip_result.intent_id],),
        ).fetchone()
    assert {row["status"] for row in statuses} == {"pending", "skipped"}
    assert {row["status"] for row in attempts} == {"manual_retry", "skipped"}
    assert audit_count["count"] == 2

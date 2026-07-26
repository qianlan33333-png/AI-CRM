from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from aicrm_next.platform.platform_foundation.execution_runtime.commands import (
    QueueRuntimeCommandService,
)
from aicrm_next.platform.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
)
from aicrm_next.platform.platform_foundation.webhook_inbox.repository import (
    PostgresWebhookInboxRepository,
)


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _inbox(*, suffix: str, lane: str = "webhook_inbox") -> dict:
    return PostgresWebhookInboxRepository(_database_url()).upsert_received(
        provider="pytest",
        event_family="owner_test",
        route="/tests/webhook-owner",
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-owner-test",
        event_type="owner_test",
        change_type="owner_test",
        external_event_id=f"event-{suffix}",
        idempotency_key=f"webhook-owner-{suffix}",
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"<xml />",
        payload_xml="<xml />",
        payload_json={"test": True},
        payload_summary_json={"test": True},
        lane=lane,
    )


def test_webhook_owner_runtime_claim_recovery_and_heartbeat(next_pg_schema) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 77,
                claim_enabled = TRUE,
                rollout_mode = 'execute',
                global_max_in_flight = 10
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE,
                rollout_mode = 'execute',
                blocked_until = NULL,
                max_in_flight = 4,
                policy_version = (
                    SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                )
            WHERE lane = 'webhook_inbox'
            """
        )
    inbox = _inbox(suffix=uuid4().hex)
    runtime = ExecutionRuntimeRepository(_database_url())

    first = runtime.claim_webhook_inbox_one(
        lane="webhook_inbox",
        worker_id="owner-worker-1",
        generation=77,
        lease_seconds=10,
    )
    assert first is not None and first.item_id == inbox["id"]
    assert runtime.renew_lease(
        queue_kind="webhook_inbox",
        item_id=first.item_id,
        lease_token=first.lease_token,
        generation=77,
        lease_seconds=20,
    )

    with _connect() as connection:
        heartbeat = connection.execute(
            "SELECT heartbeat_at FROM webhook_inbox WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        recovery_count_before = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'webhook_inbox' AND queue_row_id = %s
                """,
                (first.item_id,),
            ).fetchone()["count"]
        )
        connection.execute(
            "UPDATE webhook_inbox SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (first.item_id,),
        )
    assert heartbeat["heartbeat_at"] is not None

    recovered = runtime.claim_webhook_inbox_one(
        lane="webhook_inbox",
        worker_id="owner-worker-2",
        generation=77,
    )
    assert recovered is not None and recovered.item_id == first.item_id
    with _connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count, locked_by FROM webhook_inbox WHERE id = %s",
            (first.item_id,),
        ).fetchone()
        recovery_count_after = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'webhook_inbox' AND queue_row_id = %s
                """,
                (first.item_id,),
            ).fetchone()["count"]
        )
    assert row == {"status": "processing", "attempt_count": 1, "locked_by": "owner-worker-2"}
    assert recovery_count_after == recovery_count_before + 1


def test_webhook_owner_operator_retry_and_skip_keep_command_transaction(next_pg_schema) -> None:
    retry_inbox = _inbox(suffix=uuid4().hex)
    skip_inbox = _inbox(suffix=uuid4().hex)
    with _connect() as connection:
        connection.execute(
            "UPDATE webhook_inbox SET status = 'failed_terminal' WHERE id = %s",
            (retry_inbox["id"],),
        )
    service = QueueRuntimeCommandService()

    retry_target = service.read_target("webhook_inbox", int(retry_inbox["id"]))
    skip_target = service.read_target("webhook_inbox", int(skip_inbox["id"]))
    assert retry_target is not None and skip_target is not None
    retry_result = service.request_manual_action(
        "webhook_inbox",
        int(retry_inbox["id"]),
        action="retry",
        expected_status=retry_target.status,
        expected_version=retry_target.version_token,
        actor="pytest",
        reason="retry through owner port",
    )
    skip_result = service.request_manual_action(
        "webhook_inbox",
        int(skip_inbox["id"]),
        action="skip",
        expected_status=skip_target.status,
        expected_version=skip_target.version_token,
        actor="pytest",
        reason="skip through owner port",
    )

    with _connect() as connection:
        statuses = connection.execute(
            "SELECT id, status FROM webhook_inbox WHERE id = ANY(%s) ORDER BY id",
            ([int(retry_inbox["id"]), int(skip_inbox["id"])],),
        ).fetchall()
        audit_count = connection.execute(
            "SELECT COUNT(*) AS count FROM internal_event_outbox WHERE outbox_id = ANY(%s)",
            ([retry_result.intent_id, skip_result.intent_id],),
        ).fetchone()
    assert {row["status"] for row in statuses} == {"failed_retryable", "ignored"}
    assert audit_count["count"] == 2

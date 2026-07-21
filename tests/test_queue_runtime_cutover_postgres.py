from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.execution_runtime.commands import (
    QUEUE_RUNTIME_COMMAND_APPLIED,
    QueueCommandConflict,
    QueueRuntimeCommandService,
)
from aicrm_next.platform_foundation.execution_runtime.cutover import (
    GenerationCASConflict,
    RuntimeGenerationRepository,
)
from aicrm_next.platform_foundation.execution_runtime.invariants import (
    QueueRuntimeInvariantChecker,
)
from aicrm_next.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
)
from aicrm_next.platform_foundation.execution_runtime.listener import (
    PostgresQueueWakeListener,
)
from aicrm_next.platform_foundation.external_effects.service import (
    ExternalEffectService,
)


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect(*, autocommit: bool = True):
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


def _reset_control_plane_state() -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM queue_worker_heartbeat")
        for table in (
            "external_effect_job",
            "internal_event_consumer_run",
            "internal_event_outbox",
            "webhook_inbox",
        ):
            connection.execute(f"UPDATE {table} SET policy_version = 'queue-v2-test-loopback'")
            connection.execute(
                f"ALTER TABLE {table} ALTER COLUMN policy_version SET DEFAULT 'queue-v2-test-loopback'"
            )
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 0,
                claim_enabled = FALSE,
                rollout_mode = 'standby',
                policy_version = 'queue-v2-test-loopback',
                external_claim_scope = 'test_loopback',
                updated_by = 'pytest',
                updated_reason = 'cutover test reset'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE,
                rollout_mode = CASE
                    WHEN lane = 'outbound_webhook' THEN 'blocked'
                    ELSE 'standby'
                END,
                blocked_until = NULL,
                policy_version = 'queue-v2-test-loopback',
                updated_by = 'pytest',
                updated_reason = 'cutover test reset'
            """
        )


@pytest.fixture(autouse=True)
def _reset_control_plane() -> None:
    _reset_control_plane_state()
    yield
    _reset_control_plane_state()


def test_numeric_generation_cas_allows_only_one_concurrent_winner() -> None:
    barrier = __import__("threading").Barrier(2)

    def activate(target: int) -> tuple[str, int]:
        repository = RuntimeGenerationRepository(_database_url())
        barrier.wait(timeout=5)
        try:
            result = repository.activate_generation(
                expected_generation=0,
                target_generation=target,
                expected_policy_version="queue-v2-test-loopback",
                lanes=("internal_general",),
                actor=f"pytest-{target}",
                reason="concurrent generation CAS",
            )
            return "won", result.after.active_generation
        except GenerationCASConflict:
            return "lost", target

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, (31, 32)))

    assert [status for status, _target in results].count("won") == 1
    assert [status for status, _target in results].count("lost") == 1
    state = RuntimeGenerationRepository(_database_url()).read_state()
    assert state.claim_enabled is True
    assert state.active_generation in {31, 32}


def test_generation_cas_failure_does_not_change_lane_policy() -> None:
    repository = RuntimeGenerationRepository(_database_url())

    with pytest.raises(GenerationCASConflict):
        repository.activate_generation(
            expected_generation=99,
            target_generation=100,
            expected_policy_version="queue-v2-test-loopback",
            lanes=("internal_general",),
            actor="pytest",
            reason="wrong expected generation",
        )

    with _connect() as connection:
        lane = connection.execute(
            "SELECT rollout_mode, updated_reason FROM queue_lane_policy WHERE lane = 'internal_general'"
        ).fetchone()
    assert lane["rollout_mode"] == "standby"
    assert lane["updated_reason"] == "cutover test reset"


def test_generation_cutover_freezes_fifty_unheld_legacy_media_rows_before_claims_open() -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                idempotency_key, execution_mode, status, lane, available_at,
                ordering_key, fairness_key, rate_scope_key, policy_version,
                execution_id, created_at
            )
            SELECT
                'wecom.media.upload', 'wecom', 'upload', 'media', sequence::TEXT,
                'pr3-freeze-media-' || sequence::TEXT, 'real', 'queued',
                'wecom_media', CURRENT_TIMESTAMP,
                'pr3-media-' || sequence::TEXT, 'pr3-freeze', 'wecom:test:media',
                'queue-v2-test-loopback', 'exe-pr3-freeze-' || sequence::TEXT, CURRENT_TIMESTAMP
            FROM generate_series(1, 50) AS sequence
            """
        )
        connection.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                idempotency_key, execution_mode, status, lane, available_at,
                ordering_key, fairness_key, rate_scope_key, policy_version,
                execution_id, created_at
            ) VALUES (
                'wecom.media.upload', 'wecom', 'upload', 'media', 'after-cutoff',
                'pr3-freeze-media-after-cutoff', 'real', 'queued', 'wecom_media',
                CURRENT_TIMESTAMP + INTERVAL '1 hour', 'pr3-media-after-cutoff',
                'pr3-freeze', 'wecom:test:media', 'queue-v2-test-loopback',
                'exe-pr3-freeze-after-cutoff', CURRENT_TIMESTAMP + INTERVAL '1 hour'
            )
            """
        )

    activation = RuntimeGenerationRepository(_database_url()).activate_generation(
        expected_generation=0,
        target_generation=71,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_media",),
        actor="pytest",
        reason="freeze pre-cutover media backlog",
    )

    assert activation.freeze is not None
    assert activation.freeze.freeze_revision == "pr3_generation_71"
    assert dict(activation.freeze.counts)["external_effect"] == 50
    with _connect() as connection:
        held = connection.execute(
            """
            SELECT COUNT(*)::BIGINT AS count
            FROM external_effect_job
            WHERE idempotency_key LIKE 'pr3-freeze-media-%'
              AND idempotency_key <> 'pr3-freeze-media-after-cutoff'
              AND hold_reason = 'history_frozen_at_pr3_generation_71'
            """
        ).fetchone()
        audit = connection.execute(
            """
            SELECT COUNT(*)::BIGINT AS count
            FROM queue_history_classification audit
            JOIN external_effect_job job ON job.id = audit.queue_row_id
            WHERE audit.freeze_revision = 'pr3_generation_71'
              AND audit.queue_kind = 'external_effect'
              AND audit.classification = 'safe_pre_provider'
              AND job.idempotency_key LIKE 'pr3-freeze-media-%'
              AND audit.evidence_json ->> 'actor' = 'pytest'
              AND audit.evidence_json ->> 'reason' = 'freeze pre-cutover media backlog'
              AND audit.evidence_json ? 'cutoff_at'
            """
        ).fetchone()
        after_cutoff = connection.execute(
            """
            SELECT hold_reason
            FROM external_effect_job
            WHERE idempotency_key = 'pr3-freeze-media-after-cutoff'
            """
        ).fetchone()
    assert held["count"] == 50
    assert audit["count"] == 50
    assert after_cutoff["hold_reason"] == ""
    assert (
        ExecutionRuntimeRepository(_database_url()).claim_external_effect_one(
            lane="wecom_media",
            worker_id="pytest-cutover",
            generation=71,
        )
        is None
    )


def test_generation_cutover_quarantines_ambiguous_provider_boundary() -> None:
    with _connect() as connection:
        job = connection.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                idempotency_key, execution_mode, status, lane, available_at,
                ordering_key, fairness_key, rate_scope_key, policy_version,
                execution_id, dispatch_started_at, provider_call_started_at
            ) VALUES (
                'wecom.media.upload', 'wecom', 'upload', 'media', 'ambiguous',
                'pr3-provider-boundary-ambiguous', 'real', 'dispatching',
                'wecom_media', CURRENT_TIMESTAMP, 'pr3-ambiguous', 'pr3-freeze',
                'wecom:test:media', 'queue-v2-test-loopback', 'exe-pr3-ambiguous',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            ) RETURNING id
            """
        ).fetchone()

    RuntimeGenerationRepository(_database_url()).activate_generation(
        expected_generation=0,
        target_generation=72,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_media",),
        actor="pytest",
        reason="quarantine ambiguous provider boundary",
    )

    with _connect() as connection:
        persisted = connection.execute(
            """
            SELECT status, hold_reason, reconciliation_required
            FROM external_effect_job
            WHERE id = %s
            """,
            (job["id"],),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT classification, evidence_json
            FROM queue_history_classification
            WHERE freeze_revision = 'pr3_generation_72'
              AND queue_kind = 'external_effect'
              AND queue_row_id = %s
            """,
            (job["id"],),
        ).fetchone()
    assert persisted == {
        "status": "unknown_after_dispatch",
        "hold_reason": "provider_boundary_quarantine_at_pr3_generation_72",
        "reconciliation_required": True,
    }
    assert audit["classification"] == "inconsistent_quarantine"
    assert audit["evidence_json"]["provider_boundary_started"] is True


def test_generation_cutover_freezes_all_four_durable_queue_kinds_at_one_cutoff() -> None:
    key = uuid4().hex
    with _connect() as connection:
        external = connection.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                idempotency_key, execution_mode, status, lane, available_at,
                ordering_key, fairness_key, rate_scope_key, policy_version,
                execution_id
            ) VALUES (
                'wecom.media.upload', 'wecom', 'upload', 'media', %s,
                %s, 'real', 'queued', 'wecom_media', CURRENT_TIMESTAMP,
                %s, 'pytest', 'wecom:test:media', 'queue-v2-test-loopback', %s
            ) RETURNING id
            """,
            (key, f"external-{key}", f"external-{key}", f"exe-external-{key}"),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO internal_event (
                event_id, event_type, aggregate_type, aggregate_id,
                idempotency_key, execution_id
            ) VALUES (%s, 'test.pr3.freeze', 'test', %s, %s, %s)
            """,
            (f"iev_{key}", key, f"event-{key}", f"exe-event-{key}"),
        )
        consumer = connection.execute(
            """
            INSERT INTO internal_event_consumer_run (
                event_id, consumer_name, status, execution_id,
                parent_execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version
            ) VALUES (
                %s, 'pytest_pr3_freeze', 'pending', %s, %s,
                'internal_general', CURRENT_TIMESTAMP, %s, 'pytest', 'queue-v2-test-loopback'
            ) RETURNING id
            """,
            (f"iev_{key}", f"exe-run-{key}", f"exe-event-{key}", f"run-{key}"),
        ).fetchone()
        outbox = connection.execute(
            """
            INSERT INTO internal_event_outbox (
                outbox_id, event_type, aggregate_type, aggregate_id,
                idempotency_key, execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version
            ) VALUES (
                %s, 'test.pr3.freeze', 'test', %s, %s, %s,
                'internal_financial', CURRENT_TIMESTAMP, %s, 'pytest', 'queue-v2-test-loopback'
            ) RETURNING id
            """,
            (f"ieo_{key}", key, f"outbox-{key}", f"exe-outbox-{key}", f"outbox-{key}"),
        ).fetchone()
        future_outbox = connection.execute(
            """
            INSERT INTO internal_event_outbox (
                outbox_id, event_type, aggregate_type, aggregate_id,
                idempotency_key, execution_id, lane, available_at,
                occurred_at, created_at, ordering_key, fairness_key, policy_version
            ) VALUES (
                %s, 'test.pr3.freeze', 'test', %s, %s, %s,
                'internal_financial', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                CURRENT_TIMESTAMP - INTERVAL '1 day',
                CURRENT_TIMESTAMP + INTERVAL '1 hour',
                %s, 'pytest', 'queue-v2-test-loopback'
            ) RETURNING id
            """,
            (
                f"ieo_future_{key}",
                key,
                f"outbox-future-{key}",
                f"exe-outbox-future-{key}",
                f"outbox-future-{key}",
            ),
        ).fetchone()
        inbox = connection.execute(
            """
            INSERT INTO webhook_inbox (
                provider, event_family, route, idempotency_key,
                execution_id, lane, available_at, ordering_key,
                fairness_key, policy_version
            ) VALUES (
                'pytest', 'test', '/tests/pr3-freeze', %s, %s,
                'webhook_inbox', CURRENT_TIMESTAMP, %s, 'pytest', 'queue-v2-test-loopback'
            ) RETURNING id
            """,
            (f"inbox-{key}", f"exe-inbox-{key}", f"inbox-{key}"),
        ).fetchone()
        future_inbox = connection.execute(
            """
            INSERT INTO webhook_inbox (
                provider, event_family, route, idempotency_key,
                execution_id, lane, available_at, received_at, ordering_key,
                fairness_key, policy_version
            ) VALUES (
                'pytest', 'test', '/tests/pr3-freeze-future', %s, %s,
                'webhook_inbox', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                CURRENT_TIMESTAMP + INTERVAL '1 hour', %s, 'pytest', 'queue-v2-test-loopback'
            ) RETURNING id
            """,
            (f"inbox-future-{key}", f"exe-inbox-future-{key}", f"inbox-future-{key}"),
        ).fetchone()

    activation = RuntimeGenerationRepository(_database_url()).activate_generation(
        expected_generation=0,
        target_generation=74,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("internal_general", "internal_financial", "webhook_inbox", "wecom_media"),
        actor="pytest",
        reason="freeze all four durable queue facts",
    )

    assert activation.freeze is not None
    assert dict(activation.freeze.counts) == {
        "external_effect": 1,
        "internal_event_consumer": 1,
        "internal_event_outbox": 1,
        "webhook_inbox": 1,
    }
    with _connect() as connection:
        classifications = connection.execute(
            """
            SELECT queue_kind, classification, evidence_json ->> 'cutoff_at' AS cutoff_at
            FROM queue_history_classification
            WHERE freeze_revision = 'pr3_generation_74'
            ORDER BY queue_kind
            """
        ).fetchall()
        held = {
            "external": connection.execute(
                "SELECT hold_reason FROM external_effect_job WHERE id = %s",
                (external["id"],),
            ).fetchone()["hold_reason"],
            "consumer": connection.execute(
                "SELECT hold_reason FROM internal_event_consumer_run WHERE id = %s",
                (consumer["id"],),
            ).fetchone()["hold_reason"],
            "outbox": connection.execute(
                "SELECT hold_reason FROM internal_event_outbox WHERE id = %s",
                (outbox["id"],),
            ).fetchone()["hold_reason"],
            "future_outbox": connection.execute(
                "SELECT hold_reason FROM internal_event_outbox WHERE id = %s",
                (future_outbox["id"],),
            ).fetchone()["hold_reason"],
            "inbox": connection.execute(
                "SELECT hold_reason FROM webhook_inbox WHERE id = %s",
                (inbox["id"],),
            ).fetchone()["hold_reason"],
            "future_inbox": connection.execute(
                "SELECT hold_reason FROM webhook_inbox WHERE id = %s",
                (future_inbox["id"],),
            ).fetchone()["hold_reason"],
        }
    assert [row["queue_kind"] for row in classifications] == [
        "external_effect",
        "internal_event_consumer",
        "internal_event_outbox",
        "webhook_inbox",
    ]
    assert {row["classification"] for row in classifications} == {"safe_pre_provider"}
    assert len({row["cutoff_at"] for row in classifications}) == 1
    assert held == {
        "external": "history_frozen_at_pr3_generation_74",
        "consumer": "history_frozen_at_pr3_generation_74",
        "outbox": "history_frozen_at_pr3_generation_74",
        "future_outbox": "",
        "inbox": "history_frozen_at_pr3_generation_74",
        "future_inbox": "",
    }


def test_generation_cutover_freeze_rolls_back_with_failed_final_cas_precondition() -> None:
    with _connect() as connection:
        job = connection.execute(
            """
            INSERT INTO external_effect_job (
                effect_type, adapter_name, operation, target_type, target_id,
                idempotency_key, execution_mode, status, lane, available_at,
                ordering_key, fairness_key, rate_scope_key, policy_version,
                execution_id
            ) VALUES (
                'wecom.media.upload', 'wecom', 'upload', 'media', 'rollback',
                'pr3-freeze-rollback', 'real', 'queued', 'wecom_media',
                CURRENT_TIMESTAMP, 'pr3-rollback', 'pr3-freeze',
                'wecom:test:media', 'queue-v2-test-loopback', 'exe-pr3-freeze-rollback'
            ) RETURNING id
            """
        ).fetchone()
        connection.execute(
            "UPDATE queue_lane_policy SET enabled = FALSE WHERE lane = 'wecom_media'"
        )

    with pytest.raises(GenerationCASConflict, match="disabled"):
        RuntimeGenerationRepository(_database_url()).activate_generation(
            expected_generation=0,
            target_generation=73,
            expected_policy_version="queue-v2-test-loopback",
            lanes=("wecom_media",),
            actor="pytest",
            reason="prove freeze and CAS rollback together",
        )

    with _connect() as connection:
        persisted = connection.execute(
            "SELECT hold_reason FROM external_effect_job WHERE id = %s",
            (job["id"],),
        ).fetchone()
        audit = connection.execute(
            "SELECT COUNT(*)::BIGINT AS count FROM queue_history_classification WHERE freeze_revision = 'pr3_generation_73'"
        ).fetchone()
        control = connection.execute(
            "SELECT active_generation, claim_enabled FROM queue_runtime_control WHERE singleton = TRUE"
        ).fetchone()
    assert persisted["hold_reason"] == ""
    assert audit["count"] == 0
    assert control == {"active_generation": 0, "claim_enabled": False}


def test_command_cas_makes_target_due_writes_audit_intent_and_notifies_lane() -> None:
    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=1)
    job = ExternalEffectService().plan_effect(
        effect_type="test.queue.command",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"queue-command-{uuid4().hex}",
        scheduled_at=scheduled_at,
        lane="wecom_interactive",
        ordering_key=f"order-{uuid4().hex}",
        fairness_key="pytest",
        rate_scope_key=f"scope-{uuid4().hex}",
    )
    service = QueueRuntimeCommandService()
    before = service.read_target("external_effect", int(job["id"]))
    assert before is not None
    listener = PostgresQueueWakeListener(_database_url())
    listener.connect()
    try:
        result = service.request_immediate_execution(
            "external_effect",
            int(job["id"]),
            expected_status=before.status,
            expected_version=before.version_token,
            actor="pytest",
            reason="operator requested immediate execution",
            command_id=f"pytest-{uuid4().hex}",
        )
        hint = listener.wait(timeout_seconds=1.0)
    finally:
        listener.close()

    assert result.target.lane == "wecom_interactive"
    assert result.target.version_token != before.version_token
    assert result.notification_payload == {
        "queue_kind": "external_effect",
        "lane": "wecom_interactive",
    }
    assert result.intent_id.startswith("ieo_")
    assert hint is not None
    assert hint.queue_kind == "external_effect"
    assert hint.lane == "wecom_interactive"
    with _connect() as connection:
        persisted = connection.execute(
            "SELECT available_at, hold_reason FROM external_effect_job WHERE id = %s",
            (int(job["id"]),),
        ).fetchone()
        audit = connection.execute(
            "SELECT event_type, actor_id, payload_json FROM internal_event_outbox WHERE outbox_id = %s",
            (result.intent_id,),
        ).fetchone()
    assert persisted["available_at"] < scheduled_at
    assert persisted["hold_reason"] == ""
    assert audit["event_type"] == QUEUE_RUNTIME_COMMAND_APPLIED
    assert audit["actor_id"] == "pytest"
    assert audit["payload_json"]["reason"] == "operator requested immediate execution"


def test_command_rejects_stale_version_and_held_history_without_signal() -> None:
    job = ExternalEffectService().plan_effect(
        effect_type="test.queue.command.conflict",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"queue-command-conflict-{uuid4().hex}",
        lane="wecom_interactive",
    )
    service = QueueRuntimeCommandService()
    target = service.read_target("external_effect", int(job["id"]))
    assert target is not None
    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET hold_reason = 'history_frozen' WHERE id = %s",
            (int(job["id"]),),
        )

    with pytest.raises(QueueCommandConflict):
        service.request_immediate_execution(
            "external_effect",
            int(job["id"]),
            expected_status=target.status,
            expected_version=target.version_token,
            actor="pytest",
            reason="must remain held",
        )


@pytest.mark.parametrize(
    ("queue_kind", "status", "expected_lane"),
    (
        ("internal_event", "pending", "internal_general"),
        ("internal_outbox", "pending", "internal_financial"),
        ("webhook_inbox", "received", "webhook_inbox"),
    ),
)
def test_command_cas_supports_each_non_external_durable_queue_fact(
    queue_kind: str,
    status: str,
    expected_lane: str,
) -> None:
    key = uuid4().hex
    with _connect() as connection:
        if queue_kind == "internal_event":
            event_id = f"iev_{key}"
            connection.execute(
                """
                INSERT INTO internal_event (
                    event_id, event_type, aggregate_type, aggregate_id,
                    idempotency_key, execution_id
                ) VALUES (%s, 'test.queue.command', 'test', %s, %s, %s)
                """,
                (event_id, key, f"event-{key}", f"exe-event-{key}"),
            )
            row = connection.execute(
                """
                INSERT INTO internal_event_consumer_run (
                    event_id, consumer_name, status, execution_id,
                    parent_execution_id, lane, available_at,
                    ordering_key, fairness_key, policy_version
                ) VALUES (
                    %s, 'pytest_consumer', 'pending', %s, %s,
                    'internal_general', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                    %s, 'pytest', 'queue-v2-test-loopback'
                ) RETURNING id
                """,
                (event_id, f"exe-run-{key}", f"exe-event-{key}", f"order-{key}"),
            ).fetchone()
        elif queue_kind == "internal_outbox":
            row = connection.execute(
                """
                INSERT INTO internal_event_outbox (
                    outbox_id, event_type, aggregate_type, aggregate_id,
                    idempotency_key, execution_id, lane, available_at,
                    ordering_key, fairness_key, policy_version
                ) VALUES (
                    %s, 'test.queue.command', 'test', %s, %s, %s,
                    'internal_financial', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                    %s, 'pytest', 'queue-v2-test-loopback'
                ) RETURNING id
                """,
                (f"ieo_{key}", key, f"outbox-{key}", f"exe-outbox-{key}", f"order-{key}"),
            ).fetchone()
        else:
            row = connection.execute(
                """
                INSERT INTO webhook_inbox (
                    provider, event_family, route, idempotency_key,
                    execution_id, lane, available_at, ordering_key,
                    fairness_key, policy_version
                ) VALUES (
                    'pytest', 'test', '/tests/queue-command', %s, %s,
                    'webhook_inbox', CURRENT_TIMESTAMP + INTERVAL '1 hour',
                    %s, 'pytest', 'queue-v2-test-loopback'
                ) RETURNING id
                """,
                (f"webhook-{key}", f"exe-webhook-{key}", f"order-{key}"),
            ).fetchone()
    item_id = int(row["id"])
    service = QueueRuntimeCommandService()
    target = service.read_target(queue_kind, item_id)
    assert target is not None and target.status == status
    listener = PostgresQueueWakeListener(_database_url())
    listener.connect()
    try:
        result = service.request_immediate_execution(
            queue_kind,
            item_id,
            expected_status=status,
            expected_version=target.version_token,
            actor="pytest",
            reason=f"make {queue_kind} immediately eligible",
        )
        hint = listener.wait(timeout_seconds=1.0)
    finally:
        listener.close()

    assert result.target.lane == expected_lane
    assert result.target.version_token != target.version_token
    assert hint is not None
    assert hint.queue_kind == queue_kind
    assert hint.lane == expected_lane


def test_invariant_checker_reports_without_changing_queue_rows() -> None:
    job = ExternalEffectService().plan_effect(
        effect_type="test.queue.invariant",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"queue-invariant-{uuid4().hex}",
        lane="wecom_interactive",
    )
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching', lease_token = '', lease_expires_at = NULL,
                worker_generation = 0
            WHERE id = %s
            """,
            (int(job["id"]),),
        )
        before = connection.execute(
            "SELECT status, lease_token, lease_expires_at, worker_generation, updated_at FROM external_effect_job WHERE id = %s",
            (int(job["id"]),),
        ).fetchone()

    report = QueueRuntimeInvariantChecker(_database_url()).check()

    with _connect() as connection:
        after = connection.execute(
            "SELECT status, lease_token, lease_expires_at, worker_generation, updated_at FROM external_effect_job WHERE id = %s",
            (int(job["id"]),),
        ).fetchone()
    assert report.read_only is True
    assert any(item.code == "active_lease_incomplete" for item in report.violations)
    assert dict(after) == dict(before)


def test_invariant_checker_rejects_scope_change_without_matching_policy_snapshot() -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE queue_runtime_control SET external_claim_scope = 'allowlisted' WHERE singleton = TRUE"
        )

    report = QueueRuntimeInvariantChecker(_database_url()).check()

    assert any(item.code == "runtime_control_invalid" for item in report.violations)


def test_invariant_checker_reports_unheld_open_policy_mismatch() -> None:
    job = ExternalEffectService().plan_effect(
        effect_type="test.queue.policy-invariant",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"queue-policy-invariant-{uuid4().hex}",
        lane="wecom_interactive",
    )
    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET policy_version = 'stale-policy' WHERE id = %s",
            (int(job["id"]),),
        )

    report = QueueRuntimeInvariantChecker(_database_url()).check()

    mismatch = [item for item in report.violations if item.code == "open_item_policy_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].count == 1
    assert mismatch[0].dimensions == {
        "queue_kind": "external_effect",
        "policy_version": "stale-policy",
    }


def test_scope_transition_requires_closed_drained_gate_and_audits_snapshot_cas() -> None:
    repository = RuntimeGenerationRepository(_database_url())
    target_policy_version = f"queue-v2-allowlisted-{uuid4().hex[:10]}"
    repository.activate_generation(
        expected_generation=0,
        target_generation=78,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive", "wecom_bulk", "wecom_media"),
        actor="pytest",
        reason="activate loopback before allowlisted canary",
    )

    with pytest.raises(GenerationCASConflict):
        repository.transition_external_claim_scope(
            expected_generation=78,
            expected_policy_version="queue-v2-test-loopback",
            target_policy_version=target_policy_version,
            expected_scope="test_loopback",
            target_scope="allowlisted",
            actor="pytest",
            reason="claim gate is still open",
        )

    repository.disable_claims(
        expected_generation=78,
        actor="pytest",
        reason="drain before allowlisted canary",
    )
    transitioned = repository.transition_external_claim_scope(
        expected_generation=78,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy_version,
        expected_scope="test_loopback",
        target_scope="allowlisted",
        actor="pytest",
        reason="explicit allowlisted canary transition",
    )
    resumed = repository.resume_claims(
        expected_generation=78,
        expected_policy_version=target_policy_version,
        expected_scope="allowlisted",
        actor="pytest",
        reason="resume only after worker restart verification",
    )

    assert transitioned.claim_enabled is False
    assert transitioned.external_claim_scope == "allowlisted"
    assert resumed.claim_enabled is True
    assert resumed.external_claim_scope == "allowlisted"
    with _connect() as connection:
        snapshot = connection.execute(
            """
            SELECT policy_json
            FROM queue_policy_snapshot
            WHERE policy_version = %s
            """,
            (target_policy_version,),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT active_generation, from_policy_version, to_policy_version,
                   from_scope, to_scope, actor, reason,
                   policy_json_before, policy_json_after
            FROM queue_runtime_scope_transition_audit
            WHERE to_policy_version = %s
            """,
            (target_policy_version,),
        ).fetchone()
    assert snapshot["policy_json"]["external_claim_scope"] == "allowlisted"
    assert audit["active_generation"] == 78
    assert audit["from_policy_version"] == "queue-v2-test-loopback"
    assert audit["to_policy_version"] == target_policy_version
    assert audit["from_scope"] == "test_loopback"
    assert audit["to_scope"] == "allowlisted"
    assert audit["actor"] == "pytest"
    assert audit["policy_json_before"]["external_claim_scope"] == "test_loopback"
    assert audit["policy_json_after"]["external_claim_scope"] == "allowlisted"
    assert not any(
        item.code == "runtime_control_invalid"
        for item in QueueRuntimeInvariantChecker(_database_url()).check().violations
    )


def test_scope_transition_refuses_any_dispatching_external_effect() -> None:
    repository = RuntimeGenerationRepository(_database_url())
    target_policy_version = f"queue-v2-allowlisted-{uuid4().hex[:10]}"
    repository.activate_generation(
        expected_generation=0,
        target_generation=79,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate before drain conflict",
    )
    repository.disable_claims(
        expected_generation=79,
        actor="pytest",
        reason="close claim gate",
    )
    job = ExternalEffectService().plan_effect(
        effect_type="test.queue.scope-drain",
        adapter_name="test_provider",
        operation="send",
        target_type="test",
        target_id=uuid4().hex,
        payload={"execution_scope": "test_loopback"},
        idempotency_key=f"scope-drain-{uuid4().hex}",
        lane="wecom_interactive",
    )
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching', lease_token = 'active-scope-test',
                lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes'
            WHERE id = %s
            """,
            (int(job["id"]),),
        )

    with pytest.raises(GenerationCASConflict, match="not drained"):
        repository.transition_external_claim_scope(
            expected_generation=79,
            expected_policy_version="queue-v2-test-loopback",
            target_policy_version=target_policy_version,
            expected_scope="test_loopback",
            target_scope="allowlisted",
            actor="pytest",
            reason="must fail closed while one dispatch is active",
        )

    assert repository.read_state().external_claim_scope == "test_loopback"


def test_invariant_checker_reports_missing_active_generation_heartbeats() -> None:
    RuntimeGenerationRepository(_database_url()).activate_generation(
        expected_generation=0,
        target_generation=77,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("internal_general",),
        actor="pytest",
        reason="heartbeat invariant test",
    )

    report = QueueRuntimeInvariantChecker(_database_url()).check()

    missing = {
        item.dimensions.get("queue_kind")
        for item in report.violations
        if item.code == "missing_active_worker_heartbeat"
    }
    assert missing == {
        "aicrm-internal_event-runtime",
        "aicrm-internal_outbox-runtime",
        "aicrm-webhook_inbox-runtime",
        "aicrm-external_effect-runtime",
    }

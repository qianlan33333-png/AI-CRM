from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.data_health import checks as data_health_checks
from aicrm_next.crm.identity_contact.resolution_queue_port import build_identity_resolution_queue_port
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
from scripts.ops import recover_all_scope_contact_detail


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect(*, autocommit: bool = True):
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


def _insert_deferred_identity_effect(
    connection,
    suffix: str,
    *,
    source_route: str = "channel_entry.identity_resolution.enqueue",
    source_module: str = "aicrm_next.crm.identity_contact.resolution_effects",
    adapter_name: str = "wecom_external_contact_detail",
    provider_boundary_crossed: bool = False,
    extra_attempt: bool = False,
    with_runtime: bool = False,
    attempt_adapter_mode: str = "disabled",
) -> tuple[int, int]:
    job = connection.execute(
        """
        INSERT INTO external_effect_job (
            effect_type, adapter_name, operation, target_type, target_id,
            business_type, business_id, source_module, source_route,
            idempotency_key, execution_mode, status, attempt_count, max_attempts,
            last_error_code, last_error_message, side_effect_executed,
            provider_result_received, provider_call_started_at,
            reconciliation_required, worker_generation, policy_version,
            execution_id, lane, available_at, ordering_key, fairness_key,
            rate_scope_key, completed_at
        ) VALUES (
            'wecom.external_contact.detail.fetch', %s, 'get_external_contact_detail',
            'external_user', %s, 'identity_resolution_queue', '', %s, %s,
            %s, 'execute', 'blocked', 1, 5,
            'effect_type_not_allowed', 'blocked by generation-0 test policy',
            %s, %s,
            CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
            FALSE, 0, 'queue-v2-test-loopback', %s,
            'wecom_interactive', CURRENT_TIMESTAMP, %s, 'test-corp',
            'wecom:test-corp:external_contact_detail', CURRENT_TIMESTAMP
        )
        RETURNING id
        """,
        (
            adapter_name,
            f"external-{suffix}",
            source_module,
            source_route,
            f"deferred-identity-{suffix}",
            provider_boundary_crossed,
            provider_boundary_crossed,
            provider_boundary_crossed,
            f"exe-deferred-identity-{suffix}",
            f"external_user:external-{suffix}",
        ),
    ).fetchone()
    job_id = int(job["id"])
    queue = connection.execute(
        """
        INSERT INTO crm_user_identity_resolution_queue (
            source_type, source_key, source_table, source_id, corp_id,
            external_userid, reason, status, last_error, next_attempt_at,
            execution_id, external_effect_job_id, hold_reason, held_at
        ) VALUES (
            'channel_entry', %s, 'wecom_external_contact_event_logs', %s,
            'test-corp', %s, 'missing_unionid', 'held',
            'effect_type_not_allowed', NULL, %s, %s,
            'effect_type_not_allowed', CURRENT_TIMESTAMP
        )
        RETURNING id
        """,
        (
            f"source-{suffix}",
            suffix,
            f"external-{suffix}",
            f"exe-identity-intent-{suffix}",
            job_id,
        ),
    ).fetchone()
    queue_id = int(queue["id"])
    connection.execute(
        "UPDATE external_effect_job SET business_id = %s WHERE id = %s",
        (str(queue_id), job_id),
    )
    attempt_id = f"eea-deferred-{suffix}"
    connection.execute(
        """
        INSERT INTO external_effect_attempt (
            attempt_id, job_id, adapter_name, adapter_mode, operation,
            status, error_code, error_message, provider_call_started_at,
            worker_generation, completed_at
        ) VALUES (
            %s, %s, %s, %s, 'get_external_contact_detail',
            'blocked', 'effect_type_not_allowed', 'blocked before provider',
            CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
            0, CURRENT_TIMESTAMP
        )
        """,
        (attempt_id, job_id, adapter_name, attempt_adapter_mode, provider_boundary_crossed),
    )
    connection.execute(
        "UPDATE external_effect_job SET last_attempt_id = %s WHERE id = %s",
        (attempt_id, job_id),
    )
    if extra_attempt:
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, error_message, worker_generation, completed_at
            ) VALUES (
                %s, %s, %s, %s, 'get_external_contact_detail',
                'blocked', 'effect_type_not_allowed', 'unexpected second attempt',
                0, CURRENT_TIMESTAMP
            )
            """,
            (f"eea-deferred-{suffix}-second", job_id, adapter_name, attempt_adapter_mode),
        )
    if with_runtime:
        connection.execute(
            """
            INSERT INTO automation_channel_entry_runtime (
                corp_id, scene_value, external_userid, follow_user_userid,
                identity_status, identity_last_error,
                identity_external_effect_job_id, identity_hold_reason,
                identity_held_at
            ) VALUES (
                'test-corp', %s, %s, 'owner-test', 'held',
                'effect_type_not_allowed', %s, 'effect_type_not_allowed',
                CURRENT_TIMESTAMP
            )
            """,
            (f"scene-{suffix}", f"external-{suffix}", job_id),
        )
    return job_id, queue_id


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


def test_scope_transition_can_enter_all_scope_and_resume_execute_mode() -> None:
    repository = RuntimeGenerationRepository(_database_url())
    target_policy_version = f"queue-v2-production-all-{uuid4().hex[:10]}"
    repository.activate_generation(
        expected_generation=0,
        target_generation=81,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive", "outbound_webhook"),
        actor="pytest",
        reason="activate before production all scope",
    )
    repository.disable_claims(
        expected_generation=81,
        actor="pytest",
        reason="drain before production all scope",
    )

    transitioned = repository.transition_external_claim_scope(
        expected_generation=81,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy_version,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="explicit production all scope transition",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )
    resumed = repository.resume_claims(
        expected_generation=81,
        expected_policy_version=target_policy_version,
        expected_scope="all",
        actor="pytest",
        reason="resume after production listener verification",
    )

    assert transitioned.claim_enabled is False
    assert transitioned.external_claim_scope == "all"
    assert resumed.claim_enabled is True
    assert resumed.rollout_mode == "execute"
    assert resumed.external_claim_scope == "all"


def test_all_scope_transition_adopts_only_pre_provider_identity_effect_and_preserves_attempt() -> None:
    repository = RuntimeGenerationRepository(_database_url())
    repository.activate_generation(
        expected_generation=0,
        target_generation=82,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate before deferred identity adoption",
    )
    repository.disable_claims(
        expected_generation=82,
        actor="pytest",
        reason="drain before deferred identity adoption",
    )
    with _connect() as connection:
        job_id, queue_id = _insert_deferred_identity_effect(
            connection,
            uuid4().hex,
            with_runtime=True,
        )

    target_policy_version = f"queue-v2-production-all-{uuid4().hex[:10]}"
    repository.transition_external_claim_scope(
        expected_generation=82,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy_version,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="adopt exact pre-provider identity effect",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )

    with _connect() as connection:
        job = connection.execute(
            """
            SELECT status, attempt_count, worker_generation, policy_version,
                   provider_call_started_at, side_effect_executed,
                   provider_result_received, last_error_code, completed_at,
                   result_summary_json
            FROM external_effect_job WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        queue = connection.execute(
            """
            SELECT status, hold_reason, last_error, held_at, next_attempt_at
            FROM crm_user_identity_resolution_queue WHERE id = %s
            """,
            (queue_id,),
        ).fetchone()
        runtime = connection.execute(
            """
            SELECT identity_status, identity_hold_reason, identity_last_error,
                   identity_held_at, identity_next_attempt_at
            FROM automation_channel_entry_runtime
            WHERE identity_external_effect_job_id = %s
            """,
            (job_id,),
        ).fetchone()
        attempts = connection.execute(
            """
            SELECT status, error_code, provider_call_started_at, worker_generation
            FROM external_effect_attempt WHERE job_id = %s ORDER BY id
            """,
            (job_id,),
        ).fetchall()
        audit = connection.execute(
            """
            SELECT policy_json_after
            FROM queue_runtime_scope_transition_audit
            WHERE to_policy_version = %s
            """,
            (target_policy_version,),
        ).fetchone()

    assert job["status"] == "queued"
    assert job["attempt_count"] == 1
    assert job["worker_generation"] == 82
    assert job["policy_version"] == target_policy_version
    assert job["provider_call_started_at"] is None
    assert job["side_effect_executed"] is False
    assert job["provider_result_received"] is False
    assert job["last_error_code"] == ""
    assert job["completed_at"] is None
    assert job["result_summary_json"]["pre_provider_attempt_preserved"] is True
    assert queue["status"] == "pending"
    assert queue["hold_reason"] == queue["last_error"] == ""
    assert queue["held_at"] is None
    assert queue["next_attempt_at"] is not None
    assert runtime["identity_status"] == "pending"
    assert runtime["identity_hold_reason"] == runtime["identity_last_error"] == ""
    assert runtime["identity_held_at"] is None
    assert runtime["identity_next_attempt_at"] is not None
    assert len(attempts) == 1
    assert attempts[0]["status"] == "blocked"
    assert attempts[0]["error_code"] == "effect_type_not_allowed"
    assert attempts[0]["provider_call_started_at"] is None
    assert attempts[0]["worker_generation"] == 0
    assert audit["policy_json_after"]["pre_provider_identity_adoption"] == {
        "eligible_count": 1,
        "adopted_job_count": 1,
        "adopted_queue_count": 1,
        "adopted_runtime_count": 1,
        "predicate_version": "identity_contact_detail_test_policy_v2",
    }


def test_all_scope_transition_never_adopts_ambiguous_or_wrong_provenance_identity_effects() -> None:
    repository = RuntimeGenerationRepository(_database_url())
    repository.activate_generation(
        expected_generation=0,
        target_generation=83,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate before fail-closed adoption cases",
    )
    repository.disable_claims(
        expected_generation=83,
        actor="pytest",
        reason="drain before fail-closed adoption cases",
    )
    with _connect() as connection:
        provider_job, _ = _insert_deferred_identity_effect(
            connection,
            "provider-" + uuid4().hex,
            provider_boundary_crossed=True,
        )
        wrong_route_job, _ = _insert_deferred_identity_effect(
            connection,
            "route-" + uuid4().hex,
            source_route="unreviewed.identity.enqueue",
        )
        wrong_adapter_job, _ = _insert_deferred_identity_effect(
            connection,
            "adapter-" + uuid4().hex,
            adapter_name="unreviewed_adapter",
        )
        duplicate_attempt_job, _ = _insert_deferred_identity_effect(
            connection,
            "attempt-" + uuid4().hex,
            extra_attempt=True,
        )
        wrong_attempt_mode_job, _ = _insert_deferred_identity_effect(
            connection,
            "attempt-mode-" + uuid4().hex,
            attempt_adapter_mode="execute",
        )

    target_policy_version = f"queue-v2-production-all-{uuid4().hex[:10]}"
    repository.transition_external_claim_scope(
        expected_generation=83,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy_version,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="prove ambiguous identity effects remain terminal",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )

    with _connect() as connection:
        jobs = connection.execute(
            """
            SELECT id, status, worker_generation, policy_version
            FROM external_effect_job WHERE id = ANY(%s) ORDER BY id
            """,
            ([provider_job, wrong_route_job, wrong_adapter_job, duplicate_attempt_job, wrong_attempt_mode_job],),
        ).fetchall()
        audit = connection.execute(
            """
            SELECT policy_json_after
            FROM queue_runtime_scope_transition_audit
            WHERE to_policy_version = %s
            """,
            (target_policy_version,),
        ).fetchone()

    assert {row["status"] for row in jobs} == {"blocked"}
    assert {row["worker_generation"] for row in jobs} == {0}
    assert {row["policy_version"] for row in jobs} == {target_policy_version}
    assert audit["policy_json_after"]["pre_provider_identity_adoption"] == {
        "eligible_count": 0,
        "adopted_job_count": 0,
        "adopted_queue_count": 0,
        "adopted_runtime_count": 0,
        "predicate_version": "identity_contact_detail_test_policy_v2",
    }


def _simulate_post_cutover_contact_detail_gate_block(
    connection,
    *,
    job_id: int,
    queue_id: int,
    provider_boundary_crossed: bool = False,
) -> None:
    second_attempt_id = f"eea-post-cutover-{job_id}"
    connection.execute(
        """
        INSERT INTO external_effect_attempt (
            attempt_id, job_id, adapter_name, adapter_mode, operation,
            status, error_code, error_message, provider_call_started_at,
            worker_generation, completed_at
        ) VALUES (
            %s, %s, 'wecom_external_contact_detail', 'disabled',
            'get_external_contact_detail', 'blocked', 'effect_type_not_allowed',
            'typed effect type missing before provider',
            CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
            0, CURRENT_TIMESTAMP
        )
        """,
        (second_attempt_id, job_id, provider_boundary_crossed),
    )
    connection.execute(
        """
        UPDATE external_effect_job
        SET status = 'blocked',
            attempt_count = 2,
            last_attempt_id = %s,
            last_error_code = 'effect_type_not_allowed',
            last_error_message = 'typed effect type missing before provider',
            provider_call_started_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
            side_effect_executed = %s,
            provider_result_received = %s,
            completed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (
            second_attempt_id,
            provider_boundary_crossed,
            provider_boundary_crossed,
            provider_boundary_crossed,
            job_id,
        ),
    )
    connection.execute(
        """
        UPDATE crm_user_identity_resolution_queue
        SET status = 'held', hold_reason = 'effect_type_not_allowed',
            last_error = 'effect_type_not_allowed', held_at = CURRENT_TIMESTAMP,
            next_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (queue_id,),
    )
    connection.execute(
        """
        UPDATE automation_channel_entry_runtime
        SET identity_status = 'held',
            identity_hold_reason = 'effect_type_not_allowed',
            identity_last_error = 'effect_type_not_allowed',
            identity_held_at = CURRENT_TIMESTAMP,
            identity_next_attempt_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE identity_external_effect_job_id = %s
        """,
        (job_id,),
    )


def test_post_cutover_contact_detail_recovery_reopens_exact_one_and_two_attempt_history() -> None:
    target_policy = f"queue-v2-production-all-{uuid4().hex[:10]}"
    repository = RuntimeGenerationRepository(_database_url())
    repository.activate_generation(
        expected_generation=0,
        target_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate exact production generation",
    )
    repository.disable_claims(
        expected_generation=1,
        actor="pytest",
        reason="drain before all scope",
    )
    with _connect() as connection:
        job_id, queue_id = _insert_deferred_identity_effect(
            connection,
            "post-cutover-recovery",
            with_runtime=True,
        )
    repository.transition_external_claim_scope(
        expected_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="adopt before typed gate block",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )
    with _connect() as connection:
        _simulate_post_cutover_contact_detail_gate_block(
            connection,
            job_id=job_id,
            queue_id=queue_id,
        )
        fresh_job_id, fresh_queue_id = _insert_deferred_identity_effect(
            connection,
            "post-cutover-recovery-fresh-generation-one",
            with_runtime=True,
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET worker_generation = 1, policy_version = %s
            WHERE id = %s
            """,
            (target_policy, fresh_job_id),
        )
        connection.execute(
            """
            UPDATE external_effect_attempt
            SET worker_generation = 1
            WHERE job_id = %s
            """,
            (fresh_job_id,),
        )
    repository.resume_claims(
        expected_generation=1,
        expected_policy_version=target_policy,
        expected_scope="all",
        actor="pytest",
        reason="resume exact all scope",
    )

    with recover_all_scope_contact_detail.get_engine().begin() as connection:
        rows = recover_all_scope_contact_detail._candidate_rows(
            connection,
            policy_version=target_policy,
        )
        assert [int(row["id"]) for row in rows] == [job_id, fresh_job_id]
        assert {
            int(row["id"]): int(row["pre_provider_attempt_count"])
            for row in rows
        } == {job_id: 2, fresh_job_id: 1}
        counts = recover_all_scope_contact_detail._reopen_candidates(
            connection,
            rows,
            policy_version=target_policy,
        )

    assert counts == {"job_count": 2, "queue_count": 2, "runtime_count": 2}
    with _connect() as connection:
        job = connection.execute(
            """
            SELECT status, attempt_count, worker_generation, policy_version,
                   provider_call_started_at, side_effect_executed,
                   provider_result_received, last_error_code
            FROM external_effect_job WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        queue = connection.execute(
            "SELECT status, hold_reason, last_error FROM crm_user_identity_resolution_queue WHERE id = %s",
            (queue_id,),
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM external_effect_attempt WHERE job_id = %s",
            (job_id,),
        ).fetchone()["count"]
        fresh_job = connection.execute(
            """
            SELECT status, attempt_count, worker_generation, policy_version,
                   provider_call_started_at, side_effect_executed,
                   provider_result_received, last_error_code
            FROM external_effect_job WHERE id = %s
            """,
            (fresh_job_id,),
        ).fetchone()
        fresh_queue = connection.execute(
            "SELECT status, hold_reason, last_error FROM crm_user_identity_resolution_queue WHERE id = %s",
            (fresh_queue_id,),
        ).fetchone()

    assert job["status"] == "queued"
    assert job["attempt_count"] == 2
    assert job["worker_generation"] == 1
    assert job["policy_version"] == target_policy
    assert job["provider_call_started_at"] is None
    assert job["side_effect_executed"] is False
    assert job["provider_result_received"] is False
    assert job["last_error_code"] == ""
    assert queue == {"status": "pending", "hold_reason": "", "last_error": ""}
    assert attempt_count == 2
    assert fresh_job["status"] == "queued"
    assert fresh_job["attempt_count"] == 1
    assert fresh_job["worker_generation"] == 1
    assert fresh_job["policy_version"] == target_policy
    assert fresh_job["provider_call_started_at"] is None
    assert fresh_job["side_effect_executed"] is False
    assert fresh_job["provider_result_received"] is False
    assert fresh_job["last_error_code"] == ""
    assert fresh_queue == {"status": "pending", "hold_reason": "", "last_error": ""}


def test_post_cutover_contact_detail_recovery_rejects_any_provider_boundary() -> None:
    target_policy = f"queue-v2-production-all-{uuid4().hex[:10]}"
    repository = RuntimeGenerationRepository(_database_url())
    repository.activate_generation(
        expected_generation=0,
        target_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate exact production generation",
    )
    repository.disable_claims(
        expected_generation=1,
        actor="pytest",
        reason="drain before all scope",
    )
    with _connect() as connection:
        job_id, queue_id = _insert_deferred_identity_effect(
            connection,
            "post-cutover-provider-boundary",
            with_runtime=True,
        )
    repository.transition_external_claim_scope(
        expected_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="adopt before ambiguous provider block",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )
    with _connect() as connection:
        _simulate_post_cutover_contact_detail_gate_block(
            connection,
            job_id=job_id,
            queue_id=queue_id,
            provider_boundary_crossed=True,
        )
    repository.resume_claims(
        expected_generation=1,
        expected_policy_version=target_policy,
        expected_scope="all",
        actor="pytest",
        reason="resume exact all scope",
    )

    with recover_all_scope_contact_detail.get_engine().begin() as connection:
        rows = recover_all_scope_contact_detail._candidate_rows(
            connection,
            policy_version=target_policy,
        )

    assert rows == []
    with _connect() as connection:
        job = connection.execute(
            "SELECT status, attempt_count, provider_call_started_at FROM external_effect_job WHERE id = %s",
            (job_id,),
        ).fetchone()
    assert job["status"] == "blocked"
    assert job["attempt_count"] == 2
    assert job["provider_call_started_at"] is not None


def test_data_health_excludes_only_exact_post_cutover_recovery_candidate() -> None:
    target_policy = "queue-v2-production-all-g1"
    repository = RuntimeGenerationRepository(_database_url())
    repository.activate_generation(
        expected_generation=0,
        target_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("wecom_interactive",),
        actor="pytest",
        reason="activate exact production generation for health",
    )
    repository.disable_claims(expected_generation=1, actor="pytest", reason="drain for health")
    with _connect() as connection:
        safe_job_id, safe_queue_id = _insert_deferred_identity_effect(
            connection, "post-cutover-health-safe", with_runtime=True
        )
        unsafe_job_id, unsafe_queue_id = _insert_deferred_identity_effect(
            connection, "post-cutover-health-unsafe", with_runtime=True
        )
    repository.transition_external_claim_scope(
        expected_generation=1,
        expected_policy_version="queue-v2-test-loopback",
        target_policy_version=target_policy,
        expected_scope="test_loopback",
        target_scope="all",
        actor="pytest",
        reason="adopt exact health candidates",
        identity_queue_reopen=build_identity_resolution_queue_port().reopen_pre_provider_dbapi,
    )
    with _connect() as connection:
        _simulate_post_cutover_contact_detail_gate_block(
            connection, job_id=safe_job_id, queue_id=safe_queue_id
        )
    repository.resume_claims(
        expected_generation=1,
        expected_policy_version=target_policy,
        expected_scope="all",
        actor="pytest",
        reason="resume exact health state",
    )

    safe_health = data_health_checks._external_effect_failed_retryable_backlog()
    assert safe_health.status == "ok"
    assert safe_health.evidence["blocked_count"] == 0
    assert safe_health.evidence["post_cutover_identity_recovery"]["eligible_count"] == 1

    with _connect() as connection:
        _simulate_post_cutover_contact_detail_gate_block(
            connection,
            job_id=unsafe_job_id,
            queue_id=unsafe_queue_id,
            provider_boundary_crossed=True,
        )

    unsafe_health = data_health_checks._external_effect_failed_retryable_backlog()
    assert unsafe_health.status == "fail"
    assert unsafe_health.evidence["blocked_count"] == 1
    assert unsafe_health.evidence["post_cutover_identity_recovery"]["eligible_count"] == 1


@pytest.mark.parametrize(
    "source_module",
    [
        "aicrm_next.identity_contact.resolution_effects",
        "aicrm_next.crm.identity_contact.resolution_effects",
    ],
    ids=["legacy-package", "crm-package"],
)
def test_data_health_excludes_only_strict_provider_confirmed_contact_absence(
    source_module: str,
) -> None:
    target_policy = "queue-v2-production-all-g1"
    with _connect() as connection:
        job_id, queue_id = _insert_deferred_identity_effect(
            connection,
            "provider-confirmed-contact-absence",
            source_route="message_archive.identity_resolution.enqueue",
            source_module=source_module,
            with_runtime=True,
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET worker_generation = 1, policy_version = %s
            WHERE id = %s
            """,
            (target_policy, job_id),
        )
        _simulate_post_cutover_contact_detail_gate_block(
            connection,
            job_id=job_id,
            queue_id=queue_id,
        )
        attempt_id = f"eea-contact-absence-{job_id}"
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, error_code, error_message, response_summary_json,
                provider_call_started_at, worker_generation, completed_at
            ) VALUES (
                %s, %s, 'wecom_external_contact_detail', 'execute',
                'get_external_contact_detail', 'failed_terminal',
                'wecom_error_84061', 'not external contact',
                '{"errcode":84061,"real_external_call_executed":true}'::jsonb,
                CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP
            )
            """,
            (attempt_id, job_id),
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'failed_terminal', attempt_count = 3,
                last_attempt_id = %s, last_error_code = 'wecom_error_84061',
                provider_call_started_at = CURRENT_TIMESTAMP,
                side_effect_executed = TRUE, provider_result_received = TRUE,
                completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (attempt_id, job_id),
        )

    health = data_health_checks._external_effect_failed_retryable_backlog()
    assert health.status == "ok"
    assert health.evidence["failed_terminal_count"] == 0
    assert health.evidence["external_contact_relationship_absent"]["count"] == 1
    with recover_all_scope_contact_detail.get_engine().connect() as connection:
        assert recover_all_scope_contact_detail._business_negative_rows(connection) == [job_id]
    progress = recover_all_scope_contact_detail._progress([job_id])
    assert progress["business_negative_count"] == 1
    assert progress["unsafe_terminal_count"] == 0
    assert progress["provider_boundary_attempt_count"] == 1

    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_attempt
            SET response_summary_json = '{"errcode":84062,"real_external_call_executed":true}'::jsonb
            WHERE attempt_id = %s
            """,
            (attempt_id,),
        )
    unsafe = data_health_checks._external_effect_failed_retryable_backlog()
    assert unsafe.status == "fail"
    assert unsafe.evidence["failed_terminal_count"] == 1
    assert unsafe.evidence["external_contact_relationship_absent"]["count"] == 0
    unsafe_progress = recover_all_scope_contact_detail._progress([job_id])
    assert unsafe_progress["business_negative_count"] == 0
    assert unsafe_progress["unsafe_terminal_count"] == 1


def test_invariant_checker_reports_missing_active_generation_heartbeats() -> None:
    RuntimeGenerationRepository(_database_url()).activate_generation(
        expected_generation=0,
        target_generation=77,
        expected_policy_version="queue-v2-test-loopback",
        lanes=("internal_general",),
        actor="pytest",
        reason="heartbeat invariant test",
    )
    heartbeat_repository = ExecutionRuntimeRepository(_database_url())
    for service_name, queue_kind in (
        ("aicrm-internal_event-runtime", "internal_event"),
        ("aicrm-internal_outbox-runtime", "internal_outbox"),
        ("aicrm-webhook_inbox-runtime", "webhook_inbox"),
        ("aicrm-external_effect-runtime", "external_effect"),
    ):
        heartbeat_repository.heartbeat_worker(
            service_name=service_name,
            worker_id=f"observer:{queue_kind}",
            queue_kind=queue_kind,
            generation=77,
            rollout_mode="standby",
            listener_connected=True,
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

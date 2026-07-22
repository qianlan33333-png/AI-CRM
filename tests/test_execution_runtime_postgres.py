from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.execution_runtime.listener import (
    PostgresQueueWakeListener,
)
from aicrm_next.platform_foundation.execution_runtime.read_model import (
    ExecutionRuntimeReadModel,
)
from aicrm_next.platform_foundation.execution_runtime.repository import (
    ExecutionRuntimeRepository,
)
from aicrm_next.platform_foundation.execution_runtime.metrics import lost_lease_count
from aicrm_next.platform_foundation.repository import RuntimeReadinessRepository
from aicrm_next.platform_foundation.external_effects.models import (
    WECOM_CONTACT_TAG_MARK,
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    ExternalEffectCreateRequest,
    ExternalEffectDispatchResult,
)
from aicrm_next.platform_foundation.external_effects.repo import (
    SQLAlchemyExternalEffectRepository,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.external_effects.transactional import (
    enqueue_transactional_external_effect_job,
)
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEventConsumerSpec,
    InternalEventCreateRequest,
    InternalEventOutboxRecord,
)
from aicrm_next.platform_foundation.internal_events.outbox import (
    enqueue_internal_event_outbox_in_session,
    enqueue_transactional_internal_event_outbox,
)
from aicrm_next.platform_foundation.internal_events.repository import (
    SQLAlchemyInternalEventRepository,
)
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.platform_foundation.webhook_inbox.repository import PostgresWebhookInboxRepository
from scripts.ops.manage_queue_runtime_soak import _evidence_types


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect(*, autocommit: bool = True):
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


@pytest.fixture(autouse=True)
def _reset_runtime_control() -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM queue_fairness_cursor")
        connection.execute("DELETE FROM queue_rate_scope_cooldown")
        connection.execute("DELETE FROM queue_worker_heartbeat")
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 0,
                claim_enabled = FALSE,
                rollout_mode = 'standby',
                global_max_in_flight = 20,
                policy_version = 'queue-v2-test-loopback',
                external_claim_scope = 'test_loopback'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE,
                rollout_mode = CASE WHEN lane = 'outbound_webhook' THEN 'blocked' ELSE 'standby' END,
                blocked_until = NULL,
                max_in_flight = CASE lane
                    WHEN 'internal_general' THEN 4
                    WHEN 'internal_financial' THEN 1
                    WHEN 'webhook_inbox' THEN 4
                    WHEN 'wecom_interactive' THEN 4
                    WHEN 'wecom_bulk' THEN 1
                    WHEN 'wecom_media' THEN 2
                    WHEN 'outbound_webhook' THEN 4
                    ELSE max_in_flight
                END
            """
        )


def _enable(*, generation: int = 7, global_capacity: int = 20, **lane_capacities: int) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = %s,
                claim_enabled = TRUE,
                rollout_mode = 'execute',
                global_max_in_flight = %s,
                updated_by = 'pytest',
                updated_reason = 'runtime integration test'
            WHERE singleton = TRUE
            """,
            (generation, global_capacity),
        )
        for lane, capacity in lane_capacities.items():
            connection.execute(
                """
                UPDATE queue_lane_policy
                SET max_in_flight = %s,
                    enabled = TRUE,
                    rollout_mode = 'execute',
                    blocked_until = NULL,
                    updated_by = 'pytest',
                    updated_reason = 'runtime integration test'
                WHERE lane = %s
                """,
                (capacity, lane),
            )


def _job(
    *,
    lane: str = "wecom_interactive",
    ordering_key: str = "",
    fairness_key: str = "",
    rate_scope_key: str = "",
    execution_scope: str = "test_loopback",
    priority: int = 100,
    available_at: datetime | None = None,
) -> dict:
    key = uuid4().hex
    return ExternalEffectService().plan_effect(
        effect_type="test.queue.effect",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{key}",
        business_type="runtime_test",
        business_id=f"business-{key}",
        payload={"execution_scope": execution_scope} if execution_scope else {},
        idempotency_key=f"runtime-{key}",
        status="queued",
        lane=lane,
        ordering_key=ordering_key,
        fairness_key=fairness_key,
        rate_scope_key=rate_scope_key,
        priority=priority,
        available_at=available_at,
    )


def _finish(item_id: int, *, status: str = "simulated") -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = %s,
                lease_token = '',
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                locked_by = '',
                locked_at = NULL,
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (status, item_id),
        )


def test_generation_and_standby_gate_are_fail_closed() -> None:
    job = _job()
    repository = ExecutionRuntimeRepository(_database_url())

    assert repository.claim_external_effect_one(lane="wecom_interactive", worker_id="standby", generation=7) is None

    _enable(generation=7, wecom_interactive=1)
    assert repository.claim_external_effect_one(lane="wecom_interactive", worker_id="old-generation", generation=6) is None
    claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="active-generation", generation=7)
    assert claim is not None
    assert claim.item_id == job["id"]
    assert claim.worker_generation == 7


def test_allowlisted_canary_requires_durable_cas_authorization(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv(
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS",
        "wm-runtime-canary",
    )
    monkeypatch.setenv(
        "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS",
        "owner-runtime-canary",
    )
    repository = SQLAlchemyExternalEffectRepository(get_session_factory(_database_url()))
    service = ExternalEffectService(repository)
    planned = service.plan_effect(
        effect_type=WECOM_CONTACT_TAG_MARK,
        adapter_name="wecom_tag",
        operation="mark",
        target_type="external_userid",
        target_id="wm-runtime-canary",
        business_type="runtime_test",
        business_id=uuid4().hex,
        payload={
            "external_userid": "wm-runtime-canary",
            "follow_user_userid": "owner-runtime-canary",
            "add_tags": ["tag-runtime-canary"],
            "remove_tags": [],
        },
        idempotency_key=f"runtime-canary-{uuid4().hex}",
        status="queued",
        lane="wecom_interactive",
    )
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET external_claim_scope = 'allowlisted'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET payload_json = jsonb_set(
                    payload_json,
                    '{execution_scope}',
                    to_jsonb('allowlisted_canary'::text),
                    TRUE
                ),
                payload_summary_json = jsonb_set(
                    payload_summary_json,
                    '{canary_authorization}',
                    '{"actor":"forged","reason":"forged","authorized_at":"forged","authorized_job_id":"not-a-number","authorized_from_version":"not-a-number","duplicate_risk_confirmed":false}'::jsonb,
                    TRUE
                )
            WHERE id = %s
            """,
            (planned["id"],),
        )
    _enable(generation=7, wecom_interactive=1)

    runtime = ExecutionRuntimeRepository(_database_url())
    before = {
        item["lane"]: item
        for item in ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()["lanes"]
    }
    assert before["wecom_interactive"]["eligible"] == 0
    assert before["wecom_interactive"]["policy_gated"] == 1
    assert runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="forged-canary",
        generation=7,
    ) is None

    current = repository.get_job(planned["id"])
    assert current is not None
    authorized = service.authorize_allowlisted_canary(
        planned["id"],
        actor="pytest",
        reason="durable runtime canary authorization",
        expected_version=current.row_version,
    )
    assert authorized is not None

    after = {
        item["lane"]: item
        for item in ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()["lanes"]
    }
    assert after["wecom_interactive"]["eligible"] == 1
    claim = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="authorized-canary",
        generation=7,
    )
    assert claim is not None
    assert claim.item_id == planned["id"]


def test_active_runtime_disables_every_direct_external_effect_owner() -> None:
    job = _job()
    repository = SQLAlchemyExternalEffectRepository(get_session_factory(_database_url()))
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching',
                lease_token = 'stale-direct-owner',
                lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second',
                locked_by = 'legacy-worker'
            WHERE id = %s
            """,
            (job["id"],),
        )
    queued = _job()
    _enable(generation=7, wecom_interactive=1)

    assert repository.direct_claims_allowed() is False
    assert repository.list_due_jobs(limit=10) == []
    assert repository.acquire_due_jobs(limit=10, locked_by="legacy-worker") == []
    assert repository.acquire_job(queued["id"], locked_by="legacy-worker") is None
    assert repository.quarantine_stale_dispatching() == 0
    worker_result = ExternalEffectWorker(repository).run_due(
        batch_size=10,
        dry_run=False,
    )
    assert worker_result["ok"] is True
    assert worker_result["owner_disabled"] is True
    assert worker_result["reason"] == "postgres_queue_runtime_is_active"
    with _connect() as connection:
        stale = connection.execute(
            "SELECT status, lease_token FROM external_effect_job WHERE id = %s",
            (job["id"],),
        ).fetchone()
        waiting = connection.execute(
            "SELECT status, lease_token FROM external_effect_job WHERE id = %s",
            (queued["id"],),
        ).fetchone()
    assert stale["status"] == "dispatching"
    assert stale["lease_token"] == "stale-direct-owner"
    assert waiting["status"] == "queued"
    assert waiting["lease_token"] == ""


def test_pre_provider_typed_gate_attempt_records_active_worker_generation() -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES
              ('AICRM_WECOM_EXECUTION_MODE', 'execute', CURRENT_TIMESTAMP),
              ('AICRM_WECOM_ENABLED_EFFECT_TYPES', 'wecom.message.private.send', CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """
        )
    planned = ExternalEffectService().plan_effect(
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        adapter_name="wecom_external_contact_detail",
        operation="get_external_contact_detail",
        target_type="external_user",
        target_id="test-external-user",
        business_type="runtime_generation_test",
        business_id=uuid4().hex,
        payload={"execution_scope": "test_loopback", "is_test": True},
        idempotency_key=f"typed-gate-generation-{uuid4().hex}",
        status="queued",
        lane="wecom_interactive",
    )
    _enable(generation=7, wecom_interactive=1)
    runtime = ExecutionRuntimeRepository(_database_url())
    claim = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="typed-gate-generation",
        generation=7,
        test_only=True,
    )
    assert claim is not None
    repository = SQLAlchemyExternalEffectRepository(get_session_factory(_database_url()))

    result = ExternalEffectWorker(repository).dispatch_claimed(
        int(planned["id"]),
        lease_token=claim.lease_token,
    )

    assert result["job"]["status"] == "blocked"
    assert result["attempt"]["error_code"] == "effect_type_not_allowed"
    assert result["attempt"]["provider_call_started_at"] == ""
    assert result["attempt"]["worker_generation"] == 7


def test_generation_zero_cannot_be_enabled_for_runtime_claims() -> None:
    repository = ExecutionRuntimeRepository(_database_url())
    assert repository.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="generation-zero",
        generation=0,
    ) is None
    with pytest.raises(psycopg.errors.CheckViolation):
        with _connect() as connection:
            connection.execute(
                """
                UPDATE queue_runtime_control
                SET active_generation = 0, claim_enabled = TRUE, rollout_mode = 'execute'
                WHERE singleton = TRUE
                """
            )


def test_runtime_snapshot_uses_the_claim_policy_gate() -> None:
    job = _job()
    read_model = ExecutionRuntimeReadModel(_database_url())

    standby = {item["lane"]: item for item in read_model.runtime_snapshot()["lanes"]}
    assert standby["wecom_interactive"]["raw_open"] == 1
    assert standby["wecom_interactive"]["eligible"] == 0

    _enable(generation=7, wecom_interactive=1)
    enabled = {item["lane"]: item for item in read_model.runtime_snapshot()["lanes"]}
    assert enabled["wecom_interactive"]["eligible"] == 1

    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET policy_version = 'stale-policy' WHERE id = %s",
            (job["id"],),
        )
    mismatched = {item["lane"]: item for item in read_model.runtime_snapshot()["lanes"]}
    assert mismatched["wecom_interactive"]["eligible"] == 0


def test_every_canonical_enqueue_binds_the_current_policy_snapshot() -> None:
    policy_version = f"queue-v2-dynamic-{uuid4().hex[:12]}"
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO queue_policy_snapshot (policy_version, policy_json, created_by, created_reason)
            SELECT %s, policy_json, 'pytest', 'dynamic enqueue policy snapshot'
            FROM queue_policy_snapshot
            WHERE policy_version = 'queue-v2-test-loopback'
            """,
            (policy_version,),
        )
        connection.execute(
            "UPDATE queue_runtime_control SET policy_version = %s WHERE singleton = TRUE",
            (policy_version,),
        )
        connection.execute(
            "UPDATE queue_lane_policy SET policy_version = %s",
            (policy_version,),
        )

    external = _job()
    transactional_external_request = ExternalEffectCreateRequest(
        effect_type="test.queue.transactional",
        adapter_name="test_provider",
        operation="send",
        target_type="test_target",
        target_id=f"target-{uuid4().hex}",
        business_type="runtime_test",
        business_id=f"business-{uuid4().hex}",
        idempotency_key=f"transactional-external-{uuid4().hex}",
        payload={"execution_scope": "test_loopback"},
        context=CommandContext(actor_id="pytest", actor_type="system"),
    )
    with _connect(autocommit=False) as connection:
        transactional_external = enqueue_transactional_external_effect_job(
            connection,
            transactional_external_request,
        )
        connection.commit()

    internal_request = InternalEventCreateRequest(
        event_type="test.queue.policy",
        aggregate_type="queue_policy_test",
        aggregate_id=uuid4().hex,
        idempotency_key=f"internal-policy-{uuid4().hex}",
        context=CommandContext(actor_id="pytest", actor_type="system"),
    )
    with get_session_factory()() as session:
        internal_outbox = enqueue_internal_event_outbox_in_session(session, internal_request)
        session.commit()
    transactional_internal_request = InternalEventCreateRequest(
        event_type="test.queue.transactional_policy",
        aggregate_type="queue_policy_test",
        aggregate_id=uuid4().hex,
        idempotency_key=f"transactional-internal-{uuid4().hex}",
        context=CommandContext(actor_id="pytest", actor_type="system"),
    )
    with _connect(autocommit=False) as connection:
        transactional_internal = enqueue_transactional_internal_event_outbox(
            connection,
            transactional_internal_request,
        )
        connection.commit()

    direct_event_request = InternalEventCreateRequest(
        event_type="test.queue.direct_consumer",
        aggregate_type="queue_policy_test",
        aggregate_id=uuid4().hex,
        idempotency_key=f"direct-consumer-{uuid4().hex}",
        context=CommandContext(actor_id="pytest", actor_type="system"),
    )
    _event, direct_runs = SQLAlchemyInternalEventRepository().create_event_with_consumer_runs(
        direct_event_request,
        [InternalEventConsumerSpec(consumer_name="queue_policy_test_consumer")],
    )
    inbox = PostgresWebhookInboxRepository(_database_url()).upsert_received(
        provider="wecom",
        event_family="external_contact",
        route="/test/queue-policy",
        method="POST",
        tenant_id="aicrm",
        corp_id="corp-test",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_event_id=f"event-{uuid4().hex}",
        idempotency_key=f"webhook-policy-{uuid4().hex}",
        raw_query_json={},
        raw_headers_json={},
        raw_body=b"<xml />",
        payload_xml="<xml />",
        payload_json={},
        payload_summary_json={},
    )

    assert external["policy_version"] == policy_version
    assert transactional_external.policy_version == policy_version
    assert internal_outbox["policy_version"] == policy_version
    assert transactional_internal["policy_version"] == policy_version
    assert direct_runs[0].policy_version == policy_version
    assert inbox["policy_version"] == policy_version


def test_database_scope_unifies_claim_deadline_and_runtime_metrics() -> None:
    now = datetime.now(timezone.utc)
    real = _job(
        execution_scope="",
        priority=1,
        available_at=now - timedelta(minutes=10),
    )
    loopback = _job(
        execution_scope="test_loopback",
        priority=200,
        available_at=now - timedelta(minutes=1),
    )
    _enable(generation=7, wecom_interactive=1)
    repository = ExecutionRuntimeRepository(_database_url())
    lanes = {
        item["lane"]: item
        for item in ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()["lanes"]
    }

    assert lanes["wecom_interactive"]["raw_open"] == 2
    assert lanes["wecom_interactive"]["eligible"] == 1
    assert lanes["wecom_interactive"]["policy_gated"] == 1
    next_due = repository.next_due_at(
        queue_kind="external_effect",
        lane="wecom_interactive",
        generation=7,
    )
    assert next_due is not None
    assert next_due > now - timedelta(minutes=2)

    claim = repository.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="scope-test",
        generation=7,
    )
    assert claim is not None
    assert claim.item_id == loopback["id"]
    assert claim.item_id != real["id"]


def test_canary_runtime_and_system_health_only_count_test_loopback_external_effects() -> None:
    loopback = _job(execution_scope="test_loopback")
    real_target = _job(execution_scope="")
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET active_generation = 7, claim_enabled = TRUE, rollout_mode = 'canary'
            WHERE singleton = TRUE
            """
        )
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE, rollout_mode = 'canary', blocked_until = NULL
            WHERE lane = 'wecom_interactive'
            """
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 hours',
                available_at = CURRENT_TIMESTAMP - INTERVAL '2 hours'
            WHERE id = %s
            """,
            (real_target["id"],),
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET created_at = CURRENT_TIMESTAMP - INTERVAL '5 seconds',
                available_at = CURRENT_TIMESTAMP - INTERVAL '5 seconds'
            WHERE id = %s
            """,
            (loopback["id"],),
        )

    snapshot = ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()
    lanes = {
        item["lane"]: item
        for item in snapshot["lanes"]
    }
    with RuntimeReadinessRepository(_database_url()) as readiness:
        health = readiness.queue_metrics()

    assert lanes["wecom_interactive"]["raw_open"] == 2
    assert lanes["wecom_interactive"]["eligible"] == 1
    assert lanes["wecom_interactive"]["policy_gated"] == 1
    assert lanes["wecom_interactive"]["oldest_eligible_age_seconds"] < 60
    assert snapshot["policy_snapshot"]["external_execution_scope_mode"]["wecom_interactive"] == "test_loopback_only"
    assert health["external_effect_raw_open_count"] == 2
    assert health["external_effect_eligible_count"] == 1
    assert health["external_effect_eligible_oldest_pending_age_seconds"] < 60
    assert health["queue_policy_version"] == "queue-v2-test-loopback"
    assert health["queue_active_generation"] == 7
    assert health["queue_external_claim_scope"] == "test_loopback"

    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_runtime_control
            SET external_claim_scope = 'blocked'
            WHERE singleton = TRUE
            """
        )
    blocked_lanes = {
        item["lane"]: item
        for item in ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()["lanes"]
    }
    with RuntimeReadinessRepository(_database_url()) as readiness:
        blocked_health = readiness.queue_metrics()

    assert blocked_lanes["wecom_interactive"]["eligible"] == 0
    assert blocked_lanes["wecom_interactive"]["policy_gated"] == 2
    assert blocked_health["external_effect_eligible_count"] == 0
    assert blocked_health["queue_external_claim_scope"] == "blocked"


def test_generation_zero_is_ineligible_in_runtime_and_system_health() -> None:
    _job(execution_scope="test_loopback")
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_lane_policy
            SET enabled = TRUE, rollout_mode = 'canary', blocked_until = NULL
            WHERE lane = 'wecom_interactive'
            """
        )
    lanes = {
        item["lane"]: item
        for item in ExecutionRuntimeReadModel(_database_url()).runtime_snapshot()["lanes"]
    }
    with RuntimeReadinessRepository(_database_url()) as readiness:
        health = readiness.queue_metrics()

    assert lanes["wecom_interactive"]["eligible"] == 0
    assert health["external_effect_eligible_count"] == 0
    assert health["queue_eligible_count"] == 0


def test_legacy_broadcast_is_visible_but_never_eligible() -> None:
    from aicrm_next.data_health import checks as data_health_checks

    suffix = uuid4().hex
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO broadcast_jobs (
                source_type, source_id, source_table, scheduled_for, status,
                idempotency_key, target_unionids_json, content_payload,
                hold_reason, execution_owner
            ) VALUES
                (
                    'manual', %s, 'manual', CURRENT_TIMESTAMP - INTERVAL '1 hour',
                    'queued', %s, '[]'::jsonb, '{}'::jsonb,
                    'pre_runtime_history_requires_manual_classification', 'legacy_frozen'
                ),
                (
                    'manual', %s, 'manual', CURRENT_TIMESTAMP - INTERVAL '1 hour',
                    'failed_terminal', %s, '[]'::jsonb, '{}'::jsonb,
                    '', 'legacy_frozen'
                )
            """,
            (
                f"legacy-held-{suffix}",
                f"legacy-held-{suffix}",
                f"legacy-dlq-{suffix}",
                f"legacy-dlq-{suffix}",
            ),
        )
    _enable(generation=7, wecom_bulk=1)

    with RuntimeReadinessRepository(_database_url()) as readiness:
        health = readiness.queue_metrics()

    assert health["broadcast_raw_open_count"] == 2
    assert health["broadcast_held_count"] == 1
    assert health["broadcast_dlq_count"] == 1
    assert health["broadcast_eligible_count"] == 0

    data_health = data_health_checks._broadcast_job_blocked_backlog()
    assert data_health.evidence["execution_owner"] == "legacy_frozen"
    assert data_health.evidence["execution_semantics"] == "readonly"
    assert data_health.evidence["raw_open_count"] == 2
    assert data_health.evidence["held_count"] == 1
    assert data_health.evidence["dlq_count"] == 1
    assert data_health.evidence["eligible_count"] == 0


def test_capacity_claims_only_real_slots_and_refills_immediately() -> None:
    jobs = [_job(fairness_key=f"tenant-{index}") for index in range(3)]
    _enable(generation=7, global_capacity=2, wecom_interactive=2)
    repository = ExecutionRuntimeRepository(_database_url())

    with ThreadPoolExecutor(max_workers=3) as executor:
        claims = list(
            executor.map(
                lambda index: repository.claim_external_effect_one(
                    lane="wecom_interactive",
                    worker_id=f"capacity-{index}",
                    generation=7,
                ),
                range(3),
            )
        )
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 2
    with _connect() as connection:
        counts = connection.execute(
            """
            SELECT COUNT(*) FILTER (WHERE status = 'dispatching') AS running,
                   COUNT(*) FILTER (WHERE status = 'queued') AS waiting
            FROM external_effect_job
            WHERE id = ANY(%s)
            """,
            ([job["id"] for job in jobs],),
        ).fetchone()
    assert counts == {"running": 2, "waiting": 1}
    assert repository.next_due_at(
        queue_kind="external_effect",
        lane="wecom_interactive",
        generation=7,
    ) is None

    _finish(claimed[0].item_id)
    due_at = repository.next_due_at(
        queue_kind="external_effect",
        lane="wecom_interactive",
        generation=7,
    )
    assert due_at is not None and due_at <= datetime.now(timezone.utc)
    started = time.monotonic()
    next_claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="capacity-refill", generation=7)
    assert next_claim is not None
    assert time.monotonic() - started < 1.0


def test_ordering_is_serial_and_fairness_rotates_between_keys() -> None:
    first = _job(ordering_key="customer-1", fairness_key="tenant-a")
    second = _job(ordering_key="customer-1", fairness_key="tenant-a")
    third = _job(ordering_key="customer-2", fairness_key="tenant-b")
    _enable(generation=7, global_capacity=2, wecom_interactive=2)
    repository = ExecutionRuntimeRepository(_database_url())

    first_claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="ordering-1", generation=7)
    assert first_claim is not None and first_claim.item_id == first["id"]
    second_claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="ordering-2", generation=7)
    assert second_claim is not None and second_claim.item_id == third["id"]

    _finish(first_claim.item_id)
    _finish(second_claim.item_id)
    rotated = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="ordering-3", generation=7)
    assert rotated is not None and rotated.item_id == second["id"]


def test_rate_scope_cooldown_skips_scope_without_sleeping() -> None:
    blocked = _job(rate_scope_key="provider:corp-a:send")
    available = _job(rate_scope_key="provider:corp-b:send")
    _enable(generation=7, global_capacity=1, wecom_interactive=1)
    repository = ExecutionRuntimeRepository(_database_url())
    repository.record_rate_limit(
        rate_scope_key="provider:corp-a:send",
        blocked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        provider="test_provider",
        corp_id="corp-a",
        operation="send",
    )

    claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="rate-open", generation=7)
    assert claim is not None and claim.item_id == available["id"]
    _finish(claim.item_id)
    with _connect() as connection:
        connection.execute(
            """
            UPDATE queue_rate_scope_cooldown
            SET blocked_until = CURRENT_TIMESTAMP - INTERVAL '1 second'
            WHERE rate_scope_key = 'provider:corp-a:send'
            """
        )
    released = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="rate-released", generation=7)
    assert released is not None and released.item_id == blocked["id"]


def test_lease_heartbeat_extends_owner_and_prevents_duplicate_claim() -> None:
    job = _job()
    _enable(generation=7, global_capacity=1, wecom_interactive=1)
    repository = ExecutionRuntimeRepository(_database_url())
    claim = repository.claim_external_effect_one(lane="wecom_interactive", worker_id="heartbeat-owner", generation=7, lease_seconds=10)
    assert claim is not None
    time.sleep(0.05)
    assert (
        repository.renew_lease(
            queue_kind="external_effect",
            item_id=claim.item_id,
            lease_token=claim.lease_token,
            generation=7,
            lease_seconds=10,
        )
        is True
    )
    with _connect() as connection:
        row = connection.execute(
            "SELECT lease_expires_at, heartbeat_at FROM external_effect_job WHERE id = %s",
            (job["id"],),
        ).fetchone()
    assert row["lease_expires_at"] > claim.lease_expires_at
    assert row["heartbeat_at"] is not None
    assert repository.claim_external_effect_one(lane="wecom_interactive", worker_id="duplicate", generation=7) is None


def test_expired_pre_provider_lease_is_safely_requeued_and_reclaimed() -> None:
    job = _job()
    _enable(generation=7, global_capacity=1, wecom_interactive=1)
    repository = ExecutionRuntimeRepository(_database_url())
    first = repository.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="pre-boundary-first",
        generation=7,
    )
    assert first is not None and first.item_id == job["id"]
    with _connect() as connection:
        lease_event_count_before = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'external_effect' AND queue_row_id = %s
                """,
                (job["id"],),
            ).fetchone()["count"]
        )
        connection.execute(
            "UPDATE external_effect_job SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (job["id"],),
        )

    second = repository.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="pre-boundary-second",
        generation=7,
    )
    assert second is not None and second.item_id == job["id"]
    assert second.lease_token != first.lease_token
    with _connect() as connection:
        row = connection.execute(
            "SELECT status, attempt_count, provider_call_started_at, last_error_code FROM external_effect_job WHERE id = %s",
            (job["id"],),
        ).fetchone()
        lease_event_count_after = int(
            connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM queue_runtime_lease_recovery_event
                WHERE queue_kind = 'external_effect' AND queue_row_id = %s
                """,
                (job["id"],),
            ).fetchone()["count"]
        )
        latest_lease_event = connection.execute(
            """
            SELECT error_code, worker_generation
            FROM queue_runtime_lease_recovery_event
            WHERE queue_kind = 'external_effect' AND queue_row_id = %s
            ORDER BY lease_event_id DESC
            LIMIT 1
            """,
            (job["id"],),
        ).fetchone()
    assert row["status"] == "dispatching"
    assert row["attempt_count"] == 0
    assert row["provider_call_started_at"] is None
    assert row["last_error_code"] == "lease_expired_before_dispatch"
    assert lease_event_count_after == lease_event_count_before + 1
    assert latest_lease_event == {
        "error_code": "lease_expired_before_dispatch",
        "worker_generation": 7,
    }


def test_soak_lost_lease_metric_keeps_recovered_rows_and_active_expirations() -> None:
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    recovered = _job()
    active = _job()
    with _connect() as connection:
        baseline_count = lost_lease_count(connection, started_at=started_at)
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'succeeded',
                last_error_code = '',
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (recovered["id"],),
        )
        connection.execute(
            """
            INSERT INTO queue_runtime_lease_recovery_event (
                queue_kind, queue_row_id, worker_generation,
                error_code, lease_expires_at, detected_at
            ) VALUES (
                'external_effect', %s, 7,
                'lease_expired_before_dispatch', %s, CURRENT_TIMESTAMP
            )
            """,
            (recovered["id"], started_at + timedelta(seconds=1)),
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'dispatching',
                last_error_code = '',
                lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second',
                updated_at = %s
            WHERE id = %s
            """,
            (started_at - timedelta(minutes=1), active["id"]),
        )

        assert lost_lease_count(connection, started_at=started_at) == baseline_count + 2


def test_soak_required_evidence_uses_latest_outcome_per_type() -> None:
    release_sha = uuid4().hex + uuid4().hex[:8]
    policy_version = f"queue-v2-evidence-{uuid4().hex}"
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO queue_runtime_validation_evidence (
                evidence_id, evidence_type, release_sha, active_generation,
                policy_version, status, evidence_json, actor, reason, created_at
            ) VALUES
                (%s, 'listener_reconnect', %s, 17, %s, 'passed', '{}'::jsonb,
                 'pytest', 'older pass', CURRENT_TIMESTAMP - INTERVAL '1 minute'),
                (%s, 'listener_reconnect', %s, 17, %s, 'failed', '{}'::jsonb,
                 'pytest', 'newer failure', CURRENT_TIMESTAMP)
            """,
            (
                "qrve_" + uuid4().hex,
                release_sha,
                policy_version,
                "qrve_" + uuid4().hex,
                release_sha,
                policy_version,
            ),
        )

        assert _evidence_types(
            connection,
            release_sha=release_sha,
            generation=17,
            policy_version=policy_version,
        ) == set()

        connection.execute(
            """
            INSERT INTO queue_runtime_validation_evidence (
                evidence_id, evidence_type, release_sha, active_generation,
                policy_version, status, evidence_json, actor, reason, created_at
            ) VALUES (
                %s, 'listener_reconnect', %s, 17, %s, 'passed', '{}'::jsonb,
                'pytest', 'latest pass', CURRENT_TIMESTAMP + INTERVAL '1 second'
            )
            """,
            ("qrve_" + uuid4().hex, release_sha, policy_version),
        )

        assert _evidence_types(
            connection,
            release_sha=release_sha,
            generation=17,
            policy_version=policy_version,
        ) == {"listener_reconnect"}


def test_expired_post_provider_lease_becomes_unknown_and_attempt_is_closed() -> None:
    job = _job()
    _enable(generation=7, global_capacity=1, wecom_interactive=1)
    runtime = ExecutionRuntimeRepository(_database_url())
    claim = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="post-boundary",
        generation=7,
    )
    assert claim is not None
    effects = SQLAlchemyExternalEffectRepository()
    claimed_job = effects.get_job(job["id"])
    assert claimed_job is not None
    begun = effects.begin_provider_attempt(
        job=claimed_job,
        request_summary={"provider_request": "redacted"},
    )
    assert begun is not None
    begun_job, attempt = begun
    assert attempt.lease_token == claim.lease_token
    assert len(attempt.request_hash) == 64
    assert attempt.provider_call_started_at
    assert attempt.worker_generation == 7
    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (job["id"],),
        )
    assert (
        effects.complete_dispatch(
            job=begun_job,
            result=ExternalEffectDispatchResult(
                status="succeeded",
                provider_result_received=True,
                real_external_call_executed=True,
            ),
        )
        is None
    )
    assert runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="post-boundary-recovery",
        generation=7,
    ) is None
    with _connect() as connection:
        job_row = connection.execute(
            "SELECT status, attempt_count, reconciliation_required FROM external_effect_job WHERE id = %s",
            (job["id"],),
        ).fetchone()
        attempt_row = connection.execute(
            "SELECT status, error_code FROM external_effect_attempt WHERE attempt_id = %s",
            (attempt.attempt_id,),
        ).fetchone()
        settlement = connection.execute(
            """
            SELECT event_type, payload_json, status
            FROM internal_event_outbox
            WHERE idempotency_key = %s
            """,
            (
                f"external_effect.settled:{job['id']}:unknown_after_dispatch:"
                f"{attempt.attempt_id}",
            ),
        ).fetchone()
    assert job_row == {
        "status": "unknown_after_dispatch",
        "attempt_count": 1,
        "reconciliation_required": True,
    }
    assert attempt_row["status"] == "unknown_after_dispatch"
    assert attempt_row["error_code"] == "lease_expired_after_dispatch"
    assert settlement["event_type"] == "external_effect.settled"
    assert settlement["payload_json"]["attempt_id"] == attempt.attempt_id
    assert settlement["status"] == "pending"
    timeline = ExecutionRuntimeReadModel(_database_url()).execution_timeline(job["execution_id"])
    assert timeline is not None
    assert {item["item_kind"] for item in timeline["items"]} >= {
        "external_effect",
        "external_effect_attempt",
    }


def test_terminal_429_still_atomically_blocks_the_provider_scope() -> None:
    scope = f"test-provider:corp:app:send:{uuid4().hex}"
    first = _job(rate_scope_key=scope)
    second = _job(rate_scope_key=scope)
    _enable(generation=7, global_capacity=1, wecom_interactive=1)
    runtime = ExecutionRuntimeRepository(_database_url())
    claim = runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="terminal-rate-limit",
        generation=7,
    )
    assert claim is not None and claim.item_id == first["id"]
    effects = SQLAlchemyExternalEffectRepository()
    claimed_job = effects.get_job(first["id"])
    assert claimed_job is not None
    begun = effects.begin_provider_attempt(job=claimed_job, request_summary={"send": True})
    assert begun is not None
    begun_job, attempt = begun
    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=7)
    completed = effects.complete_dispatch(
        job=begun_job,
        result=ExternalEffectDispatchResult(
            status="failed_terminal",
            error_code="http_429",
            response_summary={"status_code": 429, "retry_after_seconds": 7},
            retry_after_seconds=7,
            provider_result_received=True,
            real_external_call_executed=True,
        ),
        next_retry_at=blocked_until,
    )
    assert completed is not None and completed[0].status == "failed_terminal"
    with _connect() as connection:
        cooldown = connection.execute(
            "SELECT blocked_until, source_attempt_id FROM queue_rate_scope_cooldown WHERE rate_scope_key = %s",
            (scope,),
        ).fetchone()
    assert cooldown["blocked_until"] >= blocked_until - timedelta(milliseconds=1)
    assert cooldown["source_attempt_id"] == attempt.attempt_id
    assert runtime.claim_external_effect_one(
        lane="wecom_interactive",
        worker_id="same-scope-blocked",
        generation=7,
    ) is None
    with _connect() as connection:
        assert connection.execute(
            "SELECT status FROM external_effect_job WHERE id = %s",
            (second["id"],),
        ).fetchone()["status"] == "queued"


def test_internal_outbox_attempt_budget_counts_claim_once_and_expired_owner_loses_cas() -> None:
    with get_session_factory()() as session:
        outbox = enqueue_internal_event_outbox_in_session(
            session,
            InternalEventCreateRequest(
                event_type="runtime.outbox.test",
                aggregate_type="runtime_test",
                aggregate_id=uuid4().hex,
                payload={"mobile": "13800000000", "token": "must-not-leak"},
                idempotency_key=f"runtime-outbox-{uuid4().hex}",
            ),
        )
        session.commit()
    timeline = ExecutionRuntimeReadModel(_database_url()).execution_timeline(outbox["execution_id"])
    assert timeline is not None
    assert any(item["item_kind"] == "internal_outbox" for item in timeline["items"])
    assert "13800000000" not in str(timeline)
    assert "must-not-leak" not in str(timeline)
    _enable(generation=7, global_capacity=1, internal_general=1)
    runtime = ExecutionRuntimeRepository(_database_url())
    first = runtime.claim_internal_outbox_one(
        lane="internal_general",
        worker_id="outbox-first",
        generation=7,
    )
    assert first is not None and first.item_id == outbox["id"]
    assert int(first.payload["attempt_count"]) == 1
    field_names = {field.name for field in fields(InternalEventOutboxRecord)}
    record = InternalEventOutboxRecord(
        **{key: value for key, value in first.payload.items() if key in field_names}
    )
    with _connect() as connection:
        connection.execute(
            "UPDATE internal_event_outbox SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = %s",
            (outbox["id"],),
        )
    internal = SQLAlchemyInternalEventRepository()
    assert internal.mark_outbox_failure(
        record,
        error_code="expired_owner",
        error_message="must not commit",
        next_retry_at=datetime.now(timezone.utc),
    ) is None
    second = runtime.claim_internal_outbox_one(
        lane="internal_general",
        worker_id="outbox-second",
        generation=7,
    )
    assert second is not None and second.item_id == outbox["id"]
    assert int(second.payload["attempt_count"]) == 2


def test_external_effect_producer_rejects_unknown_lane() -> None:
    with pytest.raises(ValueError, match="unsupported external effect lane"):
        _job(lane="unknown_lane")


def test_notify_is_visible_only_after_commit_and_transactional_rows_are_eligible() -> None:
    listener = PostgresQueueWakeListener(_database_url())
    listener.connect()
    try:
        with _connect(autocommit=False) as connection:
            request = ExternalEffectCreateRequest(
                effect_type="test.queue.notify",
                adapter_name="test_provider",
                operation="send",
                target_type="test_target",
                target_id="notify-target",
                context=CommandContext(actor_id="pytest", actor_type="system"),
                payload={"execution_scope": "test_loopback"},
                idempotency_key=f"notify-{uuid4().hex}",
                lane="wecom_interactive",
                ordering_key="notify-order",
                fairness_key="notify-fairness",
                rate_scope_key="notify-scope",
            )
            job = enqueue_transactional_external_effect_job(connection, request)
            assert job.execution_id
            assert job.available_at
            assert listener.wait(timeout_seconds=0.1) is None
            connection.commit()
        hint = listener.wait(timeout_seconds=1.0)
        assert hint is not None
        assert hint.queue_kind == "external_effect"
        assert hint.lane == "wecom_interactive"
    finally:
        listener.close()

    with get_session_factory()() as session:
        outbox = enqueue_internal_event_outbox_in_session(
            session,
            InternalEventCreateRequest(
                event_type="external_effect.completed",
                aggregate_type="external_effect_job",
                aggregate_id=str(job.id),
                parent_execution_id=job.execution_id,
                idempotency_key=f"completion-{uuid4().hex}",
            ),
        )
        session.commit()
    assert outbox["execution_id"]
    assert outbox["parent_execution_id"] == job.execution_id
    assert outbox["lane"] == "internal_general"
    assert outbox["available_at"] is not None

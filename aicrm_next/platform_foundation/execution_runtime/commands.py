from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects.repo_contract import _public_job
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    build_external_effect_settled_event,
)
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
    InternalEventCreateRequest,
)
from aicrm_next.platform_foundation.internal_events.outbox import (
    enqueue_internal_event_outbox_in_session,
)
from aicrm_next.shared.db_session import get_session_factory


QUEUE_RUNTIME_COMMAND_APPLIED = "queue.runtime.command.applied"
QUEUE_RUNTIME_COMMAND_AUDIT_CONSUMER = "queue_runtime_command_audit_consumer"


@dataclass(frozen=True)
class QueueCommandTarget:
    queue_kind: str
    item_id: int
    execution_id: str
    lane: str
    status: str
    version_token: str
    hold_reason: str


@dataclass(frozen=True)
class QueueCommandResult:
    target: QueueCommandTarget
    intent_id: str
    command_id: str
    notification_payload: dict[str, str]
    action: str = "make_eligible_now"


class QueueCommandConflict(RuntimeError):
    """The durable queue row no longer matches the operator's snapshot."""


class QueueCommandDuplicateRiskRequired(RuntimeError):
    """An unknown provider outcome cannot be retried without explicit risk acceptance."""


@dataclass(frozen=True)
class _QueueFact:
    table: str
    eligible_statuses: frozenset[str]
    version_expression: str
    version_predicate: str
    version_assignment: str
    provider_boundary_predicate: str = ""


_QUEUE_FACTS = {
    "external_effect": _QueueFact(
        table="external_effect_job",
        eligible_statuses=frozenset({"queued", "failed_retryable"}),
        version_expression="row_version::text",
        version_predicate="row_version::text = :expected_version",
        version_assignment="row_version = row_version + 1,",
        provider_boundary_predicate="AND provider_call_started_at IS NULL",
    ),
    "internal_event": _QueueFact(
        table="internal_event_consumer_run",
        eligible_statuses=frozenset({"pending", "failed_retryable"}),
        version_expression="xmin::text",
        version_predicate="xmin::text = :expected_version",
        version_assignment="",
    ),
    "internal_outbox": _QueueFact(
        table="internal_event_outbox",
        eligible_statuses=frozenset({"pending", "failed_retryable"}),
        version_expression="xmin::text",
        version_predicate="xmin::text = :expected_version",
        version_assignment="",
    ),
    "webhook_inbox": _QueueFact(
        table="webhook_inbox",
        eligible_statuses=frozenset({"received", "failed_retryable"}),
        version_expression="xmin::text",
        version_predicate="xmin::text = :expected_version",
        version_assignment="",
    ),
}


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _fact(queue_kind: str) -> tuple[str, _QueueFact]:
    normalized = _required_text(queue_kind, "queue_kind").lower()
    fact = _QUEUE_FACTS.get(normalized)
    if fact is None:
        raise ValueError(f"unsupported queue_kind: {normalized}")
    return normalized, fact


class QueueRuntimeCommandService:
    """CAS a concrete queue fact and emit a durable, auditable wake intent.

    The command never executes a handler or provider.  It only makes one
    already-unheld row eligible, appends an Internal Event Outbox audit record,
    and sends a non-sensitive PostgreSQL dirty hint in the same transaction.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()

    def read_target(self, queue_kind: str, item_id: int) -> QueueCommandTarget | None:
        normalized_kind, fact = _fact(queue_kind)
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT id, execution_id, lane, status, hold_reason,
                               {fact.version_expression} AS version_token
                        FROM {fact.table}
                        WHERE id = :item_id
                        """
                    ),
                    {"item_id": int(item_id)},
                )
                .mappings()
                .fetchone()
            )
            session.rollback()
        return self._target(normalized_kind, row) if row else None

    def read_internal_consumer_target(
        self,
        event_id: str,
        consumer_name: str,
    ) -> QueueCommandTarget | None:
        normalized_event_id = _required_text(event_id, "event_id")
        normalized_consumer_name = _required_text(consumer_name, "consumer_name")
        fact = _QUEUE_FACTS["internal_event"]
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT id, execution_id, lane, status, hold_reason,
                               {fact.version_expression} AS version_token
                        FROM {fact.table}
                        WHERE event_id = :event_id
                          AND consumer_name = :consumer_name
                        """
                    ),
                    {
                        "event_id": normalized_event_id,
                        "consumer_name": normalized_consumer_name,
                    },
                )
                .mappings()
                .fetchone()
            )
            session.rollback()
        return self._target("internal_event", row) if row else None

    def read_internal_due_target(
        self,
        item_id: int,
        *,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
    ) -> QueueCommandTarget | None:
        fact = _QUEUE_FACTS["internal_event"]
        clauses = ["run.id = :item_id"]
        params: dict[str, Any] = {"item_id": int(item_id)}
        if event_types:
            clauses.append("event.event_type = ANY(:event_types)")
            params["event_types"] = [
                _required_text(item, "event_type") for item in event_types
            ]
        if consumer_names:
            clauses.append("run.consumer_name = ANY(:consumer_names)")
            params["consumer_names"] = [
                _required_text(item, "consumer_name") for item in consumer_names
            ]
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT run.id, run.execution_id, run.lane, run.status,
                               run.hold_reason,
                               {fact.version_expression.replace('xmin', 'run.xmin')} AS version_token
                        FROM internal_event_consumer_run run
                        JOIN internal_event event ON event.event_id = run.event_id
                        WHERE {' AND '.join(clauses)}
                        """
                    ),
                    params,
                )
                .mappings()
                .fetchone()
            )
            session.rollback()
        return self._target("internal_event", row) if row else None

    def read_external_effect_target(
        self,
        item_id: int,
        *,
        effect_types: list[str] | None = None,
        test_only: bool = False,
    ) -> QueueCommandTarget | None:
        fact = _QUEUE_FACTS["external_effect"]
        clauses = ["id = :item_id"]
        params: dict[str, Any] = {"item_id": int(item_id)}
        if effect_types:
            clauses.append("effect_type = ANY(:effect_types)")
            params["effect_types"] = [
                _required_text(item, "effect_type") for item in effect_types
            ]
        if test_only:
            clauses.append("COALESCE(payload_json->>'execution_scope', '') = 'test_loopback'")
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT id, execution_id, lane, status, hold_reason,
                               {fact.version_expression} AS version_token
                        FROM external_effect_job
                        WHERE {' AND '.join(clauses)}
                        """
                    ),
                    params,
                )
                .mappings()
                .fetchone()
            )
            session.rollback()
        return self._target("external_effect", row) if row else None

    def read_webhook_inbox_target(
        self,
        item_id: int,
        *,
        provider: str,
    ) -> QueueCommandTarget | None:
        normalized_provider = _required_text(provider, "provider")
        fact = _QUEUE_FACTS["webhook_inbox"]
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        SELECT id, execution_id, lane, status, hold_reason,
                               {fact.version_expression} AS version_token
                        FROM webhook_inbox
                        WHERE id = :item_id
                          AND provider = :provider
                        """
                    ),
                    {"item_id": int(item_id), "provider": normalized_provider},
                )
                .mappings()
                .fetchone()
            )
            session.rollback()
        return self._target("webhook_inbox", row) if row else None

    def request_immediate_execution(
        self,
        queue_kind: str,
        item_id: int,
        *,
        expected_status: str,
        expected_version: str,
        actor: str,
        reason: str,
        command_id: str = "",
        source_route: str = "",
    ) -> QueueCommandResult:
        normalized_kind, fact = _fact(queue_kind)
        normalized_status = _required_text(expected_status, "expected_status")
        normalized_version = _required_text(expected_version, "expected_version")
        normalized_actor = _required_text(actor, "actor")
        normalized_reason = _required_text(reason, "reason")
        normalized_command_id = str(command_id or "").strip() or "qcmd_" + uuid4().hex
        if normalized_status not in fact.eligible_statuses:
            raise ValueError(
                f"status {normalized_status!r} is not eligible for {normalized_kind} immediate execution"
            )

        with self._session_factory() as session:
            with session.begin():
                row = (
                    session.execute(
                        text(
                            f"""
                            UPDATE {fact.table}
                            SET available_at = CURRENT_TIMESTAMP,
                                next_retry_at = CURRENT_TIMESTAMP,
                                worker_generation = 0,
                                {fact.version_assignment}
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :item_id
                              AND status = :expected_status
                              AND {fact.version_predicate}
                              AND hold_reason = ''
                              AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                              {fact.provider_boundary_predicate}
                            RETURNING id, execution_id, lane, status, hold_reason,
                                      {fact.version_expression} AS version_token
                            """
                        ),
                        {
                            "item_id": int(item_id),
                            "expected_status": normalized_status,
                            "expected_version": normalized_version,
                        },
                    )
                    .mappings()
                    .fetchone()
                )
                if not row:
                    raise QueueCommandConflict(
                        "queue command CAS failed; target changed, is held, leased, or crossed the provider boundary"
                    )
                target = self._target(normalized_kind, row)
                notification = {"queue_kind": normalized_kind, "lane": target.lane}
                session.execute(
                    text(
                        """
                        SELECT pg_notify(
                            'aicrm_queue_wakeup',
                            json_build_object(
                                'queue_kind', CAST(:queue_kind AS TEXT),
                                'lane', CAST(:lane AS TEXT)
                            )::text
                        )
                        """
                    ),
                    notification,
                )
                audit = enqueue_internal_event_outbox_in_session(
                    session,
                    InternalEventCreateRequest(
                        event_type=QUEUE_RUNTIME_COMMAND_APPLIED,
                        aggregate_type="queue_runtime_item",
                        aggregate_id=f"{normalized_kind}:{int(item_id)}",
                        subject_type="execution",
                        subject_id=target.execution_id,
                        idempotency_key=f"queue-runtime-command:{normalized_command_id}",
                        source_module="platform_foundation.execution_runtime.commands",
                        source_command_id=normalized_command_id,
                        parent_execution_id=target.execution_id,
                        payload={
                            "queue_kind": normalized_kind,
                            "item_id": int(item_id),
                            "lane": target.lane,
                            "action": "make_eligible_now",
                            "reason": normalized_reason,
                        },
                        payload_summary={
                            "queue_kind": normalized_kind,
                            "lane": target.lane,
                            "action": "make_eligible_now",
                            "expected_status": normalized_status,
                            "provider_call_requested": False,
                        },
                        context=CommandContext(
                            actor_id=normalized_actor,
                            actor_type="operator",
                            request_id=normalized_command_id,
                            trace_id=normalized_command_id,
                            source_route=str(source_route or "").strip(),
                        ),
                    ),
                )
        return QueueCommandResult(
            target=target,
            intent_id=str(audit.get("outbox_id") or ""),
            command_id=normalized_command_id,
            notification_payload=notification,
        )

    def request_manual_action(
        self,
        queue_kind: str,
        item_id: int,
        *,
        action: str,
        expected_status: str,
        expected_version: str,
        actor: str,
        reason: str,
        duplicate_risk_confirmed: bool = False,
        command_id: str = "",
        source_route: str = "",
    ) -> QueueCommandResult:
        normalized_kind, fact = _fact(queue_kind)
        normalized_action = _required_text(action, "action").lower()
        normalized_status = _required_text(expected_status, "expected_status")
        normalized_version = _required_text(expected_version, "expected_version")
        normalized_actor = _required_text(actor, "actor")
        normalized_reason = _required_text(reason, "reason")
        normalized_command_id = str(command_id or "").strip() or "qcmd_" + uuid4().hex
        supported = {
            ("external_effect", "retry"),
            ("external_effect", "cancel"),
            ("internal_event", "retry"),
            ("internal_event", "skip"),
            ("webhook_inbox", "retry"),
            ("webhook_inbox", "skip"),
        }
        if (normalized_kind, normalized_action) not in supported:
            raise ValueError(
                f"action {normalized_action!r} is not supported for {normalized_kind}"
            )
        if (
            normalized_kind == "external_effect"
            and normalized_action == "retry"
            and normalized_status == "unknown_after_dispatch"
            and duplicate_risk_confirmed is not True
        ):
            raise QueueCommandDuplicateRiskRequired(
                "duplicate_risk_confirmed=true is required for unknown_after_dispatch"
            )

        statement = self._manual_action_statement(
            queue_kind=normalized_kind,
            action=normalized_action,
            version_predicate=fact.version_predicate,
            version_expression=fact.version_expression,
        )
        with self._session_factory() as session:
            with session.begin():
                row = (
                    session.execute(
                        text(statement),
                        {
                            "item_id": int(item_id),
                            "expected_status": normalized_status,
                            "expected_version": normalized_version,
                            "actor": normalized_actor,
                            "actor_ref_hash": sha256(normalized_actor.encode("utf-8")).hexdigest(),
                            "reason": normalized_reason[:500],
                            "attempt_id": "iea_" + uuid4().hex,
                        },
                    )
                    .mappings()
                    .fetchone()
                )
                if not row:
                    raise QueueCommandConflict(
                        "manual queue command CAS failed; target changed, is leased, held, or terminal"
                    )
                target = self._target(normalized_kind, row)
                if (
                    normalized_kind == "external_effect"
                    and normalized_action == "cancel"
                    and target.status == "cancelled"
                ):
                    job_row = session.execute(
                        text("SELECT * FROM external_effect_job WHERE id = :job_id"),
                        {"job_id": int(item_id)},
                    ).mappings().one()
                    job = _public_job(dict(job_row))
                    if job is None:
                        raise RuntimeError("cancelled external effect settlement projection failed")
                    enqueue_internal_event_outbox_in_session(
                        session,
                        build_external_effect_settled_event(job=job),
                    )
                notification = {"queue_kind": normalized_kind, "lane": target.lane}
                session.execute(
                    text(
                        """
                        SELECT pg_notify(
                            'aicrm_queue_wakeup',
                            json_build_object(
                                'queue_kind', CAST(:queue_kind AS TEXT),
                                'lane', CAST(:lane AS TEXT)
                            )::text
                        )
                        """
                    ),
                    notification,
                )
                audit = enqueue_internal_event_outbox_in_session(
                    session,
                    InternalEventCreateRequest(
                        event_type=QUEUE_RUNTIME_COMMAND_APPLIED,
                        aggregate_type="queue_runtime_item",
                        aggregate_id=f"{normalized_kind}:{int(item_id)}",
                        subject_type="execution",
                        subject_id=target.execution_id,
                        idempotency_key=f"queue-runtime-command:{normalized_command_id}",
                        source_module="platform_foundation.execution_runtime.commands",
                        source_command_id=normalized_command_id,
                        parent_execution_id=target.execution_id,
                        payload={
                            "queue_kind": normalized_kind,
                            "item_id": int(item_id),
                            "lane": target.lane,
                            "action": normalized_action,
                            "reason": normalized_reason,
                            "duplicate_risk_confirmed": bool(duplicate_risk_confirmed),
                        },
                        payload_summary={
                            "queue_kind": normalized_kind,
                            "lane": target.lane,
                            "action": normalized_action,
                            "expected_status": normalized_status,
                            "duplicate_risk_confirmed": bool(duplicate_risk_confirmed),
                            "provider_call_requested": False,
                        },
                        context=CommandContext(
                            actor_id=normalized_actor,
                            actor_type="operator",
                            request_id=normalized_command_id,
                            trace_id=normalized_command_id,
                            source_route=str(source_route or "").strip(),
                        ),
                    ),
                )
        return QueueCommandResult(
            target=target,
            intent_id=str(audit.get("outbox_id") or ""),
            command_id=normalized_command_id,
            notification_payload=notification,
            action=normalized_action,
        )

    @staticmethod
    def _manual_action_statement(
        *,
        queue_kind: str,
        action: str,
        version_predicate: str,
        version_expression: str,
    ) -> str:
        if queue_kind == "external_effect" and action == "retry":
            return f"""
                UPDATE external_effect_job
                SET status = 'queued',
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    reconciliation_required = FALSE,
                    cancel_requested_at = NULL,
                    cancel_requested_by = '', cancel_reason = '',
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    completed_at = NULL,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND {version_predicate}
                  AND status IN (
                      'failed_retryable', 'failed_terminal', 'blocked',
                      'unknown_after_dispatch'
                  )
                  AND hold_reason = ''
                  AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                RETURNING id, execution_id, lane, status, hold_reason,
                          {version_expression} AS version_token
            """
        if queue_kind == "external_effect" and action == "cancel":
            return f"""
                UPDATE external_effect_job
                SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                    cancel_requested_by = CASE
                        WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                    END,
                    cancel_reason = CASE
                        WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                    END,
                    status = CASE WHEN status = 'dispatching' THEN status ELSE 'cancelled' END,
                    locked_by = CASE WHEN status = 'dispatching' THEN locked_by ELSE '' END,
                    locked_at = CASE WHEN status = 'dispatching' THEN locked_at ELSE NULL END,
                    lease_token = CASE WHEN status = 'dispatching' THEN lease_token ELSE '' END,
                    lease_expires_at = CASE
                        WHEN status = 'dispatching' THEN lease_expires_at ELSE NULL
                    END,
                    heartbeat_at = CASE WHEN status = 'dispatching' THEN heartbeat_at ELSE NULL END,
                    worker_generation = CASE
                        WHEN status = 'dispatching' THEN worker_generation ELSE 0
                    END,
                    cancelled_at = CASE
                        WHEN status = 'dispatching' THEN cancelled_at ELSE CURRENT_TIMESTAMP
                    END,
                    completed_at = CASE
                        WHEN status = 'dispatching' THEN completed_at ELSE CURRENT_TIMESTAMP
                    END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND {version_predicate}
                  AND status IN (
                      'planned', 'approved', 'queued', 'failed_retryable', 'dispatching'
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          {version_expression} AS version_token
            """
        if queue_kind == "internal_event" and action == "retry":
            return f"""
                WITH target AS (
                    SELECT id, consumer_name, status
                    FROM internal_event_consumer_run
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND {version_predicate}
                      AND status IN ('failed_retryable', 'failed_terminal', 'blocked')
                      AND hold_reason = ''
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    FOR UPDATE
                ), attempt AS (
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json,
                        error_code, error_message, started_at, completed_at
                    )
                    SELECT :attempt_id, target.id, target.consumer_name, 'manual_retry',
                           jsonb_build_object(
                               'manual_retry', TRUE,
                               'actor_ref_hash', CAST(:actor_ref_hash AS TEXT),
                               'actor_type', 'operator',
                               'reason', CAST(:reason AS TEXT),
                               'from_status', target.status
                           ),
                           '{{"status":"pending"}}'::jsonb,
                           'manual_retry', :reason,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM target
                    RETURNING attempt_id, consumer_run_id
                )
                UPDATE internal_event_consumer_run run
                SET status = 'pending',
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    last_attempt_id = attempt.attempt_id,
                    last_error_code = '', last_error_message = '',
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                FROM target, attempt
                WHERE run.id = target.id
                  AND attempt.consumer_run_id = target.id
                RETURNING run.id, run.execution_id, run.lane, run.status,
                          run.hold_reason, run.xmin::text AS version_token
            """
        if queue_kind == "internal_event" and action == "skip":
            return f"""
                WITH target AS (
                    SELECT id, consumer_name, status
                    FROM internal_event_consumer_run
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND {version_predicate}
                      AND status IN (
                          'pending', 'failed_retryable', 'failed_terminal', 'blocked'
                      )
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    FOR UPDATE
                ), attempt AS (
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json,
                        error_code, error_message, started_at, completed_at
                    )
                    SELECT :attempt_id, target.id, target.consumer_name, 'skipped',
                           jsonb_build_object(
                               'manual_skip', TRUE,
                               'actor_ref_hash', CAST(:actor_ref_hash AS TEXT),
                               'actor_type', 'operator',
                               'reason', CAST(:reason AS TEXT),
                               'from_status', target.status
                           ),
                           jsonb_build_object(
                               'skipped', TRUE,
                               'reason', CAST(:reason AS TEXT)
                           ),
                           'manual_skip', :reason,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM target
                    RETURNING attempt_id, consumer_run_id
                )
                UPDATE internal_event_consumer_run run
                SET status = 'skipped',
                    next_retry_at = NULL,
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    last_attempt_id = attempt.attempt_id,
                    last_error_code = 'manual_skip',
                    last_error_message = :reason,
                    result_summary_json = jsonb_build_object(
                        'skipped', TRUE,
                        'reason', CAST(:reason AS TEXT)
                    ),
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM target, attempt
                WHERE run.id = target.id
                  AND attempt.consumer_run_id = target.id
                RETURNING run.id, run.execution_id, run.lane, run.status,
                          run.hold_reason, run.xmin::text AS version_token
            """
        if queue_kind == "webhook_inbox" and action == "retry":
            return f"""
                UPDATE webhook_inbox
                SET status = 'failed_retryable',
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    locked_at = NULL, locked_by = '',
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    last_error_code = 'operator_retry',
                    last_error_message = :reason,
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND {version_predicate}
                  AND status IN (
                      'failed_retryable', 'failed_terminal', 'dead_letter', 'processing'
                  )
                  AND hold_reason = ''
                  AND (
                      status <> 'processing'
                      OR lease_expires_at <= CURRENT_TIMESTAMP
                      OR (
                          lease_expires_at IS NULL
                          AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                      )
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          {version_expression} AS version_token
            """
        if queue_kind == "webhook_inbox" and action == "skip":
            return f"""
                UPDATE webhook_inbox
                SET status = 'ignored',
                    next_retry_at = NULL,
                    locked_at = NULL, locked_by = '',
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    last_error_code = 'operator_skip',
                    last_error_message = :reason,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND {version_predicate}
                  AND status IN (
                      'received', 'failed_retryable', 'failed_terminal',
                      'dead_letter', 'processing'
                  )
                  AND (
                      status <> 'processing'
                      OR lease_expires_at <= CURRENT_TIMESTAMP
                      OR (
                          lease_expires_at IS NULL
                          AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                      )
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          {version_expression} AS version_token
            """
        raise ValueError(f"unsupported manual queue action: {queue_kind}:{action}")

    @staticmethod
    def _target(queue_kind: str, row: Any) -> QueueCommandTarget:
        values = dict(row or {})
        return QueueCommandTarget(
            queue_kind=queue_kind,
            item_id=int(values.get("id") or 0),
            execution_id=str(values.get("execution_id") or ""),
            lane=str(values.get("lane") or ""),
            status=str(values.get("status") or ""),
            version_token=str(values.get("version_token") or ""),
            hold_reason=str(values.get("hold_reason") or ""),
        )


def consume_queue_runtime_command_audit(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    queue_kind = str(payload.get("queue_kind") or "").strip()
    if queue_kind not in _QUEUE_FACTS:
        return InternalEventConsumerResult(
            status="failed_terminal",
            error_code="invalid_queue_kind",
            error_message="queue runtime command audit has an invalid queue kind",
        )
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={
            "queue_kind": queue_kind,
            "lane": str(payload.get("lane") or ""),
            "consumer_run_id": int(run.id),
        },
        response_summary={
            "audit_acknowledged": True,
            "inline_execution": False,
            "provider_call_started": False,
        },
        result_summary={"queue_kind": queue_kind, "audit_acknowledged": True},
    )


def register_queue_runtime_command_consumer(registry: Any) -> None:
    registry.register(
        QUEUE_RUNTIME_COMMAND_APPLIED,
        QUEUE_RUNTIME_COMMAND_AUDIT_CONSUMER,
        consume_queue_runtime_command_audit,
        consumer_type="diagnostic",
        max_attempts=3,
    )


__all__ = [
    "QUEUE_RUNTIME_COMMAND_APPLIED",
    "QUEUE_RUNTIME_COMMAND_AUDIT_CONSUMER",
    "QueueCommandConflict",
    "QueueCommandDuplicateRiskRequired",
    "QueueCommandResult",
    "QueueCommandTarget",
    "QueueRuntimeCommandService",
    "consume_queue_runtime_command_audit",
    "register_queue_runtime_command_consumer",
]

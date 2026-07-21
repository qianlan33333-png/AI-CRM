from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.internal_events.models import DEFAULT_TENANT_ID, InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_internal_event_outbox_in_session
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.sensitive_data import redact_sensitive_text

from .refresh import CustomerReadModelRefreshService


CUSTOMER_REFRESH_REQUESTED_EVENT = "customer_read_model.refresh.requested"
CUSTOMER_REFRESH_COMPLETED_EVENT = "customer_read_model.refreshed"
CUSTOMER_REFRESH_CONSUMER = "customer_read_model_refresh_intent_consumer"
CUSTOMER_REFRESH_COMPLETED_CONSUMER = "customer_read_model_refresh_completed_audit_consumer"
CUSTOMER_DIRTY_CONSUMER = "customer_read_model_dirty_consumer"
CUSTOMER_SOURCE_EVENTS = (
    "channel_entry.entered",
    "customer.phone_bound",
    "identity.resolved",
    "message_archive.batch_ingested",
    "payment.succeeded",
    "questionnaire.submitted",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _opaque(value: Any) -> str:
    normalized = _text(value)
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str, separators=(",", ":"))


def _new_execution_id(prefix: str) -> str:
    return f"exe_customer_read_model_{prefix}_{uuid4().hex}"


def _summary(row: Any) -> dict[str, Any]:
    payload = dict(row or {})
    keys = (
        "singleton_id",
        "dirty_generation",
        "completed_generation",
        "signal_generation",
        "running_generation",
        "status",
        "execution_id",
        "parent_execution_id",
        "running_execution_id",
        "running_parent_execution_id",
        "lane",
        "available_at",
        "attempt_count",
        "max_attempts",
        "last_error_code",
        "row_version",
        "updated_at",
        "completed_at",
    )
    return {key: payload.get(key) for key in keys if key in payload}


class CustomerReadModelRefreshIntentRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def get(self) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1")
            ).mappings().fetchone()
            return dict(row) if row else None

    def mark_dirty(
        self,
        *,
        source_event_key: str,
        source_event_type: str,
        execution_id: str = "",
        parent_execution_id: str = "",
        recover_blocked: bool = False,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self.mark_dirty_in_session(
                session,
                source_event_key=source_event_key,
                source_event_type=source_event_type,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                recover_blocked=recover_blocked,
                expected_row_version=expected_row_version,
            )
            session.commit()
            return result

    def mark_dirty_in_session(
        self,
        session: Session,
        *,
        source_event_key: str,
        source_event_type: str,
        execution_id: str = "",
        parent_execution_id: str = "",
        recover_blocked: bool = False,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        event_key = _opaque(source_event_key)
        if not event_key:
            raise ValueError("customer_read_model_source_event_key_required")
        session.execute(
            text(
                """
                INSERT INTO customer_read_model_refresh_intent (singleton_id)
                VALUES (1)
                ON CONFLICT (singleton_id) DO NOTHING
                """
            )
        )
        receipt = session.execute(
            text(
                """
                INSERT INTO customer_read_model_refresh_source_receipt (
                    source_event_key, source_event_type, generation,
                    execution_id, parent_execution_id, created_at
                ) VALUES (
                    :source_event_key, :source_event_type, 1,
                    :execution_id, :parent_execution_id, CURRENT_TIMESTAMP
                )
                ON CONFLICT (source_event_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "source_event_key": event_key,
                "source_event_type": _text(source_event_type),
                "execution_id": _text(execution_id),
                "parent_execution_id": _text(parent_execution_id),
            },
        ).mappings().fetchone()
        if not receipt:
            current = session.execute(
                text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1")
            ).mappings().one()
            return {
                "ok": True,
                "deduplicated": True,
                "source_event_key": event_key,
                "intent": _summary(current),
                "real_external_call_executed": False,
            }
        current = session.execute(
            text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1 FOR UPDATE")
        ).mappings().one()
        status = _text(current.get("status"))
        blocked_recovery = status == "blocked" and bool(recover_blocked)
        if blocked_recovery and (
            expected_row_version is None
            or int(expected_row_version) != int(current.get("row_version") or 0)
        ):
            raise RuntimeError("customer_read_model_refresh_recovery_version_mismatch")
        declared_owner = status in {"waiting", "running", "retry_wait"} and int(
            current.get("signal_generation") or 0
        ) > int(current.get("completed_generation") or 0)
        has_owner = declared_owner and self._has_live_signal_owner(
            session,
            signal_generation=int(current.get("signal_generation") or 0),
        )
        missing_owner_recovered = declared_owner and not has_owner
        superseded_owner = (
            self._quarantine_dead_signal_owner(
                session,
                signal_generation=int(current.get("signal_generation") or 0),
            )
            if missing_owner_recovered
            else {"outbox_count": 0, "consumer_run_count": 0}
        )
        generation = int(current.get("dirty_generation") or 0) + 1
        root_execution_id = _text(execution_id) or _new_execution_id("refresh")
        should_signal = not has_owner and (status != "blocked" or blocked_recovery)
        updated = session.execute(
            text(
                """
                UPDATE customer_read_model_refresh_intent
                SET dirty_generation = :generation,
                    signal_generation = CASE WHEN :should_signal THEN :generation ELSE signal_generation END,
                    status = CASE WHEN :should_signal THEN 'waiting' ELSE status END,
                    execution_id = :execution_id,
                    parent_execution_id = :parent_execution_id,
                    available_at = CASE WHEN :should_signal THEN CURRENT_TIMESTAMP ELSE available_at END,
                    attempt_count = CASE WHEN :should_signal THEN 0 ELSE attempt_count END,
                    running_generation = CASE WHEN :should_signal THEN 0 ELSE running_generation END,
                    running_execution_id = CASE WHEN :should_signal THEN '' ELSE running_execution_id END,
                    running_parent_execution_id = CASE WHEN :should_signal THEN '' ELSE running_parent_execution_id END,
                    owner_consumer_run_id = CASE WHEN :should_signal THEN NULL ELSE owner_consumer_run_id END,
                    owner_lease_token = CASE WHEN :should_signal THEN '' ELSE owner_lease_token END,
                    last_source_event_key = :source_event_key,
                    last_error_code = CASE
                        WHEN :missing_owner_recovered THEN 'missing_signal_owner_recovered'
                        WHEN :should_signal THEN ''
                        ELSE last_error_code
                    END,
                    last_error_message = CASE WHEN :should_signal THEN '' ELSE last_error_message END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE singleton_id = 1
                RETURNING *
                """
            ),
            {
                "generation": generation,
                "should_signal": should_signal,
                "missing_owner_recovered": missing_owner_recovered,
                "execution_id": root_execution_id,
                "parent_execution_id": _text(parent_execution_id),
                "source_event_key": event_key,
            },
        ).mappings().one()
        session.execute(
            text(
                """
                UPDATE customer_read_model_refresh_source_receipt
                SET generation = :generation,
                    execution_id = :execution_id,
                    parent_execution_id = :parent_execution_id
                WHERE id = :receipt_id
                """
            ),
            {
                "generation": generation,
                "execution_id": root_execution_id,
                "parent_execution_id": _text(parent_execution_id),
                "receipt_id": int(receipt["id"]),
            },
        )
        signal = None
        if should_signal:
            signal = self._enqueue_signal(
                session,
                generation=generation,
                execution_id=root_execution_id,
                parent_execution_id=parent_execution_id,
            )
        return {
            "ok": True,
            "deduplicated": False,
            "generation": generation,
            "signal_created": bool(signal),
            "missing_owner_recovered": missing_owner_recovered,
            "blocked_recovered": blocked_recovery,
            "superseded_owner": superseded_owner,
            "execution_id": root_execution_id,
            "parent_execution_id": _text(parent_execution_id),
            "intent": _summary(updated),
            "real_external_call_executed": False,
        }

    @staticmethod
    def _has_live_signal_owner(session: Session, *, signal_generation: int) -> bool:
        """Confirm that the coalesced signal still has a claimable durable owner.

        Intent state alone is insufficient: a terminal, held, stale-policy, or
        missing outbox/consumer row cannot ever complete the projection.  A new
        source event may safely replace only that local, pre-provider signal.
        """

        if int(signal_generation or 0) <= 0:
            return False
        idempotency_key = f"customer_read_model.refresh.requested:{int(signal_generation)}"
        return bool(
            session.execute(
                text(
                    """
                    WITH control AS (
                        SELECT active_generation, policy_version
                        FROM queue_runtime_control
                        WHERE singleton = TRUE
                    ), live_owner AS (
                        SELECT 1
                        FROM internal_event_outbox outbox
                        CROSS JOIN control
                        WHERE outbox.tenant_id = :tenant_id
                          AND outbox.idempotency_key = :idempotency_key
                          AND outbox.policy_version = control.policy_version
                          AND outbox.worker_generation IN (0, control.active_generation)
                          AND outbox.hold_reason = ''
                          AND (
                              (
                                  outbox.status IN ('pending', 'failed_retryable')
                                  AND outbox.attempt_count < outbox.max_attempts
                              )
                              OR (
                                  outbox.status = 'running'
                                  AND outbox.lease_token <> ''
                                  AND outbox.lease_expires_at > CURRENT_TIMESTAMP
                              )
                          )
                        UNION ALL
                        SELECT 1
                        FROM internal_event_consumer_run run
                        JOIN internal_event event ON event.event_id = run.event_id
                        CROSS JOIN control
                        WHERE event.tenant_id = :tenant_id
                          AND event.idempotency_key = :idempotency_key
                          AND run.consumer_name = :consumer_name
                          AND run.policy_version = control.policy_version
                          AND run.worker_generation IN (0, control.active_generation)
                          AND run.hold_reason = ''
                          AND (
                              (
                                  run.status IN ('pending', 'failed_retryable')
                                  AND run.attempt_count < run.max_attempts
                              )
                              OR (
                                  run.status = 'running'
                                  AND run.lease_token <> ''
                                  AND run.lease_expires_at > CURRENT_TIMESTAMP
                              )
                          )
                    )
                    SELECT EXISTS(SELECT 1 FROM live_owner)
                    """
                ),
                {
                    "tenant_id": DEFAULT_TENANT_ID,
                    "idempotency_key": idempotency_key,
                    "consumer_name": CUSTOMER_REFRESH_CONSUMER,
                },
            ).scalar_one()
        )

    @staticmethod
    def _quarantine_dead_signal_owner(session: Session, *, signal_generation: int) -> dict[str, int]:
        if int(signal_generation or 0) <= 0:
            return {"outbox_count": 0, "consumer_run_count": 0}
        idempotency_key = f"customer_read_model.refresh.requested:{int(signal_generation)}"
        params = {
            "tenant_id": DEFAULT_TENANT_ID,
            "idempotency_key": idempotency_key,
            "consumer_name": CUSTOMER_REFRESH_CONSUMER,
        }
        outbox_rows = session.execute(
            text(
                """
                WITH control AS (
                    SELECT active_generation, policy_version
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                )
                UPDATE internal_event_outbox outbox
                SET status = 'failed_terminal',
                    hold_reason = 'superseded_missing_signal_owner',
                    lease_token = '', locked_by = '', locked_at = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    last_error_code = 'superseded_missing_signal_owner',
                    last_error_message = '', updated_at = CURRENT_TIMESTAMP
                FROM control
                WHERE outbox.tenant_id = :tenant_id
                  AND outbox.idempotency_key = :idempotency_key
                  AND (
                      (
                          outbox.status IN ('pending', 'failed_retryable')
                          AND (
                              outbox.policy_version <> control.policy_version
                              OR outbox.worker_generation NOT IN (0, control.active_generation)
                              OR outbox.hold_reason <> ''
                              OR outbox.attempt_count >= outbox.max_attempts
                          )
                      )
                      OR (
                          outbox.status = 'running'
                          AND COALESCE(outbox.lease_expires_at, '-infinity'::timestamptz)
                              <= CURRENT_TIMESTAMP
                      )
                  )
                RETURNING outbox.id
                """
            ),
            params,
        ).mappings().fetchall()
        consumer_rows = session.execute(
            text(
                """
                WITH control AS (
                    SELECT active_generation, policy_version
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                ), signal_event AS (
                    SELECT event_id
                    FROM internal_event
                    WHERE tenant_id = :tenant_id
                      AND idempotency_key = :idempotency_key
                )
                UPDATE internal_event_consumer_run run
                SET status = 'blocked',
                    hold_reason = 'superseded_missing_signal_owner',
                    lease_token = '', locked_by = '', locked_at = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    last_error_code = 'superseded_missing_signal_owner',
                    last_error_message = '', updated_at = CURRENT_TIMESTAMP
                FROM control, signal_event
                WHERE run.event_id = signal_event.event_id
                  AND run.consumer_name = :consumer_name
                  AND (
                      (
                          run.status IN ('pending', 'failed_retryable')
                          AND (
                              run.policy_version <> control.policy_version
                              OR run.worker_generation NOT IN (0, control.active_generation)
                              OR run.hold_reason <> ''
                              OR run.attempt_count >= run.max_attempts
                          )
                      )
                      OR (
                          run.status = 'running'
                          AND COALESCE(run.lease_expires_at, '-infinity'::timestamptz)
                              <= CURRENT_TIMESTAMP
                      )
                  )
                RETURNING run.id
                """
            ),
            params,
        ).mappings().fetchall()
        return {
            "outbox_count": len(outbox_rows),
            "consumer_run_count": len(consumer_rows),
        }

    def claim_latest(
        self,
        *,
        signal_generation: int,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.execute(
                text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1 FOR UPDATE")
            ).mappings().one()
            status = _text(row.get("status"))
            dirty = int(row.get("dirty_generation") or 0)
            completed = int(row.get("completed_generation") or 0)
            if completed >= dirty or status == "idle":
                return {"ok": True, "claimed": False, "reason": "already_completed"}
            if status == "running":
                same_run_reclaim = (
                    int(owner_consumer_run_id or 0) > 0
                    and int(row.get("owner_consumer_run_id") or 0) == int(owner_consumer_run_id)
                    and bool(_text(owner_lease_token))
                    and _text(row.get("owner_lease_token")) != _text(owner_lease_token)
                )
                if not same_run_reclaim:
                    return {"ok": True, "claimed": False, "reason": "already_running"}
                session.execute(
                    text(
                        """
                        UPDATE customer_read_model_refresh_intent
                        SET status = 'retry_wait', running_generation = 0,
                            running_execution_id = '', running_parent_execution_id = '',
                            owner_consumer_run_id = NULL, owner_lease_token = '',
                            last_error_code = 'consumer_lease_reclaimed',
                            row_version = row_version + 1, updated_at = CURRENT_TIMESTAMP
                        WHERE singleton_id = 1
                        """
                    )
                )
            if status in {"waiting", "retry_wait"}:
                due = bool(
                    session.execute(
                        text(
                            """
                            SELECT available_at <= CURRENT_TIMESTAMP
                            FROM customer_read_model_refresh_intent
                            WHERE singleton_id = 1
                            """
                        )
                    ).scalar_one()
                )
                if not due:
                    return {"ok": True, "claimed": False, "reason": "not_available"}
            if status == "blocked" or int(row.get("attempt_count") or 0) >= int(row.get("max_attempts") or 0):
                session.execute(
                    text(
                        """
                        UPDATE customer_read_model_refresh_intent
                        SET status = 'blocked', row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE singleton_id = 1
                        """
                    )
                )
                session.commit()
                return {"ok": False, "claimed": False, "reason": "attempt_budget_exhausted"}
            claimed = session.execute(
                text(
                    """
                    UPDATE customer_read_model_refresh_intent
                    SET status = 'running',
                        running_generation = dirty_generation,
                        running_execution_id = execution_id,
                        running_parent_execution_id = parent_execution_id,
                        owner_consumer_run_id = :owner_consumer_run_id,
                        owner_lease_token = :owner_lease_token,
                        attempt_count = attempt_count + 1,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton_id = 1
                      AND status IN ('waiting', 'retry_wait')
                    RETURNING *
                    """
                ),
                {
                    "owner_consumer_run_id": int(owner_consumer_run_id or 0) or None,
                    "owner_lease_token": _text(owner_lease_token),
                },
            ).mappings().fetchone()
            session.commit()
            if not claimed:
                return {"ok": True, "claimed": False, "reason": "claim_raced"}
            return {
                **dict(claimed),
                "ok": True,
                "claimed": True,
                "requested_signal_generation": int(signal_generation or 0),
                "coalesced_to_latest": int(signal_generation or 0) != int(claimed.get("running_generation") or 0),
            }

    def complete(
        self,
        *,
        generation: int,
        result: dict[str, Any],
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            current = session.execute(
                text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1 FOR UPDATE")
            ).mappings().one()
            owner_mismatch = bool(owner_consumer_run_id or owner_lease_token) and (
                int(current.get("owner_consumer_run_id") or 0) != int(owner_consumer_run_id or 0)
                or _text(current.get("owner_lease_token")) != _text(owner_lease_token)
            )
            if _text(current.get("status")) != "running" or int(current.get("running_generation") or 0) != int(generation) or owner_mismatch:
                return {"ok": True, "completed": False, "reason": "stale_completion"}
            completion_execution_id = _new_execution_id("completed")
            completion = enqueue_internal_event_outbox_in_session(
                session,
                InternalEventCreateRequest(
                    event_type=CUSTOMER_REFRESH_COMPLETED_EVENT,
                    aggregate_type="customer_read_model",
                    aggregate_id="singleton",
                    subject_type="customer_read_model",
                    subject_id="singleton",
                    idempotency_key=f"customer_read_model.refreshed:{int(generation)}",
                    source_module="customer_read_model.refresh_intents",
                    payload={
                        "generation": int(generation),
                        "source_count": int(result.get("source_count") or 0),
                        "target_count": int(result.get("target_count_after") or 0),
                        "duration_ms": int(result.get("duration_ms") or 0),
                    },
                    payload_summary={
                        "generation": int(generation),
                        "source_count": int(result.get("source_count") or 0),
                        "target_count": int(result.get("target_count_after") or 0),
                    },
                    context=CommandContext(
                        actor_id="customer_read_model_refresh_intent",
                        actor_type="system",
                        source_route="customer_read_model.refresh.complete",
                    ),
                    execution_id=completion_execution_id,
                    parent_execution_id=_text(current.get("running_execution_id")),
                ),
            )
            dirty = int(current.get("dirty_generation") or 0)
            has_continuation = dirty > int(generation)
            next_signal = None
            if has_continuation:
                next_signal = self._enqueue_signal(
                    session,
                    generation=dirty,
                    execution_id=_text(current.get("execution_id")),
                    parent_execution_id=_text(current.get("parent_execution_id")),
                )
            updated = session.execute(
                text(
                    """
                    UPDATE customer_read_model_refresh_intent
                    SET completed_generation = :generation,
                        signal_generation = CASE WHEN :has_continuation THEN dirty_generation ELSE signal_generation END,
                        running_generation = 0,
                        status = CASE WHEN :has_continuation THEN 'waiting' ELSE 'idle' END,
                        running_execution_id = '', running_parent_execution_id = '',
                        owner_consumer_run_id = NULL, owner_lease_token = '',
                        attempt_count = 0,
                        last_error_code = '', last_error_message = '',
                        available_at = CURRENT_TIMESTAMP,
                        completed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton_id = 1
                    RETURNING *
                    """
                ),
                {"generation": int(generation), "has_continuation": has_continuation},
            ).mappings().one()
            session.commit()
            return {
                "ok": True,
                "completed": True,
                "generation": int(generation),
                "continuation_created": bool(next_signal),
                "completion_event": dict(completion),
                "intent": _summary(updated),
                "real_external_call_executed": False,
            }

    def fail(
        self,
        *,
        generation: int,
        error_code: str,
        error_message: str,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            current = session.execute(
                text("SELECT * FROM customer_read_model_refresh_intent WHERE singleton_id = 1 FOR UPDATE")
            ).mappings().one()
            owner_mismatch = bool(owner_consumer_run_id or owner_lease_token) and (
                int(current.get("owner_consumer_run_id") or 0) != int(owner_consumer_run_id or 0)
                or _text(current.get("owner_lease_token")) != _text(owner_lease_token)
            )
            if _text(current.get("status")) != "running" or int(current.get("running_generation") or 0) != int(generation) or owner_mismatch:
                return {"ok": True, "failed": False, "reason": "stale_failure"}
            terminal = int(current.get("attempt_count") or 0) >= int(current.get("max_attempts") or 0)
            updated = session.execute(
                text(
                    """
                    UPDATE customer_read_model_refresh_intent
                    SET status = :status,
                        running_generation = 0,
                        running_execution_id = '', running_parent_execution_id = '',
                        owner_consumer_run_id = NULL, owner_lease_token = '',
                        available_at = CURRENT_TIMESTAMP + INTERVAL '60 seconds',
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton_id = 1
                    RETURNING *
                    """
                ),
                {
                    "status": "blocked" if terminal else "retry_wait",
                    "error_code": _text(error_code)[:120] or "customer_read_model_refresh_failed",
                    "error_message": redact_sensitive_text(error_message)[:1000],
                },
            ).mappings().one()
            session.commit()
            return {"ok": not terminal, "failed": True, "terminal": terminal, "intent": _summary(updated)}

    @staticmethod
    def _enqueue_signal(
        session: Session,
        *,
        generation: int,
        execution_id: str,
        parent_execution_id: str,
    ) -> dict[str, Any]:
        return enqueue_internal_event_outbox_in_session(
            session,
            InternalEventCreateRequest(
                event_type=CUSTOMER_REFRESH_REQUESTED_EVENT,
                aggregate_type="customer_read_model",
                aggregate_id="singleton",
                subject_type="customer_read_model",
                subject_id="singleton",
                idempotency_key=f"customer_read_model.refresh.requested:{int(generation)}",
                source_module="customer_read_model.refresh_intents",
                payload={"generation": int(generation)},
                payload_summary={"generation": int(generation), "lane": "internal_general"},
                context=CommandContext(
                    actor_id="customer_read_model_dirty",
                    actor_type="system",
                    source_route="customer_read_model.refresh.request",
                ),
                execution_id=_text(execution_id) or _new_execution_id("refresh"),
                parent_execution_id=_text(parent_execution_id),
            ),
        )


class CustomerReadModelRefreshIntentService:
    def __init__(
        self,
        repository: CustomerReadModelRefreshIntentRepository | None = None,
        refresh_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._repo = repository or CustomerReadModelRefreshIntentRepository()
        self._refresh_runner = refresh_runner or CustomerReadModelRefreshService().run

    def request_refresh(
        self,
        *,
        source_event_key: str,
        source_event_type: str,
        execution_id: str = "",
        parent_execution_id: str = "",
        recover_blocked: bool = False,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        return self._repo.mark_dirty(
            source_event_key=source_event_key,
            source_event_type=source_event_type,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            recover_blocked=recover_blocked,
            expected_row_version=expected_row_version,
        )

    def process_requested(
        self,
        *,
        signal_generation: int,
        owner_consumer_run_id: int = 0,
        owner_lease_token: str = "",
    ) -> dict[str, Any]:
        claim = self._repo.claim_latest(
            signal_generation=signal_generation,
            owner_consumer_run_id=owner_consumer_run_id,
            owner_lease_token=owner_lease_token,
        )
        if not claim.get("claimed"):
            return {**claim, "ok": claim.get("reason") == "already_completed", "real_external_call_executed": False}
        generation = int(claim.get("running_generation") or 0)
        try:
            raw_result = self._refresh_runner(dry_run=False)
            result = raw_result.to_dict() if hasattr(raw_result, "to_dict") else dict(raw_result or {})
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc))[:1000]
            failure = self._repo.fail(
                generation=generation,
                error_code="customer_read_model_refresh_exception",
                error_message=safe_error,
                owner_consumer_run_id=owner_consumer_run_id,
                owner_lease_token=owner_lease_token,
            )
            return {
                "ok": False,
                "claimed": True,
                "generation": generation,
                "error": safe_error,
                "failure": failure,
                "real_external_call_executed": False,
            }
        if not result.get("ok"):
            safe_error = redact_sensitive_text(_text(result.get("error")) or "customer_read_model_refresh_failed")[:1000]
            failure = self._repo.fail(
                generation=generation,
                error_code="customer_read_model_refresh_failed",
                error_message=safe_error,
                owner_consumer_run_id=owner_consumer_run_id,
                owner_lease_token=owner_lease_token,
            )
            return {**result, "ok": False, "error": safe_error, "failure": failure, "generation": generation}
        completion = self._repo.complete(
            generation=generation,
            result=result,
            owner_consumer_run_id=owner_consumer_run_id,
            owner_lease_token=owner_lease_token,
        )
        return {
            **result,
            "claimed": True,
            "generation": generation,
            "completion": completion,
            "real_external_call_executed": False,
        }


__all__ = [
    "CUSTOMER_DIRTY_CONSUMER",
    "CUSTOMER_REFRESH_COMPLETED_EVENT",
    "CUSTOMER_REFRESH_COMPLETED_CONSUMER",
    "CUSTOMER_REFRESH_CONSUMER",
    "CUSTOMER_REFRESH_REQUESTED_EVENT",
    "CUSTOMER_SOURCE_EVENTS",
    "CustomerReadModelRefreshIntentRepository",
    "CustomerReadModelRefreshIntentService",
]

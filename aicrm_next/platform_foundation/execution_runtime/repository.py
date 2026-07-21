from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime import raw_database_url
from aicrm_next.platform_foundation.external_effects.repo_contract import _public_attempt, _public_job
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    build_external_effect_settled_event,
)
from aicrm_next.platform_foundation.internal_events.outbox import (
    enqueue_transactional_internal_event_outbox,
)


def normalize_runtime_database_url(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("postgresql+psycopg://"):
        return "postgresql://" + normalized[len("postgresql+psycopg://") :]
    if normalized.startswith("postgres://"):
        return "postgresql://" + normalized[len("postgres://") :]
    return normalized


def open_runtime_connection(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


# Kept as module-local compatibility aliases for the PR-2 read model.  New
# runtime control code uses the explicit public boundary names above.
_psycopg_url = normalize_runtime_database_url
_default_connect = open_runtime_connection


def open_listener_connection(url: str, *, autocommit: bool, application_name: str):
    """Open the dedicated session-scoped LISTEN connection at the DB boundary."""

    import psycopg

    return psycopg.connect(
        url,
        autocommit=autocommit,
        application_name=application_name,
    )


def external_canary_authorization_predicate(*, row_alias: str = "job") -> str:
    """Return the durable SQL proof required by an allowlisted canary row."""

    authorization = f"COALESCE({row_alias}.payload_summary_json, '{{}}'::jsonb)->'canary_authorization'"
    authorized_job_id = f"""
        CASE
            WHEN jsonb_typeof({authorization}->'authorized_job_id') = 'number'
             AND ({authorization}->>'authorized_job_id') ~ '^[1-9][0-9]*$'
            THEN ({authorization}->>'authorized_job_id')::numeric
            ELSE 0::numeric
        END
    """
    authorized_from_version = f"""
        CASE
            WHEN jsonb_typeof({authorization}->'authorized_from_version') = 'number'
             AND ({authorization}->>'authorized_from_version') ~ '^[1-9][0-9]*$'
            THEN ({authorization}->>'authorized_from_version')::numeric
            ELSE 0::numeric
        END
    """
    return f"""
        (
            jsonb_typeof({authorization}) = 'object'
            AND COALESCE({authorization}->>'actor', '') <> ''
            AND COALESCE({authorization}->>'reason', '') <> ''
            AND COALESCE({authorization}->>'authorized_at', '') <> ''
            AND ({authorized_job_id}) = {row_alias}.id::numeric
            AND ({authorized_from_version}) >= 1::numeric
            AND {row_alias}.row_version::numeric >= ({authorized_from_version}) + 1::numeric
            AND {authorization}->'duplicate_risk_confirmed' = 'false'::jsonb
        )
    """


def external_claim_scope_predicate(
    *,
    row_alias: str = "job",
    scope_expression: str = "control.external_claim_scope",
    execution_scope_expression: str | None = None,
    canary_authorized_expression: str | None = None,
) -> str:
    """Return the canonical SQL gate for external-effect execution scope.

    The expression is composed only from trusted static aliases supplied by
    this module and the read model; no request data is interpolated here.
    """

    execution_scope = execution_scope_expression or f"COALESCE({row_alias}.payload_json->>'execution_scope', '')"
    canary_authorized = canary_authorized_expression or external_canary_authorization_predicate(
        row_alias=row_alias
    )
    return f"""
        (
            (
                {execution_scope} <> 'allowlisted_canary'
                OR ({canary_authorized})
            )
            AND (
                {scope_expression} = 'all'
                OR (
                    {scope_expression} = 'allowlisted'
                    AND {execution_scope} IN ('test_loopback', 'allowlisted_canary')
                )
                OR (
                    {scope_expression} = 'test_loopback'
                    AND {execution_scope} = 'test_loopback'
                )
            )
        )
    """


def external_effect_claim_order_sql(
    *,
    row_alias: str = "job",
    fairness_alias: str = "fairness",
) -> str:
    """Return the canonical external-effect claim order.

    Keep operational read models on the same fairness/priority/due ordering as
    ``claim_external_effect_one``.  Aliases are trusted static identifiers
    supplied by repository code, never request input.
    """

    return (
        f"COALESCE({fairness_alias}.last_claimed_at, '-infinity'::timestamptz), "
        f"{row_alias}.priority ASC, {row_alias}.available_at ASC, {row_alias}.id ASC"
    )


@dataclass(frozen=True)
class RuntimeControl:
    active_generation: int
    claim_enabled: bool
    rollout_mode: str
    global_max_in_flight: int
    policy_version: str
    external_claim_scope: str = "blocked"


@dataclass(frozen=True)
class LanePolicy:
    lane: str
    max_in_flight: int
    enabled: bool
    rollout_mode: str
    blocked_until: datetime | None
    policy_version: str


@dataclass(frozen=True)
class RuntimeClaim:
    queue_kind: str
    item_id: int
    execution_id: str
    lane: str
    lease_token: str
    lease_expires_at: datetime
    worker_generation: int
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionRuntimeRepository:
    """Cross-process capacity gate for the three independent queue facts.

    The repository never stores task payloads in a generic table. It locks the
    global control row and one lane policy row, checks live leases in each fact
    table, and claims exactly one domain row.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[[str], Any] = open_runtime_connection,
    ) -> None:
        self._database_url = normalize_runtime_database_url(database_url or raw_database_url())
        if not self._database_url.startswith("postgresql://"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required for the execution runtime")
        self._connect = connect

    def read_control(self) -> RuntimeControl:
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT active_generation, claim_enabled, rollout_mode,
                       global_max_in_flight, policy_version,
                       external_claim_scope
                FROM queue_runtime_control
                WHERE singleton = TRUE
                """
            ).fetchone()
        if not row:
            raise RuntimeError("queue runtime control row is missing")
        return self._control(row)

    def claim_external_effect_one(
        self,
        *,
        lane: str,
        worker_id: str,
        generation: int,
        lease_seconds: int = 30,
        test_only: bool = False,
    ) -> RuntimeClaim | None:
        return self._claim_one(
            queue_kind="external_effect",
            lane=lane,
            worker_id=worker_id,
            generation=generation,
            lease_seconds=lease_seconds,
            test_only=test_only,
        )

    def claim_internal_event_one(
        self,
        *,
        lane: str,
        worker_id: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> RuntimeClaim | None:
        return self._claim_one(
            queue_kind="internal_event",
            lane=lane,
            worker_id=worker_id,
            generation=generation,
            lease_seconds=lease_seconds,
            test_only=False,
        )

    def claim_internal_outbox_one(
        self,
        *,
        lane: str,
        worker_id: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> RuntimeClaim | None:
        return self._claim_one(
            queue_kind="internal_outbox",
            lane=lane,
            worker_id=worker_id,
            generation=generation,
            lease_seconds=lease_seconds,
            test_only=False,
        )

    def claim_webhook_inbox_one(
        self,
        *,
        worker_id: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> RuntimeClaim | None:
        return self._claim_one(
            queue_kind="webhook_inbox",
            lane="webhook_inbox",
            worker_id=worker_id,
            generation=generation,
            lease_seconds=lease_seconds,
            test_only=False,
        )

    def _claim_one(
        self,
        *,
        queue_kind: str,
        lane: str,
        worker_id: str,
        generation: int,
        lease_seconds: int,
        test_only: bool,
    ) -> RuntimeClaim | None:
        normalized_lane = str(lane or "").strip()
        if not normalized_lane:
            raise ValueError("lane is required")
        if queue_kind not in {
            "external_effect",
            "internal_event",
            "internal_outbox",
            "webhook_inbox",
        }:
            raise ValueError("unsupported queue kind")
        ttl = max(10, min(int(lease_seconds or 30), 300))
        lease_token = "qrl_" + uuid4().hex
        with self._connect(self._database_url) as connection:
            with connection.transaction():
                control_row = connection.execute(
                    """
                    SELECT active_generation, claim_enabled, rollout_mode,
                           global_max_in_flight, policy_version,
                           external_claim_scope
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                    FOR UPDATE
                    """
                ).fetchone()
                lane_row = connection.execute(
                    """
                    SELECT lane, max_in_flight, enabled, rollout_mode,
                           blocked_until, policy_version
                    FROM queue_lane_policy
                    WHERE lane = %s
                    FOR UPDATE
                    """,
                    (normalized_lane,),
                ).fetchone()
                if not control_row or not lane_row:
                    raise RuntimeError("queue runtime policy is incomplete")
                control = self._control(control_row)
                policy = self._lane_policy(lane_row)
                if not self._claim_allowed(control=control, lane=policy, generation=generation):
                    return None
                self._recover_expired_claims(
                    connection,
                    queue_kind=queue_kind,
                    lane=normalized_lane,
                )
                in_flight = connection.execute(self._in_flight_sql(), (normalized_lane,)).fetchone()
                global_count = int((in_flight or {}).get("global_count") or 0)
                lane_count = int((in_flight or {}).get("lane_count") or 0)
                if global_count >= control.global_max_in_flight or lane_count >= policy.max_in_flight:
                    return None
                row = connection.execute(
                    self._claim_sql(queue_kind=queue_kind, test_only=test_only),
                    (
                        normalized_lane,
                        int(generation),
                        str(worker_id or "").strip(),
                        lease_token,
                        ttl,
                        int(generation),
                    ),
                ).fetchone()
                if not row:
                    return None
                fairness_key = str(row.get("fairness_key") or "default")
                connection.execute(
                    """
                    INSERT INTO queue_fairness_cursor (
                        lane, fairness_key, last_claimed_at, claim_count
                    ) VALUES (%s, %s, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT (lane, fairness_key) DO UPDATE
                    SET last_claimed_at = EXCLUDED.last_claimed_at,
                        claim_count = queue_fairness_cursor.claim_count + 1
                    """,
                    (normalized_lane, fairness_key),
                )
        return RuntimeClaim(
            queue_kind=queue_kind,
            item_id=int(row.get("id") or 0),
            execution_id=str(row.get("execution_id") or ""),
            lane=normalized_lane,
            lease_token=lease_token,
            lease_expires_at=row["lease_expires_at"],
            worker_generation=int(generation),
            payload=dict(row),
        )

    @staticmethod
    def _recover_expired_claims(connection: Any, *, queue_kind: str, lane: str) -> None:
        if queue_kind == "external_effect":
            connection.execute(
                """
                INSERT INTO queue_runtime_lease_recovery_event (
                    queue_kind, queue_row_id, worker_generation,
                    error_code, lease_expires_at
                )
                SELECT 'external_effect', id, worker_generation,
                       CASE
                           WHEN provider_call_started_at IS NULL
                           THEN 'lease_expired_before_dispatch'
                           ELSE 'lease_expired_after_dispatch'
                       END,
                       lease_expires_at
                FROM external_effect_job
                WHERE lane = %s
                  AND status = 'dispatching'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                ON CONFLICT (
                    queue_kind, queue_row_id, lease_expires_at, error_code
                ) DO NOTHING
                """,
                (lane,),
            )
            connection.execute(
                """
                UPDATE external_effect_attempt attempt
                SET status = 'unknown_after_dispatch',
                    response_summary_json = COALESCE(attempt.response_summary_json, '{}'::jsonb)
                        || '{"provider_result_received":false,"lease_expired":true}'::jsonb,
                    error_code = CASE
                        WHEN attempt.error_code = '' THEN 'lease_expired_after_dispatch'
                        ELSE attempt.error_code
                    END,
                    error_message = CASE
                        WHEN attempt.error_message = ''
                        THEN 'Dispatch lease expired; reconcile provider outcome before retry.'
                        ELSE attempt.error_message
                    END,
                    completed_at = COALESCE(attempt.completed_at, CURRENT_TIMESTAMP)
                FROM external_effect_job job
                WHERE job.lane = %s
                  AND job.status = 'dispatching'
                  AND job.lease_expires_at IS NOT NULL
                  AND job.lease_expires_at <= CURRENT_TIMESTAMP
                  AND job.provider_call_started_at IS NOT NULL
                  AND attempt.job_id = job.id
                  AND attempt.attempt_id = job.last_attempt_id
                  AND attempt.lease_token = job.lease_token
                  AND attempt.status = 'dispatching'
                """,
                (lane,),
            )
            unknown_rows = connection.execute(
                """
                UPDATE external_effect_job
                SET status = 'unknown_after_dispatch',
                    attempt_count = attempt_count + 1,
                    reconciliation_required = TRUE,
                    last_error_code = 'lease_expired_after_dispatch',
                    last_error_message = 'Dispatch lease expired; reconcile provider outcome before retry.',
                    lease_token = '',
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    locked_by = '',
                    locked_at = NULL,
                    completed_at = CURRENT_TIMESTAMP,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lane = %s
                  AND status = 'dispatching'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                  AND provider_call_started_at IS NOT NULL
                RETURNING *
                """,
                (lane,),
            ).fetchall()
            for unknown_row in unknown_rows:
                job = _public_job(dict(unknown_row))
                if job is None:
                    raise RuntimeError("stale external effect settlement job projection failed")
                attempt_row = connection.execute(
                    "SELECT * FROM external_effect_attempt WHERE attempt_id = %s AND job_id = %s",
                    (job.last_attempt_id, int(job.id)),
                ).fetchone()
                attempt = _public_attempt(dict(attempt_row)) if attempt_row else None
                enqueue_transactional_internal_event_outbox(
                    connection,
                    build_external_effect_settled_event(job=job, attempt=attempt),
                )
            connection.execute(
                """
                UPDATE external_effect_job
                SET status = 'queued',
                    available_at = CURRENT_TIMESTAMP,
                    next_retry_at = CURRENT_TIMESTAMP,
                    worker_generation = 0,
                    last_error_code = 'lease_expired_before_dispatch',
                    last_error_message = 'Pre-dispatch lease expired and was safely requeued.',
                    lease_token = '',
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    locked_by = '',
                    locked_at = NULL,
                    dispatch_started_at = NULL,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lane = %s
                  AND status = 'dispatching'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                  AND provider_call_started_at IS NULL
                """,
                (lane,),
            )
            return

        if queue_kind == "internal_event":
            connection.execute(
                """
                INSERT INTO queue_runtime_lease_recovery_event (
                    queue_kind, queue_row_id, worker_generation,
                    error_code, lease_expires_at
                )
                SELECT 'internal_event', id, worker_generation,
                       'lease_expired', lease_expires_at
                FROM internal_event_consumer_run
                WHERE lane = %s
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                ON CONFLICT (
                    queue_kind, queue_row_id, lease_expires_at, error_code
                ) DO NOTHING
                """,
                (lane,),
            )
            connection.execute(
                """
                UPDATE internal_event_consumer_run
                SET status = CASE
                        WHEN attempt_count + 1 >= max_attempts THEN 'failed_terminal'
                        ELSE 'failed_retryable'
                    END,
                    attempt_count = attempt_count + 1,
                    available_at = CASE
                        WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                        ELSE available_at
                    END,
                    next_retry_at = CASE
                        WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    worker_generation = CASE
                        WHEN attempt_count + 1 < max_attempts THEN 0
                        ELSE worker_generation
                    END,
                    last_error_code = 'lease_expired',
                    last_error_message = 'Consumer lease expired before completion.',
                    lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                    locked_by = '', locked_at = NULL,
                    finished_at = CASE
                        WHEN attempt_count + 1 >= max_attempts THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lane = %s
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                """,
                (lane,),
            )
            return

        if queue_kind == "internal_outbox":
            connection.execute(
                """
                INSERT INTO queue_runtime_lease_recovery_event (
                    queue_kind, queue_row_id, worker_generation,
                    error_code, lease_expires_at
                )
                SELECT 'internal_outbox', id, worker_generation,
                       'lease_expired', lease_expires_at
                FROM internal_event_outbox
                WHERE lane = %s
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                ON CONFLICT (
                    queue_kind, queue_row_id, lease_expires_at, error_code
                ) DO NOTHING
                """,
                (lane,),
            )
            connection.execute(
                """
                UPDATE internal_event_outbox
                SET status = CASE
                        WHEN attempt_count >= max_attempts THEN 'failed_terminal'
                        ELSE 'failed_retryable'
                    END,
                    available_at = CASE
                        WHEN attempt_count < max_attempts THEN CURRENT_TIMESTAMP
                        ELSE available_at
                    END,
                    next_retry_at = CASE
                        WHEN attempt_count < max_attempts THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    worker_generation = CASE
                        WHEN attempt_count < max_attempts THEN 0
                        ELSE worker_generation
                    END,
                    last_error_code = 'lease_expired',
                    last_error_message = 'Outbox relay lease expired before completion.',
                    lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                    locked_by = '', locked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lane = %s
                  AND status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= CURRENT_TIMESTAMP
                """,
                (lane,),
            )
            return

        connection.execute(
            """
            INSERT INTO queue_runtime_lease_recovery_event (
                queue_kind, queue_row_id, worker_generation,
                error_code, lease_expires_at
            )
            SELECT 'webhook_inbox', id, worker_generation,
                   'lease_expired', lease_expires_at
            FROM webhook_inbox
            WHERE lane = %s
              AND status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= CURRENT_TIMESTAMP
            ON CONFLICT (
                queue_kind, queue_row_id, lease_expires_at, error_code
            ) DO NOTHING
            """,
            (lane,),
        )
        connection.execute(
            """
            UPDATE webhook_inbox
            SET status = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN 'dead_letter'
                    ELSE 'failed_retryable'
                END,
                attempt_count = attempt_count + 1,
                available_at = CASE
                    WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE available_at
                END,
                next_retry_at = CASE
                    WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                worker_generation = CASE
                    WHEN attempt_count + 1 < max_attempts THEN 0
                    ELSE worker_generation
                END,
                last_error_code = 'lease_expired',
                last_error_message = 'Webhook processing lease expired before completion.',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                locked_by = '', locked_at = NULL,
                finished_at = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE lane = %s
              AND status = 'processing'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= CURRENT_TIMESTAMP
            """,
            (lane,),
        )

    @staticmethod
    def _claim_allowed(*, control: RuntimeControl, lane: LanePolicy, generation: int) -> bool:
        now = datetime.now(tz=lane.blocked_until.tzinfo) if lane.blocked_until else None
        return bool(
            int(generation) > 0
            and control.claim_enabled
            and control.active_generation == int(generation)
            and control.policy_version == lane.policy_version
            and control.rollout_mode in {"canary", "execute"}
            and lane.enabled
            and lane.rollout_mode in {"canary", "execute"}
            and (lane.blocked_until is None or (now is not None and lane.blocked_until <= now))
        )

    @staticmethod
    def _control(row: Any) -> RuntimeControl:
        return RuntimeControl(
            active_generation=int(row.get("active_generation") or 0),
            claim_enabled=bool(row.get("claim_enabled")),
            rollout_mode=str(row.get("rollout_mode") or "blocked"),
            global_max_in_flight=int(row.get("global_max_in_flight") or 0),
            policy_version=str(row.get("policy_version") or ""),
            external_claim_scope=str(row.get("external_claim_scope") or "blocked"),
        )

    @staticmethod
    def _lane_policy(row: Any) -> LanePolicy:
        return LanePolicy(
            lane=str(row.get("lane") or ""),
            max_in_flight=int(row.get("max_in_flight") or 0),
            enabled=bool(row.get("enabled")),
            rollout_mode=str(row.get("rollout_mode") or "blocked"),
            blocked_until=row.get("blocked_until"),
            policy_version=str(row.get("policy_version") or ""),
        )

    @staticmethod
    def _in_flight_sql() -> str:
        return """
            WITH active AS (
                SELECT lane FROM external_effect_job
                WHERE status = 'dispatching'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT lane FROM internal_event_consumer_run
                WHERE status = 'running'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT lane FROM internal_event_outbox
                WHERE status = 'running'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT lane FROM webhook_inbox
                WHERE status = 'processing'
                  AND lease_expires_at > CURRENT_TIMESTAMP
            )
            SELECT COUNT(*)::BIGINT AS global_count,
                   COUNT(*) FILTER (WHERE lane = %s)::BIGINT AS lane_count
            FROM active
        """

    @staticmethod
    def _claim_sql(*, queue_kind: str, test_only: bool) -> str:
        if queue_kind == "external_effect":
            test_predicate = "AND COALESCE(job.payload_json->>'execution_scope', '') = 'test_loopback'" if test_only else ""
            scope_predicate = external_claim_scope_predicate(
                row_alias="job",
                scope_expression="(SELECT external_claim_scope FROM queue_runtime_control WHERE singleton = TRUE)",
            )
            claim_order = external_effect_claim_order_sql()
            return f"""
                WITH candidate AS (
                    SELECT job.id
                    FROM external_effect_job job
                    LEFT JOIN queue_fairness_cursor fairness
                      ON fairness.lane = job.lane
                     AND fairness.fairness_key = job.fairness_key
                    LEFT JOIN queue_rate_scope_cooldown cooldown
                      ON cooldown.rate_scope_key = job.rate_scope_key
                     AND cooldown.blocked_until > CURRENT_TIMESTAMP
                    WHERE job.lane = %s
                      AND job.worker_generation IN (0, %s)
                      AND job.policy_version = (
                          SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                      )
                      AND job.status IN ('queued', 'failed_retryable')
                      AND job.hold_reason = ''
                      AND job.attempt_count < job.max_attempts
                      AND job.available_at <= CURRENT_TIMESTAMP
                      AND {scope_predicate}
                      AND (job.lease_expires_at IS NULL OR job.lease_expires_at <= CURRENT_TIMESTAMP)
                      AND cooldown.rate_scope_key IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM external_effect_job active
                          WHERE active.lane = job.lane
                            AND active.ordering_key = job.ordering_key
                            AND active.ordering_key <> ''
                            AND active.status = 'dispatching'
                            AND active.lease_expires_at > CURRENT_TIMESTAMP
                      )
                      {test_predicate}
                    ORDER BY {claim_order}
                    LIMIT 1
                    FOR UPDATE OF job SKIP LOCKED
                )
                UPDATE external_effect_job job
                SET status = 'dispatching',
                    locked_by = %s,
                    lease_token = %s,
                    locked_at = CURRENT_TIMESTAMP,
                    dispatch_started_at = CURRENT_TIMESTAMP,
                    provider_call_started_at = NULL,
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    heartbeat_at = CURRENT_TIMESTAMP,
                    worker_generation = %s,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.*
            """
        if queue_kind == "internal_event":
            return """
                WITH candidate AS (
                    SELECT run.id
                    FROM internal_event_consumer_run run
                    LEFT JOIN queue_fairness_cursor fairness
                      ON fairness.lane = run.lane
                     AND fairness.fairness_key = run.fairness_key
                    WHERE run.lane = %s
                      AND run.worker_generation IN (0, %s)
                      AND run.policy_version = (
                          SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                      )
                      AND run.status IN ('pending', 'failed_retryable')
                      AND run.hold_reason = ''
                      AND run.attempt_count < run.max_attempts
                      AND run.available_at <= CURRENT_TIMESTAMP
                      AND (run.lease_expires_at IS NULL OR run.lease_expires_at <= CURRENT_TIMESTAMP)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM internal_event_consumer_run active
                          WHERE active.lane = run.lane
                            AND active.ordering_key = run.ordering_key
                            AND active.ordering_key <> ''
                            AND active.status = 'running'
                            AND active.lease_expires_at > CURRENT_TIMESTAMP
                      )
                    ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                             run.available_at ASC, run.id ASC
                    LIMIT 1
                    FOR UPDATE OF run SKIP LOCKED
                )
                UPDATE internal_event_consumer_run run
                SET status = 'running',
                    locked_by = %s,
                    lease_token = %s,
                    locked_at = CURRENT_TIMESTAMP,
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    heartbeat_at = CURRENT_TIMESTAMP,
                    worker_generation = %s,
                    updated_at = CURRENT_TIMESTAMP
                FROM candidate
                WHERE run.id = candidate.id
                RETURNING run.*
            """
        if queue_kind == "internal_outbox":
            return """
                WITH candidate AS (
                    SELECT outbox.id
                    FROM internal_event_outbox outbox
                    LEFT JOIN queue_fairness_cursor fairness
                      ON fairness.lane = outbox.lane
                     AND fairness.fairness_key = outbox.fairness_key
                    WHERE outbox.lane = %s
                      AND outbox.worker_generation IN (0, %s)
                      AND outbox.policy_version = (
                          SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                      )
                      AND outbox.status IN ('pending', 'failed_retryable')
                      AND outbox.hold_reason = ''
                      AND outbox.attempt_count < outbox.max_attempts
                      AND outbox.available_at <= CURRENT_TIMESTAMP
                      AND (outbox.lease_expires_at IS NULL OR outbox.lease_expires_at <= CURRENT_TIMESTAMP)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM internal_event_outbox active
                          WHERE active.lane = outbox.lane
                            AND active.ordering_key = outbox.ordering_key
                            AND active.ordering_key <> ''
                            AND active.status = 'running'
                            AND active.lease_expires_at > CURRENT_TIMESTAMP
                      )
                    ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                             outbox.available_at ASC, outbox.id ASC
                    LIMIT 1
                    FOR UPDATE OF outbox SKIP LOCKED
                )
                UPDATE internal_event_outbox outbox
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    locked_by = %s,
                    lease_token = %s,
                    locked_at = CURRENT_TIMESTAMP,
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    heartbeat_at = CURRENT_TIMESTAMP,
                    worker_generation = %s,
                    updated_at = CURRENT_TIMESTAMP
                FROM candidate
                WHERE outbox.id = candidate.id
                RETURNING outbox.*
            """
        return """
            WITH candidate AS (
                SELECT inbox.id
                FROM webhook_inbox inbox
                LEFT JOIN queue_fairness_cursor fairness
                  ON fairness.lane = inbox.lane
                 AND fairness.fairness_key = inbox.fairness_key
                WHERE inbox.lane = %s
                  AND inbox.worker_generation IN (0, %s)
                  AND inbox.policy_version = (
                      SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                  )
                  AND inbox.status IN ('received', 'failed_retryable')
                  AND inbox.hold_reason = ''
                  AND inbox.attempt_count < inbox.max_attempts
                  AND inbox.available_at <= CURRENT_TIMESTAMP
                  AND (inbox.lease_expires_at IS NULL OR inbox.lease_expires_at <= CURRENT_TIMESTAMP)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM webhook_inbox active
                      WHERE active.lane = inbox.lane
                        AND active.ordering_key = inbox.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = 'processing'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                         inbox.received_at ASC, inbox.id ASC
                LIMIT 1
                FOR UPDATE OF inbox SKIP LOCKED
            )
            UPDATE webhook_inbox inbox
            SET status = 'processing',
                locked_by = %s,
                lease_token = %s,
                locked_at = CURRENT_TIMESTAMP,
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                heartbeat_at = CURRENT_TIMESTAMP,
                worker_generation = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE inbox.id = candidate.id
            RETURNING inbox.*
        """

    def renew_lease(
        self,
        *,
        queue_kind: str,
        item_id: int,
        lease_token: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> bool:
        table, status = {
            "external_effect": ("external_effect_job", "dispatching"),
            "internal_event": ("internal_event_consumer_run", "running"),
            "internal_outbox": ("internal_event_outbox", "running"),
            "webhook_inbox": ("webhook_inbox", "processing"),
        }.get(queue_kind, ("", ""))
        if not table:
            raise ValueError("unsupported queue kind")
        ttl = max(10, min(int(lease_seconds or 30), 300))
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                f"""
                UPDATE {table}
                SET heartbeat_at = CURRENT_TIMESTAMP,
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status = %s
                  AND lease_token = %s
                  AND worker_generation = %s
                  AND lease_expires_at > CURRENT_TIMESTAMP
                RETURNING id
                """,
                (ttl, int(item_id), status, str(lease_token or ""), int(generation)),
            ).fetchone()
            connection.commit()
        return bool(row)

    def next_due_at(
        self,
        *,
        queue_kind: str,
        lane: str,
        generation: int,
        test_only: bool = False,
    ) -> datetime | None:
        if int(generation) <= 0:
            return None
        table, statuses = {
            "external_effect": ("external_effect_job", ("queued", "failed_retryable")),
            "internal_event": ("internal_event_consumer_run", ("pending", "failed_retryable")),
            "internal_outbox": ("internal_event_outbox", ("pending", "failed_retryable")),
            "webhook_inbox": ("webhook_inbox", ("received", "failed_retryable")),
        }.get(queue_kind, ("", ()))
        if not table:
            raise ValueError("unsupported queue kind")
        with self._connect(self._database_url) as connection:
            policy = connection.execute(
                """
                SELECT control.active_generation, control.claim_enabled,
                       control.rollout_mode AS control_mode,
                       lane.enabled, lane.rollout_mode AS lane_mode,
                       lane.blocked_until,
                       control.policy_version AS control_policy_version,
                       lane.policy_version AS lane_policy_version,
                       control.external_claim_scope,
                       control.global_max_in_flight,
                       lane.max_in_flight
                FROM queue_runtime_control control
                JOIN queue_lane_policy lane ON lane.lane = %s
                WHERE control.singleton = TRUE
                """,
                (str(lane or ""),),
            ).fetchone()
            if (
                not policy
                or not bool(policy.get("claim_enabled"))
                or int(policy.get("active_generation") or 0) != int(generation)
                or str(policy.get("control_policy_version") or "")
                != str(policy.get("lane_policy_version") or "")
                or str(policy.get("control_mode") or "") not in {"canary", "execute"}
                or not bool(policy.get("enabled"))
                or str(policy.get("lane_mode") or "") not in {"canary", "execute"}
            ):
                return None
            blocked_until = policy.get("blocked_until")
            if blocked_until and blocked_until > datetime.now(tz=blocked_until.tzinfo):
                return blocked_until
            in_flight = connection.execute(self._in_flight_sql(), (str(lane or ""),)).fetchone()
            if (
                int((in_flight or {}).get("global_count") or 0)
                >= int(policy.get("global_max_in_flight") or 0)
                or int((in_flight or {}).get("lane_count") or 0)
                >= int(policy.get("max_in_flight") or 0)
            ):
                return None
            if queue_kind == "external_effect":
                test_predicate = (
                    "AND COALESCE(job.payload_json->>'execution_scope', '') = 'test_loopback'"
                    if test_only
                    else ""
                )
                scope_predicate = external_claim_scope_predicate(
                    row_alias="job",
                    scope_expression="control.external_claim_scope",
                )
                row = connection.execute(
                    f"""
                    SELECT MIN(GREATEST(
                        job.available_at,
                        COALESCE(cooldown.blocked_until, job.available_at)
                    )) AS available_at
                    FROM external_effect_job job
                    CROSS JOIN queue_runtime_control control
                    LEFT JOIN queue_rate_scope_cooldown cooldown
                      ON cooldown.rate_scope_key = job.rate_scope_key
                     AND cooldown.blocked_until > CURRENT_TIMESTAMP
                    WHERE control.singleton = TRUE
                      AND job.lane = %s
                      AND job.worker_generation IN (0, %s)
                      AND job.policy_version = %s
                      AND job.status = ANY(%s)
                      AND job.hold_reason = ''
                      AND job.attempt_count < job.max_attempts
                      AND {scope_predicate}
                      {test_predicate}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM external_effect_job active
                          WHERE active.lane = job.lane
                            AND active.ordering_key = job.ordering_key
                            AND active.ordering_key <> ''
                            AND active.status = 'dispatching'
                            AND active.lease_expires_at > CURRENT_TIMESTAMP
                      )
                    """,
                    (
                        str(lane or ""),
                        int(generation),
                        str(policy.get("control_policy_version") or ""),
                        list(statuses),
                    ),
                ).fetchone()
                return row.get("available_at") if row else None
            active_status = {
                "internal_event": "running",
                "internal_outbox": "running",
                "webhook_inbox": "processing",
            }[queue_kind]
            row = connection.execute(
                f"""
                SELECT MIN(item.available_at) AS available_at
                FROM {table} item
                WHERE item.lane = %s
                  AND item.worker_generation IN (0, %s)
                  AND item.policy_version = %s
                  AND item.status = ANY(%s)
                  AND item.hold_reason = ''
                  AND item.attempt_count < item.max_attempts
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {table} active
                      WHERE active.lane = item.lane
                        AND active.ordering_key = item.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = '{active_status}'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                """,
                (
                    str(lane or ""),
                    int(generation),
                    str(policy.get("control_policy_version") or ""),
                    list(statuses),
                ),
            ).fetchone()
        return row.get("available_at") if row else None

    def record_rate_limit(
        self,
        *,
        rate_scope_key: str,
        blocked_until: datetime,
        provider: str = "",
        corp_id: str = "",
        app_id: str = "",
        operation: str = "",
        reason: str = "provider_429",
        source_attempt_id: str = "",
    ) -> None:
        if not str(rate_scope_key or "").strip():
            raise ValueError("rate_scope_key is required")
        with self._connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO queue_rate_scope_cooldown (
                    rate_scope_key, provider, corp_id, app_id, operation,
                    blocked_until, reason, source_attempt_id, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (rate_scope_key) DO UPDATE
                SET blocked_until = GREATEST(
                        queue_rate_scope_cooldown.blocked_until,
                        EXCLUDED.blocked_until
                    ),
                    provider = EXCLUDED.provider,
                    corp_id = EXCLUDED.corp_id,
                    app_id = EXCLUDED.app_id,
                    operation = EXCLUDED.operation,
                    reason = EXCLUDED.reason,
                    source_attempt_id = EXCLUDED.source_attempt_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(rate_scope_key),
                    str(provider),
                    str(corp_id),
                    str(app_id),
                    str(operation),
                    blocked_until,
                    str(reason),
                    str(source_attempt_id),
                ),
            )
            connection.commit()

    def heartbeat_worker(
        self,
        *,
        service_name: str,
        worker_id: str,
        queue_kind: str,
        generation: int,
        rollout_mode: str,
        listener_connected: bool,
        notification_seen: bool = False,
        drain_completed: bool = False,
        release_sha: str | None = None,
    ) -> None:
        with self._connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO queue_worker_heartbeat (
                    service_name, worker_id, queue_kind, generation, release_sha,
                    rollout_mode, listener_connected, last_notification_at,
                    last_drain_at, heartbeat_at, started_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (service_name, worker_id) DO UPDATE
                SET queue_kind = EXCLUDED.queue_kind,
                    generation = EXCLUDED.generation,
                    release_sha = EXCLUDED.release_sha,
                    rollout_mode = EXCLUDED.rollout_mode,
                    listener_connected = EXCLUDED.listener_connected,
                    last_notification_at = CASE
                        WHEN %s THEN CURRENT_TIMESTAMP
                        ELSE queue_worker_heartbeat.last_notification_at
                    END,
                    last_drain_at = CASE
                        WHEN %s THEN CURRENT_TIMESTAMP
                        ELSE queue_worker_heartbeat.last_drain_at
                    END,
                    heartbeat_at = CURRENT_TIMESTAMP
                """,
                (
                    str(service_name),
                    str(worker_id),
                    str(queue_kind),
                    int(generation),
                    str(release_sha or current_release_sha()),
                    str(rollout_mode),
                    bool(listener_connected),
                    bool(notification_seen),
                    bool(drain_completed),
                    bool(notification_seen),
                    bool(drain_completed),
                ),
            )
            connection.commit()


__all__ = [
    "ExecutionRuntimeRepository",
    "LanePolicy",
    "RuntimeClaim",
    "RuntimeControl",
    "open_listener_connection",
    "normalize_runtime_database_url",
    "open_runtime_connection",
]

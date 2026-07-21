from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence
from uuid import uuid4

from psycopg import sql

from aicrm_next.shared.runtime import raw_database_url

from .repository import normalize_runtime_database_url, open_runtime_connection


CANONICAL_RUNTIME_SERVICES = (
    "aicrm-internal-queue-runtime.service",
    "aicrm-inbox-queue-runtime.service",
    "aicrm-external-queue-runtime.service",
)
PR3_OWNER_INVENTORY_NAME = "pr3"
PR3_LEGACY_TIMER_OWNERS = (
    ("openclaw-internal-event-worker.timer", "openclaw-internal-event-worker.service"),
    ("openclaw-external-effect-worker.timer", "openclaw-external-effect-worker.service"),
    ("openclaw-broadcast-queue-worker.timer", "openclaw-broadcast-queue-worker.service"),
    ("openclaw-ai-audience-scheduler.timer", "openclaw-ai-audience-scheduler.service"),
    ("openclaw-identity-resolution-worker.timer", "openclaw-identity-resolution-worker.service"),
    ("openclaw-customer-read-model-refresh.timer", "openclaw-customer-read-model-refresh.service"),
    ("openclaw-automation-ops-scheduler.timer", "openclaw-automation-ops-scheduler.service"),
)
PR3_LEGACY_PERSISTENT_SERVICES = (
    "openclaw-wecom-callback-inbox-worker.service",
)
PR3_REPLACEMENT_TIMER_OWNERS = (
    ("aicrm-ai-audience-daily-intent.timer", "aicrm-ai-audience-daily-intent.service"),
    ("aicrm-next-broadcast-delegation.timer", "aicrm-next-broadcast-delegation.service"),
    ("aicrm-next-group-ops-planning.timer", "aicrm-next-group-ops-planning.service"),
)
PR3_SUCCESSOR_OWNERS = (
    (
        "openclaw-internal-event-worker.timer",
        "internal_event_dispatch",
        "persistent_service",
        "aicrm-internal-queue-runtime.service",
        "queue_worker_heartbeat:aicrm-internal_event-runtime",
        "internal_event_consumer_run",
    ),
    (
        "openclaw-external-effect-worker.timer",
        "external_effect_dispatch",
        "persistent_service",
        "aicrm-external-queue-runtime.service",
        "queue_worker_heartbeat:aicrm-external_effect-runtime",
        "external_effect_job",
    ),
    (
        "openclaw-broadcast-queue-worker.timer",
        "broadcast_external_effect_delegation",
        "timer",
        "aicrm-next-broadcast-delegation.timer",
        "systemd_timer:aicrm-next-broadcast-delegation.timer",
        "broadcast_jobs",
    ),
    (
        "openclaw-ai-audience-scheduler.timer",
        "ai_audience_daily_intent",
        "timer",
        "aicrm-ai-audience-daily-intent.timer",
        "systemd_timer:aicrm-ai-audience-daily-intent.timer",
        "ai_audience_package_intent",
    ),
    (
        "openclaw-identity-resolution-worker.timer",
        "identity_resolution_effect_execution",
        "persistent_service",
        "aicrm-external-queue-runtime.service",
        "queue_worker_heartbeat:aicrm-external_effect-runtime",
        "crm_user_identity_resolution_queue+external_effect_job",
    ),
    (
        "openclaw-customer-read-model-refresh.timer",
        "customer_read_model_refresh",
        "persistent_service",
        "aicrm-internal-queue-runtime.service",
        "queue_worker_heartbeat:aicrm-internal_event-runtime",
        "customer_read_model_refresh_intent+internal_event_consumer_run",
    ),
    (
        "openclaw-automation-ops-scheduler.timer",
        "group_ops_effect_graph_planning",
        "timer",
        "aicrm-next-group-ops-planning.timer",
        "systemd_timer:aicrm-next-group-ops-planning.timer",
        "automation_group_ops_plan+automation_group_ops_effect_graph",
    ),
    (
        "openclaw-wecom-callback-inbox-worker.service",
        "wecom_callback_inbox_dispatch",
        "persistent_service",
        "aicrm-inbox-queue-runtime.service",
        "queue_worker_heartbeat:aicrm-webhook_inbox-runtime",
        "webhook_inbox",
    ),
)
REQUIRED_RUNTIME_HEARTBEATS = (
    "aicrm-internal_event-runtime",
    "aicrm-internal_outbox-runtime",
    "aicrm-webhook_inbox-runtime",
    "aicrm-external_effect-runtime",
)
ACTIVATABLE_LANES = frozenset(
    {
        "internal_general",
        "internal_financial",
        "webhook_inbox",
        "wecom_interactive",
        "wecom_bulk",
        "wecom_media",
        "outbound_webhook",
    }
)


@dataclass(frozen=True)
class GenerationState:
    active_generation: int
    claim_enabled: bool
    rollout_mode: str
    policy_version: str
    updated_by: str
    updated_reason: str
    updated_at: datetime | None
    external_claim_scope: str = "blocked"


@dataclass(frozen=True)
class GenerationActivation:
    before: GenerationState
    after: GenerationState
    activated_lanes: tuple[str, ...]
    freeze: CutoverFreeze | None = None


@dataclass(frozen=True)
class CutoverFreeze:
    freeze_revision: str
    cutoff_at: datetime
    counts: tuple[tuple[str, int], ...]


class GenerationCASConflict(RuntimeError):
    """The runtime control row no longer matches the cutover precondition."""


class RuntimeGenerationRepository:
    """Fail-closed numeric generation control over PR-2's canonical tables."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[[str], Any] = open_runtime_connection,
    ) -> None:
        self._database_url = normalize_runtime_database_url(database_url or raw_database_url())
        if not self._database_url.startswith("postgresql://"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required for queue generation cutover")
        self._connect = connect

    def read_state(self) -> GenerationState:
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT active_generation, claim_enabled, rollout_mode,
                       policy_version, external_claim_scope,
                       updated_by, updated_reason, updated_at
                FROM queue_runtime_control
                WHERE singleton = TRUE
                """
            ).fetchone()
        if not row:
            raise RuntimeError("queue runtime control row is missing")
        return self._state(row)

    def assert_gate_closed(
        self,
        *,
        expected_generation: int,
        expected_policy_version: str,
    ) -> GenerationState:
        expected = self._generation(expected_generation, allow_zero=True)
        policy_version = str(expected_policy_version or "").strip()
        if not policy_version:
            raise ValueError("expected_policy_version is required")
        state = self.read_state()
        if (
            state.active_generation != expected
            or state.claim_enabled
            or state.policy_version != policy_version
            or state.external_claim_scope != "test_loopback"
        ):
            raise GenerationCASConflict(
                "queue claim gate is not closed at the expected generation and policy version"
            )
        return state

    def activate_generation(
        self,
        *,
        expected_generation: int,
        target_generation: int,
        expected_policy_version: str,
        lanes: Sequence[str],
        actor: str,
        reason: str,
    ) -> GenerationActivation:
        expected = self._generation(expected_generation, allow_zero=True)
        target = self._generation(target_generation, allow_zero=False)
        if target <= expected:
            raise ValueError("target_generation must be greater than expected_generation")
        policy_version = str(expected_policy_version or "").strip()
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        normalized_lanes = tuple(dict.fromkeys(str(lane or "").strip() for lane in lanes))
        if not policy_version:
            raise ValueError("expected_policy_version is required")
        if not normalized_actor:
            raise ValueError("actor is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if not normalized_lanes or any(lane not in ACTIVATABLE_LANES for lane in normalized_lanes):
            raise ValueError("lanes must be a non-empty subset of the approved runtime lanes")

        with self._connect(self._database_url) as connection:
            with connection.transaction():
                before_row = connection.execute(
                    """
                    SELECT active_generation, claim_enabled, rollout_mode,
                           policy_version, external_claim_scope,
                           updated_by, updated_reason, updated_at
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                    FOR UPDATE
                    """
                ).fetchone()
                if not before_row:
                    raise RuntimeError("queue runtime control row is missing")
                before = self._state(before_row)
                if (
                    before.active_generation != expected
                    or before.claim_enabled
                    or before.policy_version != policy_version
                    or before.external_claim_scope != "test_loopback"
                ):
                    raise GenerationCASConflict(
                        "generation activation precondition changed before the CAS"
                    )
                freeze = self._freeze_legacy_rows(
                    connection,
                    target_generation=target,
                    actor=normalized_actor,
                    reason=normalized_reason,
                )
                lane_rows = connection.execute(
                    """
                    SELECT lane, enabled, rollout_mode, blocked_until, policy_version
                    FROM queue_lane_policy
                    WHERE lane = ANY(%s)
                    FOR UPDATE
                    """,
                    (list(normalized_lanes),),
                ).fetchall()
                lane_by_name = {str(row.get("lane") or ""): row for row in lane_rows}
                if set(lane_by_name) != set(normalized_lanes):
                    raise GenerationCASConflict("one or more requested lane policy rows are missing")
                now = datetime.now().astimezone()
                for lane in normalized_lanes:
                    row = lane_by_name[lane]
                    blocked_until = row.get("blocked_until")
                    if (
                        not bool(row.get("enabled"))
                        or str(row.get("policy_version") or "") != policy_version
                        or (blocked_until is not None and blocked_until > now)
                    ):
                        raise GenerationCASConflict(
                            f"lane {lane} is disabled, rate-limited, or on another policy version"
                        )
                connection.execute(
                    """
                    UPDATE queue_lane_policy
                    SET rollout_mode = 'canary',
                        updated_by = %s,
                        updated_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE lane = ANY(%s)
                      AND enabled = TRUE
                      AND policy_version = %s
                    """,
                    (
                        normalized_actor,
                        normalized_reason,
                        list(normalized_lanes),
                        policy_version,
                    ),
                )
                after_row = connection.execute(
                    """
                    UPDATE queue_runtime_control
                    SET active_generation = %s,
                        claim_enabled = TRUE,
                        rollout_mode = 'canary',
                        updated_by = %s,
                        updated_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton = TRUE
                      AND active_generation = %s
                      AND claim_enabled = FALSE
                      AND policy_version = %s
                      AND external_claim_scope = 'test_loopback'
                    RETURNING active_generation, claim_enabled, rollout_mode,
                              policy_version, external_claim_scope,
                              updated_by, updated_reason, updated_at
                    """,
                    (
                        target,
                        normalized_actor,
                        normalized_reason,
                        expected,
                        policy_version,
                    ),
                ).fetchone()
                if not after_row:
                    raise GenerationCASConflict("generation activation CAS lost")
        return GenerationActivation(
            before=before,
            after=self._state(after_row),
            activated_lanes=normalized_lanes,
            freeze=freeze,
        )

    def disable_claims(
        self,
        *,
        expected_generation: int,
        actor: str,
        reason: str,
    ) -> GenerationState:
        expected = self._generation(expected_generation, allow_zero=False)
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason:
            raise ValueError("actor and reason are required")
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                """
                UPDATE queue_runtime_control
                SET claim_enabled = FALSE,
                    rollout_mode = 'standby',
                    updated_by = %s,
                    updated_reason = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE singleton = TRUE
                  AND active_generation = %s
                  AND claim_enabled = TRUE
                RETURNING active_generation, claim_enabled, rollout_mode,
                          policy_version, external_claim_scope,
                          updated_by, updated_reason, updated_at
                """,
                (normalized_actor, normalized_reason, expected),
            ).fetchone()
            connection.commit()
        if not row:
            raise GenerationCASConflict("disable-claims CAS lost")
        return self._state(row)

    def active_claim_count(self) -> int:
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)::BIGINT AS active_count
                FROM (
                    SELECT id FROM external_effect_job WHERE status = 'dispatching'
                    UNION ALL
                    SELECT id FROM internal_event_consumer_run WHERE status = 'running'
                    UNION ALL
                    SELECT id FROM internal_event_outbox WHERE status = 'running'
                    UNION ALL
                    SELECT id FROM webhook_inbox WHERE status = 'processing'
                ) active
                """
            ).fetchone()
        return int((row or {}).get("active_count") or 0)

    def wait_claims_drained(
        self,
        *,
        timeout_seconds: int = 60,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        deadline = time.monotonic() + max(1, int(timeout_seconds or 60))
        while self.active_claim_count():
            if time.monotonic() >= deadline:
                raise RuntimeError("queue claims did not drain before scope transition")
            time.sleep(max(0.1, min(float(poll_interval_seconds or 0.5), 5.0)))

    def transition_external_claim_scope(
        self,
        *,
        expected_generation: int,
        expected_policy_version: str,
        target_policy_version: str,
        expected_scope: str,
        target_scope: str,
        actor: str,
        reason: str,
    ) -> GenerationState:
        generation = self._generation(expected_generation, allow_zero=False)
        policy_version = str(expected_policy_version or "").strip()
        next_policy_version = str(target_policy_version or "").strip()
        source_scope = str(expected_scope or "").strip()
        destination_scope = str(target_scope or "").strip()
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if (source_scope, destination_scope) not in {
            ("test_loopback", "allowlisted"),
            ("allowlisted", "test_loopback"),
        }:
            raise ValueError("only test_loopback <-> allowlisted scope transitions are supported")
        if (
            not policy_version
            or not next_policy_version
            or next_policy_version == policy_version
            or not normalized_actor
            or not normalized_reason
        ):
            raise ValueError("distinct source/target policy versions, actor and reason are required")

        with self._connect(self._database_url) as connection:
            with connection.transaction():
                before_row = connection.execute(
                    """
                    SELECT active_generation, claim_enabled, rollout_mode,
                           policy_version, external_claim_scope,
                           updated_by, updated_reason, updated_at
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                    FOR UPDATE
                    """
                ).fetchone()
                if not before_row:
                    raise RuntimeError("queue runtime control row is missing")
                before = self._state(before_row)
                if (
                    before.active_generation != generation
                    or before.claim_enabled
                    or before.policy_version != policy_version
                    or before.external_claim_scope != source_scope
                ):
                    raise GenerationCASConflict("scope transition precondition changed before the CAS")
                active = connection.execute(
                    """
                    SELECT COUNT(*)::BIGINT AS active_count
                    FROM (
                        SELECT id FROM external_effect_job WHERE status = 'dispatching'
                        UNION ALL
                        SELECT id FROM internal_event_consumer_run WHERE status = 'running'
                        UNION ALL
                        SELECT id FROM internal_event_outbox WHERE status = 'running'
                        UNION ALL
                        SELECT id FROM webhook_inbox WHERE status = 'processing'
                    ) active
                    """
                ).fetchone()
                if int((active or {}).get("active_count") or 0):
                    raise GenerationCASConflict("queue claims are not drained")
                snapshot = connection.execute(
                    """
                    SELECT policy_json
                    FROM queue_policy_snapshot
                    WHERE policy_version = %s
                    FOR UPDATE
                    """,
                    (policy_version,),
                ).fetchone()
                if not snapshot:
                    raise GenerationCASConflict("queue policy snapshot is missing")
                before_policy = dict(snapshot.get("policy_json") or {})
                if str(before_policy.get("external_claim_scope") or "") != source_scope:
                    raise GenerationCASConflict("queue policy snapshot scope does not match runtime control")
                after_policy = {**before_policy, "external_claim_scope": destination_scope}
                inserted_snapshot = connection.execute(
                    """
                    INSERT INTO queue_policy_snapshot (
                        policy_version, policy_json, created_by, created_reason
                    ) VALUES (%s, %s::jsonb, %s, %s)
                    ON CONFLICT (policy_version) DO NOTHING
                    RETURNING policy_version
                    """,
                    (
                        next_policy_version,
                        json.dumps(after_policy, ensure_ascii=False),
                        normalized_actor,
                        normalized_reason,
                    ),
                ).fetchone()
                if not inserted_snapshot:
                    raise GenerationCASConflict("target queue policy version already exists")
                connection.execute(
                    """
                    UPDATE queue_lane_policy
                    SET policy_version = %s,
                        updated_by = %s,
                        updated_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE policy_version = %s
                    """,
                    (
                        next_policy_version,
                        normalized_actor,
                        normalized_reason,
                        policy_version,
                    ),
                )
                open_statuses = {
                    "external_effect_job": ("planned", "approved", "queued", "failed_retryable", "blocked"),
                    "internal_event_consumer_run": ("pending", "failed_retryable", "blocked", "manual_retry"),
                    "internal_event_outbox": ("pending", "failed_retryable", "blocked"),
                    "webhook_inbox": ("received", "failed_retryable", "blocked"),
                }
                for table, statuses in open_statuses.items():
                    connection.execute(
                        sql.SQL(
                            "UPDATE {} SET policy_version = %s, updated_at = CURRENT_TIMESTAMP "
                            "WHERE policy_version = %s AND status = ANY(%s)"
                        ).format(sql.Identifier(table)),
                        (next_policy_version, policy_version, list(statuses)),
                    )
                    connection.execute(
                        sql.SQL("ALTER TABLE {} ALTER COLUMN policy_version SET DEFAULT {}").format(
                            sql.Identifier(table),
                            sql.Literal(next_policy_version),
                        )
                    )
                after_row = connection.execute(
                    """
                    UPDATE queue_runtime_control
                    SET external_claim_scope = %s,
                        policy_version = %s,
                        updated_by = %s,
                        updated_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton = TRUE
                      AND active_generation = %s
                      AND claim_enabled = FALSE
                      AND policy_version = %s
                      AND external_claim_scope = %s
                    RETURNING active_generation, claim_enabled, rollout_mode,
                              policy_version, external_claim_scope,
                              updated_by, updated_reason, updated_at
                    """,
                    (
                        destination_scope,
                        next_policy_version,
                        normalized_actor,
                        normalized_reason,
                        generation,
                        policy_version,
                        source_scope,
                    ),
                ).fetchone()
                if not after_row:
                    raise GenerationCASConflict("scope transition CAS lost")
                connection.execute(
                    """
                    INSERT INTO queue_runtime_scope_transition_audit (
                        transition_id, active_generation,
                        from_policy_version, to_policy_version,
                        from_scope, to_scope, actor, reason,
                        policy_json_before, policy_json_after
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        "qrst_" + uuid4().hex,
                        generation,
                        policy_version,
                        next_policy_version,
                        source_scope,
                        destination_scope,
                        normalized_actor,
                        normalized_reason,
                        json.dumps(before_policy, ensure_ascii=False),
                        json.dumps(after_policy, ensure_ascii=False),
                    ),
                )
        return self._state(after_row)

    def resume_claims(
        self,
        *,
        expected_generation: int,
        expected_policy_version: str,
        expected_scope: str,
        actor: str,
        reason: str,
    ) -> GenerationState:
        generation = self._generation(expected_generation, allow_zero=False)
        policy_version = str(expected_policy_version or "").strip()
        scope = str(expected_scope or "").strip()
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if scope not in {"test_loopback", "allowlisted"}:
            raise ValueError("unsupported external claim scope")
        if not policy_version or not normalized_actor or not normalized_reason:
            raise ValueError("policy version, actor and reason are required")
        with self._connect(self._database_url) as connection:
            row = connection.execute(
                """
                UPDATE queue_runtime_control
                SET claim_enabled = TRUE,
                    rollout_mode = 'canary',
                    updated_by = %s,
                    updated_reason = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE singleton = TRUE
                  AND active_generation = %s
                  AND claim_enabled = FALSE
                  AND policy_version = %s
                  AND external_claim_scope = %s
                  AND EXISTS (
                      SELECT 1
                      FROM queue_policy_snapshot snapshot
                      WHERE snapshot.policy_version = queue_runtime_control.policy_version
                        AND snapshot.policy_json->>'external_claim_scope' = %s
                  )
                RETURNING active_generation, claim_enabled, rollout_mode,
                          policy_version, external_claim_scope,
                          updated_by, updated_reason, updated_at
                """,
                (
                    normalized_actor,
                    normalized_reason,
                    generation,
                    policy_version,
                    scope,
                    scope,
                ),
            ).fetchone()
            connection.commit()
        if not row:
            raise GenerationCASConflict("resume-claims CAS lost")
        return self._state(row)

    def ready_heartbeat_names(
        self,
        *,
        generation: int,
        freshness_seconds: int = 30,
    ) -> frozenset[str]:
        target = self._generation(generation, allow_zero=False)
        freshness = max(10, min(int(freshness_seconds or 30), 300))
        with self._connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT service_name
                FROM queue_worker_heartbeat
                WHERE generation = %s
                  AND listener_connected = TRUE
                  AND rollout_mode = 'canary'
                  AND heartbeat_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                  AND service_name = ANY(%s)
                """,
                (target, freshness, list(REQUIRED_RUNTIME_HEARTBEATS)),
            ).fetchall()
        return frozenset(str(row.get("service_name") or "") for row in rows)

    def fresh_listener_heartbeat_names(
        self,
        *,
        generation: int,
        freshness_seconds: int = 30,
    ) -> frozenset[str]:
        target = self._generation(generation, allow_zero=False)
        freshness = max(10, min(int(freshness_seconds or 30), 300))
        with self._connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT service_name
                FROM queue_worker_heartbeat
                WHERE generation = %s
                  AND listener_connected = TRUE
                  AND heartbeat_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                  AND service_name = ANY(%s)
                """,
                (target, freshness, list(REQUIRED_RUNTIME_HEARTBEATS)),
            ).fetchall()
        return frozenset(str(row.get("service_name") or "") for row in rows)

    @staticmethod
    def _freeze_legacy_rows(
        connection: Any,
        *,
        target_generation: int,
        actor: str,
        reason: str,
    ) -> CutoverFreeze:
        revision = f"pr3_generation_{int(target_generation)}"
        hold_reason = f"history_frozen_at_{revision}"
        quarantine_reason = f"provider_boundary_quarantine_at_{revision}"
        cutoff_row = connection.execute(
            "SELECT CURRENT_TIMESTAMP AS cutoff_at"
        ).fetchone()
        cutoff_at = dict(cutoff_row or {}).get("cutoff_at")
        if not isinstance(cutoff_at, datetime):
            raise RuntimeError("database cutoff timestamp is missing")

        connection.execute(
            """
            INSERT INTO queue_history_classification (
                freeze_revision, queue_kind, queue_row_id, source_status,
                classification, hold_reason, evidence_json
            )
            SELECT
                %s, 'external_effect', job.id, job.status,
                CASE
                    WHEN job.provider_call_started_at IS NOT NULL
                      OR job.status IN ('dispatching', 'unknown_after_dispatch')
                      OR (
                          job.dispatch_started_at IS NOT NULL
                          AND job.provider_result_received = FALSE
                      )
                        THEN 'inconsistent_quarantine'
                    WHEN job.attempt_count >= job.max_attempts
                        THEN 'inconsistent_quarantine'
                    WHEN job.status = 'failed_retryable'
                      AND job.reconciliation_required = FALSE
                      AND (job.side_effect_executed = FALSE OR job.provider_result_received = TRUE)
                        THEN 'safe_retryable'
                    WHEN job.status IN ('planned', 'approved', 'queued')
                      AND job.last_attempt_id = ''
                      AND job.side_effect_executed = FALSE
                        THEN 'safe_pre_provider'
                    ELSE 'ambiguous_hold'
                END,
                CASE
                    WHEN job.provider_call_started_at IS NOT NULL
                      OR job.status IN ('dispatching', 'unknown_after_dispatch')
                      OR (
                          job.dispatch_started_at IS NOT NULL
                          AND job.provider_result_received = FALSE
                      )
                        THEN %s
                    ELSE %s
                END,
                jsonb_build_object(
                    'actor', %s::TEXT,
                    'reason', %s::TEXT,
                    'cutoff_at', %s::TIMESTAMPTZ,
                    'attempt_count', job.attempt_count,
                    'max_attempts', job.max_attempts,
                    'provider_boundary_started', (
                        job.provider_call_started_at IS NOT NULL
                        OR job.status IN ('dispatching', 'unknown_after_dispatch')
                        OR (
                            job.dispatch_started_at IS NOT NULL
                            AND job.provider_result_received = FALSE
                        )
                    ),
                    'provider_call_started_at', job.provider_call_started_at,
                    'provider_result_received', job.provider_result_received,
                    'reconciliation_required', job.reconciliation_required
                )
            FROM external_effect_job job
            WHERE job.hold_reason = ''
              AND job.created_at <= %s
              AND job.status IN (
                  'planned', 'approved', 'queued', 'dispatching',
                  'failed_retryable', 'unknown_after_dispatch'
              )
            ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
            """,
            (
                revision,
                quarantine_reason,
                hold_reason,
                actor,
                reason,
                cutoff_at,
                cutoff_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO queue_history_classification (
                freeze_revision, queue_kind, queue_row_id, source_status,
                classification, hold_reason, evidence_json
            )
            SELECT
                %s, 'internal_event_consumer', run.id, run.status,
                CASE
                    WHEN run.status = 'running' THEN 'ambiguous_hold'
                    WHEN run.attempt_count >= run.max_attempts THEN 'inconsistent_quarantine'
                    WHEN run.status = 'failed_retryable' THEN 'safe_retryable'
                    WHEN run.status = 'pending' AND run.attempt_count = 0 THEN 'safe_pre_provider'
                    ELSE 'ambiguous_hold'
                END,
                %s,
                jsonb_build_object(
                    'actor', %s::TEXT,
                    'reason', %s::TEXT,
                    'cutoff_at', %s::TIMESTAMPTZ,
                    'attempt_count', run.attempt_count,
                    'max_attempts', run.max_attempts,
                    'provider_boundary_started', FALSE,
                    'had_active_lease', run.lease_token <> '' OR run.locked_at IS NOT NULL
                )
            FROM internal_event_consumer_run run
            WHERE run.hold_reason = ''
              AND run.created_at <= %s
              AND run.status IN ('pending', 'running', 'failed_retryable')
            ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
            """,
            (revision, hold_reason, actor, reason, cutoff_at, cutoff_at),
        )
        connection.execute(
            """
            INSERT INTO queue_history_classification (
                freeze_revision, queue_kind, queue_row_id, source_status,
                classification, hold_reason, evidence_json
            )
            SELECT
                %s, 'internal_event_outbox', outbox.id, outbox.status,
                CASE
                    WHEN outbox.status = 'running' THEN 'ambiguous_hold'
                    WHEN outbox.attempt_count >= outbox.max_attempts THEN 'inconsistent_quarantine'
                    WHEN outbox.status = 'failed_retryable' THEN 'safe_retryable'
                    WHEN outbox.status = 'pending' AND outbox.attempt_count = 0 THEN 'safe_pre_provider'
                    ELSE 'ambiguous_hold'
                END,
                %s,
                jsonb_build_object(
                    'actor', %s::TEXT,
                    'reason', %s::TEXT,
                    'cutoff_at', %s::TIMESTAMPTZ,
                    'attempt_count', outbox.attempt_count,
                    'max_attempts', outbox.max_attempts,
                    'provider_boundary_started', FALSE,
                    'had_active_lease', outbox.lease_token <> '' OR outbox.locked_at IS NOT NULL
                )
            FROM internal_event_outbox outbox
            WHERE outbox.hold_reason = ''
              AND outbox.created_at <= %s
              AND outbox.status IN ('pending', 'running', 'failed_retryable')
            ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
            """,
            (revision, hold_reason, actor, reason, cutoff_at, cutoff_at),
        )
        connection.execute(
            """
            INSERT INTO queue_history_classification (
                freeze_revision, queue_kind, queue_row_id, source_status,
                classification, hold_reason, evidence_json
            )
            SELECT
                %s, 'webhook_inbox', inbox.id, inbox.status,
                CASE
                    WHEN inbox.status = 'processing' THEN 'ambiguous_hold'
                    WHEN inbox.attempt_count >= inbox.max_attempts THEN 'inconsistent_quarantine'
                    WHEN inbox.status = 'failed_retryable' THEN 'safe_retryable'
                    WHEN inbox.status = 'received' AND inbox.attempt_count = 0 THEN 'safe_pre_provider'
                    ELSE 'ambiguous_hold'
                END,
                %s,
                jsonb_build_object(
                    'actor', %s::TEXT,
                    'reason', %s::TEXT,
                    'cutoff_at', %s::TIMESTAMPTZ,
                    'attempt_count', inbox.attempt_count,
                    'max_attempts', inbox.max_attempts,
                    'provider_boundary_started', FALSE,
                    'had_active_lock', inbox.locked_at IS NOT NULL
                )
            FROM webhook_inbox inbox
            WHERE inbox.hold_reason = ''
              AND inbox.received_at <= %s
              AND inbox.status IN ('received', 'processing', 'failed_retryable')
            ON CONFLICT (freeze_revision, queue_kind, queue_row_id) DO NOTHING
            """,
            (revision, hold_reason, actor, reason, cutoff_at, cutoff_at),
        )

        for table_name, queue_kind in (
            ("external_effect_job", "external_effect"),
            ("internal_event_consumer_run", "internal_event_consumer"),
            ("internal_event_outbox", "internal_event_outbox"),
            ("webhook_inbox", "webhook_inbox"),
        ):
            connection.execute(
                f"""
                UPDATE {table_name} target
                SET hold_reason = audit.hold_reason,
                    hold_at = %s
                FROM queue_history_classification audit
                WHERE audit.freeze_revision = %s
                  AND audit.queue_kind = %s
                  AND audit.queue_row_id = target.id
                  AND target.hold_reason = ''
                """,
                (cutoff_at, revision, queue_kind),
            )
        connection.execute(
            """
            UPDATE external_effect_job target
            SET status = 'unknown_after_dispatch',
                reconciliation_required = TRUE
            FROM queue_history_classification audit
            WHERE audit.freeze_revision = %s
              AND audit.queue_kind = 'external_effect'
              AND audit.queue_row_id = target.id
              AND audit.classification = 'inconsistent_quarantine'
              AND COALESCE((audit.evidence_json ->> 'provider_boundary_started')::BOOLEAN, FALSE)
            """,
            (revision,),
        )
        rows = connection.execute(
            """
            SELECT queue_kind, COUNT(*)::BIGINT AS count
            FROM queue_history_classification
            WHERE freeze_revision = %s
            GROUP BY queue_kind
            ORDER BY queue_kind
            """,
            (revision,),
        ).fetchall()
        return CutoverFreeze(
            freeze_revision=revision,
            cutoff_at=cutoff_at,
            counts=tuple(
                (str(row.get("queue_kind") or ""), int(row.get("count") or 0))
                for row in rows
            ),
        )

    @staticmethod
    def _generation(value: int, *, allow_zero: bool) -> int:
        generation = int(value)
        if generation < 0 or (generation == 0 and not allow_zero):
            comparator = ">= 0" if allow_zero else "> 0"
            raise ValueError(f"generation must be {comparator}")
        return generation

    @staticmethod
    def _state(row: Any) -> GenerationState:
        values = dict(row or {})
        return GenerationState(
            active_generation=int(values.get("active_generation") or 0),
            claim_enabled=bool(values.get("claim_enabled")),
            rollout_mode=str(values.get("rollout_mode") or "blocked"),
            policy_version=str(values.get("policy_version") or ""),
            updated_by=str(values.get("updated_by") or ""),
            updated_reason=str(values.get("updated_reason") or ""),
            updated_at=values.get("updated_at"),
            external_claim_scope=str(values.get("external_claim_scope") or "blocked"),
        )


class QueueRuntimeLifecycle(Protocol):
    def stage_target_generation(self, generation: int) -> None: ...

    def start_target_service(self, service: str) -> None: ...

    def stop_legacy_triggers(self, units: Sequence[str]) -> None: ...

    def stop_legacy_services(self, units: Sequence[str]) -> None: ...

    def wait_legacy_services_drained(self, units: Sequence[str], timeout_seconds: int) -> None: ...

    def retire_legacy_units(self, units: Sequence[str]) -> None: ...

    def verify_single_owner(
        self,
        *,
        legacy_triggers: Sequence[str],
        legacy_services: Sequence[str],
        legacy_persistent_services: Sequence[str],
        replacement_active: bool = False,
    ) -> None: ...

    def activate_post_cutover_replacements(self, generation: int) -> None: ...

    def deactivate_post_cutover_replacements(self, generation: int) -> None: ...


@dataclass(frozen=True)
class QueueRuntimeCutoverRequest:
    expected_generation: int
    target_generation: int
    expected_policy_version: str
    lanes: tuple[str, ...]
    actor: str
    reason: str
    legacy_triggers: tuple[str, ...] = ()
    legacy_services: tuple[str, ...] = ()
    legacy_persistent_services: tuple[str, ...] = ()
    readiness_timeout_seconds: int = 60
    drain_timeout_seconds: int = 600


class QueueRuntimeCutoverCoordinator:
    """Start replacements behind the closed gate, drain old owners, then CAS."""

    def __init__(
        self,
        *,
        repository: RuntimeGenerationRepository,
        lifecycle: QueueRuntimeLifecycle,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repository = repository
        self._lifecycle = lifecycle
        self._monotonic = monotonic
        self._sleep = sleep

    def activate(self, request: QueueRuntimeCutoverRequest) -> GenerationActivation:
        self._validate_request(request)
        self._repository.assert_gate_closed(
            expected_generation=request.expected_generation,
            expected_policy_version=request.expected_policy_version,
        )
        self._lifecycle.stage_target_generation(int(request.target_generation))
        for service in CANONICAL_RUNTIME_SERVICES:
            self._lifecycle.start_target_service(service)
        self._wait_until_target_ready(
            generation=int(request.target_generation),
            timeout_seconds=int(request.readiness_timeout_seconds),
        )
        self._lifecycle.stop_legacy_triggers(request.legacy_triggers)
        self._lifecycle.stop_legacy_services(request.legacy_persistent_services)
        all_legacy_services = tuple(
            dict.fromkeys((*request.legacy_services, *request.legacy_persistent_services))
        )
        self._lifecycle.wait_legacy_services_drained(
            all_legacy_services,
            int(request.drain_timeout_seconds),
        )
        self._wait_until_target_ready(
            generation=int(request.target_generation),
            timeout_seconds=int(request.readiness_timeout_seconds),
        )
        self._lifecycle.retire_legacy_units(
            tuple(
                dict.fromkeys(
                    (
                        *request.legacy_triggers,
                        *request.legacy_services,
                        *request.legacy_persistent_services,
                    )
                )
            )
        )
        self._lifecycle.verify_single_owner(
            legacy_triggers=request.legacy_triggers,
            legacy_services=request.legacy_services,
            legacy_persistent_services=request.legacy_persistent_services,
            replacement_active=False,
        )
        activation = self._repository.activate_generation(
            expected_generation=request.expected_generation,
            target_generation=request.target_generation,
            expected_policy_version=request.expected_policy_version,
            lanes=request.lanes,
            actor=request.actor,
            reason=request.reason,
        )
        try:
            self._lifecycle.activate_post_cutover_replacements(
                int(request.target_generation)
            )
        except Exception:
            self._repository.disable_claims(
                expected_generation=int(request.target_generation),
                actor=request.actor,
                reason=f"post-cutover replacement activation failed: {request.reason}",
            )
            self._lifecycle.deactivate_post_cutover_replacements(
                int(request.target_generation)
            )
            raise
        return activation

    @staticmethod
    def _validate_request(request: QueueRuntimeCutoverRequest) -> None:
        if int(request.expected_generation) < 0:
            raise ValueError("expected_generation must be >= 0")
        if int(request.target_generation) <= 0:
            raise ValueError("target_generation must be > 0")
        if int(request.target_generation) <= int(request.expected_generation):
            raise ValueError("target_generation must be greater than expected_generation")
        if not str(request.expected_policy_version or "").strip():
            raise ValueError("expected_policy_version is required")
        if not str(request.actor or "").strip() or not str(request.reason or "").strip():
            raise ValueError("actor and reason are required")
        if not request.lanes or any(lane not in ACTIVATABLE_LANES for lane in request.lanes):
            raise ValueError("lanes must be a non-empty subset of the approved non-outbound lanes")
        if (
            len(request.legacy_triggers) != len(request.legacy_services)
            or any(not unit.endswith(".timer") for unit in request.legacy_triggers)
            or any(not unit.endswith(".service") for unit in request.legacy_services)
            or any(not unit.endswith(".service") for unit in request.legacy_persistent_services)
            or not (request.legacy_triggers or request.legacy_persistent_services)
        ):
            raise ValueError(
                "declare a service for each legacy timer or at least one persistent legacy service"
            )

    def _wait_until_target_ready(self, *, generation: int, timeout_seconds: int) -> None:
        timeout = max(1, min(int(timeout_seconds or 60), 600))
        deadline = self._monotonic() + timeout
        while True:
            ready = self._repository.ready_heartbeat_names(generation=generation)
            if set(REQUIRED_RUNTIME_HEARTBEATS) <= set(ready):
                return
            if self._monotonic() >= deadline:
                missing = sorted(set(REQUIRED_RUNTIME_HEARTBEATS) - set(ready))
                raise RuntimeError(f"target queue runtime heartbeat timeout: {missing}")
            self._sleep(min(1.0, max(0.0, deadline - self._monotonic())))


__all__ = [
    "ACTIVATABLE_LANES",
    "CANONICAL_RUNTIME_SERVICES",
    "PR3_LEGACY_PERSISTENT_SERVICES",
    "PR3_LEGACY_TIMER_OWNERS",
    "PR3_OWNER_INVENTORY_NAME",
    "PR3_REPLACEMENT_TIMER_OWNERS",
    "PR3_SUCCESSOR_OWNERS",
    "REQUIRED_RUNTIME_HEARTBEATS",
    "GenerationActivation",
    "CutoverFreeze",
    "GenerationCASConflict",
    "GenerationState",
    "QueueRuntimeCutoverCoordinator",
    "QueueRuntimeCutoverRequest",
    "QueueRuntimeLifecycle",
    "RuntimeGenerationRepository",
]

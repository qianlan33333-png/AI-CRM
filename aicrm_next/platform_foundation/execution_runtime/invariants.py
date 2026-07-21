from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from aicrm_next.shared.runtime import raw_database_url

from .repository import normalize_runtime_database_url, open_runtime_connection


@dataclass(frozen=True)
class QueueInvariantViolation:
    code: str
    count: int
    dimensions: dict[str, str]


@dataclass(frozen=True)
class QueueInvariantReport:
    ok: bool
    read_only: bool
    checked_at: str
    violations: tuple[QueueInvariantViolation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "read_only": self.read_only,
            "checked_at": self.checked_at,
            "violation_count": sum(item.count for item in self.violations),
            "violations": [asdict(item) for item in self.violations],
            "claimed_count": 0,
            "executed_count": 0,
            "real_external_call_executed": False,
        }


class QueueRuntimeInvariantChecker:
    """Report queue ownership and lease contradictions without mutating facts."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[[str], Any] = open_runtime_connection,
    ) -> None:
        self._database_url = normalize_runtime_database_url(database_url or raw_database_url())
        if not self._database_url.startswith("postgresql://"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required for queue invariant checks")
        self._connect = connect

    def check(self) -> QueueInvariantReport:
        violations: list[QueueInvariantViolation] = []
        with self._connect(self._database_url) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                self._append_rows(
                    violations,
                    connection.execute(self._control_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._lease_shape_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._lease_state_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._generation_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._policy_alignment_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._ordering_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._capacity_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._provider_boundary_sql()).fetchall(),
                )
                self._append_rows(
                    violations,
                    connection.execute(self._orphan_sql()).fetchall(),
                )
        return QueueInvariantReport(
            ok=not violations,
            read_only=True,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            violations=tuple(violations),
        )

    @staticmethod
    def _append_rows(target: list[QueueInvariantViolation], rows: Any) -> None:
        for row in rows or ():
            values = dict(row or {})
            count = int(values.pop("violation_count", 0) or 0)
            code = str(values.pop("code", "") or "").strip()
            if not code or count <= 0:
                continue
            target.append(
                QueueInvariantViolation(
                    code=code,
                    count=count,
                    dimensions={
                        str(key): str(value or "")
                        for key, value in values.items()
                        if value not in (None, "")
                    },
                )
            )

    @staticmethod
    def _control_sql() -> str:
        return """
            SELECT 'runtime_control_invalid' AS code,
                   COUNT(*)::BIGINT AS violation_count
            FROM queue_runtime_control control
            LEFT JOIN queue_policy_snapshot snapshot
              ON snapshot.policy_version = control.policy_version
            WHERE control.singleton = TRUE
              AND (
                  (claim_enabled AND active_generation <= 0)
                  OR (claim_enabled AND rollout_mode NOT IN ('canary', 'execute'))
                  OR (NOT claim_enabled AND rollout_mode IN ('canary', 'execute'))
                  OR (claim_enabled AND external_claim_scope = 'blocked')
                  OR snapshot.policy_version IS NULL
                  OR COALESCE(snapshot.policy_json->>'external_claim_scope', '')
                     <> control.external_claim_scope
              )
        """

    @staticmethod
    def _active_facts_sql() -> str:
        return """
            SELECT 'external_effect'::TEXT AS queue_kind, id, lane, ordering_key,
                   status, lease_token, lease_expires_at, worker_generation
            FROM external_effect_job WHERE status = 'dispatching'
            UNION ALL
            SELECT 'internal_event', id, lane, ordering_key,
                   status, lease_token, lease_expires_at, worker_generation
            FROM internal_event_consumer_run WHERE status = 'running'
            UNION ALL
            SELECT 'internal_outbox', id, lane, ordering_key,
                   status, lease_token, lease_expires_at, worker_generation
            FROM internal_event_outbox WHERE status = 'running'
            UNION ALL
            SELECT 'webhook_inbox', id, lane, ordering_key,
                   status, lease_token, lease_expires_at, worker_generation
            FROM webhook_inbox WHERE status = 'processing'
        """

    @classmethod
    def _lease_shape_sql(cls) -> str:
        return f"""
            WITH active AS ({cls._active_facts_sql()})
            SELECT 'active_lease_incomplete' AS code, queue_kind,
                   COUNT(*)::BIGINT AS violation_count
            FROM active
            WHERE lease_token = '' OR lease_expires_at IS NULL OR worker_generation <= 0
            GROUP BY queue_kind
        """

    @staticmethod
    def _lease_state_sql() -> str:
        return """
            WITH contradictions AS (
                SELECT 'external_effect'::TEXT AS queue_kind
                FROM external_effect_job
                WHERE status <> 'dispatching'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'internal_event'
                FROM internal_event_consumer_run
                WHERE status <> 'running'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'internal_outbox'
                FROM internal_event_outbox
                WHERE status <> 'running'
                  AND lease_expires_at > CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'webhook_inbox'
                FROM webhook_inbox
                WHERE status <> 'processing'
                  AND lease_expires_at > CURRENT_TIMESTAMP
            )
            SELECT 'lease_status_conflict' AS code, queue_kind,
                   COUNT(*)::BIGINT AS violation_count
            FROM contradictions
            GROUP BY queue_kind
            UNION ALL
            SELECT 'expired_active_lease' AS code, queue_kind,
                   COUNT(*)::BIGINT AS violation_count
            FROM (
                SELECT 'external_effect'::TEXT AS queue_kind
                FROM external_effect_job
                WHERE status = 'dispatching' AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'internal_event'
                FROM internal_event_consumer_run
                WHERE status = 'running' AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'internal_outbox'
                FROM internal_event_outbox
                WHERE status = 'running' AND lease_expires_at <= CURRENT_TIMESTAMP
                UNION ALL
                SELECT 'webhook_inbox'
                FROM webhook_inbox
                WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP
            ) expired
            GROUP BY queue_kind
        """

    @classmethod
    def _generation_sql(cls) -> str:
        return f"""
            WITH active AS ({cls._active_facts_sql()}),
            control AS (
                SELECT active_generation, claim_enabled
                FROM queue_runtime_control WHERE singleton = TRUE
            ),
            expected_heartbeats(service_name) AS (
                VALUES
                    ('aicrm-internal_event-runtime'),
                    ('aicrm-internal_outbox-runtime'),
                    ('aicrm-webhook_inbox-runtime'),
                    ('aicrm-external_effect-runtime')
            ),
            fresh_heartbeats AS (
                SELECT DISTINCT heartbeat.service_name
                FROM queue_worker_heartbeat heartbeat
                CROSS JOIN control
                WHERE heartbeat.generation = control.active_generation
                  AND heartbeat.listener_connected = TRUE
                  AND heartbeat.heartbeat_at >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'
            )
            SELECT 'active_generation_conflict' AS code, active.queue_kind,
                   COUNT(*)::BIGINT AS violation_count
            FROM active CROSS JOIN control
            WHERE active.lease_expires_at > CURRENT_TIMESTAMP
              AND control.claim_enabled
              AND active.worker_generation <> control.active_generation
            GROUP BY active.queue_kind
            UNION ALL
            SELECT 'missing_active_worker_heartbeat' AS code,
                   expected.service_name AS queue_kind,
                   COUNT(*)::BIGINT AS violation_count
            FROM expected_heartbeats expected
            CROSS JOIN control
            LEFT JOIN fresh_heartbeats fresh
              ON fresh.service_name = expected.service_name
            WHERE control.claim_enabled
              AND fresh.service_name IS NULL
            GROUP BY expected.service_name
        """

    @staticmethod
    def _policy_alignment_sql() -> str:
        return """
            WITH control AS (
                SELECT policy_version
                FROM queue_runtime_control
                WHERE singleton = TRUE
            ), mismatched AS (
                SELECT 'external_effect'::TEXT AS queue_kind, job.policy_version
                FROM external_effect_job job CROSS JOIN control
                WHERE job.status IN ('queued', 'failed_retryable')
                  AND job.hold_reason = ''
                  AND job.policy_version <> control.policy_version
                UNION ALL
                SELECT 'internal_event', run.policy_version
                FROM internal_event_consumer_run run CROSS JOIN control
                WHERE run.status IN ('pending', 'failed_retryable')
                  AND run.hold_reason = ''
                  AND run.policy_version <> control.policy_version
                UNION ALL
                SELECT 'internal_outbox', outbox.policy_version
                FROM internal_event_outbox outbox CROSS JOIN control
                WHERE outbox.status IN ('pending', 'failed_retryable')
                  AND outbox.hold_reason = ''
                  AND outbox.policy_version <> control.policy_version
                UNION ALL
                SELECT 'webhook_inbox', inbox.policy_version
                FROM webhook_inbox inbox CROSS JOIN control
                WHERE inbox.status IN ('received', 'failed_retryable')
                  AND inbox.hold_reason = ''
                  AND inbox.policy_version <> control.policy_version
            )
            SELECT 'open_item_policy_mismatch' AS code, queue_kind, policy_version,
                   COUNT(*)::BIGINT AS violation_count
            FROM mismatched
            GROUP BY queue_kind, policy_version
        """

    @classmethod
    def _ordering_sql(cls) -> str:
        return f"""
            WITH active AS ({cls._active_facts_sql()})
            SELECT 'ordering_key_concurrency' AS code, lane, ordering_key,
                   COUNT(*)::BIGINT AS violation_count
            FROM active
            WHERE lease_expires_at > CURRENT_TIMESTAMP
              AND ordering_key <> ''
            GROUP BY lane, ordering_key
            HAVING COUNT(*) > 1
        """

    @classmethod
    def _capacity_sql(cls) -> str:
        return f"""
            WITH active AS ({cls._active_facts_sql()}),
            lane_counts AS (
                SELECT lane, COUNT(*)::BIGINT AS in_flight
                FROM active
                WHERE lease_expires_at > CURRENT_TIMESTAMP
                GROUP BY lane
            ),
            global_count AS (
                SELECT COUNT(*)::BIGINT AS in_flight
                FROM active
                WHERE lease_expires_at > CURRENT_TIMESTAMP
            )
            SELECT 'lane_capacity_exceeded' AS code, policy.lane,
                   counts.in_flight::BIGINT AS violation_count
            FROM lane_counts counts
            JOIN queue_lane_policy policy ON policy.lane = counts.lane
            WHERE counts.in_flight > policy.max_in_flight
            UNION ALL
            SELECT 'global_capacity_exceeded' AS code, 'all' AS lane,
                   global_count.in_flight::BIGINT AS violation_count
            FROM global_count
            CROSS JOIN queue_runtime_control control
            WHERE control.singleton = TRUE
              AND global_count.in_flight > control.global_max_in_flight
        """

    @staticmethod
    def _provider_boundary_sql() -> str:
        return """
            SELECT 'retryable_after_provider_boundary' AS code,
                   COUNT(*)::BIGINT AS violation_count
            FROM external_effect_job
            WHERE status IN ('queued', 'failed_retryable')
              AND provider_call_started_at IS NOT NULL
            UNION ALL
            SELECT 'multiple_open_provider_attempts' AS code,
                   COUNT(*)::BIGINT AS violation_count
            FROM (
                SELECT job_id
                FROM external_effect_attempt
                WHERE status = 'dispatching'
                GROUP BY job_id
                HAVING COUNT(*) > 1
            ) duplicate_attempts
        """

    @staticmethod
    def _orphan_sql() -> str:
        return """
            SELECT 'orphan_provider_attempt' AS code,
                   COUNT(*)::BIGINT AS violation_count
            FROM external_effect_attempt attempt
            LEFT JOIN external_effect_job job ON job.id = attempt.job_id
            WHERE job.id IS NULL
            UNION ALL
            SELECT 'orphan_consumer_run' AS code,
                   COUNT(*)::BIGINT AS violation_count
            FROM internal_event_consumer_run run
            LEFT JOIN internal_event event ON event.event_id = run.event_id
            WHERE event.id IS NULL
        """


__all__ = [
    "QueueInvariantReport",
    "QueueInvariantViolation",
    "QueueRuntimeInvariantChecker",
]

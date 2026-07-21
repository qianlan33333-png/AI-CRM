from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable

from aicrm_next.shared.runtime import raw_database_url
from aicrm_next.platform_foundation.execution_runtime.read_model import queue_policy_eligible_predicate
from aicrm_next.platform_foundation.execution_runtime.repository import (
    external_canary_authorization_predicate,
)


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def connect_operation_members_db() -> Any | None:
    database_url = _psycopg_url(raw_database_url())
    if not database_url.startswith(("postgresql://", "postgres://")):
        return None
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


ConnectionFactory = Callable[[str], AbstractContextManager[Any]]


def _connect_readiness_db(database_url: str) -> AbstractContextManager[Any]:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        _psycopg_url(database_url),
        autocommit=True,
        connect_timeout=3,
        row_factory=dict_row,
    )


class RuntimeReadinessRepository:
    def __init__(self, database_url: str, *, connection_factory: ConnectionFactory | None = None) -> None:
        self._database_url = database_url
        self._connection_factory = connection_factory or _connect_readiness_db
        self._connection_context: AbstractContextManager[Any] | None = None
        self._connection: Any | None = None

    def __enter__(self) -> "RuntimeReadinessRepository":
        self._connection_context = self._connection_factory(self._database_url)
        self._connection = self._connection_context.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._connection_context is None:
            return False
        return bool(self._connection_context.__exit__(exc_type, exc, traceback))

    def ping(self) -> bool:
        row = self._execute("SELECT 1 AS ok").fetchone()
        return bool(row and int(row.get("ok") or 0) == 1)

    def migration_revisions(self) -> tuple[str, ...]:
        rows = self._execute("SELECT version_num FROM alembic_version ORDER BY version_num").fetchall()
        return tuple(sorted(str(row.get("version_num") or "").strip() for row in rows if row.get("version_num")))

    def queue_metrics(
        self,
        *,
        allowed_pairs: tuple[tuple[str, str], ...] = (),
        allowed_event_types: tuple[str, ...] = (),
        allowed_consumers: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if allowed_pairs:
            predicates = ["(e.event_type || ':' || r.consumer_name) = ANY(%(allowed_pairs)s)"]
            params["allowed_pairs"] = [f"{event_type}:{consumer_name}" for event_type, consumer_name in allowed_pairs]
            if allowed_event_types:
                predicates.append("e.event_type = ANY(%(allowed_event_types)s)")
                params["allowed_event_types"] = list(allowed_event_types)
            actionable_predicate = " AND ".join(predicates)
        else:
            predicates: list[str] = []
            if allowed_event_types:
                predicates.append("e.event_type = ANY(%(allowed_event_types)s)")
                params["allowed_event_types"] = list(allowed_event_types)
            if allowed_consumers:
                predicates.append("r.consumer_name = ANY(%(allowed_consumers)s)")
                params["allowed_consumers"] = list(allowed_consumers)
            actionable_predicate = " AND ".join(predicates) if predicates else "TRUE"
        eligible_predicate = queue_policy_eligible_predicate()
        external_canary_authorized = external_canary_authorization_predicate(row_alias="j")
        row = self._execute(
            f"""
            WITH internal_rows AS (
              SELECT
                r.*,
                ({actionable_predicate}) AS policy_allowed,
                r.status IN ('pending', 'running', 'failed_retryable') AS raw_open
              FROM internal_event_consumer_run r
              JOIN internal_event e ON e.event_id = r.event_id
            ), internal_terminal AS (
              SELECT ({actionable_predicate}) AS policy_allowed
              FROM internal_event_consumer_run r
              JOIN internal_event e ON e.event_id = r.event_id
              WHERE r.status IN ('failed_terminal', 'blocked')
            ), queue_candidates AS (
              SELECT
                'webhook_inbox'::text AS queue_kind,
                i.received_at AS enqueued_at,
                i.lane, i.status, i.hold_reason, i.available_at,
                i.lease_expires_at, i.attempt_count, i.max_attempts,
                i.worker_generation, i.policy_version,
                i.status IN ('received', 'failed_retryable') AS ready_state,
                i.status = 'processing' AS in_flight,
                FALSE AS unknown_state,
                i.status IN ('dead_letter', 'failed_terminal') AS dlq_state,
                FALSE AS rate_limited,
                ''::text AS execution_scope,
                TRUE AS canary_authorized,
                EXISTS (
                  SELECT 1 FROM webhook_inbox active
                  WHERE active.id <> i.id AND active.lane = i.lane
                    AND active.ordering_key = i.ordering_key AND active.ordering_key <> ''
                    AND active.status = 'processing'
                    AND active.lease_expires_at > CURRENT_TIMESTAMP
                ) AS ordering_blocked,
                TRUE AS policy_allowed
              FROM webhook_inbox i
              WHERE i.status IN ('received', 'processing', 'failed_retryable', 'failed_terminal', 'dead_letter')

              UNION ALL

              SELECT
                'internal_event', r.created_at, r.lane, r.status, r.hold_reason,
                r.available_at, r.lease_expires_at, r.attempt_count, r.max_attempts,
                r.worker_generation, r.policy_version,
                r.status IN ('pending', 'failed_retryable'),
                r.status = 'running', FALSE,
                r.status IN ('failed_terminal', 'blocked'), FALSE, '', TRUE,
                EXISTS (
                  SELECT 1 FROM internal_event_consumer_run active
                  WHERE active.id <> r.id AND active.lane = r.lane
                    AND active.ordering_key = r.ordering_key AND active.ordering_key <> ''
                    AND active.status = 'running'
                    AND active.lease_expires_at > CURRENT_TIMESTAMP
                ),
                r.policy_allowed
              FROM internal_rows r
              WHERE r.status IN ('pending', 'running', 'failed_retryable', 'failed_terminal', 'blocked')

              UNION ALL

              SELECT
                'internal_event_outbox', o.created_at, o.lane, o.status, o.hold_reason,
                o.available_at, o.lease_expires_at, o.attempt_count, o.max_attempts,
                o.worker_generation, o.policy_version,
                o.status IN ('pending', 'failed_retryable'),
                o.status = 'running', FALSE, o.status = 'failed_terminal', FALSE, '', TRUE,
                EXISTS (
                  SELECT 1 FROM internal_event_outbox active
                  WHERE active.id <> o.id AND active.lane = o.lane
                    AND active.ordering_key = o.ordering_key AND active.ordering_key <> ''
                    AND active.status = 'running'
                    AND active.lease_expires_at > CURRENT_TIMESTAMP
                ),
                TRUE
              FROM internal_event_outbox o
              WHERE o.status IN ('pending', 'running', 'failed_retryable', 'failed_terminal')

              UNION ALL

              SELECT
                'external_effect', j.created_at, j.lane, j.status, j.hold_reason,
                j.available_at, j.lease_expires_at, j.attempt_count, j.max_attempts,
                j.worker_generation, j.policy_version,
                j.status IN ('queued', 'failed_retryable'),
                j.status = 'dispatching',
                j.status = 'unknown_after_dispatch',
                j.status IN ('failed_terminal', 'blocked'),
                EXISTS (
                  SELECT 1 FROM queue_rate_scope_cooldown cooldown
                  WHERE cooldown.rate_scope_key = j.rate_scope_key
                    AND cooldown.blocked_until > CURRENT_TIMESTAMP
                ),
                COALESCE(j.payload_json->>'execution_scope', ''),
                ({external_canary_authorized}),
                EXISTS (
                  SELECT 1 FROM external_effect_job active
                  WHERE active.id <> j.id AND active.lane = j.lane
                    AND active.ordering_key = j.ordering_key AND active.ordering_key <> ''
                    AND active.status = 'dispatching'
                    AND active.lease_expires_at > CURRENT_TIMESTAMP
                ),
                TRUE
              FROM external_effect_job j
              WHERE j.status IN (
                'planned', 'approved', 'queued', 'dispatching', 'failed_retryable',
                'unknown_after_dispatch', 'failed_terminal', 'blocked'
              )
            ), runtime_queue_rows AS (
              SELECT
                rows.queue_kind,
                rows.enqueued_at,
                TRUE AS raw_open,
                rows.hold_reason <> '' AS held,
                (({eligible_predicate}) AND rows.policy_allowed) AS eligible,
                (
                  rows.hold_reason = '' AND rows.ready_state
                  AND rows.available_at > CURRENT_TIMESTAMP
                  AND rows.status <> 'failed_retryable'
                ) AS scheduled,
                (
                  rows.hold_reason = '' AND rows.ready_state
                  AND rows.available_at > CURRENT_TIMESTAMP
                  AND rows.status = 'failed_retryable'
                ) AS retry_wait,
                rows.rate_limited AND rows.ready_state AND rows.hold_reason = '' AS rate_limited,
                rows.in_flight AND rows.lease_expires_at > CURRENT_TIMESTAMP AS in_flight,
                rows.unknown_state AS unknown,
                rows.dlq_state AS dlq
              FROM queue_candidates rows
              CROSS JOIN queue_runtime_control control
              JOIN queue_lane_policy policy ON policy.lane = rows.lane
            ), legacy_broadcast_rows AS (
              SELECT
                'broadcast'::text AS queue_kind,
                job.created_at AS enqueued_at,
                TRUE AS raw_open,
                job.hold_reason <> '' AS held,
                FALSE AS eligible,
                FALSE AS scheduled,
                FALSE AS retry_wait,
                FALSE AS rate_limited,
                FALSE AS in_flight,
                job.status = 'unknown_after_dispatch' AS unknown,
                job.status IN ('failed', 'failed_terminal', 'blocked') AS dlq
              FROM broadcast_jobs job
              WHERE COALESCE(NULLIF(job.execution_owner, ''), 'legacy_frozen') = 'legacy_frozen'
                AND job.status IN (
                  'waiting_approval', 'queued', 'claimed', 'dispatching',
                  'failed', 'failed_retryable', 'failed_terminal', 'blocked',
                  'unknown_after_dispatch'
                )
            ), queue_policy_rows AS (
              SELECT * FROM runtime_queue_rows
              UNION ALL
              SELECT * FROM legacy_broadcast_rows
            )
            SELECT
              (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE)::TEXT AS queue_policy_version,
              (SELECT active_generation FROM queue_runtime_control WHERE singleton = TRUE)::BIGINT AS queue_active_generation,
              (SELECT external_claim_scope FROM queue_runtime_control WHERE singleton = TRUE)::TEXT AS queue_external_claim_scope,
              COUNT(*) FILTER (WHERE raw_open)::BIGINT AS queue_raw_open_count,
              COUNT(*) FILTER (WHERE held)::BIGINT AS queue_held_count,
              COUNT(*) FILTER (WHERE eligible)::BIGINT AS queue_eligible_count,
              COUNT(*) FILTER (WHERE scheduled)::BIGINT AS queue_scheduled_count,
              COUNT(*) FILTER (WHERE retry_wait)::BIGINT AS queue_retry_wait_count,
              COUNT(*) FILTER (WHERE rate_limited)::BIGINT AS queue_rate_limited_count,
              COUNT(*) FILTER (WHERE in_flight)::BIGINT AS queue_in_flight_count,
              COUNT(*) FILTER (WHERE unknown)::BIGINT AS queue_unknown_count,
              COUNT(*) FILTER (WHERE dlq)::BIGINT AS queue_dlq_count,

              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND raw_open)::BIGINT AS webhook_pending_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND raw_open)::BIGINT AS webhook_raw_open_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND held)::BIGINT AS webhook_held_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND eligible)::BIGINT AS webhook_eligible_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND scheduled)::BIGINT AS webhook_scheduled_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND retry_wait)::BIGINT AS webhook_retry_wait_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND rate_limited)::BIGINT AS webhook_rate_limited_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND in_flight)::BIGINT AS webhook_in_flight_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND unknown)::BIGINT AS webhook_unknown_count,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'webhook_inbox' AND raw_open
              ))), 0)::BIGINT AS webhook_oldest_pending_age_seconds,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'webhook_inbox' AND eligible
              ))), 0)::BIGINT AS webhook_eligible_oldest_pending_age_seconds,
              (SELECT COUNT(*) FROM webhook_inbox WHERE status = 'dead_letter')::BIGINT AS webhook_dead_letter_count,
              COUNT(*) FILTER (WHERE queue_kind = 'webhook_inbox' AND dlq)::BIGINT AS webhook_dlq_count,

              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND raw_open)::BIGINT AS internal_event_pending_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND raw_open)::BIGINT AS internal_event_raw_open_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND held)::BIGINT AS internal_event_held_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND eligible)::BIGINT AS internal_event_actionable_pending_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND eligible)::BIGINT AS internal_event_eligible_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND scheduled)::BIGINT AS internal_event_scheduled_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND retry_wait)::BIGINT AS internal_event_retry_wait_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND rate_limited)::BIGINT AS internal_event_rate_limited_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND in_flight)::BIGINT AS internal_event_in_flight_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND unknown)::BIGINT AS internal_event_unknown_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event' AND dlq)::BIGINT AS internal_event_dlq_count,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'internal_event' AND raw_open
              ))), 0)::BIGINT AS internal_event_oldest_pending_age_seconds,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'internal_event' AND eligible
              ))), 0)::BIGINT AS internal_event_actionable_oldest_pending_age_seconds,
              (SELECT COUNT(*) FROM internal_rows WHERE raw_open AND NOT policy_allowed)::BIGINT AS internal_event_rollout_gated_pending_count,
              (SELECT COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(created_at))), 0)
                 FROM internal_rows WHERE raw_open AND NOT policy_allowed)::BIGINT AS internal_event_rollout_gated_oldest_pending_age_seconds,
              (SELECT COUNT(*) FROM internal_terminal)::BIGINT AS internal_event_terminal_count,
              (SELECT COUNT(*) FROM internal_terminal WHERE policy_allowed)::BIGINT AS internal_event_actionable_terminal_count,
              (SELECT COUNT(*) FROM internal_terminal WHERE NOT policy_allowed)::BIGINT AS internal_event_rollout_gated_terminal_count,

              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND raw_open)::BIGINT AS internal_event_outbox_raw_open_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND held)::BIGINT AS internal_event_outbox_held_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND eligible)::BIGINT AS internal_event_outbox_eligible_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND scheduled)::BIGINT AS internal_event_outbox_scheduled_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND retry_wait)::BIGINT AS internal_event_outbox_retry_wait_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND rate_limited)::BIGINT AS internal_event_outbox_rate_limited_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND in_flight)::BIGINT AS internal_event_outbox_in_flight_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND unknown)::BIGINT AS internal_event_outbox_unknown_count,
              COUNT(*) FILTER (WHERE queue_kind = 'internal_event_outbox' AND dlq)::BIGINT AS internal_event_outbox_dlq_count,

              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND raw_open)::BIGINT AS external_effect_pending_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND raw_open)::BIGINT AS external_effect_raw_open_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND held)::BIGINT AS external_effect_held_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND eligible)::BIGINT AS external_effect_eligible_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND scheduled)::BIGINT AS external_effect_scheduled_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND retry_wait)::BIGINT AS external_effect_retry_wait_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND rate_limited)::BIGINT AS external_effect_rate_limited_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND in_flight)::BIGINT AS external_effect_in_flight_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND unknown)::BIGINT AS external_effect_unknown_count,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'external_effect' AND raw_open
              ))), 0)::BIGINT AS external_effect_oldest_pending_age_seconds,
              COALESCE(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(enqueued_at) FILTER (
                WHERE queue_kind = 'external_effect' AND eligible
              ))), 0)::BIGINT AS external_effect_eligible_oldest_pending_age_seconds,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND dlq)::BIGINT AS external_effect_terminal_count,
              COUNT(*) FILTER (WHERE queue_kind = 'external_effect' AND dlq)::BIGINT AS external_effect_dlq_count,

              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND raw_open)::BIGINT AS broadcast_raw_open_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND held)::BIGINT AS broadcast_held_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND eligible)::BIGINT AS broadcast_eligible_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND scheduled)::BIGINT AS broadcast_scheduled_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND retry_wait)::BIGINT AS broadcast_retry_wait_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND rate_limited)::BIGINT AS broadcast_rate_limited_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND in_flight)::BIGINT AS broadcast_in_flight_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND unknown)::BIGINT AS broadcast_unknown_count,
              COUNT(*) FILTER (WHERE queue_kind = 'broadcast' AND dlq)::BIGINT AS broadcast_dlq_count
            FROM queue_policy_rows
            """,
            params,
        ).fetchone()
        text_fields = {"queue_policy_version", "queue_external_claim_scope"}
        return {
            str(key): str(value or "") if key in text_fields else int(value or 0)
            for key, value in dict(row or {}).items()
        }

    def _execute(self, sql: str, params: dict[str, Any] | None = None):
        if self._connection is None:
            raise RuntimeError("readiness repository is not open")
        return self._connection.execute(sql, params) if params else self._connection.execute(sql)

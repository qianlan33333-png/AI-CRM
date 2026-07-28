from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from datetime import datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from aicrm_next.platform.shared.release import current_release_sha
from aicrm_next.platform.shared.runtime import raw_database_url
from aicrm_next.platform.platform_foundation.external_effects.claim_policy import (
    external_claim_scope_predicate,
)
from aicrm_next.platform.platform_foundation.external_effects.repo_contract import _public_attempt, _public_job
from aicrm_next.platform.platform_foundation.external_effects.runtime_write_port import (
    build_external_effect_runtime_write_port,
)
from aicrm_next.platform.platform_foundation.external_effects.settlement_events import (
    build_external_effect_settled_event,
)
from aicrm_next.platform.platform_foundation.execution_runtime.lanes import (
    WECOM_WELCOME_RESERVED_LANES,
)
from aicrm_next.platform.platform_foundation.internal_events.outbox import (
    enqueue_transactional_internal_event_outbox,
)
from aicrm_next.platform.platform_foundation.internal_events.consumer_run_write_port import (
    build_internal_event_consumer_run_write_port,
)
from aicrm_next.platform.platform_foundation.internal_events.outbox_runtime_write_port import (
    build_internal_event_outbox_runtime_write_port,
)
from aicrm_next.platform.platform_foundation.rate_scope_cooldown import (
    RateScopeCooldownRequest,
    build_rate_scope_cooldown_port,
)
from aicrm_next.platform.platform_foundation.webhook_inbox.runtime_write_port import (
    build_webhook_inbox_runtime_write_port,
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
        lane: str = "webhook_inbox",
        worker_id: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> RuntimeClaim | None:
        return self._claim_one(
            queue_kind="webhook_inbox",
            lane=str(lane or "webhook_inbox").strip() or "webhook_inbox",
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
                reserved_count = int((in_flight or {}).get("reserved_count") or 0)
                reserved_capacity = self._active_reserved_capacity(
                    connection,
                    policy_version=control.policy_version,
                )
                ordinary_count = max(0, global_count - reserved_count)
                ordinary_capacity = max(
                    0,
                    control.global_max_in_flight - reserved_capacity,
                )
                global_capacity_exhausted = (
                    global_count >= control.global_max_in_flight
                    if normalized_lane in WECOM_WELCOME_RESERVED_LANES
                    else ordinary_count >= ordinary_capacity
                )
                if global_capacity_exhausted or lane_count >= policy.max_in_flight:
                    return None
                if queue_kind == "internal_event":
                    row = build_internal_event_consumer_run_write_port().claim_dbapi(
                        connection,
                        lane=normalized_lane,
                        generation=int(generation),
                        worker_id=str(worker_id or "").strip(),
                        lease_token=lease_token,
                        lease_seconds=ttl,
                    )
                elif queue_kind == "internal_outbox":
                    row = build_internal_event_outbox_runtime_write_port().claim_dbapi(
                        connection,
                        lane=normalized_lane,
                        generation=int(generation),
                        worker_id=str(worker_id or "").strip(),
                        lease_token=lease_token,
                        lease_seconds=ttl,
                    )
                elif queue_kind == "webhook_inbox":
                    row = build_webhook_inbox_runtime_write_port().claim_dbapi(
                        connection,
                        lane=normalized_lane,
                        generation=int(generation),
                        worker_id=str(worker_id or "").strip(),
                        lease_token=lease_token,
                        lease_seconds=ttl,
                    )
                else:
                    row = build_external_effect_runtime_write_port().claim_dbapi(
                        connection,
                        lane=normalized_lane,
                        generation=int(generation),
                        worker_id=str(worker_id or "").strip(),
                        lease_token=lease_token,
                        lease_seconds=ttl,
                        test_only=bool(test_only),
                    )
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
            unknown_rows = build_external_effect_runtime_write_port().recover_expired_dbapi(
                connection,
                lane=lane,
            )
            for unknown_row in unknown_rows:
                job = _public_job(unknown_row)
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
            build_internal_event_consumer_run_write_port().recover_expired_dbapi(
                connection,
                lane=lane,
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
            build_internal_event_outbox_runtime_write_port().recover_expired_dbapi(
                connection,
                lane=lane,
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
        build_webhook_inbox_runtime_write_port().recover_expired_dbapi(
            connection,
            lane=lane,
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
                   COUNT(*) FILTER (WHERE lane = %s)::BIGINT AS lane_count,
                   COUNT(*) FILTER (
                       WHERE lane IN ('wecom_welcome_ingress', 'wecom_welcome')
                   )::BIGINT AS reserved_count
            FROM active
        """

    @staticmethod
    def _active_reserved_capacity(connection: Any, *, policy_version: str) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(max_in_flight), 0)::BIGINT AS reserved_capacity
            FROM queue_lane_policy
            WHERE lane IN ('wecom_welcome_ingress', 'wecom_welcome')
              AND enabled = TRUE
              AND rollout_mode IN ('canary', 'execute')
              AND policy_version = %s
            """,
            (str(policy_version or ""),),
        ).fetchone()
        return int((row or {}).get("reserved_capacity") or 0)

    def renew_lease(
        self,
        *,
        queue_kind: str,
        item_id: int,
        lease_token: str,
        generation: int,
        lease_seconds: int = 30,
    ) -> bool:
        if queue_kind == "internal_event":
            with self._connect(self._database_url) as connection:
                renewed = build_internal_event_consumer_run_write_port().renew_lease_dbapi(
                    connection,
                    item_id=int(item_id),
                    lease_token=str(lease_token or ""),
                    generation=int(generation),
                    lease_seconds=lease_seconds,
                )
                connection.commit()
            return renewed
        if queue_kind == "internal_outbox":
            with self._connect(self._database_url) as connection:
                renewed = build_internal_event_outbox_runtime_write_port().renew_lease_dbapi(
                    connection,
                    item_id=int(item_id),
                    lease_token=str(lease_token or ""),
                    generation=int(generation),
                    lease_seconds=lease_seconds,
                )
                connection.commit()
            return renewed
        if queue_kind == "webhook_inbox":
            with self._connect(self._database_url) as connection:
                renewed = build_webhook_inbox_runtime_write_port().renew_lease_dbapi(
                    connection,
                    item_id=int(item_id),
                    lease_token=str(lease_token or ""),
                    generation=int(generation),
                    lease_seconds=lease_seconds,
                )
                connection.commit()
            return renewed
        if queue_kind != "external_effect":
            raise ValueError("unsupported queue kind")
        with self._connect(self._database_url) as connection:
            renewed = build_external_effect_runtime_write_port().renew_lease_dbapi(
                connection,
                item_id=int(item_id),
                lease_token=str(lease_token or ""),
                generation=int(generation),
                lease_seconds=lease_seconds,
            )
            connection.commit()
        return renewed

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
            build_rate_scope_cooldown_port().persist_dbapi(
                connection,
                request=RateScopeCooldownRequest(
                    rate_scope_key=str(rate_scope_key),
                    provider=str(provider),
                    corp_id=str(corp_id),
                    app_id=str(app_id),
                    operation=str(operation),
                    blocked_until=blocked_until,
                    reason=str(reason),
                    source_attempt_id=str(source_attempt_id),
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
        metrics: Mapping[str, Any] | None = None,
    ) -> None:
        with self._connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO queue_worker_heartbeat (
                    service_name, worker_id, queue_kind, generation, release_sha,
                    rollout_mode, listener_connected, last_notification_at,
                    last_drain_at, metrics_json, heartbeat_at, started_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    %s::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (service_name, worker_id) DO UPDATE
                SET queue_kind = EXCLUDED.queue_kind,
                    generation = EXCLUDED.generation,
                    release_sha = EXCLUDED.release_sha,
                    rollout_mode = EXCLUDED.rollout_mode,
                    listener_connected = EXCLUDED.listener_connected,
                    metrics_json = EXCLUDED.metrics_json,
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
                    json.dumps(dict(metrics or {}), ensure_ascii=False, default=str),
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

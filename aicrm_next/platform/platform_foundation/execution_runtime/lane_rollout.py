from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable
from uuid import uuid4

from aicrm_next.platform.shared.runtime import raw_database_url

from .cutover import GenerationCASConflict
from .repository import normalize_runtime_database_url, open_runtime_connection


AI_AUTOMATION_LANE_CAPACITIES: dict[str, tuple[int, ...]] = {
    "ai_generation": (4, 8, 16, 32, 64),
    "wecom_ai_assistant_bulk": (4, 8, 16, 24),
}
ALLOWED_TRANSITIONS = frozenset(
    {
        ("blocked", "canary"),
        ("canary", "execute"),
        ("canary", "blocked"),
        ("execute", "blocked"),
    }
)


@dataclass(frozen=True)
class LaneBacklogSnapshot:
    open_count: int
    due_count: int
    dispatching_count: int
    open_job_ids: tuple[int, ...]
    open_job_versions: dict[str, int]
    oldest_open_at: str


@dataclass(frozen=True)
class LaneRolloutPlan:
    lane: str
    active_generation: int
    policy_version: str
    external_claim_scope: str
    from_mode: str
    to_mode: str
    from_capacity: int
    to_capacity: int
    backlog: LaneBacklogSnapshot
    expected_open_job_ids: tuple[int, ...]
    max_open_jobs: int
    transition_id: str = ""
    applied: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AiAutomationLaneRolloutRepository:
    """CAS and audit one fail-closed AI automation lane transition."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connect: Callable[[str], Any] = open_runtime_connection,
    ) -> None:
        self._database_url = normalize_runtime_database_url(database_url or raw_database_url())
        if not self._database_url.startswith("postgresql://"):
            raise RuntimeError("PostgreSQL DATABASE_URL is required for AI automation lane rollout")
        self._connect = connect

    def plan(
        self,
        *,
        lane: str,
        expected_generation: int,
        expected_policy_version: str,
        expected_mode: str,
        target_mode: str,
        expected_capacity: int,
        target_capacity: int,
        expected_open_job_ids: tuple[int, ...] = (),
        max_open_jobs: int = 0,
    ) -> LaneRolloutPlan:
        request = self._normalize_request(
            lane=lane,
            expected_generation=expected_generation,
            expected_policy_version=expected_policy_version,
            expected_mode=expected_mode,
            target_mode=target_mode,
            expected_capacity=expected_capacity,
            target_capacity=target_capacity,
            expected_open_job_ids=expected_open_job_ids,
            max_open_jobs=max_open_jobs,
        )
        with self._connect(self._database_url) as connection:
            control, policy = self._read_locked_state(connection, lane=request["lane"], lock=False)
            backlog = self._backlog_snapshot(
                connection,
                lane=request["lane"],
                policy_version=request["expected_policy_version"],
            )
        return self._validate_and_build(control=control, policy=policy, backlog=backlog, **request)

    def apply(
        self,
        *,
        lane: str,
        expected_generation: int,
        expected_policy_version: str,
        expected_mode: str,
        target_mode: str,
        expected_capacity: int,
        target_capacity: int,
        expected_open_job_ids: tuple[int, ...] = (),
        max_open_jobs: int = 0,
        actor: str,
        reason: str,
    ) -> LaneRolloutPlan:
        request = self._normalize_request(
            lane=lane,
            expected_generation=expected_generation,
            expected_policy_version=expected_policy_version,
            expected_mode=expected_mode,
            target_mode=target_mode,
            expected_capacity=expected_capacity,
            target_capacity=target_capacity,
            expected_open_job_ids=expected_open_job_ids,
            max_open_jobs=max_open_jobs,
        )
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason:
            raise ValueError("actor and reason are required")
        transition_id = "qlr_" + uuid4().hex
        with self._connect(self._database_url) as connection:
            with connection.transaction():
                control, policy = self._read_locked_state(connection, lane=request["lane"], lock=True)
                backlog = self._backlog_snapshot(
                    connection,
                    lane=request["lane"],
                    policy_version=request["expected_policy_version"],
                )
                plan = self._validate_and_build(
                    control=control,
                    policy=policy,
                    backlog=backlog,
                    **request,
                )
                updated = connection.execute(
                    """
                    UPDATE queue_lane_policy
                    SET rollout_mode = %s,
                        max_in_flight = %s,
                        blocked_until = NULL,
                        updated_by = %s,
                        updated_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE lane = %s
                      AND enabled = TRUE
                      AND rollout_mode = %s
                      AND max_in_flight = %s
                      AND policy_version = %s
                    RETURNING lane
                    """,
                    (
                        plan.to_mode,
                        plan.to_capacity,
                        normalized_actor,
                        normalized_reason,
                        plan.lane,
                        plan.from_mode,
                        plan.from_capacity,
                        plan.policy_version,
                    ),
                ).fetchone()
                if not updated:
                    raise GenerationCASConflict("AI automation lane rollout CAS lost")
                connection.execute(
                    """
                    INSERT INTO queue_lane_rollout_audit (
                        transition_id, lane, active_generation, policy_version,
                        from_mode, to_mode, from_capacity, to_capacity,
                        backlog_snapshot_json, actor, reason
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        CAST(%s AS jsonb), %s, %s
                    )
                    """,
                    (
                        transition_id,
                        plan.lane,
                        plan.active_generation,
                        plan.policy_version,
                        plan.from_mode,
                        plan.to_mode,
                        plan.from_capacity,
                        plan.to_capacity,
                        json.dumps(asdict(plan.backlog), ensure_ascii=False, sort_keys=True),
                        normalized_actor,
                        normalized_reason,
                    ),
                )
                for job_id in plan.expected_open_job_ids if plan.to_mode == "canary" else ():
                    inserted = connection.execute(
                        """
                        INSERT INTO queue_lane_canary_job_authorization (
                            transition_id, lane, external_effect_job_id,
                            authorized_row_version, policy_version
                        )
                        SELECT %s, %s, id, row_version, %s
                        FROM external_effect_job
                        WHERE id = %s
                          AND lane = %s
                          AND policy_version = %s
                          AND row_version = %s
                          AND status IN ('queued', 'failed_retryable')
                          AND hold_reason = ''
                          AND attempt_count < max_attempts
                          AND provider_call_started_at IS NULL
                        RETURNING external_effect_job_id
                        """,
                        (
                            transition_id,
                            plan.lane,
                            plan.policy_version,
                            job_id,
                            plan.lane,
                            plan.policy_version,
                            plan.backlog.open_job_versions[str(job_id)],
                        ),
                    ).fetchone()
                    if not inserted:
                        raise GenerationCASConflict("reviewed canary job changed before authorization")
                connection.execute(
                    "SELECT pg_notify('aicrm_queue_wakeup', json_build_object('queue_kind', 'external_effect', 'lane', CAST(%s AS TEXT))::text)",
                    (plan.lane,),
                )
        return LaneRolloutPlan(
            **{
                **plan.as_dict(),
                "backlog": plan.backlog,
                "transition_id": transition_id,
                "applied": True,
            }
        )

    @staticmethod
    def _normalize_request(**values: Any) -> dict[str, Any]:
        lane = str(values.get("lane") or "").strip()
        expected_policy_version = str(values.get("expected_policy_version") or "").strip()
        expected_mode = str(values.get("expected_mode") or "").strip()
        target_mode = str(values.get("target_mode") or "").strip()
        expected_generation = int(values.get("expected_generation") or 0)
        expected_capacity = int(values.get("expected_capacity") or 0)
        target_capacity = int(values.get("target_capacity") or 0)
        max_open_jobs = max(0, int(values.get("max_open_jobs") or 0))
        expected_ids = tuple(sorted({int(item) for item in values.get("expected_open_job_ids") or ()}))
        if lane not in AI_AUTOMATION_LANE_CAPACITIES:
            raise ValueError("lane must be ai_generation or wecom_ai_assistant_bulk")
        if not expected_policy_version or expected_generation <= 0:
            raise ValueError("expected generation and policy version are required")
        if (expected_mode, target_mode) not in ALLOWED_TRANSITIONS:
            raise ValueError("unsupported AI automation lane rollout direction")
        allowed_capacities = AI_AUTOMATION_LANE_CAPACITIES[lane]
        if expected_capacity not in allowed_capacities or target_capacity not in allowed_capacities:
            raise ValueError("lane capacity must use an approved rung")
        if target_mode == "canary" and target_capacity != allowed_capacities[0]:
            raise ValueError("canary rollout must start at the initial capacity rung")
        if target_mode == "execute" and target_capacity < expected_capacity:
            raise ValueError("execute promotion cannot lower capacity")
        if target_mode == "blocked" and target_capacity != expected_capacity:
            raise ValueError("rollback must preserve the last capacity")
        return {
            "lane": lane,
            "expected_generation": expected_generation,
            "expected_policy_version": expected_policy_version,
            "expected_mode": expected_mode,
            "target_mode": target_mode,
            "expected_capacity": expected_capacity,
            "target_capacity": target_capacity,
            "expected_open_job_ids": expected_ids,
            "max_open_jobs": max_open_jobs,
        }

    @staticmethod
    def _read_locked_state(connection: Any, *, lane: str, lock: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        suffix = " FOR UPDATE" if lock else ""
        control = connection.execute(
            """
            SELECT active_generation, claim_enabled, rollout_mode,
                   policy_version, external_claim_scope
            FROM queue_runtime_control
            WHERE singleton = TRUE
            """ + suffix
        ).fetchone()
        policy = connection.execute(
            """
            SELECT lane, max_in_flight, enabled, rollout_mode,
                   blocked_until, policy_version
            FROM queue_lane_policy
            WHERE lane = %s
            """ + suffix,
            (lane,),
        ).fetchone()
        if not control or not policy:
            raise RuntimeError("queue runtime control or lane policy is missing")
        return dict(control), dict(policy)

    @staticmethod
    def _backlog_snapshot(connection: Any, *, lane: str, policy_version: str) -> LaneBacklogSnapshot:
        row = connection.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('queued', 'failed_retryable', 'dispatching'))::BIGINT AS open_count,
                COUNT(*) FILTER (
                    WHERE status IN ('queued', 'failed_retryable')
                      AND hold_reason = ''
                      AND attempt_count < max_attempts
                      AND available_at <= CURRENT_TIMESTAMP
                )::BIGINT AS due_count,
                COUNT(*) FILTER (WHERE status = 'dispatching')::BIGINT AS dispatching_count,
                COALESCE(
                    jsonb_agg(id ORDER BY id) FILTER (
                        WHERE status IN ('queued', 'failed_retryable', 'dispatching')
                    ),
                    '[]'::jsonb
                ) AS open_job_ids,
                COALESCE(
                    jsonb_object_agg(id::TEXT, row_version) FILTER (
                        WHERE status IN ('queued', 'failed_retryable', 'dispatching')
                    ),
                    '{}'::jsonb
                ) AS open_job_versions,
                MIN(created_at) FILTER (
                    WHERE status IN ('queued', 'failed_retryable', 'dispatching')
                ) AS oldest_open_at
            FROM external_effect_job
            WHERE lane = %s
              AND policy_version = %s
            """,
            (lane, policy_version),
        ).fetchone() or {}
        return LaneBacklogSnapshot(
            open_count=int(row.get("open_count") or 0),
            due_count=int(row.get("due_count") or 0),
            dispatching_count=int(row.get("dispatching_count") or 0),
            open_job_ids=tuple(int(item) for item in (row.get("open_job_ids") or [])),
            open_job_versions={
                str(key): int(value)
                for key, value in dict(row.get("open_job_versions") or {}).items()
            },
            oldest_open_at=(row.get("oldest_open_at").isoformat() if row.get("oldest_open_at") else ""),
        )

    @staticmethod
    def _validate_and_build(
        *,
        control: dict[str, Any],
        policy: dict[str, Any],
        backlog: LaneBacklogSnapshot,
        lane: str,
        expected_generation: int,
        expected_policy_version: str,
        expected_mode: str,
        target_mode: str,
        expected_capacity: int,
        target_capacity: int,
        expected_open_job_ids: tuple[int, ...],
        max_open_jobs: int,
    ) -> LaneRolloutPlan:
        if (
            int(control.get("active_generation") or 0) != expected_generation
            or not bool(control.get("claim_enabled"))
            or str(control.get("rollout_mode") or "") != "execute"
            or str(control.get("policy_version") or "") != expected_policy_version
            or str(control.get("external_claim_scope") or "") != "all"
        ):
            raise GenerationCASConflict("runtime is not the expected production all-scope generation")
        if (
            not bool(policy.get("enabled"))
            or str(policy.get("policy_version") or "") != expected_policy_version
            or str(policy.get("rollout_mode") or "") != expected_mode
            or int(policy.get("max_in_flight") or 0) != expected_capacity
        ):
            raise GenerationCASConflict("lane policy does not match the reviewed rollout precondition")
        if backlog.dispatching_count and target_mode != "blocked":
            raise GenerationCASConflict("lane has active dispatches and cannot be promoted")
        actual_ids = tuple(sorted(backlog.open_job_ids))
        if target_mode == "canary" and actual_ids != expected_open_job_ids:
            raise GenerationCASConflict("canary backlog IDs changed or were not explicitly reviewed")
        if target_mode != "blocked" and backlog.open_count > max_open_jobs:
            raise GenerationCASConflict("open lane backlog exceeds the reviewed rollout limit")
        return LaneRolloutPlan(
            lane=lane,
            active_generation=expected_generation,
            policy_version=expected_policy_version,
            external_claim_scope=str(control.get("external_claim_scope") or ""),
            from_mode=expected_mode,
            to_mode=target_mode,
            from_capacity=expected_capacity,
            to_capacity=target_capacity,
            backlog=backlog,
            expected_open_job_ids=expected_open_job_ids,
            max_open_jobs=max_open_jobs,
        )


__all__ = [
    "AI_AUTOMATION_LANE_CAPACITIES",
    "AiAutomationLaneRolloutRepository",
    "LaneBacklogSnapshot",
    "LaneRolloutPlan",
]

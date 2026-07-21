from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.external_effects.settlement_events import (
    enqueue_external_effect_settled_rows_in_session,
)

from .domain import clean_text


_TERMINAL_EFFECT_STATUSES = {
    "succeeded",
    "simulated",
    "unknown_after_dispatch",
    "failed_terminal",
    "blocked",
    "cancelled",
}


def request_cancel_dispatching_group_ops_jobs_in_session(
    session: Session,
    *,
    graph_id: int,
    final_effect_job_id: int,
    actor: str,
    reason: str,
    exclude_job_id: int = 0,
) -> list[int]:
    """Fence claimed graph jobs that have not crossed the provider boundary."""

    rows = (
        session.execute(
            text(
                """
                UPDATE external_effect_job job
                SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                    cancel_requested_by = CASE
                        WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                    END,
                    cancel_reason = CASE
                        WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                    END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job.id IN (
                    SELECT dependent_effect_job_id
                    FROM automation_group_ops_effect_dependency
                    WHERE graph_id = :graph_id
                    UNION
                    SELECT prerequisite_effect_job_id
                    FROM automation_group_ops_effect_dependency
                    WHERE graph_id = :graph_id
                    UNION SELECT :final_effect_job_id
                )
                  AND job.id <> :exclude_job_id
                  AND job.status = 'dispatching'
                  AND job.cancel_requested_at IS NULL
                  AND job.provider_call_started_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM external_effect_attempt attempt
                      WHERE attempt.job_id = job.id
                        AND attempt.provider_call_started_at IS NOT NULL
                  )
                RETURNING job.id
                """
            ),
            {
                "graph_id": int(graph_id),
                "final_effect_job_id": int(final_effect_job_id),
                "exclude_job_id": int(exclude_job_id),
                "actor": clean_text(actor),
                "reason": clean_text(reason),
            },
        )
        .scalars()
        .all()
    )
    return [int(value) for value in rows]


class SQLAlchemyGroupOpsEffectGraphLifecycleMixin:
    def settle_effect(
        self,
        effect_job_id: int,
        *,
        status: str,
        attempt_id: str = "",
    ) -> dict[str, Any]:
        terminal_status = clean_text(status)
        if terminal_status not in _TERMINAL_EFFECT_STATUSES:
            return {"ok": False, "applicable": False, "reason": "effect_not_terminal"}
        with self._session_factory() as session:
            graph_row = (
                session.execute(
                    text(
                        """
                        SELECT graph.*, dependency.id AS dependency_id,
                               dependency.prerequisite_effect_job_id
                        FROM automation_group_ops_effect_graph graph
                        LEFT JOIN automation_group_ops_effect_dependency dependency
                          ON dependency.graph_id = graph.id
                         AND dependency.prerequisite_effect_job_id = :effect_job_id
                        WHERE graph.final_effect_job_id = :effect_job_id
                           OR dependency.prerequisite_effect_job_id = :effect_job_id
                        ORDER BY graph.id DESC
                        LIMIT 1
                        FOR UPDATE OF graph
                        """
                    ),
                    {"effect_job_id": int(effect_job_id)},
                )
                .mappings()
                .first()
            )
            if not graph_row:
                session.rollback()
                return {"ok": True, "applicable": False, "reason": "group_ops_graph_not_found"}
            graph = dict(graph_row)
            if clean_text(graph["status"]) in {"superseded", "cancelled", "terminal"}:
                session.rollback()
                return {
                    "ok": True,
                    "applicable": True,
                    "settled": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": graph["execution_id"],
                }

            is_final = int(graph.get("final_effect_job_id") or 0) == int(effect_job_id)
            cancelled_job_ids: list[int] = []
            cancel_requested_job_ids: list[int] = []
            if not is_final:
                dependency_status = "cancelled" if terminal_status == "cancelled" else "failed"
                session.execute(
                    text(
                        """
                        UPDATE automation_group_ops_effect_dependency
                        SET status = :status, completed_attempt_id = :attempt_id,
                            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :dependency_id AND status IN ('waiting', 'failed', 'cancelled')
                        """
                    ),
                    {
                        "status": dependency_status,
                        "attempt_id": clean_text(attempt_id),
                        "dependency_id": int(graph["dependency_id"]),
                    },
                )
            if terminal_status != "succeeded":
                cancel_requested_job_ids = request_cancel_dispatching_group_ops_jobs_in_session(
                    session,
                    graph_id=int(graph["id"]),
                    final_effect_job_id=int(graph.get("final_effect_job_id") or 0),
                    exclude_job_id=int(effect_job_id),
                    actor="group_ops_graph_settlement",
                    reason="group_ops_sibling_terminal",
                )
                cancelled_rows = (
                    session.execute(
                        text(
                            """
                            UPDATE external_effect_job job
                            SET status = 'cancelled',
                                cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                                cancel_requested_by = CASE
                                    WHEN cancel_requested_by = '' THEN 'group_ops_dependency_settlement'
                                    ELSE cancel_requested_by
                                END,
                                cancel_reason = CASE
                                    WHEN cancel_reason = '' THEN 'group_ops_dependency_terminal'
                                    ELSE cancel_reason
                                END,
                                cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                                row_version = row_version + 1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE job.id IN (
                                SELECT dependent_effect_job_id
                                FROM automation_group_ops_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION
                                SELECT prerequisite_effect_job_id
                                FROM automation_group_ops_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION SELECT :final_effect_job_id
                            )
                              AND job.id <> :effect_job_id
                              AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                              AND job.provider_call_started_at IS NULL
                              AND NOT EXISTS (
                                  SELECT 1 FROM external_effect_attempt attempt
                                  WHERE attempt.job_id = job.id
                                    AND attempt.provider_call_started_at IS NOT NULL
                              )
                            RETURNING job.*
                            """
                        ),
                        {
                            "graph_id": int(graph["id"]),
                            "effect_job_id": int(effect_job_id),
                            "final_effect_job_id": int(graph.get("final_effect_job_id") or 0),
                        },
                    )
                    .mappings()
                    .all()
                )
                cancelled_job_ids = enqueue_external_effect_settled_rows_in_session(
                    session,
                    cancelled_rows,
                )
                if is_final:
                    session.execute(
                        text(
                            """
                            UPDATE automation_group_ops_effect_dependency
                            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                            WHERE graph_id = :graph_id AND status = 'waiting'
                            """
                        ),
                        {"graph_id": int(graph["id"])},
                    )
            final_boundary = (
                session.execute(
                    text(
                        """
                        SELECT status,
                               provider_call_started_at IS NOT NULL OR EXISTS (
                                   SELECT 1 FROM external_effect_attempt attempt
                                   WHERE attempt.job_id = external_effect_job.id
                                     AND attempt.provider_call_started_at IS NOT NULL
                               ) AS provider_boundary_crossed
                        FROM external_effect_job
                        WHERE id = :job_id
                        """
                    ),
                    {"job_id": int(graph.get("final_effect_job_id") or 0)},
                )
                .mappings()
                .first()
            )
            provider_boundary_crossed = bool(
                final_boundary and final_boundary["provider_boundary_crossed"]
            )
            session.execute(
                text(
                    """
                    UPDATE automation_group_ops_effect_graph
                    SET status = 'terminal', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :graph_id
                    """
                ),
                {"graph_id": int(graph["id"])},
            )
            session.commit()
            return {
                "ok": True,
                "applicable": True,
                "settled": True,
                "execution_id": graph["execution_id"],
                "effect_job_id": int(effect_job_id),
                "effect_status": terminal_status,
                "cancelled_job_ids": cancelled_job_ids,
                "cancel_requested_job_ids": cancel_requested_job_ids,
                "provider_boundary_crossed": provider_boundary_crossed,
            }

    def cancel_plan(
        self,
        plan_id: int,
        *,
        actor: str,
        reason: str,
        node_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_actor = clean_text(actor) or "group_ops_plan_editor"
        normalized_reason = clean_text(reason) or "group_ops_plan_revised"
        with self._session_factory() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"group_ops_plan_scope:{int(plan_id)}"},
            )
            node_clause = "AND node_id = :node_id" if node_id is not None else ""
            graphs = [
                dict(row)
                for row in session.execute(
                    text(
                        f"""
                        SELECT id, execution_id, final_effect_job_id
                        FROM automation_group_ops_effect_graph
                        WHERE source_kind = 'plan_node'
                          AND plan_id = :plan_id
                          {node_clause}
                          AND status IN ('waiting_dependencies', 'ready')
                        ORDER BY id ASC
                        FOR UPDATE
                        """
                    ),
                    {"plan_id": int(plan_id), "node_id": int(node_id or 0)},
                )
                .mappings()
                .all()
            ]
            cancelled_job_ids: list[int] = []
            cancelled_executions: list[str] = []
            boundary_executions: list[str] = []
            cancel_requested_job_ids: list[int] = []
            for graph in graphs:
                cancel_requested_job_ids.extend(
                    request_cancel_dispatching_group_ops_jobs_in_session(
                        session,
                        graph_id=int(graph["id"]),
                        final_effect_job_id=int(graph.get("final_effect_job_id") or 0),
                        actor=normalized_actor,
                        reason=normalized_reason,
                    )
                )
                cancelled_rows = (
                    session.execute(
                        text(
                            """
                            UPDATE external_effect_job job
                            SET status = 'cancelled',
                                cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                                cancel_requested_by = CASE
                                    WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                                END,
                                cancel_reason = CASE
                                    WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                                END,
                                cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                                row_version = row_version + 1,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE job.id IN (
                                SELECT dependent_effect_job_id
                                FROM automation_group_ops_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION
                                SELECT prerequisite_effect_job_id
                                FROM automation_group_ops_effect_dependency
                                WHERE graph_id = :graph_id
                                UNION SELECT :final_effect_job_id
                            )
                              AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                              AND job.provider_call_started_at IS NULL
                              AND NOT EXISTS (
                                  SELECT 1 FROM external_effect_attempt attempt
                                  WHERE attempt.job_id = job.id
                                    AND attempt.provider_call_started_at IS NOT NULL
                              )
                            RETURNING job.*
                            """
                        ),
                        {
                            "graph_id": int(graph["id"]),
                            "final_effect_job_id": int(graph.get("final_effect_job_id") or 0),
                            "actor": normalized_actor,
                            "reason": normalized_reason,
                        },
                    )
                    .mappings()
                    .all()
                )
                cancelled_job_ids.extend(
                    enqueue_external_effect_settled_rows_in_session(session, cancelled_rows)
                )
                final = (
                    session.execute(
                        text(
                            """
                            SELECT status,
                                   provider_call_started_at IS NOT NULL OR EXISTS (
                                       SELECT 1 FROM external_effect_attempt attempt
                                       WHERE attempt.job_id = external_effect_job.id
                                         AND attempt.provider_call_started_at IS NOT NULL
                                   ) AS provider_boundary_crossed
                            FROM external_effect_job WHERE id = :job_id
                            """
                        ),
                        {"job_id": int(graph.get("final_effect_job_id") or 0)},
                    )
                    .mappings()
                    .first()
                )
                boundary_crossed = bool(
                    final
                    and (
                        final["provider_boundary_crossed"]
                        or clean_text(final["status"]) in {"succeeded", "unknown_after_dispatch"}
                    )
                )
                graph_status = "terminal" if boundary_crossed else "cancelled"
                session.execute(
                    text(
                        """
                        UPDATE automation_group_ops_effect_graph
                        SET status = :status, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :graph_id
                        """
                    ),
                    {"status": graph_status, "graph_id": int(graph["id"])},
                )
                session.execute(
                    text(
                        """
                        UPDATE automation_group_ops_effect_dependency
                        SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                        WHERE graph_id = :graph_id AND status = 'waiting'
                        """
                    ),
                    {"graph_id": int(graph["id"])},
                )
                if boundary_crossed:
                    boundary_executions.append(clean_text(graph["execution_id"]))
                else:
                    cancelled_executions.append(clean_text(graph["execution_id"]))
            session.commit()
            return {
                "ok": True,
                "plan_id": int(plan_id),
                "node_id": int(node_id) if node_id is not None else None,
                "matched_graph_count": len(graphs),
                "cancelled_execution_ids": cancelled_executions,
                "provider_boundary_execution_ids": boundary_executions,
                "cancelled_job_ids": sorted(set(cancelled_job_ids)),
                "cancel_requested_job_ids": sorted(set(cancel_requested_job_ids)),
                "provider_boundary_crossed": bool(boundary_executions),
            }


class InMemoryGroupOpsEffectGraphLifecycleMixin:
    def settle_effect(
        self,
        effect_job_id: int,
        *,
        status: str,
        attempt_id: str = "",
    ) -> dict[str, Any]:
        del attempt_id
        terminal_status = clean_text(status)
        if terminal_status not in _TERMINAL_EFFECT_STATUSES:
            return {"ok": False, "applicable": False, "reason": "effect_not_terminal"}
        with self._lock:
            graph: dict[str, Any] | None = None
            dependency_key = ""
            for candidate in self._by_execution.values():
                if int(candidate["response"]["final_effect_job_id"]) == int(effect_job_id):
                    graph = candidate
                    break
                for material_key, dependency in candidate["dependencies"].items():
                    if int(dependency["job_id"]) == int(effect_job_id):
                        graph = candidate
                        dependency_key = material_key
                        break
                if graph is not None:
                    break
            if graph is None:
                return {"ok": True, "applicable": False, "reason": "group_ops_graph_not_found"}
            if graph["status"] in {"superseded", "cancelled", "terminal"}:
                return {
                    "ok": True,
                    "applicable": True,
                    "settled": False,
                    "reason": f"graph_{graph['status']}",
                    "execution_id": graph["response"]["execution_id"],
                }
            cancelled: list[int] = []
            if dependency_key:
                graph["dependencies"][dependency_key]["status"] = (
                    "cancelled" if terminal_status == "cancelled" else "failed"
                )
            if terminal_status != "succeeded":
                for job_id in graph["response"]["job_ids"]:
                    if int(job_id) == int(effect_job_id):
                        continue
                    job = self._service.get(int(job_id))
                    if not job or job.provider_call_started_at:
                        continue
                    if self._service.cancel(
                        int(job_id),
                        actor="group_ops_dependency_settlement",
                        reason="group_ops_dependency_terminal",
                        expected_version=job.row_version,
                    ):
                        cancelled.append(int(job_id))
            final = self._service.get(int(graph["response"]["final_effect_job_id"]))
            graph["status"] = "terminal"
            graph["response"]["status"] = "terminal"
            return {
                "ok": True,
                "applicable": True,
                "settled": True,
                "execution_id": graph["response"]["execution_id"],
                "effect_job_id": int(effect_job_id),
                "effect_status": terminal_status,
                "cancelled_job_ids": cancelled,
                "provider_boundary_crossed": bool(final and final.provider_call_started_at),
            }

    def cancel_plan(
        self,
        plan_id: int,
        *,
        actor: str,
        reason: str,
        node_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            candidates = [
                (execution_id, graph)
                for execution_id, graph in self._by_execution.items()
                if graph["request"].source_kind == "plan_node"
                and int(graph["request"].plan_id) == int(plan_id)
                and (node_id is None or int(graph["request"].node_id) == int(node_id))
                and graph["status"] in {"waiting_dependencies", "ready"}
            ]
            cancelled_execution_ids: list[str] = []
            boundary_execution_ids: list[str] = []
            cancelled_job_ids: list[int] = []
            for execution_id, graph in candidates:
                final = self._service.get(int(graph["response"]["final_effect_job_id"]))
                boundary_crossed = bool(
                    final
                    and (
                        final.provider_call_started_at
                        or final.status in {"succeeded", "unknown_after_dispatch"}
                    )
                )
                for job_id in graph["response"]["job_ids"]:
                    job = self._service.get(int(job_id))
                    if not job or job.provider_call_started_at:
                        continue
                    if self._service.cancel(
                        int(job_id),
                        actor=clean_text(actor) or "group_ops_plan_editor",
                        reason=clean_text(reason) or "group_ops_plan_revised",
                        expected_version=job.row_version,
                    ):
                        cancelled_job_ids.append(int(job_id))
                graph["status"] = "terminal" if boundary_crossed else "cancelled"
                graph["response"]["status"] = graph["status"]
                for dependency in graph["dependencies"].values():
                    if dependency["status"] == "waiting":
                        dependency["status"] = "cancelled"
                if boundary_crossed:
                    boundary_execution_ids.append(execution_id)
                else:
                    cancelled_execution_ids.append(execution_id)
            return {
                "ok": True,
                "plan_id": int(plan_id),
                "node_id": int(node_id) if node_id is not None else None,
                "matched_graph_count": len(candidates),
                "cancelled_execution_ids": cancelled_execution_ids,
                "provider_boundary_execution_ids": boundary_execution_ids,
                "cancelled_job_ids": sorted(set(cancelled_job_ids)),
                "provider_boundary_crossed": bool(boundary_execution_ids),
            }


__all__ = [
    "InMemoryGroupOpsEffectGraphLifecycleMixin",
    "SQLAlchemyGroupOpsEffectGraphLifecycleMixin",
    "request_cancel_dispatching_group_ops_jobs_in_session",
]

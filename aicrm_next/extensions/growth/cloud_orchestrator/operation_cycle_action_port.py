from __future__ import annotations

from typing import Any

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


class OperationCycleActionEvidenceError(Exception):
    def __init__(self, code: str) -> None:
        self.code = str(code or "operation_cycle_action_evidence_invalid")
        self.status_code = 409
        super().__init__(self.code)


class PostgresOperationCycleActionPlanEvidencePort:
    """Read-only cross-context evidence adapter for action completion gates."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def verify_prepare_result(
        self,
        *,
        strategy_key: str,
        run_key: str,
        strategy_version: int,
        context_hash: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT preparation.preparation_id, preparation.status AS preparation_status,
                           preparation.source_hash, preparation.strategy_key,
                           preparation.strategy_version, preparation.context_hash,
                           preparation.run_key, preparation.eligible_count,
                           preparation.plan_id,
                           plan.review_status, plan.run_status,
                           (SELECT COUNT(*) FROM broadcast_jobs job
                             WHERE job.source_type = 'cloud_plan'
                               AND job.source_id = plan.plan_id)::integer AS broadcast_jobs
                    FROM external_campaign_preparations preparation
                    JOIN cloud_broadcast_plans plan ON plan.plan_id = preparation.plan_id
                    WHERE preparation.preparation_id = :preparation_id
                      AND plan.plan_id = :plan_id
                    """
                ),
                {
                    "preparation_id": str(result.get("preparation_id") or ""),
                    "plan_id": str(result.get("plan_id") or ""),
                },
            ).mappings().fetchone()
        if row is None:
            raise OperationCycleActionEvidenceError("campaign_preparation_commit_evidence_not_found")
        evidence = dict(row)
        exact_matches = {
            "preparation_status": "committed",
            "source_hash": str(result.get("excel_sha256") or "").lower(),
            "strategy_key": str(strategy_key or ""),
            "strategy_version": int(strategy_version),
            "context_hash": str(context_hash or ""),
            "run_key": str(run_key or ""),
            "plan_id": str(result.get("plan_id") or ""),
            "review_status": "pending_review",
            "run_status": "draft",
            "broadcast_jobs": 0,
            "eligible_count": int(result.get("total_count") or 0),
        }
        for key, expected in exact_matches.items():
            actual = evidence.get(key)
            if isinstance(expected, int):
                actual = int(actual or 0)
            else:
                actual = str(actual or "")
            if actual != expected:
                raise OperationCycleActionEvidenceError(f"prepare_result_{key}_mismatch")
        return evidence

    def get_plan_state(self, plan_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT plan.plan_id, plan.review_status, plan.run_status,
                           COALESCE(link.task_count, 0)::integer AS task_count,
                           COALESCE(link.finalized_count, 0)::integer AS finalized_count,
                           COALESCE(link.sent_count, 0)::integer AS sent_count,
                           COALESCE(link.failed_count, 0)::integer AS failed_count,
                           (SELECT COUNT(*) FROM broadcast_jobs job
                             WHERE job.source_type = 'cloud_plan'
                               AND job.source_id = plan.plan_id)::integer AS broadcast_jobs
                    FROM cloud_broadcast_plans plan
                    LEFT JOIN operation_cycle_plan_links link
                      ON link.tenant_id = 'aicrm' AND link.plan_id = plan.plan_id
                    WHERE plan.plan_id = :plan_id
                    """
                ),
                {"plan_id": str(plan_id or "")},
            ).mappings().fetchone()
        if row is None:
            raise OperationCycleActionEvidenceError("cloud_plan_not_found")
        state = dict(row)
        task_count = int(state.get("task_count") or 0)
        finalized_count = int(state.get("finalized_count") or 0)
        return {
            **state,
            "source_type": "cloud_plan",
            "delivery_terminal": task_count > 0 and finalized_count >= task_count,
        }


def build_operation_cycle_action_plan_evidence_port() -> PostgresOperationCycleActionPlanEvidencePort:
    return PostgresOperationCycleActionPlanEvidencePort()


__all__ = [
    "OperationCycleActionEvidenceError",
    "PostgresOperationCycleActionPlanEvidencePort",
    "build_operation_cycle_action_plan_evidence_port",
]

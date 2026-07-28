from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.background_jobs.cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)
from aicrm_next.platform.shared.db_session import get_session_factory


class CampaignPreparationCommitError(Exception):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class CampaignPreparationCommandPort(Protocol):
    def commit(
        self,
        preparation_id: str,
        *,
        preparation_hash: str,
        actor_id: str,
    ) -> dict[str, Any]: ...


def _plan_id(preparation_id: str, preparation_hash: str) -> str:
    digest = hashlib.sha256(f"{preparation_id}\0{preparation_hash}".encode("utf-8")).hexdigest()
    return f"plan_campaign_{digest[:28]}"


class PostgresCampaignPreparationCommandPort:
    """Atomic Cloud Orchestrator command port for reviewed campaign plans."""

    def commit(
        self,
        preparation_id: str,
        *,
        preparation_hash: str,
        actor_id: str,
    ) -> dict[str, Any]:
        Session = get_session_factory()
        with Session.begin() as session:
            preparation = session.execute(
                text(
                    """
                    SELECT *
                    FROM external_campaign_preparations
                    WHERE preparation_id = :preparation_id
                    FOR UPDATE
                    """
                ),
                {"preparation_id": preparation_id},
            ).mappings().first()
            if not preparation:
                raise CampaignPreparationCommitError("preparation_not_found", status_code=404)
            if str(preparation.get("preparation_hash") or "") != preparation_hash:
                raise CampaignPreparationCommitError("preparation_hash_conflict")
            status = str(preparation.get("status") or "")
            plan_id = str(preparation.get("plan_id") or "") or _plan_id(preparation_id, preparation_hash)
            if status == "committed":
                return self._result(session, plan_id, reused=True)
            expires_at = preparation.get("expires_at")
            if (
                status == "expired"
                or expires_at is None
                or expires_at <= datetime.now(timezone.utc)
            ):
                raise CampaignPreparationCommitError("preparation_expired")
            if status == "blocked" or list(preparation.get("blockers_json") or []):
                raise CampaignPreparationCommitError("preparation_blocked")
            if status != "ready":
                raise CampaignPreparationCommitError("preparation_not_ready")

            eligible_count = int(preparation.get("eligible_count") or 0)
            actual = session.execute(
                text(
                    """
                    SELECT COUNT(*)::integer AS count,
                           COUNT(DISTINCT resolved_unionid)::integer AS distinct_count
                    FROM external_campaign_preparation_recipients
                    WHERE preparation_id = :preparation_id AND row_status = 'eligible'
                    """
                ),
                {"preparation_id": preparation_id},
            ).mappings().one()
            if eligible_count <= 0 or int(actual.get("count") or 0) != eligible_count:
                raise CampaignPreparationCommitError("preparation_conservation_failed")
            if int(actual.get("distinct_count") or 0) != eligible_count:
                raise CampaignPreparationCommitError("preparation_recipient_duplicate")

            selection = {
                "source": "external_campaign_preparation.v1",
                "preparation_id": preparation_id,
                "preparation_hash": preparation_hash,
                "strategy_key": str(preparation.get("strategy_key") or ""),
                "strategy_version": int(preparation.get("strategy_version") or 0),
                "context_hash": str(preparation.get("context_hash") or ""),
                "run_key": str(preparation.get("run_key") or ""),
                "scheduled_for": preparation.get("scheduled_for").isoformat(),
                "timezone": str(preparation.get("timezone") or "Asia/Shanghai"),
            }
            explanation = {
                "review_required": True,
                "auto_approve_allowed": False,
                "direct_broadcast_jobs_allowed": False,
                "counts": dict(preparation.get("counts_json") or {}),
                "timings_ms": dict(preparation.get("timings_json") or {}),
            }
            session.execute(
                text(
                    """
                    INSERT INTO cloud_broadcast_plans (
                        plan_id, trace_id, session_id, operator, intent, display_name,
                        owner_userid, selection_json, content_strategy, max_recipients,
                        candidate_count, skipped_count, explanation_json, status,
                        review_status, run_status, created_at, updated_at
                    ) VALUES (
                        :plan_id, :preparation_id, :preparation_id, :operator,
                        :intent, :display_name, :owner_userid,
                        CAST(:selection_json AS jsonb), 'dynamic_recipient_messages_v1',
                        :eligible_count, :input_count, :skipped_count,
                        CAST(:explanation_json AS jsonb), 'draft',
                        'pending_review', 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (plan_id) DO NOTHING
                    """
                ),
                {
                    "plan_id": plan_id,
                    "preparation_id": preparation_id,
                    "operator": actor_id or "campaign_agent",
                    "intent": f"Campaign preparation {preparation_id}",
                    "display_name": str(preparation.get("display_name") or ""),
                    "owner_userid": str(preparation.get("owner_userid") or ""),
                    "selection_json": json.dumps(selection, ensure_ascii=False),
                    "eligible_count": eligible_count,
                    "input_count": int(preparation.get("input_count") or 0),
                    "skipped_count": int(preparation.get("skipped_count") or 0),
                    "explanation_json": json.dumps(explanation, ensure_ascii=False),
                },
            )
            build_cloud_broadcast_projection_write_port().insert_campaign_preparation_projection_sqlalchemy(
                session,
                plan_id=plan_id,
                preparation_id=preparation_id,
            )
            session.execute(
                text(
                    """
                    INSERT INTO operation_cycle_plan_links (
                        tenant_id, strategy_key, strategy_version, run_key,
                        plan_id, preparation_id
                    ) VALUES (
                        'aicrm', :strategy_key, :strategy_version, :run_key,
                        :plan_id, :preparation_id
                    ) ON CONFLICT (tenant_id, plan_id) DO NOTHING
                    """
                ),
                {
                    "strategy_key": str(preparation.get("strategy_key") or ""),
                    "strategy_version": int(preparation.get("strategy_version") or 0),
                    "run_key": str(preparation.get("run_key") or ""),
                    "plan_id": plan_id,
                    "preparation_id": preparation_id,
                },
            )
            event_key = f"ops-plan-created:{plan_id}"
            session.execute(
                text(
                    """
                    INSERT INTO operation_cycle_system_facts (
                        tenant_id, plan_id, event_type, event_key, fact_json, occurred_at
                    ) VALUES (
                        'aicrm', :plan_id, 'ops_plan.created', :event_key,
                        CAST(:fact_json AS jsonb), CURRENT_TIMESTAMP
                    ) ON CONFLICT (tenant_id, event_key) DO NOTHING
                    """
                ),
                {
                    "plan_id": plan_id,
                    "event_key": event_key,
                    "fact_json": json.dumps(
                        {
                            "eligible_count": eligible_count,
                            "review_status": "pending_review",
                            "run_status": "draft",
                        }
                    ),
                },
            )
            session.execute(
                text(
                    """
                    UPDATE external_campaign_preparations
                    SET status = 'committed', plan_id = :plan_id,
                        committed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE preparation_id = :preparation_id
                    """
                ),
                {"plan_id": plan_id, "preparation_id": preparation_id},
            )
            result = self._result(session, plan_id, reused=False)
            if result["broadcast_jobs"] != 0:
                raise CampaignPreparationCommitError("ordinary_broadcast_job_created")
            if result["recipient_count"] != eligible_count or result["message_count"] != eligible_count:
                raise CampaignPreparationCommitError("commit_conservation_failed")
            return result

    @staticmethod
    def _result(session: Any, plan_id: str, *, reused: bool) -> dict[str, Any]:
        counts = session.execute(
            text(
                """
                SELECT plan.plan_id, plan.review_status, plan.run_status,
                       (SELECT COUNT(*) FROM cloud_broadcast_plan_recipients recipient
                         WHERE recipient.plan_id = plan.plan_id)::integer AS recipient_count,
                       (SELECT COUNT(*) FROM cloud_broadcast_plan_recipient_messages message
                         WHERE message.plan_id = plan.plan_id)::integer AS message_count,
                       (SELECT COUNT(*) FROM broadcast_jobs job
                         WHERE job.source_type = 'cloud_plan'
                           AND job.source_id = plan.plan_id)::integer AS broadcast_jobs
                FROM cloud_broadcast_plans plan
                WHERE plan.plan_id = :plan_id
                """
            ),
            {"plan_id": plan_id},
        ).mappings().first()
        if not counts:
            raise CampaignPreparationCommitError("committed_plan_not_found")
        return {
            "ok": True,
            "status": "reused" if reused else "created",
            "plan_id": plan_id,
            "review_status": str(counts.get("review_status") or ""),
            "run_status": str(counts.get("run_status") or ""),
            "recipient_count": int(counts.get("recipient_count") or 0),
            "message_count": int(counts.get("message_count") or 0),
            "broadcast_jobs": int(counts.get("broadcast_jobs") or 0),
        }


def build_campaign_preparation_command_port() -> CampaignPreparationCommandPort:
    return PostgresCampaignPreparationCommandPort()


__all__ = [
    "CampaignPreparationCommandPort",
    "CampaignPreparationCommitError",
    "PostgresCampaignPreparationCommandPort",
    "build_campaign_preparation_command_port",
]

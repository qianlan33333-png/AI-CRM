from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import text

from .dto import DataHealthCheckResult


CHECK_ID = "ai_automation_lane_readiness"
TITLE = "AI automation lane readiness"
SOURCE_TABLES = [
    "automation_agent_runtime_config",
    "automation_agent_webhook_item",
    "external_effect_job",
    "queue_lane_policy",
]


def ai_automation_lane_readiness(
    *,
    schema_available: Callable[[], bool],
    session_factory: Callable[[], Any],
    unavailable_result: Callable[[str, str, list[str]], DataHealthCheckResult],
) -> DataHealthCheckResult:
    if not schema_available():
        return unavailable_result(CHECK_ID, TITLE, SOURCE_TABLES)
    try:
        with session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) FILTER (
                                WHERE agent.status = 'active'
                                  AND agent.automation_type = 'agent'
                                  AND agent.need_human_review = FALSE
                                  AND agent.bound_package_key <> ''
                                  AND agent.send_webhook_url <> ''
                            )::BIGINT AS active_dynamic_agent_count,
                            (SELECT rollout_mode FROM queue_lane_policy WHERE lane = 'ai_generation')
                                AS ai_generation_mode,
                            (SELECT rollout_mode FROM queue_lane_policy WHERE lane = 'wecom_ai_assistant_bulk')
                                AS wecom_ai_assistant_bulk_mode,
                            (
                                SELECT COUNT(*) FROM automation_agent_webhook_item item
                                WHERE item.status = 'generation_queued'
                            )::BIGINT AS generation_queued_item_count,
                            (
                                SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(item.updated_at)))
                                FROM automation_agent_webhook_item item
                                WHERE item.status = 'generation_queued'
                            ) AS oldest_generation_queued_age_seconds,
                            (
                                SELECT COUNT(*) FROM external_effect_job job
                                WHERE job.lane = 'ai_generation'
                                  AND job.status IN ('queued', 'failed_retryable', 'dispatching')
                            )::BIGINT AS ai_generation_open_job_count,
                            (
                                SELECT COUNT(*) FROM external_effect_job job
                                WHERE job.lane = 'wecom_ai_assistant_bulk'
                                  AND job.status IN ('queued', 'failed_retryable', 'dispatching')
                            )::BIGINT AS wecom_ai_assistant_bulk_open_job_count
                        FROM automation_agent_runtime_config agent
                        """
                    )
                )
                .mappings()
                .first()
                or {}
            )
    except Exception as exc:  # pragma: no cover - defensive health endpoint guard
        return DataHealthCheckResult(
            check_id=CHECK_ID,
            title=TITLE,
            status="fail",
            severity="red",
            summary="AI automation lane readiness could not be read.",
            evidence={"error": type(exc).__name__, "message": str(exc)[:300]},
            remediation="Verify AI automation pipeline migrations and queue policy read access.",
        )
    active_count = int(row.get("active_dynamic_agent_count") or 0)
    generation_mode = str(row.get("ai_generation_mode") or "missing")
    send_mode = str(row.get("wecom_ai_assistant_bulk_mode") or "missing")
    queued_item_count = int(row.get("generation_queued_item_count") or 0)
    generation_job_count = int(row.get("ai_generation_open_job_count") or 0)
    send_job_count = int(row.get("wecom_ai_assistant_bulk_open_job_count") or 0)
    blocked_modes = {
        lane: mode
        for lane, mode in (
            ("ai_generation", generation_mode),
            ("wecom_ai_assistant_bulk", send_mode),
        )
        if mode not in {"canary", "execute"}
    }
    stranded = bool(
        (generation_mode not in {"canary", "execute"} and (queued_item_count or generation_job_count))
        or (send_mode not in {"canary", "execute"} and send_job_count)
    )
    evidence = {
        "active_dynamic_agent_count": active_count,
        "lane_modes": {
            "ai_generation": generation_mode,
            "wecom_ai_assistant_bulk": send_mode,
        },
        "generation_queued_item_count": queued_item_count,
        "oldest_generation_queued_age_seconds": int(
            float(row.get("oldest_generation_queued_age_seconds") or 0)
        ),
        "open_job_counts": {
            "ai_generation": generation_job_count,
            "wecom_ai_assistant_bulk": send_job_count,
        },
        "blocked_lanes": blocked_modes,
    }
    if stranded:
        return DataHealthCheckResult(
            check_id=CHECK_ID,
            title=TITLE,
            status="fail",
            severity="red",
            summary="AI automation work is stranded behind a blocked production lane.",
            evidence=evidence,
            remediation="Use the audited AI automation lane rollout command; do not replay or duplicate queued recipients.",
        )
    if active_count and blocked_modes:
        return DataHealthCheckResult(
            check_id=CHECK_ID,
            title=TITLE,
            status="warn",
            severity="yellow",
            summary="Active automatic agents depend on one or more fail-closed lanes.",
            evidence=evidence,
            remediation="Complete an authorized canary or pause the affected automatic agents before new traffic arrives.",
        )
    return DataHealthCheckResult(
        check_id=CHECK_ID,
        title=TITLE,
        status="ok",
        severity="green",
        summary="AI automation generation and send lanes are aligned with active agent traffic.",
        evidence=evidence,
        remediation="",
    )


__all__ = ["ai_automation_lane_readiness"]

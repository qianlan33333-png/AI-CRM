from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.internal_events.consumer_registry import (
    InternalEventConsumerRegistry,
)
from aicrm_next.platform.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
)
from aicrm_next.platform.shared.db_session import get_session_factory

from .extensions.hxc.operation_cycles.feature_flags import operation_fact_projection_v1_enabled


FACT_EVENT_TYPES = (
    "ops_plan.approved",
    "broadcast_task.created",
    "broadcast_task.finalized",
)


def _payload_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def operation_cycle_system_fact_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    request_summary = {"event_id": event.event_id, "consumer_name": run.consumer_name}
    if not operation_fact_projection_v1_enabled():
        return InternalEventConsumerResult(
            status="skipped",
            request_summary=request_summary,
            response_summary={"reason": "operation_fact_projection_v1_disabled"},
            result_summary={"reason": "operation_fact_projection_v1_disabled"},
        )
    payload = _payload_dict(event.payload_json)
    finalized = _payload_dict(payload.get("broadcast_task"))
    plan = _payload_dict(payload.get("plan"))
    plan_id = str(plan.get("plan_id") or finalized.get("plan_id") or "").strip()
    Session = get_session_factory()
    with Session.begin() as session:
        if not plan_id and event.event_type == "broadcast_task.created":
            row = session.execute(
                text(
                    """
                    SELECT recipient.plan_id
                    FROM cloud_broadcast_plan_recipients recipient
                    WHERE recipient.broadcast_job_id = :job_id
                    LIMIT 1
                    """
                ),
                {"job_id": int(event.aggregate_id or 0)},
            ).mappings().first()
            plan_id = str((row or {}).get("plan_id") or "")
        link = session.execute(
            text(
                """
                SELECT id FROM operation_cycle_plan_links
                WHERE tenant_id = 'aicrm' AND plan_id = :plan_id
                FOR UPDATE
                """
            ),
            {"plan_id": plan_id},
        ).mappings().first()
        if not link:
            return InternalEventConsumerResult(
                status="skipped",
                request_summary=request_summary,
                response_summary={"reason": "operation_cycle_plan_link_not_found"},
                result_summary={"reason": "operation_cycle_plan_link_not_found"},
            )
        fact = {
            "status": str(finalized.get("status") or plan.get("review_status") or "created"),
            "sent_count": max(0, int(finalized.get("sent_count") or 0)),
            "failed_count": max(0, int(finalized.get("failed_count") or 0)),
        }
        inserted = session.execute(
            text(
                """
                INSERT INTO operation_cycle_system_facts (
                    tenant_id, plan_id, event_type, event_key, fact_json, occurred_at
                ) VALUES (
                    'aicrm', :plan_id, :event_type, :event_key,
                    CAST(:fact_json AS jsonb), CAST(:occurred_at AS timestamptz)
                ) ON CONFLICT (tenant_id, event_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "plan_id": plan_id,
                "event_type": event.event_type,
                "event_key": event.event_id,
                "fact_json": json.dumps(fact, ensure_ascii=False),
                "occurred_at": event.occurred_at or None,
            },
        ).mappings().first()
        if inserted:
            if event.event_type == "ops_plan.approved":
                session.execute(
                    text(
                        """
                        UPDATE operation_cycle_plan_links
                        SET approved_at = COALESCE(approved_at, CAST(:occurred_at AS timestamptz))
                        WHERE id = :link_id
                        """
                    ),
                    {"occurred_at": event.occurred_at or None, "link_id": int(link["id"])},
                )
            elif event.event_type == "broadcast_task.created":
                session.execute(
                    text("UPDATE operation_cycle_plan_links SET task_count = task_count + 1 WHERE id = :link_id"),
                    {"link_id": int(link["id"])},
                )
            else:
                session.execute(
                    text(
                        """
                        UPDATE operation_cycle_plan_links
                        SET finalized_count = finalized_count + 1,
                            sent_count = sent_count + :sent_count,
                            failed_count = failed_count + :failed_count,
                            last_delivery_at = GREATEST(
                                COALESCE(last_delivery_at, '-infinity'::timestamptz),
                                CAST(:occurred_at AS timestamptz)
                            )
                        WHERE id = :link_id
                        """
                    ),
                    {
                        "link_id": int(link["id"]),
                        "sent_count": fact["sent_count"],
                        "failed_count": fact["failed_count"],
                        "occurred_at": event.occurred_at or None,
                    },
                )
    summary = {"projected": True, "event_type": event.event_type, "plan_linked": True}
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary=request_summary,
        response_summary=summary,
        result_summary=summary,
    )


def register_operation_cycle_system_fact_consumers(registry: InternalEventConsumerRegistry) -> None:
    if not operation_fact_projection_v1_enabled():
        return
    for event_type in FACT_EVENT_TYPES:
        registry.register(
            event_type,
            "operation_cycle_system_fact_consumer",
            operation_cycle_system_fact_consumer,
            consumer_type="projection",
        )


__all__ = [
    "FACT_EVENT_TYPES",
    "operation_cycle_system_fact_consumer",
    "register_operation_cycle_system_fact_consumers",
]

from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.internal_events.shadow import (
    emit_broadcast_task_created_shadow_event,
    emit_ops_plan_approved_shadow_event,
    safe_emit,
)
from aicrm_next.send_content.application import normalize_send_content_package

from .repository import CloudPlanRepository, build_cloud_plan_repository


class CloudPlanNotFoundError(LookupError):
    pass


def _clean_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _clean_offset(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _internal_event_response(internal_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "internal_event_id": internal_event.get("event_id") or "",
        "internal_event_status": internal_event.get("status") or "",
        "internal_event_reason": internal_event.get("reason") or "",
        "internal_event_error": internal_event.get("error") or "",
        "internal_event_consumer_run_count": int(internal_event.get("consumer_run_count") or 0),
    }


class ListCloudPlansQuery:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, *, status: str = "", keyword: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
        normalized_limit = _clean_limit(limit, default=20, maximum=100)
        normalized_offset = _clean_offset(offset)
        plans, total = self._repo.list_plans(status=status, keyword=keyword, limit=normalized_limit, offset=normalized_offset)
        return {"ok": True, "plans": plans, "limit": normalized_limit, "offset": normalized_offset, "total": total}

    __call__ = execute


class GetCloudPlanQuery:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str) -> dict[str, Any]:
        plan = self._repo.get_plan(plan_id)
        if not plan:
            raise CloudPlanNotFoundError("plan not found")
        return {"ok": True, "plan": plan, "stats": self._repo.plan_stats(plan_id)}

    __call__ = execute


class ListCloudPlanRecipientsQuery:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, *, status: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        plan = self._repo.get_plan(plan_id)
        if not plan:
            raise CloudPlanNotFoundError("plan not found")
        normalized_limit = _clean_limit(limit, default=50, maximum=200)
        normalized_offset = _clean_offset(offset)
        rows, total = self._repo.list_recipients(plan_id, status=status, limit=normalized_limit, offset=normalized_offset)
        return {"ok": True, "plan": plan, "rows": rows, "limit": normalized_limit, "offset": normalized_offset, "total": total}

    __call__ = execute


class GetCloudPlanRecipientQuery:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, recipient_id: int) -> dict[str, Any]:
        recipient = self._repo.get_recipient(plan_id, recipient_id)
        if not recipient:
            raise CloudPlanNotFoundError("recipient not found")
        messages = self._repo.list_recipient_messages(int(recipient["recipient_id"]))
        return {"ok": True, "recipient": recipient, "messages": messages}

    __call__ = execute


class ApproveCloudPlanCommand:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, *, operator: str) -> dict[str, Any]:
        plan = self._repo.approve_plan(plan_id, operator=operator)
        if not plan:
            raise CloudPlanNotFoundError("plan not found")
        stats = self._repo.plan_stats(plan_id)
        broadcast_enqueue = self._repo.create_or_reuse_recipient_broadcast_jobs(plan_id, operator=operator)
        internal_event = safe_emit(
            "ops_plan.approved",
            emit_ops_plan_approved_shadow_event,
            plan=plan,
            stats=stats,
            operator=operator,
            aggregate_type="cloud_orchestrator_plan",
            source_module="cloud_orchestrator.application",
            source_route="/api/admin/cloud-orchestrator/plans/{plan_id}/approve",
        )
        return {
            "ok": True,
            "plan": plan,
            "stats": stats,
            "broadcast_enqueue": broadcast_enqueue,
            **_internal_event_response(internal_event),
        }

    __call__ = execute


class RejectCloudPlanCommand:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, *, operator: str, reason: str = "") -> dict[str, Any]:
        plan = self._repo.reject_plan(plan_id, operator=operator, reason=reason)
        if not plan:
            raise CloudPlanNotFoundError("plan not found")
        return {"ok": True, "plan": plan, "stats": self._repo.plan_stats(plan_id)}

    __call__ = execute


class ApproveCloudPlanRecipientCommand:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, recipient_id: int, *, operator: str) -> dict[str, Any]:
        result = self._repo.approve_recipient(plan_id, recipient_id, operator=operator)
        internal_event = {"status": ""}
        if result.get("status") in {"approved", "already_approved"} and result.get("job_id"):
            recipient = result.get("recipient") if isinstance(result.get("recipient"), dict) else {}
            internal_event = safe_emit(
                "broadcast_task.created",
                emit_broadcast_task_created_shadow_event,
                job={
                    "id": result.get("job_id"),
                    "source_type": "cloud_plan",
                    "source_table": "cloud_broadcast_plan_recipients",
                    "source_id": f"{plan_id}:{int(recipient_id)}",
                    "idempotency_key": f"cloud_plan_recipient:{plan_id}:{int(recipient_id)}",
                    "target_count": 1,
                    "batch_key": f"cloud_plan_recipient:{plan_id}",
                    "trace_id": plan_id,
                    "created_by": operator,
                    "recipient_id": int(recipient_id),
                    "external_userid": recipient.get("external_userid"),
                },
                source_module="cloud_orchestrator.application",
                source_route="/api/admin/cloud-orchestrator/plans/{plan_id}/recipients/{recipient_id}/approve",
                operator=operator,
                source="cloud_plan_recipient_approval",
            )
        return {
            "ok": True,
            **result,
            "stats": self._repo.plan_stats(plan_id),
            **_internal_event_response(internal_event),
        }

    __call__ = execute


class RejectCloudPlanRecipientCommand:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(self, plan_id: str, recipient_id: int, *, operator: str, reason: str = "") -> dict[str, Any]:
        result = self._repo.reject_recipient(plan_id, recipient_id, operator=operator, reason=reason)
        return {"ok": True, **result, "stats": self._repo.plan_stats(plan_id)}

    __call__ = execute


class UpdateCloudPlanRecipientMessageCommand:
    def __init__(self, repo: CloudPlanRepository | None = None) -> None:
        self._repo = repo or build_cloud_plan_repository()

    def execute(
        self,
        plan_id: str,
        recipient_id: int,
        message_id: int,
        *,
        payload: dict[str, Any],
        operator: str,
    ) -> dict[str, Any]:
        content_payload = payload.get("content_payload") if isinstance(payload.get("content_payload"), dict) else {}
        content_package = payload.get("content_package")
        if not isinstance(content_package, dict):
            content_package = content_payload.get("content_package") if isinstance(content_payload.get("content_package"), dict) else content_payload
        normalized = normalize_send_content_package(content_package, text_enabled=True, require_body=False)
        result = self._repo.update_recipient_message(
            plan_id,
            recipient_id,
            message_id,
            content_package=normalized,
            day_offset=payload.get("day_offset"),
            send_time=payload.get("send_time"),
            operator=operator,
        )
        return {"ok": True, **result, "stats": self._repo.plan_stats(plan_id)}

    __call__ = execute

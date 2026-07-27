from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.send_content.application import normalize_send_content_package

from .repository import CloudPlanRepository, build_cloud_plan_repository


def _text(value: Any) -> str:
    return str(value or "").strip()


def create_ai_assist_review_plan(
    payload: dict[str, Any],
    *,
    repository: CloudPlanRepository | None = None,
) -> dict[str, Any]:
    external_userid = _text(payload.get("external_userid") or payload.get("target_external_userid"))
    owner_userid = _text(payload.get("owner_userid") or payload.get("sender_userid"))
    content_text = _text(payload.get("content_text") or payload.get("message"))
    if not external_userid:
        raise ValueError("external_userid_required")
    if not owner_userid:
        raise ValueError("owner_userid_required")

    raw_package = payload.get("content_package") if isinstance(payload.get("content_package"), dict) else {}
    content_package = normalize_send_content_package(
        {**raw_package, "content_text": content_text or _text(raw_package.get("content_text"))},
        text_enabled=True,
        require_body=True,
    )
    event_id = _text(payload.get("external_event_id") or payload.get("idempotency_key"))
    if not event_id:
        digest = hashlib.sha256(
            f"{owner_userid}\0{external_userid}\0{content_package.get('content_text', '')}".encode("utf-8")
        ).hexdigest()[:24]
        event_id = f"admin_ai_assist_review_{digest}"

    result = (repository or build_cloud_plan_repository()).create_or_reuse_agent_send_plan(
        external_event_id=event_id,
        package_key="admin_ai_assist_review_plan",
        external_userid=external_userid,
        owner_userid=owner_userid,
        content_package=content_package,
        operator=_text(payload.get("operator")) or "admin_ai_assist_review",
        requires_review=True,
    )
    status = _text(result.get("status"))
    if status == "skipped":
        raise ValueError(_text(result.get("reason")) or "review_plan_create_skipped")
    plan_id = _text(result.get("plan_id"))
    return {
        "ok": True,
        **result,
        "route_owner": "ai_crm_next",
        "send_path": "ai_assist_review_plan",
        "review_status": "pending_review",
        "run_status": "draft",
        "broadcast_job_created": False,
        "real_external_call_executed": False,
        "next_step": "admin_click_approve_and_start",
        "plan_url": f"/admin/cloud-orchestrator/plans/{plan_id}",
    }


__all__ = ["create_ai_assist_review_plan"]

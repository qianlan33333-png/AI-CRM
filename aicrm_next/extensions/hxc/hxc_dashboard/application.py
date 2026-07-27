from __future__ import annotations

from typing import Any

from aicrm_next.send_content.application import NormalizeSendContentPackageCommand
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.repository_provider import RepositoryProviderError

from .dto import HxcBroadcastTaskRequest
from .repo import HXC_BROADCAST_SOURCE_TYPE, HxcDashboardBroadcastRepository, build_hxc_dashboard_broadcast_repository


class CreateHxcBroadcastTaskCommand:
    def __init__(self, repo: HxcDashboardBroadcastRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: HxcBroadcastTaskRequest) -> dict[str, Any]:
        if str(request.source_type or "").strip() != HXC_BROADCAST_SOURCE_TYPE:
            raise ContractError("source_type 必须是 hxc_dashboard_broadcast")
        source_id = str(request.source_id or "").strip()
        idempotency_key = str(request.idempotency_key or "").strip()
        sender_userid = str(request.sender_userid or "").strip()
        if not idempotency_key:
            raise ContractError("idempotency_key 不能为空")
        if not sender_userid:
            raise ContractError("sender_userid 不能为空")

        content_package = NormalizeSendContentPackageCommand()(
            request.content_package,
            text_enabled=True,
            require_body=True,
        )
        selected_customer_ids = _normalize_selected_ids(request.selected_customer_ids)
        try:
            repo = self._repo_or_build()
            audience = repo.preview_audience(
                selected_customer_ids=selected_customer_ids,
                audience_filter=request.audience_filter,
                sender_userid=sender_userid,
            )
            base_payload = {
                "source_type": HXC_BROADCAST_SOURCE_TYPE,
                "source_id": source_id,
                "idempotency_key": idempotency_key,
                "sender_userid": sender_userid,
                "audience_filter": request.audience_filter,
                "selected_customer_ids": selected_customer_ids,
                "content_package": content_package,
                "audience_total": int(audience.get("audience_total") or 0),
                "eligible_count": int(audience.get("eligible_count") or 0),
                "skipped_count": int(audience.get("skipped_count") or 0),
                "skipped_by_reason": audience.get("skipped_by_reason") or {},
            }
            if request.dry_run:
                return {"ok": True, "task": _task_response(base_payload, status="created", task_id="", duplicate=False, dry_run=True)}
            existing = repo.get_task_by_key(
                source_type=HXC_BROADCAST_SOURCE_TYPE,
                source_id=source_id,
                idempotency_key=idempotency_key,
            )
            if existing:
                return {"ok": True, "duplicate": True, "task": _task_response(existing, duplicate=True)}
            created = repo.create_task(base_payload)
            return {"ok": True, "duplicate": False, "task": _task_response(created, fallback=base_payload, duplicate=False)}
        except RepositoryProviderError as exc:
            return {
                "ok": True,
                "degraded": True,
                "task": _production_unavailable_task(
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                    sender_userid=sender_userid,
                    audience_filter=request.audience_filter,
                    selected_customer_ids=selected_customer_ids,
                    content_package=content_package,
                    detail=str(exc),
                ),
            }

    def _repo_or_build(self) -> HxcDashboardBroadcastRepository:
        if self._repo is None:
            self._repo = build_hxc_dashboard_broadcast_repository()
        return self._repo

    __call__ = execute


def _normalize_selected_ids(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _task_response(
    task: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
    status: str | None = None,
    task_id: str | None = None,
    duplicate: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = {**(fallback or {}), **(task or {})}
    return {
        "task_id": task_id if task_id is not None else str(source.get("task_id") or ""),
        "status": status or str(source.get("status") or "created"),
        "dispatch_status": str(source.get("dispatch_status") or "pending_external_dispatch"),
        "audience_total": int(source.get("audience_total") or 0),
        "eligible_count": int(source.get("eligible_count") or 0),
        "skipped_count": int(source.get("skipped_count") or 0),
        "skipped_by_reason": source.get("skipped_by_reason") if isinstance(source.get("skipped_by_reason"), dict) else {},
        "content_package": source.get("content_package") if isinstance(source.get("content_package"), dict) else {},
        "source_type": str(source.get("source_type") or HXC_BROADCAST_SOURCE_TYPE),
        "source_id": str(source.get("source_id") or ""),
        "idempotency_key": str(source.get("idempotency_key") or ""),
        "sender_userid": str(source.get("sender_userid") or ""),
        "source_status": str(source.get("source_status") or ""),
        "duplicate": duplicate,
        "dry_run": dry_run,
    }


def _production_unavailable_task(
    *,
    source_id: str,
    idempotency_key: str,
    sender_userid: str,
    audience_filter: dict[str, Any],
    selected_customer_ids: list[str],
    content_package: dict[str, Any],
    detail: str,
) -> dict[str, Any]:
    return {
        "task_id": "",
        "status": "production_unavailable",
        "dispatch_status": "not_created",
        "audience_total": 0,
        "eligible_count": 0,
        "skipped_count": 0,
        "skipped_by_reason": {},
        "content_package": content_package,
        "source_type": HXC_BROADCAST_SOURCE_TYPE,
        "source_id": source_id,
        "idempotency_key": idempotency_key,
        "sender_userid": sender_userid,
        "audience_filter": audience_filter,
        "selected_customer_ids": selected_customer_ids,
        "source_status": "production_unavailable",
        "error": detail,
        "duplicate": False,
        "dry_run": False,
    }

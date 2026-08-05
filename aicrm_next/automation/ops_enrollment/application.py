from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from aicrm_next.integration_ports import (
    UserOpsBatchSendGateway,
    UserOpsDeferredJobGateway,
    UserOpsDndWriteGateway,
    UserOpsReviewPlanGateway,
    build_user_ops_batch_send_gateway,
    build_user_ops_deferred_job_gateway,
    build_user_ops_dnd_gateway,
    build_user_ops_review_plan_gateway,
    build_wecom_message_dispatch_adapter,
)
from aicrm_next.platform.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan
from aicrm_next.platform.shared.config import get_settings
from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from aicrm_next.platform.shared.runtime import fixture_mode
from aicrm_next.platform.shared.safe_logging import safe_log_exception
from aicrm_next.platform.shared.typing import JsonDict

from .dto import BatchSendRequest, BroadcastPreviewRequest, DoNotDisturbRequest, ExportPreviewRequest, UserOpsListRequest
from .audience_target_port import build_audience_target_query
from .effect_enqueue import user_ops_send_disabled
from .repo import UserOpsRepository, build_user_ops_repository, resolve_user_ops_repo_backend
from .send_record_projection import build_send_record_external_effect_projection
from .user_ops import apply_filters, build_overview_cards, normalize_filters, resolve_batch_targets

_REPO: UserOpsRepository | None = None
_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filter_options(rows: list[JsonDict]) -> JsonDict:
    return {
        "class_term_no": sorted({row["class_term_no"] for row in rows if row.get("class_term_no")}),
        "owner_userid": sorted({row["owner_userid"] for row in rows if row.get("owner_userid")}),
        "tag": sorted({str(tag) for row in rows for tag in (row.get("tags") or []) if tag}),
        "wecom_status": ["all", "added", "not_added"],
        "mobile_binding_status": ["all", "bound", "unbound"],
        "activation_bucket": ["all", "activated", "not_activated", "pending_input"],
    }


def reset_user_ops_fixture_state() -> None:
    global _REPO, _audit_ledger, _side_effect_plans, _command_bus
    if not fixture_mode() or not _user_ops_repo_cache_enabled():
        return
    if _REPO is None:
        _REPO = build_user_ops_repository()
    _REPO.reset()
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=_audit_hook)
    _register_preview_handlers()


def _user_ops_repo_cache_enabled() -> bool:
    backend = resolve_user_ops_repo_backend(get_settings())
    return backend not in {"sql", "sqlalchemy", "postgres", "postgresql"}


def _default_repo() -> UserOpsRepository:
    global _REPO
    if not _user_ops_repo_cache_enabled():
        return build_user_ops_repository()
    if _REPO is None:
        _REPO = build_user_ops_repository()
    return _REPO


def _close_repository(repo: UserOpsRepository | None) -> None:
    close = getattr(repo, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        safe_log_exception(LOGGER, "failed to close user ops repository", exc, level=logging.WARNING)


def _should_close_repo(repo_owner: UserOpsRepository | None) -> bool:
    return repo_owner is None


def _readonly_meta() -> JsonDict:
    return {
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "runtime_owner": "next_native",
        "real_external_call_executed": False,
    }


def _media_refs_from_batch_request(request: BatchSendRequest) -> list[JsonDict]:
    refs: list[JsonDict] = []
    for index, image in enumerate(request.images):
        item = image if isinstance(image, dict) else {}
        refs.append({"kind": "image", "index": index, "library_id": item.get("library_id")})
    for index, attachment in enumerate(request.attachments):
        item = attachment if isinstance(attachment, dict) else {}
        msgtype = str(item.get("msgtype") or "").strip().lower()
        payload = item.get(msgtype) if isinstance(item.get(msgtype), dict) else {}
        refs.append(
            {
                "kind": msgtype or "attachment",
                "index": index,
                "library_id": payload.get("library_id") or item.get("library_id"),
            }
        )
    return refs


def _content_package_from_batch_request(request: BatchSendRequest) -> JsonDict:
    package: JsonDict = {
        "content_text": str(request.content or "").strip(),
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }
    for image in request.images:
        item = image if isinstance(image, dict) else {}
        if item.get("library_id"):
            package["image_library_ids"].append(item["library_id"])
    attachment_field = {
        "miniprogram": "miniprogram_library_ids",
        "file": "attachment_library_ids",
        "link": "group_invite_library_ids",
    }
    for attachment in request.attachments:
        item = attachment if isinstance(attachment, dict) else {}
        msgtype = str(item.get("msgtype") or "").strip().lower()
        payload = item.get(msgtype) if isinstance(item.get(msgtype), dict) else {}
        library_id = payload.get("library_id") or item.get("library_id")
        field = attachment_field.get(msgtype)
        if field and library_id:
            package[field].append(library_id)
    return package


def _generated_batch_send_idempotency_key(request: BatchSendRequest, preview: JsonDict) -> str:
    payload = {
        "selection_mode": request.selection_mode,
        "filters": preview.get("filters") or {},
        "target_unionids": preview.get("target_unionids") or [],
        "content": request.content,
        "images": request.images,
        "attachments": request.attachments,
        "operator": request.operator,
        "target_source": request.target_source,
        "target_source_id": request.target_source_id,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"user_ops_batch_send:{digest[:32]}"


def _is_ai_audience_batch_request(request: BatchSendRequest) -> bool:
    return str(request.target_source or "").strip() == "ai_audience_package"


def _batch_rows_for_request(request: BatchSendRequest, repo: UserOpsRepository | None) -> tuple[list[JsonDict], UserOpsRepository | None]:
    request.filters = normalize_filters(request.filters)
    if _is_ai_audience_batch_request(request):
        package_id = int(request.target_source_id or 0)
        if package_id <= 0:
            raise ContractError("target_source_id is required")
        return build_audience_target_query().rows_for_package(package_id), repo
    active_repo = repo or _default_repo()
    return apply_filters(active_repo.list_rows(), request.filters), active_repo


def _user_ops_side_effect_safety() -> JsonDict:
    return {
        "user_ops_dnd_mode": build_user_ops_dnd_gateway().mode,
        "user_ops_batch_send_mode": build_user_ops_batch_send_gateway().mode,
        "wecom_dispatch_mode": build_wecom_message_dispatch_adapter().mode,
        "user_ops_deferred_jobs_mode": build_user_ops_deferred_job_gateway().mode,
        "real_dnd_write_executed": False,
        "real_batch_send_executed": False,
        "real_wecom_dispatch_executed": False,
        "real_deferred_jobs_executed": False,
        "real_wecom_media_upload_executed": False,
        "side_effect_executed": False,
    }


def _audit_hook(command: Command, result: CommandResult) -> None:
    _audit_ledger.record_event(
        event_type=f"{command.command_name}.{result.status}",
        actor_id=result.actor_id,
        actor_type=result.actor_type,
        target_type="user_ops",
        target_id=str(command.payload.get("target_id") or ""),
        source_route=result.source_route,
        command_id=result.command_id,
        trace_id=result.trace_id,
        payload={
            "status": result.status,
            "preview_only": True,
            "real_external_call_executed": False,
        },
    )


_command_bus = CommandBus(audit_hook=_audit_hook)


def _plan_response(plan: SideEffectPlan) -> JsonDict:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["next_step"] = summary.get("next_step") or "requires_approval"
    payload["real_external_call_executed"] = False
    return payload


def _create_preview_plan(
    *,
    command: Command,
    effect_type: str,
    adapter_name: str,
    target_id: str,
    payload_summary: JsonDict,
    risk_level: str = "medium",
) -> JsonDict:
    plan = _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name=adapter_name,
        adapter_mode="real_blocked",
        target_type="user_ops_preview",
        target_id=target_id,
        payload={"payload_summary": payload_summary, "next_step": "requires_approval", "real_external_call_executed": False},
        status="planned",
        risk_level=risk_level,
        requires_approval=True,
    )
    return _plan_response(plan)


def _filters_are_empty(filters: JsonDict) -> bool:
    return not any(str(value or "").strip() for value in filters.values())


def _register_preview_handlers() -> None:
    _command_bus.register("user_ops.broadcast.preview", _handle_broadcast_preview)
    _command_bus.register("user_ops.export.preview", _handle_export_preview)


def get_user_ops_audit_events() -> list[JsonDict]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_user_ops_side_effect_plans() -> list[JsonDict]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def _message_preview(text: str, limit: int = 120) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _mask_value(field: str, value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    if field == "unionid":
        return f"{text[:6]}***{text[-4:]}" if len(text) >= 12 else "***"
    if field == "mobile":
        return f"{text[:3]}****{text[-4:]}" if len(text) >= 7 else "***"
    if field == "external_userid":
        return f"{text[:4]}***{text[-3:]}" if len(text) >= 8 else "***"
    if field in {"customer_name", "name"}:
        return text[0] + "*" if text else ""
    return "***" if text else ""


def _customer_summary(row: JsonDict) -> JsonDict:
    return {
        "id": row["id"],
        "unionid": row["unionid"],
        "external_userid": row["external_userid"],
        "customer_name": row["customer_name"],
        "mobile_masked": _mask_value("mobile", row.get("mobile")),
        "owner_userid": row["owner_userid"],
        "owner_display_name": row.get("owner_display_name") or "",
        "class_term_no": row.get("class_term_no") or "",
        "class_term_label": row.get("class_term_label") or "",
        "activation_bucket": row.get("activation_bucket") or "",
        "activation_bucket_label": row.get("activation_bucket_label") or "",
        "is_added_wecom": bool(row.get("is_added_wecom")),
        "is_mobile_bound": bool(row.get("is_mobile_bound")),
        "do_not_disturb": bool(row.get("do_not_disturb")),
        "tags": list(row.get("tags") or []),
        "updated_at": row.get("updated_at") or "",
    }


def _timeline_for_customer(row: JsonDict) -> list[JsonDict]:
    events = [
        {
            "event_id": f"user_ops_created_{row['id']}",
            "event_type": "lead_pool.created",
            "title": "进入 User Ops 池",
            "occurred_at": row.get("created_at") or "",
            "source": row.get("source_type") or "lead_pool",
        },
        {
            "event_id": f"user_ops_activation_{row['id']}",
            "event_type": "activation.status",
            "title": row.get("activation_bucket_label") or row.get("activation_bucket") or "激活状态",
            "occurred_at": row.get("updated_at") or "",
            "source": "ops_projection",
        },
    ]
    if row.get("do_not_disturb"):
        events.append(
            {
                "event_id": f"user_ops_dnd_{row['id']}",
                "event_type": "do_not_disturb.active",
                "title": "免打扰规则生效",
                "occurred_at": row.get("updated_at") or "",
                "source": "ops_projection",
            }
        )
    return events


def _find_projected_row(repo: UserOpsRepository, *, unionid: str) -> JsonDict | None:
    for row in repo.list_rows():
        if str(row.get("unionid") or "") == unionid:
            return row
    return None


class GetUserOpsOverviewQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: UserOpsListRequest) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            normalized_filters = normalize_filters(request.filters)
            base_rows = repo.list_rows()
            rows = apply_filters(base_rows, normalized_filters)
            return {
                "ok": True,
                **_readonly_meta(),
                "filters": normalized_filters.model_dump(),
                "cards": build_overview_cards(rows),
                "metrics": {"lead_pool_total_count": len(base_rows), "filtered_total": len(rows)},
                "generated_at": _now_iso(),
                "class_term_options": sorted({row["class_term_no"] for row in base_rows if row.get("class_term_no")}),
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class GetUserOpsCardsQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: UserOpsListRequest) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            normalized_filters = normalize_filters(request.filters)
            rows = apply_filters(repo.list_rows(), normalized_filters)
            return {
                "ok": True,
                **_readonly_meta(),
                "cards": build_overview_cards(rows),
                "filters": normalized_filters.model_dump(),
                "generated_at": _now_iso(),
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class GetUserOpsFilterOptionsQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            rows = repo.list_rows()
            return {
                "ok": True,
                **_readonly_meta(),
                "filter_options": _filter_options(rows),
                "generated_at": _now_iso(),
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class ListLeadPoolQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: UserOpsListRequest) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            normalized_filters = normalize_filters(request.filters)
            base_rows = repo.list_rows()
            rows = apply_filters(base_rows, normalized_filters)
            total = len(rows)
            page = rows[request.offset : request.offset + request.limit]
            return {
                "ok": True,
                **_readonly_meta(),
                "items": page,
                "total": total,
                "count": len(page),
                "limit": request.limit,
                "offset": request.offset,
                "filters": normalized_filters.model_dump(),
                "filter_options": _filter_options(base_rows),
                "meta": {"source": "aicrm_next", "generated_at": _now_iso()},
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class ListUserOpsCustomersQuery(ListLeadPoolQuery):
    pass


class GetUserOpsCustomerQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, unionid: str) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            row = _find_projected_row(repo, unionid=unionid)
            if row is None:
                raise NotFoundError("user ops customer not found")
            projected = _customer_summary(row)
            return {
                "ok": True,
                **_readonly_meta(),
                "customer": projected,
                "profile": {
                    "unionid": projected["unionid"],
                    "external_userid": projected["external_userid"],
                    "customer_name": projected["customer_name"],
                    "owner_userid": projected["owner_userid"],
                    "mobile_masked": projected["mobile_masked"],
                },
                "drawer": {
                    "sections": [
                        {"key": "profile", "title": "客户资料", "items": projected},
                        {"key": "ops", "title": "运营状态", "items": {"activation_bucket": projected["activation_bucket"], "do_not_disturb": projected["do_not_disturb"]}},
                    ]
                },
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class GetUserOpsCustomerTimelineQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, unionid: str, *, limit: int = 20, offset: int = 0) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            row = _find_projected_row(repo, unionid=unionid)
            if row is None:
                raise NotFoundError("user ops customer not found")
            events = _timeline_for_customer(row)
            page = events[offset : offset + limit]
            return {
                "ok": True,
                **_readonly_meta(),
                "unionid": unionid,
                "items": page,
                "timeline": page,
                "total": len(events),
                "count": len(page),
                "limit": limit,
                "offset": offset,
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class PreviewUserOpsBatchSendCommand:
    def __init__(
        self,
        repo: UserOpsRepository | None = None,
        batch_gateway: UserOpsBatchSendGateway | None = None,
    ) -> None:
        self._repo = repo
        self._batch_gateway = batch_gateway or build_user_ops_batch_send_gateway()

    def execute(self, request: BatchSendRequest) -> JsonDict:
        repo: UserOpsRepository | None = self._repo
        try:
            rows, repo = _batch_rows_for_request(request, repo)
            preview = resolve_batch_targets(rows, request)
            gateway_result = self._batch_gateway.build_batch_send_preview(
                selection_mode=request.selection_mode,
                filters=preview["filters"],
                selected_ids=request.selected_ids,
                excluded_ids=request.excluded_ids,
                content=request.content,
                targets=preview["final_targets"],
                owner_buckets=preview["owner_buckets"],
                include_do_not_disturb=preview["include_do_not_disturb"],
                media_refs=_media_refs_from_batch_request(request),
            )
            if not gateway_result["ok"]:
                raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])
            return {
                "ok": True,
                **preview,
                "side_effect_safety": _user_ops_side_effect_safety(),
                "adapter_contract": {
                    "batch_send": gateway_result,
                },
            }
        finally:
            if repo is not None and _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class PreviewUserOpsBroadcastCommand:
    command_name = "user_ops.broadcast.preview"

    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: BroadcastPreviewRequest, *, idempotency_key: str = "") -> JsonDict:
        if self._repo is not None:
            return _build_broadcast_preview_response(
                Command(
                    command_name=self.command_name,
                    payload={"request": request.model_dump(), "target_id": "broadcast_preview"},
                    idempotency_key=idempotency_key,
                    context=CommandContext(
                        actor_id=request.operator,
                        actor_type="admin",
                        source_route="/api/admin/user-ops/broadcast/preview",
                    ),
                ),
                request=request,
                repo=self._repo,
            )
        command = Command(
            command_name=self.command_name,
            payload={"request": request.model_dump(), "target_id": "broadcast_preview"},
            idempotency_key=idempotency_key,
            context=CommandContext(
                actor_id=request.operator,
                actor_type="admin",
                source_route="/api/admin/user-ops/broadcast/preview",
            ),
        )
        result = _command_bus.execute(command)
        if result.status == "failed":
            raise ContractError(result.error)
        return dict(result.payload)

    __call__ = execute


class PreviewUserOpsExportCommand:
    command_name = "user_ops.export.preview"

    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, request: ExportPreviewRequest, *, idempotency_key: str = "") -> JsonDict:
        if self._repo is not None:
            return _build_export_preview_response(
                Command(
                    command_name=self.command_name,
                    payload={"request": request.model_dump(), "target_id": "export_preview"},
                    idempotency_key=idempotency_key,
                    context=CommandContext(
                        actor_id=request.operator,
                        actor_type="admin",
                        source_route="/api/admin/user-ops/export/preview",
                    ),
                ),
                request=request,
                repo=self._repo,
            )
        command = Command(
            command_name=self.command_name,
            payload={"request": request.model_dump(), "target_id": "export_preview"},
            idempotency_key=idempotency_key,
            context=CommandContext(
                actor_id=request.operator,
                actor_type="admin",
                source_route="/api/admin/user-ops/export/preview",
            ),
        )
        result = _command_bus.execute(command)
        if result.status == "failed":
            raise ContractError(result.error)
        return dict(result.payload)

    __call__ = execute


def _build_broadcast_preview_response(command: Command, *, request: BroadcastPreviewRequest, repo: UserOpsRepository) -> JsonDict:
    filters = normalize_filters(request.filters)
    filter_payload = filters.model_dump()
    is_controlled_default = not request.model_fields_set or (_filters_are_empty(filter_payload) and not request.message.text.strip())
    rows = apply_filters(repo.list_rows(), filters)
    batch_request = BatchSendRequest(
        selection_mode=request.selection_mode,
        filters=filters,
        selected_ids=request.selected_ids,
        excluded_ids=request.excluded_ids,
        content=request.message.text,
        include_do_not_disturb=request.include_do_not_disturb,
        operator=request.operator,
    )
    preview = resolve_batch_targets(rows, batch_request)
    side_effect_plan = _create_preview_plan(
        command=command,
        effect_type="wecom.broadcast.preview",
        adapter_name="wecom",
        target_id="broadcast_preview",
        payload_summary={
            "candidate_count": preview["selected_count"],
            "eligible_count": preview["eligible_count"],
            "message_preview": _message_preview(request.message.text),
        },
        risk_level="medium",
    )
    return {
        "ok": True,
        "preview_id": f"user_ops_broadcast_preview_{command.command_id[:16]}",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "source_status": "next_command",
        "preview_status": "controlled_default_preview" if is_controlled_default else "controlled_preview",
        "candidate_count": preview["selected_count"],
        "eligible_count": preview["eligible_count"],
        "excluded_count": preview["skipped_count"],
        "excluded_reasons": preview["skipped_summary"],
        "sample_customers": [
            {**item, "mobile": _mask_value("mobile", item.get("mobile")), "mobile_masked": _mask_value("mobile", item.get("mobile"))}
            for item in preview["sendable_samples"]
        ],
        "message_preview": _message_preview(request.message.text),
        "side_effect_plan": side_effect_plan,
        "real_external_call_executed": False,
        "audit_recorded": True,
        "adapter_mode": "real_blocked",
        "filters": filter_payload,
    }


def _handle_broadcast_preview(command: Command) -> JsonDict:
    request = BroadcastPreviewRequest(**dict(command.payload.get("request") or {}))
    repo = _default_repo()
    try:
        return _build_broadcast_preview_response(command, request=request, repo=repo)
    finally:
        _close_repository(repo)


def _build_export_preview_response(command: Command, *, request: ExportPreviewRequest, repo: UserOpsRepository) -> JsonDict:
    filters = normalize_filters(request.filters)
    filter_payload = filters.model_dump()
    rows = apply_filters(repo.list_rows(), filters)
    requested_fields = [field.strip() for field in request.fields if field.strip()]
    is_controlled_default = _filters_are_empty(filter_payload) and not requested_fields
    allowed_fields = ["unionid", "external_userid", "customer_name", "mobile", "owner_userid", "class_term_no", "activation_bucket"]
    fields = [field for field in requested_fields if field in allowed_fields] or allowed_fields[:4]
    masked_sample = [
        {
            field: _mask_value(field, row.get(field)) if field in {"unionid", "external_userid", "customer_name", "mobile"} else str(row.get(field) or "")
            for field in fields
        }
        for row in rows[:5]
    ]
    side_effect_plan = _create_preview_plan(
        command=command,
        effect_type="user_ops.export.file",
        adapter_name="storage",
        target_id="export_preview",
        payload_summary={"estimated_count": len(rows), "fields": fields},
        risk_level="medium",
    )
    return {
        "ok": True,
        "preview_id": f"user_ops_export_preview_{command.command_id[:16]}",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "source_status": "next_command",
        "preview_status": "controlled_default_preview" if is_controlled_default else "controlled_preview",
        "estimated_count": len(rows),
        "fields": fields,
        "masked_sample": masked_sample,
        "requires_approval": True,
        "side_effect_plan": side_effect_plan,
        "real_external_call_executed": False,
        "audit_recorded": True,
        "adapter_mode": "real_blocked",
        "filters": filter_payload,
    }


def _handle_export_preview(command: Command) -> JsonDict:
    request = ExportPreviewRequest(**dict(command.payload.get("request") or {}))
    repo = _default_repo()
    try:
        return _build_export_preview_response(command, request=request, repo=repo)
    finally:
        _close_repository(repo)


class ExecuteUserOpsBatchSendCommand:
    def __init__(
        self,
        repo: UserOpsRepository | None = None,
        batch_gateway: UserOpsBatchSendGateway | None = None,
        review_plan_gateway: UserOpsReviewPlanGateway | None = None,
    ) -> None:
        self._repo = repo
        self._batch_gateway = batch_gateway or build_user_ops_batch_send_gateway()
        self._review_plan_gateway = review_plan_gateway or build_user_ops_review_plan_gateway()

    def execute(self, request: BatchSendRequest, *, idempotency_key: str = "") -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            if not request.confirm:
                raise ContractError("confirm=true is required")
            if user_ops_send_disabled():
                raise ContractError("user ops batch send execute is disabled")
            preview = PreviewUserOpsBatchSendCommand(repo, batch_gateway=self._batch_gateway)(request)
            if not preview["has_body"]:
                raise ContractError("content is required")
            if int(preview["eligible_count"] or 0) <= 0:
                raise ContractError("no eligible targets")

            execute_idempotency_key = str(idempotency_key or "").strip() or _generated_batch_send_idempotency_key(request, preview)
            plan_result = self._review_plan_gateway.create_pending_review_plan(
                idempotency_key=execute_idempotency_key,
                target_source=str(request.target_source or "").strip(),
                target_source_id=request.target_source_id,
                targets=preview["final_targets"],
                content_package=_content_package_from_batch_request(request),
                operator=request.operator,
            )
            if not plan_result.get("ok"):
                raise ContractError(str(plan_result.get("error") or "review plan create failed"))
            plan_id = str(plan_result.get("plan_id") or "").strip()
            if not plan_id:
                raise ContractError("review plan id is required")
            if str(plan_result.get("review_status") or "") != "pending_review":
                raise ContractError("review plan must remain pending review")
            if str(plan_result.get("run_status") or "") != "draft":
                raise ContractError("review plan must remain draft")
            if int(plan_result.get("broadcast_job_count") or 0) != 0:
                raise ContractError("review plan created send jobs before approval")
            review_task = {
                "task_id": f"cloud_plan:{plan_id}",
                "task_type": "ai_assist_review_plan",
                "status": "pending_review",
                "plan_id": plan_id,
                "plan_url": str(plan_result.get("plan_url") or f"/admin/cloud-orchestrator/plans/{plan_id}"),
                "recipient_count": int(plan_result.get("recipient_count") or 0),
                "message_count": int(plan_result.get("message_count") or 0),
            }
            record_payload = {
                "idempotency_key": execute_idempotency_key,
                "execution_backend": "ai_assist_review_plan",
                "target_source": str(request.target_source or "").strip(),
                "target_source_id": request.target_source_id,
                "selected_count": preview["selected_count"],
                "eligible_count": preview["eligible_count"],
                "sent_count": 0,
                "skipped_count": preview["skipped_count"],
                "skipped_reasons": preview["skipped_by_reason"],
                "skipped_by_reason": preview["skipped_by_reason"],
                "skipped_summary": preview["skipped_summary"],
                "skip_summary": preview["skip_summary"],
                "include_do_not_disturb": preview["include_do_not_disturb"],
                "content_preview": preview["content_preview"],
                "image_count": preview["image_count"],
                "sender_userids": sorted({bucket["sender_userid"] for bucket in preview["owner_buckets"]}),
                "target_unionids": preview["target_unionids"],
                "filter_snapshot": preview["filters"],
                "operator": request.operator,
                "status": "planned",
                "status_label": "AI 助手待审批",
                "planned_count": preview["eligible_count"],
                "queued_count": 0,
                "external_effect_status_summary": {},
                "task_results": [review_task],
            }
            record = repo.create_or_get_send_record_by_idempotency(
                idempotency_key=execute_idempotency_key,
                payload=record_payload,
            )
            execution_summary = {
                "backend": "ai_assist_review_plan",
                "external_effect_job_count": 0,
                "broadcast_job_count": 0,
                "task_count": 1,
                "sent_count": 0,
                "planned_count": int(preview["eligible_count"] or 0),
                "queued_count": 0,
                "blocked_count": 0,
                "external_effect_status_supported": False,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
                "requires_approval": True,
                "execution_mode": "approval_gated",
                "next_step": "ai_assist_review",
                "side_effect_safety": _user_ops_side_effect_safety(),
            }
            return {
                "ok": True,
                **preview,
                "record_id": record["record_id"],
                "execution_backend": "ai_assist_review_plan",
                "plan_id": plan_id,
                "plan_url": review_task["plan_url"],
                "review_status": "pending_review",
                "run_status": "draft",
                "external_effect_job_ids": [],
                "external_effect_jobs": [],
                "broadcast_job_count": 0,
                "sent_count": 0,
                "planned_count": int(preview["eligible_count"] or 0),
                "queued_count": 0,
                "blocked_count": 0,
                "execution_summary": execution_summary,
                "task_results": [review_task],
                "real_external_call_executed": False,
                "next_step": "ai_assist_review",
                "send_record_url": f"/api/admin/user-ops/send-records/{record['record_id']}",
                "external_effect_jobs_url": "",
                "external_effect_status_supported": False,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
                "side_effect_safety": _user_ops_side_effect_safety(),
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class ListUserOpsSendRecordsQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, limit: int = 20, offset: int = 0) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            records = repo.list_send_records()
            page = records[offset : offset + limit]
            summaries = [{key: value for key, value in record.items() if key != "task_results"} for record in page]
            return {
                "ok": True,
                **_readonly_meta(),
                "items": summaries,
                "records": summaries,
                "count": len(summaries),
                "total": len(records),
                "limit": limit,
                "offset": offset,
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class GetUserOpsSendRecordQuery:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, record_id: str) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            record = repo.get_send_record(record_id)
            if record is None:
                raise NotFoundError("send record not found")
            if record.get("execution_backend") == "external_effect_queue":
                projection = build_send_record_external_effect_projection(
                    record["record_id"],
                    job_ids=repo.get_send_record_external_effect_job_ids(record["record_id"]),
                )
                refreshed = repo.refresh_send_record_external_effect_status(record["record_id"], projection)
                if refreshed:
                    record = refreshed
            task_results = record.get("task_results", [])
            record_summary = {key: value for key, value in record.items() if key != "task_results"}
            external_effect_supported = bool(record.get("external_effect_status_supported"))
            return {
                "ok": True,
                **_readonly_meta(),
                "record": record_summary,
                "task_results": task_results,
                "external_effect_status_supported": external_effect_supported,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
                "status_note": (
                    "当前支持 external_effect_job 执行状态投影，暂不支持企微终端送达状态。"
                    if external_effect_supported
                    else (
                        "该任务已进入 AI 助手待审批；审批通过前不会创建发送任务。"
                        if record.get("execution_backend") == "ai_assist_review_plan"
                        else "旧发送记录只保留 legacy fake 任务创建结果，不轮询企业微信送达状态。"
                    )
                ),
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class RefreshUserOpsSendRecordStatusCommand:
    def __init__(self, repo: UserOpsRepository | None = None) -> None:
        self._repo = repo

    def execute(self, record_id: str) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            record = repo.get_send_record(record_id)
            if record is None:
                raise NotFoundError("send record not found")
            if record.get("execution_backend") != "external_effect_queue":
                detail = GetUserOpsSendRecordQuery(repo)(record_id)
                return {"ok": True, **detail, "refreshed": False}
            projection = build_send_record_external_effect_projection(
                record["record_id"],
                job_ids=repo.get_send_record_external_effect_job_ids(record["record_id"]),
            )
            refreshed = repo.refresh_send_record_external_effect_status(record["record_id"], projection)
            if not refreshed:
                raise NotFoundError("send record not found")
            record_summary = {key: value for key, value in refreshed.items() if key != "task_results"}
            summary = {
                "planned_count": projection["planned_count"],
                "queued_count": projection["queued_count"],
                "dispatching_count": projection["dispatching_count"],
                "succeeded_count": projection["succeeded_count"],
                "failed_count": projection["failed_count"],
                "blocked_count": projection["blocked_count"],
                "cancelled_count": projection["cancelled_count"],
            }
            return {
                "ok": True,
                **_readonly_meta(),
                "refreshed": True,
                "record_id": refreshed["record_id"],
                "status": projection["status"],
                "summary": summary,
                "record": record_summary,
                "task_results": projection["task_results"],
                "external_effect_status_supported": True,
                "wecom_delivery_status_supported": False,
                "delivery_status_supported": False,
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


class EnqueueUserOpsDeferredJobCommand:
    def __init__(self, gateway: UserOpsDeferredJobGateway | None = None) -> None:
        self._gateway = gateway or build_user_ops_deferred_job_gateway()

    def execute(self, *, job_id: str = "", job_type: str = "", run_at: str = "", target: JsonDict | None = None, payload_summary: JsonDict | None = None) -> JsonDict:
        return self._gateway.enqueue_deferred_job(job_id=job_id, job_type=job_type, run_at=run_at, target=target or {}, payload_summary=payload_summary or {})

    __call__ = execute


class RunDueUserOpsDeferredJobsCommand:
    def __init__(self, gateway: UserOpsDeferredJobGateway | None = None) -> None:
        self._gateway = gateway or build_user_ops_deferred_job_gateway()

    def execute(self, *, now: str = "", limit: int = 100, job_ids: list[str] | None = None) -> JsonDict:
        return self._gateway.run_due_jobs(now=now, limit=limit, job_ids=job_ids or [])

    __call__ = execute


class SetUserOpsDoNotDisturbCommand:
    def __init__(
        self,
        repo: UserOpsRepository | None = None,
        dnd_gateway: UserOpsDndWriteGateway | None = None,
    ) -> None:
        self._repo = repo
        self._dnd_gateway = dnd_gateway or build_user_ops_dnd_gateway()

    def execute(self, request: DoNotDisturbRequest) -> JsonDict:
        repo = self._repo or _default_repo()
        try:
            unionid = request.unionid.strip()
            if not unionid:
                raise ContractError("unionid is required")

            action = request.action.strip().lower()
            is_active = request.is_active
            if is_active is None:
                is_active = action not in {"disable", "cancel", "clear", "remove"}

            gateway_result = (
                self._dnd_gateway.enable_do_not_disturb(
                    reason_code=request.reason_code.strip() or "manual_set",
                    reason_text=request.reason_text.strip() or "运营设置",
                    operator=request.operator,
                    unionid=unionid,
                )
                if is_active
                else self._dnd_gateway.cancel_do_not_disturb(
                    reason_code=request.reason_code.strip() or "manual_set",
                    reason_text=request.reason_text.strip() or "运营设置",
                    operator=request.operator,
                    unionid=unionid,
                )
            )
            if not gateway_result["ok"]:
                raise ContractError(gateway_result["error_message"] or gateway_result["error_code"])

            row = repo.set_do_not_disturb(
                unionid=unionid,
                reason_code=request.reason_code.strip() or "manual_set",
                reason_text=request.reason_text.strip() or "运营设置",
                is_active=bool(is_active),
                operator=request.operator,
            )
            if row is None:
                raise NotFoundError("target is not in user_ops_pool_current")
            return {
                "ok": True,
                "target": {
                    "id": row["id"],
                    "unionid": row["unionid"],
                    "external_userid": row["external_userid"],
                    "mobile": row["mobile"],
                },
                "do_not_disturb": row["do_not_disturb"],
                "do_not_disturb_reasons": row["do_not_disturb_reasons"],
                "side_effect_safety": _user_ops_side_effect_safety(),
                "adapter_contract": {
                    "dnd_write": gateway_result,
                },
            }
        finally:
            if _should_close_repo(self._repo):
                _close_repository(repo)

    __call__ = execute


_register_preview_handlers()

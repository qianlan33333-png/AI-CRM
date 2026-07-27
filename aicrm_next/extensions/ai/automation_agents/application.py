from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import ValidationError

from aicrm_next.send_content.application import PreviewSendContentPackageQuery, assert_group_invite_bindings_ready, normalize_send_content_package
from aicrm_next.send_content.dto import SendContentPackage, SendContentPreviewRequest
from aicrm_next.send_content.repo import build_send_content_repository
from aicrm_next.shared.errors import ContractError

from .dto import AutomationAgentCreateRequest, AutomationAgentUpdateRequest
from .repository import AutomationAgentRepository, build_automation_agent_repository, _text


ALLOWED_STATUSES = {"active", "paused", "archived"}
ALLOWED_AUTOMATION_TYPES = {"agent", "fixed_script"}
MAX_WEBHOOK_USERS = 200


def _base_url(request_base_url: str = "") -> str:
    return _text(request_base_url).rstrip("/")


def _receive_webhook_url(agent_code: str, request_base_url: str = "") -> str:
    path = f"/api/ai/agents/{_text(agent_code)}/audience-webhook"
    return f"{_base_url(request_base_url)}{path}" if _base_url(request_base_url) else path


def _default_send_webhook_url(package_key: str) -> str:
    return f"/api/ai/audience/packages/{_text(package_key)}/webhook"


def _send_webhook_package_key(value: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme else raw.split("?", 1)[0]
    prefix = "/api/ai/audience/packages/"
    suffix = "/webhook"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return ""
    return path[len(prefix) : -len(suffix)].strip("/")


def _normalize_send_webhook_url(value: str, *, package_key: str = "") -> str:
    normalized = _text(value) or _default_send_webhook_url(package_key)
    if not normalized:
        raise ContractError("发送地址不能为空")
    if not _send_webhook_package_key(normalized):
        raise ContractError("发送地址必须指向 AI Audience package webhook")
    if normalized.startswith("/"):
        return normalized
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError("发送地址必须是 / 开头路径或 http(s) URL")
    return normalized


def _automation_type(value: Any) -> str:
    normalized = _text(value) or "agent"
    return normalized if normalized in ALLOWED_AUTOMATION_TYPES else ""


def _automation_type_label(value: Any) -> str:
    return "固定话术" if _automation_type(value) == "fixed_script" else "agent"


def _normalize_fixed_content(value: Any, *, automation_type: str = "agent") -> dict[str, Any]:
    if isinstance(value, SendContentPackage):
        raw = value
    elif isinstance(value, dict):
        raw = SendContentPackage.model_validate(value)
    else:
        raw = SendContentPackage()
    normalized = normalize_send_content_package(
        raw,
        text_enabled=_automation_type(automation_type) == "fixed_script",
        require_body=False,
    )
    attachment_ids = list(normalized.get("attachment_library_ids") or [])
    if attachment_ids:
        repo = build_send_content_repository()
        materials = repo.get_materials_by_ids("attachment", attachment_ids)
        by_id = {int(item.get("library_id") or 0): item for item in materials}
        missing = [item_id for item_id in attachment_ids if item_id not in by_id]
        if missing:
            raise ContractError("附件素材不存在或不可用")
        for item_id in attachment_ids:
            item = by_id[item_id]
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            mime_type = _text(item.get("mime_type") or metadata.get("mime_type")).split(";", 1)[0].lower()
            if mime_type != "application/pdf":
                raise ContractError("附件只允许选择 PDF")
    return normalized


def _summary(content_package: dict[str, Any]) -> dict[str, int]:
    return {
        "image_count": len(content_package.get("image_library_ids") or []),
        "miniprogram_count": len(content_package.get("miniprogram_library_ids") or []),
        "attachment_count": len(content_package.get("attachment_library_ids") or []),
        "group_invite_count": len(content_package.get("group_invite_library_ids") or []),
    }


def _agent_payload(row: dict[str, Any], *, request_base_url: str = "", include_detail: bool = False) -> dict[str, Any]:
    content_package = row.get("fixed_content_package_json") if isinstance(row.get("fixed_content_package_json"), dict) else {}
    automation_type = _automation_type(row.get("automation_type")) or "agent"
    item = {
        "id": int(row.get("id") or 0),
        "automation_type": automation_type,
        "automation_type_label": _automation_type_label(automation_type),
        "agent_code": _text(row.get("agent_code")),
        "agent_name": _text(row.get("agent_name")),
        "bound_package_key": _text(row.get("bound_package_key")),
        "bound_package_name": _text(row.get("bound_package_name")),
        "fixed_material_summary": _summary(content_package),
        "status": _text(row.get("status")),
        "updated_at": _text(row.get("updated_at")),
    }
    if include_detail:
        preview = PreviewSendContentPackageQuery()(
            SendContentPreviewRequest(
                content_package=SendContentPackage.model_validate(content_package),
                text_enabled=automation_type == "fixed_script",
                require_body=False,
            )
        )
        item.update(
            {
                "receive_webhook_url": _receive_webhook_url(
                    item["agent_code"],
                    request_base_url,
                ),
                "receive_webhook_auth_mode": "aicrm_hmac_sha256",
                "receive_webhook_capability": "automation_agent_webhook_receive",
                "send_webhook_url": _text(row.get("send_webhook_url")) or _default_send_webhook_url(item["bound_package_key"]),
                "draft_role_prompt": _text(row.get("draft_role_prompt")),
                "draft_task_prompt": _text(row.get("draft_task_prompt")),
                "published_role_prompt": _text(row.get("published_role_prompt")),
                "published_task_prompt": _text(row.get("published_task_prompt")),
                "draft_version": int(row.get("draft_version") or 0),
                "published_version": int(row.get("published_version") or 0),
                "has_unpublished_changes": (
                    int(row.get("draft_version") or 0) != int(row.get("published_version") or 0)
                    or _text(row.get("draft_role_prompt")) != _text(row.get("published_role_prompt"))
                    or _text(row.get("draft_task_prompt")) != _text(row.get("published_task_prompt"))
                ),
                "fixed_content_package": content_package,
                "fixed_content_package_preview": preview.get("preview", {}),
            }
        )
    return item


class AutomationAgentAdminService:
    def __init__(self, repository: AutomationAgentRepository | None = None) -> None:
        self._repo = repository or build_automation_agent_repository()

    def list_agents(self) -> dict[str, Any]:
        rows = self._repo.list_agents()
        return {"ok": True, "items": [_agent_payload(row) for row in rows], "total": int(rows[0].get("total_count") or 0) if rows else 0}

    def create_agent(self, payload: dict[str, Any], *, request_base_url: str = "") -> dict[str, Any]:
        try:
            request = AutomationAgentCreateRequest.model_validate(payload)
            automation_type = _automation_type(request.automation_type)
            if not automation_type:
                return {"ok": False, "error": "invalid_automation_type"}
            content_package = _normalize_fixed_content(request.fixed_content_package, automation_type=automation_type)
            send_webhook_url = _normalize_send_webhook_url(
                request.send_webhook_url or "",
                package_key=request.bound_package_key,
            )
        except (ValidationError, ContractError) as exc:
            return {"ok": False, "error": "invalid_agent_payload", "detail": str(exc)}
        required = {
            "agent_name": request.agent_name,
            "agent_code": request.agent_code,
            "bound_package_key": request.bound_package_key,
        }
        if automation_type == "agent":
            required.update({"role_prompt": request.role_prompt, "task_prompt": request.task_prompt})
        missing = [key for key, value in required.items() if not _text(value)]
        if missing:
            return {"ok": False, "error": "required_field_missing", "fields": missing}
        if _text(request.status) not in ALLOWED_STATUSES:
            return {"ok": False, "error": "invalid_status"}
        row = self._repo.create_agent(
            {
                **request.model_dump(exclude={"fixed_content_package"}),
                "automation_type": automation_type,
                "fixed_content_package": content_package,
                "send_webhook_url": send_webhook_url,
            }
        )
        detail = self._repo.get_agent(int(row["id"])) or row
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}

    def get_agent(self, agent_id: int, *, request_base_url: str = "") -> dict[str, Any]:
        row = self._repo.get_agent(agent_id)
        if not row or _text(row.get("status")) == "archived":
            return {"ok": False, "error": "agent_not_found"}
        return {"ok": True, "agent": _agent_payload(row, request_base_url=request_base_url, include_detail=True)}

    def update_agent(self, agent_id: int, payload: dict[str, Any], *, request_base_url: str = "") -> dict[str, Any]:
        try:
            request = AutomationAgentUpdateRequest.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": "invalid_agent_payload", "detail": str(exc)}
        updates = request.model_dump(exclude_unset=True)
        existing = self._repo.get_agent(agent_id)
        if not existing:
            return {"ok": False, "error": "agent_not_found"}
        automation_type = _automation_type(updates.get("automation_type", existing.get("automation_type")))
        if not automation_type:
            return {"ok": False, "error": "invalid_automation_type"}
        if "fixed_content_package" in updates:
            try:
                updates["fixed_content_package"] = _normalize_fixed_content(
                    updates["fixed_content_package"],
                    automation_type=automation_type,
                )
            except (ValidationError, ContractError) as exc:
                return {"ok": False, "error": "invalid_fixed_content_package", "detail": str(exc)}
        if "send_webhook_url" in updates:
            try:
                updates["send_webhook_url"] = _normalize_send_webhook_url(
                    updates.get("send_webhook_url") or "",
                    package_key=updates.get("bound_package_key") or _text((self._repo.get_agent(agent_id) or {}).get("bound_package_key")),
                )
            except ContractError as exc:
                return {"ok": False, "error": "invalid_send_webhook_url", "detail": str(exc)}
        if "status" in updates and _text(updates["status"]) not in ALLOWED_STATUSES:
            return {"ok": False, "error": "invalid_status"}
        row = self._repo.update_agent(agent_id, updates)
        if not row:
            return {"ok": False, "error": "agent_not_found"}
        detail = self._repo.get_agent(agent_id) or row
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}

    def copy_agent(self, agent_id: int, *, request_base_url: str = "") -> dict[str, Any]:
        source = self._repo.get_agent(agent_id)
        if not source or _text(source.get("status")) == "archived":
            return {"ok": False, "error": "agent_not_found"}
        copied = self._repo.create_agent(
            {
                "agent_code": self._repo.next_copy_code(_text(source.get("agent_code"))),
                "agent_name": f"{_text(source.get('agent_name'))} 副本".strip(),
                "automation_type": _automation_type(source.get("automation_type")) or "agent",
                "bound_package_key": _text(source.get("bound_package_key")),
                "status": _text(source.get("status")) or "active",
                "role_prompt": _text(source.get("draft_role_prompt")),
                "task_prompt": _text(source.get("draft_task_prompt")),
                "fixed_content_package": source.get("fixed_content_package_json") or {},
                "send_webhook_url": _text(source.get("send_webhook_url")) or _default_send_webhook_url(_text(source.get("bound_package_key"))),
            }
        )
        detail = self._repo.get_agent(int(copied["id"])) or copied
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}

    def publish_agent(self, agent_id: int, *, request_base_url: str = "") -> dict[str, Any]:
        existing = self._repo.get_agent(agent_id)
        if not existing or _text(existing.get("status")) == "archived":
            return {"ok": False, "error": "agent_not_found"}
        try:
            assert_group_invite_bindings_ready(existing.get("fixed_content_package_json") or {})
        except ContractError as exc:
            return {"ok": False, "error": "group_invite_not_ready", "detail": str(exc)}
        row = self._repo.publish_agent(agent_id)
        if not row:
            return {"ok": False, "error": "agent_not_found"}
        detail = self._repo.get_agent(agent_id) or row
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}

    def set_status(self, agent_id: int, status: str, *, request_base_url: str = "") -> dict[str, Any]:
        if status == "active":
            existing = self._repo.get_agent(agent_id)
            if not existing:
                return {"ok": False, "error": "agent_not_found"}
            try:
                assert_group_invite_bindings_ready(existing.get("fixed_content_package_json") or {})
            except ContractError as exc:
                return {"ok": False, "error": "group_invite_not_ready", "detail": str(exc)}
        row = self._repo.set_status(agent_id, status)
        if not row:
            return {"ok": False, "error": "agent_not_found"}
        if status == "archived":
            return {"ok": True, "agent": {"id": int(agent_id), "status": "archived"}}
        detail = self._repo.get_agent(agent_id) or row
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}

    def save_fixed_content(self, agent_id: int, content_package: Any, *, request_base_url: str = "") -> dict[str, Any]:
        existing = self._repo.get_agent(agent_id)
        if not existing:
            return {"ok": False, "error": "agent_not_found"}
        try:
            normalized = _normalize_fixed_content(
                content_package,
                automation_type=_automation_type(existing.get("automation_type")) or "agent",
            )
        except (ValidationError, ContractError) as exc:
            return {"ok": False, "error": "invalid_fixed_content_package", "detail": str(exc)}
        row = self._repo.update_agent(agent_id, {"fixed_content_package": normalized})
        if not row:
            return {"ok": False, "error": "agent_not_found"}
        detail = self._repo.get_agent(agent_id) or row
        return {"ok": True, "agent": _agent_payload(detail, request_base_url=request_base_url, include_detail=True)}


def parse_external_userids(payload: Any) -> tuple[list[str], int]:
    raw_items: list[Any] = []
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("external_userids"), list):
            raw_items = payload.get("external_userids") or []
        elif isinstance(payload.get("items"), list):
            raw_items = [item.get("external_userid") if isinstance(item, dict) else "" for item in payload.get("items") or []]
        elif isinstance(payload.get("members"), list):
            raw_items = [item.get("external_userid") if isinstance(item, dict) else "" for item in payload.get("members") or []]
    received_count = len(raw_items)
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_items:
        value = _text(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result, received_count


class AutomationAgentWebhookService:
    def __init__(self, repository: AutomationAgentRepository | None = None) -> None:
        self._repo = repository or build_automation_agent_repository()

    def handle(
        self,
        agent_code: str,
        payload: Any,
        *,
        raw_body: bytes,
        headers: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        agent = self._repo.get_agent_by_code(agent_code)
        if not agent:
            return {"ok": False, "error": "agent_not_found"}, 404
        if _text(agent.get("status")) != "active":
            return {"ok": False, "error": "agent_not_active"}, 409
        external_userids, received_count = parse_external_userids(payload)
        if len(external_userids) > MAX_WEBHOOK_USERS:
            return {"ok": False, "error": "too_many_external_userids", "limit": MAX_WEBHOOK_USERS}, 400
        if not external_userids and received_count == 0:
            return {"ok": False, "error": "external_userids_required"}, 400
        idempotency_key = _text(headers.get("X-AICRM-Idempotency-Key") or headers.get("x-aicrm-idempotency-key"))
        if not idempotency_key:
            idempotency_key = f"agent_webhook:{agent_code}:{hashlib.sha256(raw_body).hexdigest()}"
        batch_id = f"agent_batch_{uuid4().hex}"
        batch, items = self._repo.create_batch(
            batch_id=batch_id,
            agent=agent,
            headers=headers,
            payload=payload,
            external_userids=external_userids,
            received_count=received_count,
            idempotency_key=idempotency_key,
            source_event_type=_text(headers.get("X-AICRM-Event-Type") or headers.get("x-aicrm-event-type")),
            refresh_run_id=_text(headers.get("X-AICRM-Refresh-Run-Id") or headers.get("x-aicrm-refresh-run-id")),
        )
        return (
            {
                "ok": True,
                "batch_id": batch.get("batch_id"),
                "received_count": received_count,
                "deduped_count": len(external_userids),
                "accepted_count": len(items),
                "mode": "queued",
            },
            200,
        )

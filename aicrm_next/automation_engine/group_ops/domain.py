from __future__ import annotations

import secrets
import re
from datetime import datetime, timezone
from typing import Any

from aicrm_next.send_content.application import NormalizeSendContentPackageCommand
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.wecom_payload_contract import normalize_group_admin_userids

from .message_content import build_group_ops_private_message_request_payload

PLAN_TYPES = {"standard", "webhook"}
PLAN_TYPE_ALIASES = {
    "webhook_receiver": "webhook",
    "trigger_audience_plan": "webhook",
}
PLAN_STATUSES = {"draft", "active", "disabled"}
PLAN_STATUS_ALIASES = {"enabled": "active", "archived": "disabled"}
NODE_STATUSES = {"draft", "active", "disabled"}
GROUP_BINDING_STATUSES = {"active", "removed"}
WEBHOOK_EVENT_STATUSES = {"accepted", "queued", "duplicate", "rejected", "failed"}
WEBHOOK_SEND_MODES = {"queued"}
ACTION_TYPES = {
    "enqueue",
    "add_to_audience",
    "publish_task",
    "send_message",
    "send_group_message",
    "group_notice",
    "webhook_notify",
    "record_only",
}
SCHEDULED_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def group_manageable_by_userid(group: dict[str, Any], userid: str) -> bool:
    member_userid = clean_text(userid)
    if not member_userid:
        return False
    if clean_text(group.get("owner_userid")) == member_userid:
        return True
    return member_userid in normalize_group_admin_userids(group.get("admin_userids"))


def clamp_limit(value: int, *, default: int = 50, maximum: int = 200) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def normalize_plan_type(value: Any) -> str:
    plan_type = clean_text(value).lower()
    plan_type = PLAN_TYPE_ALIASES.get(plan_type, plan_type)
    if plan_type not in PLAN_TYPES:
        raise ContractError("plan_type must be standard, webhook, webhook_receiver, or trigger_audience_plan")
    return plan_type


def normalize_status(value: Any, *, allowed: set[str], default: str) -> str:
    status = clean_text(value).lower() or default
    status = PLAN_STATUS_ALIASES.get(status, status)
    if status not in allowed:
        raise ContractError(f"invalid status: {status}")
    return status


def normalize_action_type(value: Any, *, default: str = "record_only") -> str:
    action_type = clean_text(value).lower() or default
    if action_type not in ACTION_TYPES:
        raise ContractError(f"invalid action_type: {action_type}")
    return action_type


def scheduled_time_options() -> list[str]:
    return [f"{hour:02d}:{minute:02d}" for hour in range(8, 24) for minute in (0, 30)]


def extract_scheduled_time(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    match = re.search(r"(?:[01]\d|2[0-3]):[0-5]\d", text)
    return match.group(0) if match else ""


def normalize_scheduled_time(value: Any) -> str:
    scheduled_time = clean_text(value)
    if not SCHEDULED_TIME_PATTERN.match(scheduled_time):
        raise ContractError("scheduled_time must use HH:MM")
    if scheduled_time not in set(scheduled_time_options()):
        raise ContractError("scheduled_time must be 08:00-23:30 in 30 minute steps")
    return scheduled_time


def derive_node_scheduled_time(node: dict[str, Any]) -> str:
    direct = clean_text(node.get("scheduled_time"))
    if direct:
        try:
            return normalize_scheduled_time(direct)
        except ContractError:
            return ""
    return extract_scheduled_time(node.get("trigger_time_label"))


def normalize_plan_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    plan_name = clean_text(payload.get("plan_name") or payload.get("name") or existing.get("plan_name"))
    if not plan_name:
        raise ContractError("plan_name is required")
    owner_userid = clean_text(
        payload.get("owner_userid") or payload.get("operator_member_id") or payload.get("operatorMemberId") or existing.get("owner_userid")
    )
    if not owner_userid:
        raise ContractError("owner_userid is required")
    plan_type = normalize_plan_type(payload.get("plan_type") or payload.get("type") or existing.get("plan_type") or "standard")
    status = normalize_status(payload.get("status") or existing.get("status") or "draft", allowed=PLAN_STATUSES, default="draft")
    plan_code = clean_text(payload.get("plan_code") or payload.get("code") or existing.get("plan_code"))
    return {
        "plan_code": plan_code,
        "plan_name": plan_name,
        "plan_type": plan_type,
        "owner_userid": owner_userid,
        "status": status,
        "default_action_type": normalize_action_type(
            payload.get("default_action_type")
            or payload.get("defaultActionType")
            or existing.get("default_action_type")
            or ("enqueue" if plan_type == "webhook" else "record_only"),
            default="record_only",
        ),
        "allow_no_sop": bool(
            payload.get("allow_no_sop")
            if "allow_no_sop" in payload
            else payload.get("allowNoSop")
            if "allowNoSop" in payload
            else existing.get("allow_no_sop", True)
        ),
        "allow_external_recipients": bool(
            payload.get("allow_external_recipients")
            if "allow_external_recipients" in payload
            else payload.get("allowExternalRecipients")
            if "allowExternalRecipients" in payload
            else existing.get("allow_external_recipients", True)
        ),
        "description": clean_text(payload.get("description") if "description" in payload else existing.get("description")),
        "created_by": clean_text(payload.get("created_by") or existing.get("created_by") or payload.get("operator") or "system"),
        "updated_by": clean_text(payload.get("updated_by") or payload.get("operator") or "system"),
    }


def normalize_scope_ids(payload: dict[str, Any], *names: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        value = payload.get(name)
        if not isinstance(value, list):
            continue
        for item in value:
            text = clean_text(item)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def normalize_recipient(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ContractError("recipient entries must be objects")
    user_id = clean_text(value.get("userId") or value.get("user_id") or value.get("userId"))
    external_user_id = clean_text(value.get("external_user_id") or value.get("externalUserId") or value.get("external_userid") or value.get("externalUserId"))
    wechat_user_id = clean_text(value.get("wechatUserId") or value.get("wechat_user_id") or value.get("openid"))
    group_id = clean_text(value.get("groupId") or value.get("group_id") or value.get("chat_id"))
    if not any((user_id, external_user_id, wechat_user_id, group_id)):
        raise ContractError("recipient must include userId, external_user_id, wechatUserId, or groupId")
    return {
        "user_id": user_id,
        "external_user_id": external_user_id,
        "wechat_user_id": wechat_user_id,
        "group_id": group_id,
    }


def normalize_recipients(values: Any) -> list[dict[str, str]]:
    if not values:
        return []
    if not isinstance(values, list):
        raise ContractError("recipients must be a list")
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, str]] = []
    for value in values:
        recipient = normalize_recipient(value)
        key = (
            recipient["user_id"],
            recipient["external_user_id"],
            recipient["wechat_user_id"],
            recipient["group_id"],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(recipient)
    return result


def normalize_action_payload(value: Any, *, default_action_type: str = "record_only") -> dict[str, Any]:
    action = dict(value or {}) if isinstance(value, dict) else {}
    action_type = normalize_action_type(action.get("action_type") or action.get("actionType") or action.get("actionType"), default=default_action_type)
    return {
        "action_type": action_type,
        "content": clean_text(action.get("content") or action.get("messageTemplate") or action.get("message_template")),
        "task_template_id": clean_text(action.get("taskTemplateId") or action.get("task_template_id")),
        "queue_key": clean_text(action.get("queueKey") or action.get("queue_key")),
        "audience_id": clean_text(action.get("audienceId") or action.get("audience_id")),
        "raw": action,
    }


def mask_sensitive_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [mask_sensitive_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    masked: dict[str, Any] = {}
    for key, item in value.items():
        lower = clean_text(key).lower()
        if any(marker in lower for marker in ("token", "secret", "authorization", "signature")):
            masked[key] = "[redacted]"
        elif lower in {"external_user_id", "external_userid", "externaluserid"}:
            text = clean_text(item)
            masked[key] = f"{text[:6]}...[redacted]" if text else ""
        else:
            masked[key] = mask_sensitive_payload(item)
    return masked


def normalize_attachments_for_builder(attachments: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_attachments: list[dict[str, Any]] = []
    image_media_ids: list[str] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            raise ContractError("attachments entries must be objects")
        msgtype = clean_text(item.get("msgtype")).lower()
        if msgtype == "image":
            image_payload = item.get("image")
            if not isinstance(image_payload, dict):
                raise ContractError("image attachments must include image object")
            media_id = clean_text(image_payload.get("media_id"))
            if not media_id:
                raise ContractError("image attachments must include media_id")
            image_media_ids.append(media_id)
        else:
            normalized_attachments.append(dict(item))
    return normalized_attachments, image_media_ids


def normalize_message_content(
    *,
    text: Any = "",
    attachments: list[Any] | None = None,
    image_media_ids: list[Any] | None = None,
    sender: str = "",
) -> dict[str, Any]:
    builder_attachments, attachment_image_media_ids = normalize_attachments_for_builder(list(attachments or []))
    builder_image_ids = [clean_text(item) for item in list(image_media_ids or []) if clean_text(item)]
    payload: dict[str, Any] = {
        "content": clean_text(text),
        "attachments": builder_attachments,
        "image_media_ids": attachment_image_media_ids + builder_image_ids,
    }
    if sender:
        payload["sender"] = clean_text(sender)
    try:
        normalized, _image_count = build_group_ops_private_message_request_payload(payload)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    if not normalized.get("text") and not normalized.get("attachments"):
        raise ContractError("content.text or content.attachments is required")
    return normalized


def normalize_group_ops_content_package(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return NormalizeSendContentPackageCommand()(value, text_enabled=True, require_body=False)


def _content_package_has_body(content_package: dict[str, Any]) -> bool:
    return bool(
        clean_text(content_package.get("content_text"))
        or list(content_package.get("image_library_ids") or [])
        or list(content_package.get("miniprogram_library_ids") or [])
        or list(content_package.get("attachment_library_ids") or [])
        or list(content_package.get("group_invite_library_ids") or [])
    )


def normalize_node_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or {}
    day_index = int(payload.get("day_index", existing.get("day_index", 1)) or 1)
    if day_index < 1:
        raise ContractError("day_index must be >= 1")
    scheduled_source = payload.get("scheduled_time") or derive_node_scheduled_time(existing) or extract_scheduled_time(payload.get("trigger_time_label"))
    scheduled_time = normalize_scheduled_time(scheduled_source)
    trigger_time_label = scheduled_time
    action_title = clean_text(payload.get("action_title") or existing.get("action_title"))
    if not action_title:
        raise ContractError("action_title is required")
    attachments = payload.get("attachments", existing.get("attachments", []))
    if not isinstance(attachments, list):
        raise ContractError("attachments must be a list")
    status = normalize_status(payload.get("status", existing.get("status", "active")), allowed=NODE_STATUSES, default="active")
    raw_content_package = payload.get("content_package_json") if "content_package_json" in payload else existing.get("content_package_json")
    has_content_package = isinstance(raw_content_package, dict) and bool(raw_content_package)
    if has_content_package:
        content_package = normalize_group_ops_content_package(raw_content_package)
        text_content = clean_text(content_package.get("content_text"))
    else:
        text_content = clean_text(payload.get("text_content") if "text_content" in payload else existing.get("text_content"))
        content_package = normalize_group_ops_content_package({"content_text": text_content})
    if text_content or attachments:
        normalized_content = normalize_message_content(text=text_content, attachments=attachments)
        normalized_attachments = normalized_content.get("attachments", [])
    elif _content_package_has_body(content_package) or status == "draft":
        normalized_attachments = []
    else:
        raise ContractError("content.text or content.attachments is required")
    return {
        "day_index": day_index,
        "scheduled_time": scheduled_time,
        "trigger_time_label": trigger_time_label,
        "action_title": action_title,
        "text_content": text_content,
        "content_package_json": content_package,
        "attachments": normalized_attachments,
        "sort_order": int(payload.get("sort_order", existing.get("sort_order", 0)) or 0),
        "status": status,
    }


def normalize_group_snapshots(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        chat_id = clean_text(group.get("chat_id"))
        if not chat_id:
            continue
        normalized.append(
            {
                "chat_id": chat_id,
                "group_name": clean_text(group.get("group_name") or chat_id),
                "owner_userid": clean_text(group.get("owner_userid")),
                "owner_name": clean_text(group.get("owner_name") or group.get("owner_userid")),
                "admin_userids": normalize_group_admin_userids(group.get("admin_userids") or group.get("admin_list")),
                "internal_member_count": int(group.get("internal_member_count") or 0),
                "external_member_count": int(group.get("external_member_count") or 0),
                "status": normalize_status(group.get("status") or "active", allowed={"active", "disabled"}, default="active"),
            }
        )
    return normalized


def build_node_group_message_content(
    *,
    node: dict[str, Any],
    sender: str,
    resolved_attachments: list[dict[str, Any]] | None = None,
    resolved_image_media_ids: list[str] | None = None,
    allow_deferred_materials: bool = False,
) -> dict[str, Any]:
    content_package = node.get("content_package_json") if isinstance(node.get("content_package_json"), dict) else {}
    text_content = clean_text(node.get("text_content")) or clean_text(content_package.get("content_text"))
    attachments = list(node.get("attachments") or []) + list(resolved_attachments or [])
    image_media_ids = list(resolved_image_media_ids or [])
    if allow_deferred_materials and not text_content and not attachments and not image_media_ids:
        payload: dict[str, Any] = {}
        if clean_text(sender):
            payload["sender"] = clean_text(sender)
        return payload
    return normalize_message_content(
        text=text_content,
        attachments=attachments,
        image_media_ids=image_media_ids,
        sender=sender,
    )


def assert_run_due_guard(
    *,
    plan_id: int,
    node_ids: list[int],
    operator: str,
    allow_plan_ids: list[int],
    allow_node_ids: list[int],
    max_outbound_tasks: int,
) -> None:
    if not clean_text(operator):
        raise ContractError("operator is required")
    allowed_plans = {int(item) for item in allow_plan_ids or []}
    allowed_nodes = {int(item) for item in allow_node_ids or []}
    if not allowed_plans and not allowed_nodes:
        raise ContractError("run-due allowlist is required")
    if int(plan_id) not in allowed_plans and not allowed_nodes.intersection({int(item) for item in node_ids}):
        raise ContractError("run-due allowlist does not include this plan or node")
    if int(max_outbound_tasks or 0) < 1:
        raise ContractError("max_outbound_tasks is required")


def binding_stats(groups: list[dict[str, Any]]) -> dict[str, int]:
    active = [item for item in groups if clean_text(item.get("status") or "active") == "active"]
    internal = sum(int(item.get("internal_member_count_snapshot") or item.get("internal_member_count") or 0) for item in active)
    external = sum(int(item.get("external_member_count_snapshot") or item.get("external_member_count") or 0) for item in active)
    return {
        "bound_group_count": len(active),
        "internal_member_count": internal,
        "external_member_count": external,
        "estimated_reach": internal + external,
    }


def generate_webhook_key(plan_name: str) -> str:
    base = clean_text(plan_name).lower().replace(" ", "-")[:32] or "group-ops"
    safe = "".join(ch for ch in base if ch.isalnum() or ch == "-").strip("-") or "group-ops"
    return f"{safe}-{secrets.token_hex(3)}"

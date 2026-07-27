from __future__ import annotations

from copy import deepcopy
from typing import Any

from aicrm_next.platform.shared.wecom_payload_contract import normalize_miniprogram_attachment_payload
from aicrm_next.media_library.dto import normalize_group_invite_join_url, normalize_http_url


MAX_GROUP_OPS_MESSAGE_IMAGES = 3
MAX_GROUP_OPS_MESSAGE_ATTACHMENTS = 9
SUPPORTED_GROUP_OPS_ATTACHMENT_TYPES = {"file", "miniprogram", "link"}


def _normalize_str(value: Any) -> str:
    return str(value or "")


def _normalize_sender(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_str(item).strip()
            if normalized:
                return normalized
        return ""
    return _normalize_str(value).strip()


def _normalize_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _extract_text(payload: dict[str, Any]) -> str:
    text_payload = payload.get("text")
    if isinstance(text_payload, dict):
        return _normalize_str(text_payload.get("content"))
    return _normalize_str(payload.get("content"))


def _normalize_attachment(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("attachments entries must be objects")
    normalized = deepcopy(item)
    msgtype = _normalize_str(normalized.get("msgtype")).strip().lower()
    if not msgtype:
        raise ValueError("attachments entries must include msgtype")
    if msgtype not in SUPPORTED_GROUP_OPS_ATTACHMENT_TYPES:
        raise ValueError("attachments msgtype is not supported")
    attachment_payload = normalized.get(msgtype)
    if not isinstance(attachment_payload, dict):
        raise ValueError(f"attachments entries must include a non-empty '{msgtype}' object")
    if msgtype == "file":
        media_id = _normalize_str(attachment_payload.get("media_id")).strip()
        if not media_id:
            raise ValueError("file attachments must include media_id")
        return {"msgtype": "file", "file": {"media_id": media_id}}
    if msgtype == "link":
        title = _normalize_str(attachment_payload.get("title")).strip()
        if not title:
            raise ValueError("link attachments must include title")
        try:
            url = normalize_group_invite_join_url(attachment_payload.get("url"))
            picurl = normalize_http_url(attachment_payload.get("picurl"), field_name="卡片封面链接") if attachment_payload.get("picurl") else ""
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        link = {"title": title, "url": url}
        desc = _normalize_str(attachment_payload.get("desc")).strip()
        if desc:
            link["desc"] = desc
        if picurl:
            link["picurl"] = picurl
        return {"msgtype": "link", "link": link}
    return {"msgtype": "miniprogram", "miniprogram": normalize_miniprogram_attachment_payload(attachment_payload)}


def build_group_ops_private_message_request_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    normalized_payload = deepcopy(payload or {})
    content = _extract_text(normalized_payload)
    attachments = [_normalize_attachment(item) for item in _normalize_list(normalized_payload.get("attachments"))]

    image_attachments: list[dict[str, Any]] = []
    for item in _normalize_list(normalized_payload.get("image_media_ids")):
        media_id = _normalize_str(item).strip()
        if media_id:
            image_attachments.append({"msgtype": "image", "image": {"media_id": media_id}})
    if len(image_attachments) > MAX_GROUP_OPS_MESSAGE_IMAGES:
        raise ValueError(f"at most {MAX_GROUP_OPS_MESSAGE_IMAGES} images are allowed")

    attachments.extend(image_attachments)
    if len(attachments) > MAX_GROUP_OPS_MESSAGE_ATTACHMENTS:
        raise ValueError(f"at most {MAX_GROUP_OPS_MESSAGE_ATTACHMENTS} attachments are allowed")

    for transient_key in ("content", "images", "image_media_ids", "attachment_library_ids", "group_invite_library_ids"):
        normalized_payload.pop(transient_key, None)

    if content.strip():
        normalized_payload["text"] = {"content": content}
    else:
        normalized_payload.pop("text", None)

    if attachments:
        normalized_payload["attachments"] = attachments
    else:
        normalized_payload.pop("attachments", None)

    if not normalized_payload.get("text") and not normalized_payload.get("attachments"):
        raise ValueError("content, images, or attachments is required")

    sender = _normalize_sender(normalized_payload.get("sender"))
    if sender:
        normalized_payload["sender"] = sender
    else:
        normalized_payload.pop("sender", None)

    return normalized_payload, len(image_attachments)

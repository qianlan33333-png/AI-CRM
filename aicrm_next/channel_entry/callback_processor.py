from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.webhook_inbox.models import WebhookInboxItem

from .application import process_wecom_external_contact_event
from .domain import text
from .schemas import ProcessWeComExternalContactEventCommand


def process_wecom_callback_payload(inbox_item: WebhookInboxItem | dict[str, Any]) -> dict[str, Any]:
    row = inbox_item.to_dict() if hasattr(inbox_item, "to_dict") else dict(inbox_item or {})
    payload_json = row.get("payload_json") or {}
    if not isinstance(payload_json, dict):
        raise ValueError("payload_json must be an object")
    return process_wecom_external_contact_event(
        ProcessWeComExternalContactEventCommand(
            corp_id=text(row.get("corp_id")),
            event_data=payload_json,
            payload_xml=text(row.get("payload_xml")),
            route=text(row.get("route")),
            callback_received_at=row.get("received_at"),
        )
    )


def process_wecom_external_contact_event_log(event_log_id: int) -> dict[str, Any]:
    return {
        "ok": False,
        "event_log_id": int(event_log_id or 0),
        "error": "event_log_replay_is_not_supported_use_webhook_inbox",
    }

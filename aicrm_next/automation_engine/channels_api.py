from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from aicrm_next.automation_engine import channels_repo
from aicrm_next.common_operation_members import search_operation_members
from aicrm_next.shared.repository_provider import RepositoryProviderError, blocked_production_payload
from aicrm_next.shared.runtime import production_repository_required

from .channel_fixture_state import FIXTURE_CHANNELS as _FIXTURE_CHANNELS

router = APIRouter()

_FIXTURE_CHANNEL_ASSIGNEES: dict[int, list[dict[str, Any]]] = {}
_FIXTURE_ASSIGNMENT_EVENTS: list[dict[str, Any]] = []
_FIXTURE_WE_COM_LINKS: list[dict[str, Any]] = []
_NEXT_ID = 1
_NEXT_ASSIGNEE_ID = 1
_NEXT_ASSIGNMENT_EVENT_ID = 1
_NEXT_WE_COM_LINK_ID = 1


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_list(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        source = value
    elif isinstance(value, str):
        try:
            source = json.loads(value)
        except ValueError:
            source = []
    else:
        source = []
    result: list[int] = []
    for item in source:
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in result:
            result.append(item_id)
    return result[:9]


def _json_text_list(value: Any, *, max_count: int = 12) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        source = value
    elif isinstance(value, str):
        try:
            source = json.loads(value)
        except ValueError:
            source = [part.strip() for part in value.split(",")]
    else:
        source = []
    result: list[str] = []
    for item in source:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result[:max_count]


def _json_dict(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = _text(value).lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _channel_type(payload: dict[str, Any]) -> tuple[str, str]:
    channel_type = _text(payload.get("channel_type")) or "qrcode"
    carrier_type = _text(payload.get("carrier_type")) or ("link" if channel_type == "wecom_customer_acquisition" else "qrcode")
    if channel_type == "wecom_customer_acquisition" or carrier_type == "link":
        return "wecom_customer_acquisition", "link"
    return "qrcode", "qrcode"


def _assignment_mode(value: Any) -> str:
    mode = _text(value) or "single_owner"
    return mode if mode in {"single_owner", "multi_staff"} else "single_owner"


def _assignment_strategy(value: Any) -> str:
    strategy = _text(value) or "ratio"
    return strategy if strategy in {"ratio", "cap_switch"} else "ratio"


def _validate_assignment_contract(payload: dict[str, Any], data: dict[str, Any]) -> None:
    if "assignment_strategy" in payload:
        strategy = _text(payload.get("assignment_strategy"))
        if strategy and strategy not in {"ratio", "cap_switch"}:
            raise ValueError("invalid_assignment_strategy")
    if "assignees" not in payload:
        return
    channels_repo.normalize_channel_assignees(
        payload.get("assignees") or [],
        strategy=_assignment_strategy(data.get("assignment_strategy")),
    )


def _assert_fixture_channel_write_allowed(*, detail: str) -> None:
    if not production_repository_required():
        return
    payload = blocked_production_payload(
        capability_owner="automation_engine",
        detail=detail,
    )
    raise RepositoryProviderError(payload["page_error"])


def _serialize_assignment_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "channel_id": int(row.get("channel_id") or 0),
        "assignee_staff_id": _text(row.get("assignee_staff_id")),
        "strategy": _text(row.get("strategy")),
        "reason": _text(row.get("reason")),
        "status": _text(row.get("status")) or "assigned",
        "external_contact_id": _text(row.get("external_contact_id")),
        "wecom_user_id": _text(row.get("wecom_user_id")),
        "assigned_at": _iso(row.get("assigned_at")),
    }


def _fixture_assignees(channel_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    rows = [dict(item) for item in _FIXTURE_CHANNEL_ASSIGNEES.get(int(channel_id), [])]
    if active_only:
        rows = [item for item in rows if _text(item.get("status")) == "active"]
    return sorted(rows, key=lambda item: (int(item.get("priority") or 0), int(item.get("id") or 0)))


def _fixture_stats_24h(channel_id: int) -> list[dict[str, Any]]:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    counts: dict[str, int] = {}
    for event in _FIXTURE_ASSIGNMENT_EVENTS:
        if int(event.get("channel_id") or 0) != int(channel_id) or _text(event.get("status")) != "assigned":
            continue
        assigned_at = event.get("assigned_at")
        if isinstance(assigned_at, str):
            try:
                assigned_at = datetime.fromisoformat(assigned_at)
            except ValueError:
                assigned_at = datetime.now(timezone.utc)
        if isinstance(assigned_at, datetime) and assigned_at.tzinfo is None:
            assigned_at = assigned_at.replace(tzinfo=timezone.utc)
        if isinstance(assigned_at, datetime) and assigned_at < cutoff:
            continue
        staff_id = _text(event.get("assignee_staff_id"))
        counts[staff_id] = counts.get(staff_id, 0) + 1
    return [
        {
            "staff_id": item["staff_id"],
            "assignee_staff_id": item["staff_id"],
            "assigned_count": counts.get(item["staff_id"], 0),
            "window": "24h",
        }
        for item in _fixture_assignees(int(channel_id), active_only=True)
    ]


def _fixture_save_channel_assignees(
    channel_id: int,
    *,
    assignment_mode: str,
    assignment_strategy: str,
    assignees: list[dict[str, Any]] | None,
    overflow_policy: str = "",
) -> dict[str, Any]:
    global _NEXT_ASSIGNEE_ID
    if int(channel_id) not in _FIXTURE_CHANNELS:
        raise LookupError("channel_not_found")
    normalized_mode = _assignment_mode(assignment_mode or "multi_staff")
    normalized_strategy = _assignment_strategy(assignment_strategy)
    normalized = channels_repo.normalize_channel_assignees(assignees or [], strategy=normalized_strategy)
    rows = _FIXTURE_CHANNEL_ASSIGNEES.setdefault(int(channel_id), [])
    existing_by_staff = {_text(item.get("staff_id")): item for item in rows}
    active_staff = {item["staff_id"] for item in normalized if item["status"] == "active"}
    for item in normalized:
        row = existing_by_staff.get(item["staff_id"])
        if row is None:
            row = {"id": _NEXT_ASSIGNEE_ID, "channel_id": int(channel_id), "created_at": datetime.now(timezone.utc).isoformat()}
            _NEXT_ASSIGNEE_ID += 1
            rows.append(row)
        row.update(
            {
                "channel_id": int(channel_id),
                "staff_id": item["staff_id"],
                "display_name": item["display_name"],
                "display_name_snapshot": item["display_name_snapshot"],
                "priority": item["priority"],
                "ratio_percent": item.get("ratio_percent"),
                "max_scans_24h": item.get("max_scans_24h"),
                "status": item["status"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    for row in rows:
        if _text(row.get("status")) == "active" and _text(row.get("staff_id")) not in active_staff:
            row["status"] = "archived"
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
    channel = _FIXTURE_CHANNELS[int(channel_id)]
    channel["assignment_mode"] = normalized_mode
    channel["assignment_strategy"] = normalized_strategy
    channel["overflow_policy"] = _text(overflow_policy) or "least_loaded"
    channel["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {
        "channel_id": int(channel_id),
        "assignment_mode": normalized_mode,
        "assignment_strategy": normalized_strategy,
        "overflow_policy": channel["overflow_policy"],
        "assignees": _fixture_assignees(int(channel_id)),
    }


def _fixture_insert_assignment_event(
    *,
    channel_id: int,
    assignee_staff_id: str,
    strategy: str,
    reason: str,
    external_contact_id: str = "",
    wecom_user_id: str = "",
    source_payload_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    global _NEXT_ASSIGNMENT_EVENT_ID
    now = datetime.now(timezone.utc)
    event = {
        "id": _NEXT_ASSIGNMENT_EVENT_ID,
        "channel_id": int(channel_id),
        "assignee_staff_id": _text(assignee_staff_id),
        "strategy": _text(strategy),
        "reason": _text(reason),
        "status": "assigned",
        "external_contact_id": _text(external_contact_id),
        "wecom_user_id": _text(wecom_user_id),
        "source_payload_json": dict(source_payload_json or {}),
        "assigned_at": now,
        "created_at": now,
        "updated_at": now,
    }
    _NEXT_ASSIGNMENT_EVENT_ID += 1
    _FIXTURE_ASSIGNMENT_EVENTS.append(event)
    return _serialize_assignment_event(event)


def _fixture_assignment_counts(channel_id: int, staff_ids: list[str], *, window_24h: bool = False) -> dict[str, int]:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    counts = {staff_id: 0 for staff_id in staff_ids}
    for event in _FIXTURE_ASSIGNMENT_EVENTS:
        if int(event.get("channel_id") or 0) != int(channel_id):
            continue
        staff_id = _text(event.get("assignee_staff_id"))
        if staff_id not in counts or _text(event.get("status")) != "assigned":
            continue
        assigned_at = event.get("assigned_at")
        if isinstance(assigned_at, str):
            try:
                assigned_at = datetime.fromisoformat(assigned_at)
            except ValueError:
                assigned_at = datetime.now(timezone.utc)
        if isinstance(assigned_at, datetime) and assigned_at.tzinfo is None:
            assigned_at = assigned_at.replace(tzinfo=timezone.utc)
        if window_24h and isinstance(assigned_at, datetime) and assigned_at < cutoff:
            continue
        counts[staff_id] += 1
    return counts


def _fixture_choose_channel_assignee(
    channel_id: int,
    *,
    external_contact_id: str = "",
    wecom_user_id: str = "",
    write_event: bool = False,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    channel = _FIXTURE_CHANNELS.get(int(channel_id))
    if not channel:
        raise LookupError("channel_not_found")
    strategy = _assignment_strategy(channel.get("assignment_strategy"))
    assignees = _fixture_assignees(int(channel_id), active_only=True)
    channels_repo.normalize_channel_assignees(assignees, strategy=strategy)
    staff_ids = [item["staff_id"] for item in assignees]
    selected: dict[str, Any] | None = None
    reason = ""
    if strategy == "ratio":
        counts = _fixture_assignment_counts(int(channel_id), staff_ids)
        total = sum(counts.values())
        ranked = []
        for item in assignees:
            expected = (total + 1) * int(item.get("ratio_percent") or 0) / 100
            deficit = expected - int(counts.get(item["staff_id"], 0))
            ranked.append((deficit, int(item.get("priority") or 0), int(item.get("id") or 0), item))
        ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        selected = ranked[0][3] if ranked else None
        reason = "ratio_deficit_selected"
    elif strategy == "cap_switch":
        counts = _fixture_assignment_counts(int(channel_id), staff_ids, window_24h=True)
        for item in assignees:
            if int(counts.get(item["staff_id"], 0)) < int(item.get("max_scans_24h") or 0):
                selected = item
                reason = "cap_switch_priority_available"
                break
        if selected is None:
            return {
                "ok": False,
                "channel_id": int(channel_id),
                "assignment_strategy": strategy,
                "reason": "all_assignees_reached_24h_cap",
                "source": "ai_crm_next",
            }
    if selected is None:
        raise ValueError("active_assignees_required")
    event = None
    if write_event:
        event = _fixture_insert_assignment_event(
            channel_id=int(channel_id),
            assignee_staff_id=selected["staff_id"],
            strategy=strategy,
            reason=reason,
            external_contact_id=external_contact_id,
            wecom_user_id=wecom_user_id,
            source_payload_json=source_payload or {},
        )
    return {
        "ok": True,
        "channel_id": int(channel_id),
        "assignment_strategy": strategy,
        "assignee_staff_id": selected["staff_id"],
        "assignee": selected,
        "reason": reason,
        "event": event,
        "source": "ai_crm_next",
    }


def _list_channel_assignees_resource(channel_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    if not channels_repo.uses_postgres():
        return _fixture_assignees(int(channel_id), active_only=active_only)
    return channels_repo.list_channel_assignees(int(channel_id), active_only=active_only)


def _list_assignment_stats_24h_resource(channel_id: int) -> list[dict[str, Any]]:
    if not channels_repo.uses_postgres():
        return _fixture_stats_24h(int(channel_id))
    return channels_repo.list_assignment_stats_24h(int(channel_id))


def _save_channel_assignees_resource(
    channel_id: int,
    *,
    assignment_mode: str,
    assignment_strategy: str,
    assignees: list[dict[str, Any]] | None,
    overflow_policy: str = "",
) -> dict[str, Any]:
    if not channels_repo.uses_postgres():
        _assert_fixture_channel_write_allowed(detail="channel assignee write requires production database")
        return _fixture_save_channel_assignees(
            int(channel_id),
            assignment_mode=assignment_mode,
            assignment_strategy=assignment_strategy,
            assignees=assignees,
            overflow_policy=overflow_policy,
        )
    return channels_repo.save_channel_assignees(
        int(channel_id),
        assignment_mode=assignment_mode,
        assignment_strategy=assignment_strategy,
        assignees=assignees,
        overflow_policy=overflow_policy,
    )


def _list_assignment_events_resource(channel_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 200))
    if not channels_repo.uses_postgres():
        rows = [
            _serialize_assignment_event(item)
            for item in _FIXTURE_ASSIGNMENT_EVENTS
            if int(item.get("channel_id") or 0) == int(channel_id)
        ]
        rows.sort(key=lambda item: (_text(item.get("assigned_at")), int(item.get("id") or 0)), reverse=True)
        return rows[:safe_limit]
    return channels_repo.list_assignment_events(int(channel_id), limit=safe_limit)


def _choose_channel_assignee_resource(
    channel_id: int,
    *,
    external_contact_id: str = "",
    wecom_user_id: str = "",
    write_event: bool = False,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not channels_repo.uses_postgres():
        if write_event:
            _assert_fixture_channel_write_allowed(detail="channel assignment event write requires production database")
        return _fixture_choose_channel_assignee(
            int(channel_id),
            external_contact_id=external_contact_id,
            wecom_user_id=wecom_user_id,
            write_event=write_event,
            source_payload=source_payload or {},
        )
    return channels_repo.choose_channel_assignee(
        int(channel_id),
        external_contact_id=external_contact_id,
        wecom_user_id=wecom_user_id,
        write_event=write_event,
        source_payload=source_payload or {},
    )


def _attach_assignment_payload(channel: dict[str, Any], *, include_assignees: bool = True) -> dict[str, Any]:
    channel_id = int(channel.get("id") or 0)
    if not channel_id:
        return channel
    if include_assignees:
        assignees = _list_channel_assignees_resource(channel_id, active_only=False)
        channel["assignees"] = assignees
        channel["assignment_stats_24h"] = _list_assignment_stats_24h_resource(channel_id)
        channel["assignee_count"] = len([item for item in assignees if _text(item.get("status")) == "active"])
    else:
        channel["assignee_count"] = len(_list_channel_assignees_resource(channel_id, active_only=True))
    return channel


def _serialize_channel(row: dict[str, Any]) -> dict[str, Any]:
    channel = dict(row)
    channel_type, carrier_type = _channel_type(channel)
    channel["id"] = int(channel.get("id") or 0)
    channel["channel_type"] = channel_type
    channel["carrier_type"] = carrier_type
    channel["channel_code"] = _text(channel.get("channel_code"))
    channel["channel_name"] = _text(channel.get("channel_name"))
    channel["scene_value"] = _text(channel.get("scene_value"))
    channel["historical_scene_values"] = [
        item for item in _json_text_list(channel.get("historical_scene_values")) if item != channel["scene_value"]
    ]
    channel["qr_url"] = _text(channel.get("active_qrcode_asset_url") or channel.get("qr_url"))
    channel["customer_channel"] = _text(channel.get("customer_channel"))
    channel["link_url"] = _text(channel.get("link_url"))
    channel["final_url"] = _text(channel.get("final_url"))
    if carrier_type == "link":
        channel["customer_channel"] = channel["customer_channel"] or _text(channel.get("wca_customer_channel"))
        channel["link_url"] = channel["link_url"] or _text(channel.get("wca_link_url"))
        channel["final_url"] = channel["final_url"] or _text(channel.get("wca_final_url"))
    channel["share_url"] = channel.get("share_url") or channel["final_url"] or channel["link_url"]
    channel["copy_text"] = channel.get("copy_text") or channel["share_url"] or channel["qr_url"]
    channel["welcome_message"] = _text(channel.get("welcome_message"))
    channel["welcome_image_library_ids"] = _json_list(channel.get("welcome_image_library_ids"))
    channel["welcome_miniprogram_library_ids"] = _json_list(channel.get("welcome_miniprogram_library_ids"))
    channel["welcome_attachment_library_ids"] = _json_list(channel.get("welcome_attachment_library_ids"))
    channel["welcome_group_invite_library_ids"] = _json_list(channel.get("welcome_group_invite_library_ids"))
    channel["welcome_attachment_count"] = len(channel["welcome_image_library_ids"]) + len(channel["welcome_miniprogram_library_ids"]) + len(channel["welcome_attachment_library_ids"]) + len(channel["welcome_group_invite_library_ids"])
    channel["welcome_message_configured"] = bool(channel["welcome_message"])
    channel["auto_accept_friend"] = bool(channel.get("auto_accept_friend", False))
    channel["entry_tag_id"] = _text(channel.get("entry_tag_id"))
    channel["entry_tag_name"] = _text(channel.get("entry_tag_name"))
    channel["entry_tag_group_name"] = _text(channel.get("entry_tag_group_name"))
    channel["entry_tag_configured"] = bool(channel["entry_tag_id"] or channel["entry_tag_name"])
    channel["status"] = _text(channel.get("status")) or "active"
    channel["owner_staff_id"] = _text(channel.get("owner_staff_id"))
    channel["assignment_mode"] = _assignment_mode(channel.get("assignment_mode"))
    channel["assignment_strategy"] = _assignment_strategy(channel.get("assignment_strategy"))
    channel["overflow_policy"] = _text(channel.get("overflow_policy")) or "least_loaded"
    channel["assignment_config_json"] = _json_dict(channel.get("assignment_config_json"))
    channel["assignees"] = list(channel.get("assignees") or [])
    channel["assignment_stats_24h"] = list(channel.get("assignment_stats_24h") or [])
    channel["assignee_count"] = int(channel.get("assignee_count") or 0)
    channel["channel_contact_count"] = int(channel.get("channel_contact_count") or 0)
    channel["latest_channel_entered_at"] = _iso(channel.get("latest_channel_entered_at"))
    channel["qr_download_url"] = f"/api/admin/channels/{channel['id']}/qrcode/download" if carrier_type != "link" and channel["id"] else ""
    channel["qrcode_status"] = _text(channel.get("qrcode_status")) or ("legacy_untracked" if channel["qr_url"] and channel["scene_value"] else "not_generated")
    channel["qrcode_asset_id"] = int(channel.get("qrcode_asset_id") or channel.get("active_qrcode_asset_id") or 0)
    channel["created_at"] = _iso(channel.get("created_at"))
    channel["updated_at"] = _iso(channel.get("updated_at"))
    return channel


def _default_channel() -> dict[str, Any]:
    return {
        "channel_type": "qrcode",
        "carrier_type": "qrcode",
        "status": "active",
        "assignment_mode": "single_owner",
        "assignment_strategy": "ratio",
        "overflow_policy": "least_loaded",
        "assignment_config_json": {},
        "assignees": [],
        "assignment_stats_24h": [],
        "assignee_count": 0,
        "welcome_image_library_ids": [],
        "welcome_miniprogram_library_ids": [],
        "welcome_attachment_library_ids": [],
        "welcome_group_invite_library_ids": [],
        "auto_accept_friend": False,
    }


def get_channel_resource(channel_id: int) -> dict[str, Any] | None:
    if not channels_repo.uses_postgres():
        channel = _FIXTURE_CHANNELS.get(int(channel_id))
        return _attach_assignment_payload(_serialize_channel(channel), include_assignees=True) if channel else None
    row = channels_repo.fetch_channel(int(channel_id))
    return _attach_assignment_payload(_serialize_channel(dict(row)), include_assignees=True) if row else None


def _list_channels_from_postgres(
    *,
    limit: int,
    status: str = "",
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    if not channels_repo.uses_postgres():
        channels = [_serialize_channel(item) for item in _FIXTURE_CHANNELS.values()]
        if status:
            channels = [item for item in channels if item.get("status") == status]
        elif not include_archived:
            channels = [item for item in channels if item.get("status") != "archived"]
        return [_attach_assignment_payload(item, include_assignees=False) for item in sorted(channels, key=lambda item: int(item.get("id") or 0), reverse=True)[:limit]]
    rows = channels_repo.list_channels(limit=limit, status=status, include_archived=include_archived)
    return [_attach_assignment_payload(_serialize_channel(row), include_assignees=False) for row in rows]


def _payload_value(payload: dict[str, Any], existing: dict[str, Any], key: str, *, partial: bool) -> Any:
    if partial and key not in payload:
        return existing.get(key)
    return payload.get(key)


def _generated_channel_code() -> str:
    return f"channel_{uuid.uuid4().hex}"


def _channel_write_error_detail(exc: Exception) -> str:
    constraint_name = _text(getattr(getattr(exc, "diag", None), "constraint_name", ""))
    error_text = _text(exc)
    if constraint_name == "automation_channel_channel_code_key" or "automation_channel_channel_code_key" in error_text:
        return "渠道编码已存在，请更换后重试"
    return error_text or "渠道保存失败"


def _coerce_channel_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None, partial: bool = False) -> dict[str, Any]:
    existing = existing or {}
    type_source = {**existing, **payload} if partial else payload
    channel_type, carrier_type = _channel_type(type_source)
    customer_channel = _text(_payload_value(payload, existing, "customer_channel", partial=partial) or _payload_value(payload, existing, "scene_value", partial=partial))
    channel_code = _text(_payload_value(payload, existing, "channel_code", partial=partial)) or _text(existing.get("channel_code")) or _generated_channel_code()
    scene_payload = _text(payload.get("scene_value")) if "scene_value" in payload else ""
    qr_payload = _text(payload.get("qr_url")) if "qr_url" in payload else ""
    if carrier_type != "link" and scene_payload and scene_payload != _text(existing.get("scene_value")):
        raise ValueError("scene_value_is_system_managed")
    if carrier_type != "link" and qr_payload and qr_payload != _text(existing.get("qr_url")):
        raise ValueError("qr_url_is_system_managed")
    scene_value = customer_channel if carrier_type == "link" else _text(existing.get("scene_value"))
    qr_url = _text(existing.get("qr_url")) if carrier_type != "link" else _text(_payload_value(payload, existing, "qr_url", partial=partial))
    final_url = _text(_payload_value(payload, existing, "final_url", partial=partial))
    link_url = _text(_payload_value(payload, existing, "link_url", partial=partial))
    if carrier_type == "link" and link_url and customer_channel and not final_url:
        separator = "&" if "?" in link_url else "?"
        final_url = f"{link_url}{separator}customer_channel={customer_channel}"
    assignment_config_value = _payload_value(payload, existing, "assignment_config_json", partial=partial)
    auto_accept_friend = False
    if carrier_type != "link":
        auto_accept_friend = _bool(
            _payload_value(payload, existing, "auto_accept_friend", partial=partial),
            default=_bool(existing.get("auto_accept_friend")),
        )
    return {
        "channel_type": channel_type,
        "carrier_type": carrier_type,
        "channel_name": _text(_payload_value(payload, existing, "channel_name", partial=partial)) or channel_code or "未命名渠道",
        "channel_code": channel_code,
        "scene_value": scene_value,
        "qr_url": qr_url,
        "status": _text(_payload_value(payload, existing, "status", partial=partial)) or "active",
        "owner_staff_id": _text(_payload_value(payload, existing, "owner_staff_id", partial=partial)),
        "customer_channel": customer_channel if carrier_type == "link" else "",
        "link_url": link_url if carrier_type == "link" else "",
        "final_url": final_url if carrier_type == "link" else "",
        "welcome_message": _text(_payload_value(payload, existing, "welcome_message", partial=partial)),
        "welcome_image_library_ids": _json_list(_payload_value(payload, existing, "welcome_image_library_ids", partial=partial)),
        "welcome_miniprogram_library_ids": _json_list(_payload_value(payload, existing, "welcome_miniprogram_library_ids", partial=partial)),
        "welcome_attachment_library_ids": _json_list(_payload_value(payload, existing, "welcome_attachment_library_ids", partial=partial)),
        "welcome_group_invite_library_ids": _json_list(_payload_value(payload, existing, "welcome_group_invite_library_ids", partial=partial)),
        "auto_accept_friend": auto_accept_friend,
        "entry_tag_id": _text(_payload_value(payload, existing, "entry_tag_id", partial=partial)),
        "entry_tag_name": _text(_payload_value(payload, existing, "entry_tag_name", partial=partial)),
        "entry_tag_group_name": _text(_payload_value(payload, existing, "entry_tag_group_name", partial=partial)),
        "assignment_mode": _assignment_mode(_payload_value(payload, existing, "assignment_mode", partial=partial)),
        "assignment_strategy": _assignment_strategy(_payload_value(payload, existing, "assignment_strategy", partial=partial)),
        "overflow_policy": _text(_payload_value(payload, existing, "overflow_policy", partial=partial)) or "least_loaded",
        "assignment_config_json": _json_dict(assignment_config_value),
    }


def _save_fixture_channel(payload: dict[str, Any], channel_id: int | None = None) -> dict[str, Any]:
    global _NEXT_ID, _FIXTURE_ASSIGNMENT_EVENTS
    existing = _FIXTURE_CHANNELS.get(int(channel_id or 0), {}) if channel_id else {}
    data = _coerce_channel_payload(payload, existing=existing, partial=bool(channel_id))
    _validate_assignment_contract(payload, data)
    if channel_id is None:
        channel_id = _NEXT_ID
        _NEXT_ID += 1
        _FIXTURE_CHANNEL_ASSIGNEES.pop(int(channel_id), None)
        _FIXTURE_ASSIGNMENT_EVENTS = [item for item in _FIXTURE_ASSIGNMENT_EVENTS if int(item.get("channel_id") or 0) != int(channel_id)]
    now = datetime.now(timezone.utc).isoformat()
    channel = {**existing, **data, "id": int(channel_id), "updated_at": now, "created_at": existing.get("created_at") or now}
    _FIXTURE_CHANNELS[int(channel_id)] = channel
    if "assignees" in payload:
        _fixture_save_channel_assignees(
            int(channel_id),
            assignment_mode=data["assignment_mode"],
            assignment_strategy=data["assignment_strategy"],
            overflow_policy=data["overflow_policy"],
            assignees=payload.get("assignees") or [],
        )
    return get_channel_resource(int(channel_id)) or _serialize_channel(channel)


def _save_postgres_channel(payload: dict[str, Any], channel_id: int | None = None) -> dict[str, Any]:
    existing = get_channel_resource(int(channel_id)) if channel_id else None
    if channel_id and not existing:
        raise LookupError("channel_not_found")
    data = _coerce_channel_payload(payload, existing=existing, partial=bool(channel_id))
    _validate_assignment_contract(payload, data)
    if data.get("welcome_group_invite_library_ids"):
        from aicrm_next.media_library.application import EnsureGroupInviteBindingReadyCommand

        provisioner = EnsureGroupInviteBindingReadyCommand()
        for group_invite_id in data["welcome_group_invite_library_ids"]:
            provisioner(str(group_invite_id))
    if not channels_repo.uses_postgres():
        _assert_fixture_channel_write_allowed(detail="channel admin write requires production database")
        return _save_fixture_channel(payload, channel_id)
    owner_changed = bool(channel_id and _text((existing or {}).get("owner_staff_id")) and _text(data.get("owner_staff_id")) and _text((existing or {}).get("owner_staff_id")) != _text(data.get("owner_staff_id")))
    saved_id = channels_repo.save_channel(data, channel_id=channel_id)
    if "assignees" in payload:
        _save_channel_assignees_resource(
            saved_id,
            assignment_mode=data["assignment_mode"],
            assignment_strategy=data["assignment_strategy"],
            overflow_policy=data["overflow_policy"],
            assignees=payload.get("assignees") or [],
        )
    if owner_changed:
        channels_repo.mark_qrcode_asset_stale(saved_id, reason="owner_staff_id_changed")
    return get_channel_resource(saved_id) or {"id": saved_id, **data}


def get_channel_qrcode_status_resource(channel_id: int) -> dict[str, Any]:
    channel = get_channel_resource(int(channel_id))
    if not channel:
        raise LookupError("channel_not_found")
    if channel.get("carrier_type") == "link" or channel.get("channel_type") == "wecom_customer_acquisition":
        return {"channel_id": int(channel_id), "downloadable": False, "reason": "link_channel_does_not_support_qrcode_download", "channel": channel}
    if not channels_repo.uses_postgres():
        raw_channel = _FIXTURE_CHANNELS.get(int(channel_id), {})
        asset = dict(raw_channel.get("_active_qrcode_asset") or {})
        aliases: list[dict[str, Any]] = list(raw_channel.get("_scene_aliases") or [])
        effects: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
    else:
        asset = channels_repo.get_active_qrcode_asset(int(channel_id)) or {}
        aliases = channels_repo.list_channel_scene_aliases(int(channel_id))
        effects = channels_repo.list_channel_entry_effect_logs(channel_id=int(channel_id), limit=10)
        events = channels_repo.list_recent_events(_text(channel.get("scene_value")), limit=10) if _text(channel.get("scene_value")) else []
    reason = "downloadable"
    downloadable = True
    if not asset:
        downloadable = False
        reason = "qrcode_not_generated"
    elif int(asset.get("channel_id") or 0) != int(channel_id):
        downloadable = False
        reason = "qrcode_asset_channel_mismatch"
    elif _text(asset.get("status")) != "active":
        downloadable = False
        reason = "qrcode_asset_not_downloadable"
    elif _text(asset.get("scene_value")) != _text(channel.get("scene_value")) or _text(asset.get("qr_url")) != _text(channel.get("qr_url")):
        downloadable = False
        reason = "qrcode_asset_mismatch"
    elif not _text(asset.get("qr_url")).startswith(("http://", "https://")):
        downloadable = False
        reason = "qrcode_asset_missing_url"
    return {
        "channel_id": int(channel_id),
        "channel_name": _text(channel.get("channel_name")),
        "active_qrcode_asset": asset,
        "channel_cached_scene": _text(channel.get("scene_value")),
        "channel_cached_qr_url": _text(channel.get("qr_url")),
        "consistency_status": "ok" if downloadable else reason,
        "downloadable": downloadable,
        "reason": reason,
        "aliases": aliases,
        "recent_callback_states": events,
        "recent_effect_logs": effects,
    }


def list_channel_owner_candidates() -> list[dict[str, Any]]:
    # Compatibility wrapper for older channel-code callers. The page-level picker
    # now calls /api/admin/common/operation-members directly.
    payload = search_operation_members(scope="channel_code", page_size=100)
    return [
        {
            "owner_staff_id": item["user_id"],
            "display_name": item["display_name"] or item["user_id"],
            "position": _text((item.get("extra") or {}).get("position") or (item.get("extra") or {}).get("role")),
            "source": item.get("source") or "",
        }
        for item in payload.get("items", [])
    ]


def default_channel_form_payload() -> dict[str, Any]:
    return _default_channel()


_WE_COM_LINK_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
}


def reset_wecom_customer_acquisition_link_fixture_state() -> None:
    global _FIXTURE_WE_COM_LINKS, _NEXT_WE_COM_LINK_ID
    _NEXT_WE_COM_LINK_ID = 1
    now = datetime.now(timezone.utc).isoformat()
    _FIXTURE_WE_COM_LINKS = [
        {
            "id": 1,
            "link_id": "next_fixture_link",
            "link_name": "Next Fixture",
            "name": "Next Fixture",
            "description": "safe-mode local fixture",
            "link_url": "https://work.weixin.qq.com/ca/next-fixture",
            "customer_channel": "wca_next_fixture",
            "final_url": "https://work.weixin.qq.com/ca/next-fixture?customer_channel=wca_next_fixture",
            "status": "active",
            "adapter_mode": "real_blocked",
            "wecom_api_called": False,
            "real_external_call_executed": False,
            "created_at": now,
            "updated_at": now,
        }
    ]
    _NEXT_WE_COM_LINK_ID = 2


def _wecom_link_common_payload(source_status: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": source_status,
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
    }


def _wecom_link_json(payload: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=_WE_COM_LINK_HEADERS)


def _wecom_link_options(source_status: str) -> JSONResponse:
    payload = _wecom_link_common_payload(source_status)
    payload.update({"allowed": True})
    return _wecom_link_json(payload)


def _wecom_customer_channel(*, link_id: str, name: str) -> str:
    seed = _text(link_id) or _text(name) or uuid.uuid4().hex
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in seed).strip("_")
    return f"wca_{normalized[:48] or uuid.uuid4().hex[:12]}"


def _wecom_final_url(link_url: str, customer_channel: str) -> str:
    base = _text(link_url) or "https://work.weixin.qq.com/ca/next-local"
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["customer_channel"] = customer_channel
    return urlunsplit(
        (
            parsed.scheme or "https",
            parsed.netloc or "work.weixin.qq.com",
            parsed.path or "/ca/next-local",
            urlencode(query),
            parsed.fragment,
        )
    )


async def _wecom_link_payload(request: Request) -> dict[str, Any]:
    if request.method.upper() == "GET":
        return dict(request.query_params)
    if "application/json" in _text(request.headers.get("content-type")).lower():
        body = await request.json()
        return dict(body or {}) if isinstance(body, dict) else {}
    form = await request.form()
    return dict(form)


def _wecom_link_view(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _find_wecom_link(link_id: str) -> dict[str, Any] | None:
    normalized = _text(link_id)
    for row in _FIXTURE_WE_COM_LINKS:
        if str(row.get("id")) == normalized or _text(row.get("link_id")) == normalized:
            return row
    return None


@router.api_route("/api/admin/wecom-customer-acquisition-links", methods=["GET", "POST", "OPTIONS"])
async def wecom_customer_acquisition_links(request: Request) -> JSONResponse:
    global _NEXT_WE_COM_LINK_ID
    source_status = "next_wecom_customer_acquisition_links" if request.method.upper() == "GET" else "next_command"
    if request.method.upper() == "OPTIONS":
        return _wecom_link_options(source_status)
    if request.method.upper() == "GET":
        status = _text(request.query_params.get("status"))
        links = [_wecom_link_view(row) for row in _FIXTURE_WE_COM_LINKS if not status or _text(row.get("status")) == status]
        payload = _wecom_link_common_payload(source_status)
        payload.update(
            {
                "items": links,
                "links": links,
                "count": len(links),
                "adapter_mode": "real_blocked",
                "wecom_api_called": False,
                "degraded": False,
                "warnings": [],
            }
        )
        return _wecom_link_json(payload)

    _assert_fixture_channel_write_allowed(detail="wecom customer acquisition link write requires production repository")
    body = await _wecom_link_payload(request)
    now = datetime.now(timezone.utc).isoformat()
    link_id = _text(body.get("link_id")) or f"next_link_{_NEXT_WE_COM_LINK_ID}"
    link_name = _text(body.get("link_name")) or _text(body.get("name")) or link_id
    link_url = _text(body.get("link_url")) or "https://work.weixin.qq.com/ca/next-local"
    customer_channel = _wecom_customer_channel(link_id=link_id, name=link_name)
    row = {
        "id": _NEXT_WE_COM_LINK_ID,
        "link_id": link_id,
        "link_name": link_name,
        "name": link_name,
        "description": _text(body.get("description")),
        "link_url": link_url,
        "customer_channel": customer_channel,
        "final_url": _wecom_final_url(link_url, customer_channel),
        "status": "active",
        "adapter_mode": "real_blocked",
        "wecom_api_called": False,
        "real_external_call_executed": False,
        "created_at": now,
        "updated_at": now,
    }
    _FIXTURE_WE_COM_LINKS.insert(0, row)
    _NEXT_WE_COM_LINK_ID += 1
    payload = _wecom_link_common_payload(source_status)
    payload.update(
        {
            "command_id": f"cmd_wecom_ca_{uuid.uuid4().hex}",
            "command_name": "wecom_customer_acquisition_link.create.plan",
            "idempotency_key": _text(request.headers.get("Idempotency-Key")),
            "link": _wecom_link_view(row),
            "adapter_mode": "real_blocked",
            "wecom_api_called": False,
            "side_effect_plan": {
                "kind": "wecom_customer_acquisition_link_create",
                "status": "blocked",
                "reason": "real_wecom_api_blocked_by_default",
            },
        }
    )
    return _wecom_link_json(payload)


@router.api_route(
    "/api/admin/wecom-customer-acquisition-links/{link_id}",
    methods=["GET", "PATCH", "DELETE", "OPTIONS"],
)
async def wecom_customer_acquisition_link_detail(request: Request, link_id: str) -> JSONResponse:
    if request.method.upper() == "OPTIONS":
        return _wecom_link_options("next_wecom_customer_acquisition_links")
    row = _find_wecom_link(link_id)
    if not row:
        payload = _wecom_link_common_payload("next_wecom_customer_acquisition_links")
        payload.update({"ok": False, "error_code": "wecom_customer_acquisition_link_not_found", "link": {}})
        return _wecom_link_json(payload, status_code=404)
    if request.method.upper() == "GET":
        payload = _wecom_link_common_payload("next_wecom_customer_acquisition_links")
        payload.update({"link": _wecom_link_view(row), "adapter_mode": "real_blocked", "wecom_api_called": False})
        return _wecom_link_json(payload)
    _assert_fixture_channel_write_allowed(detail="wecom customer acquisition link write requires production repository")
    if request.method.upper() == "DELETE":
        row["status"] = "disabled"
    elif request.method.upper() == "PATCH":
        body = await _wecom_link_payload(request)
        for key in ("link_name", "name", "description"):
            if key in body:
                row[key] = _text(body.get(key))
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _wecom_link_common_payload("next_command")
    payload.update(
        {
            "command_id": f"cmd_wecom_ca_{uuid.uuid4().hex}",
            "link": _wecom_link_view(row),
            "adapter_mode": "real_blocked",
            "wecom_api_called": False,
            "side_effect_plan": {"kind": "wecom_customer_acquisition_link_mutation", "status": "blocked"},
        }
    )
    return _wecom_link_json(payload)


@router.api_route(
    "/api/admin/wecom-customer-acquisition-links/{link_id}/{action}",
    methods=["POST", "OPTIONS"],
)
async def wecom_customer_acquisition_link_action(request: Request, link_id: str, action: str) -> JSONResponse:
    if request.method.upper() == "OPTIONS":
        return _wecom_link_options("next_command")
    normalized_action = _text(action)
    row = _find_wecom_link(link_id)
    if not row:
        payload = _wecom_link_common_payload("next_command")
        payload.update({"ok": False, "error_code": "wecom_customer_acquisition_link_not_found"})
        return _wecom_link_json(payload, status_code=404)
    if normalized_action not in {"enable", "disable", "sync"}:
        payload = _wecom_link_common_payload("next_command")
        payload.update(
            {
                "ok": False,
                "error_code": "wecom_customer_acquisition_action_deprecated",
                "replacement": "/api/admin/wecom-customer-acquisition-links/{link_id}",
            }
        )
        return _wecom_link_json(payload, status_code=410)
    _assert_fixture_channel_write_allowed(detail="wecom customer acquisition link write requires production repository")
    if normalized_action == "enable":
        row["status"] = "active"
    elif normalized_action == "disable":
        row["status"] = "disabled"
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = _wecom_link_common_payload("next_command")
    payload.update(
        {
            "command_id": f"cmd_wecom_ca_{uuid.uuid4().hex}",
            "command_name": f"wecom_customer_acquisition_link.{normalized_action}.plan",
            "link": _wecom_link_view(row),
            "adapter_mode": "real_blocked",
            "wecom_api_called": False,
            "sync_executed": False,
            "side_effect_plan": {
                "kind": f"wecom_customer_acquisition_link_{normalized_action}",
                "status": "blocked",
                "reason": "real_wecom_api_blocked_by_default",
            },
        }
    )
    return _wecom_link_json(payload)


@router.get("/api/admin/channels")
def list_channels(
    limit: int = Query(100),
    status: str = "",
    include_archived: bool = False,
) -> dict[str, Any]:
    return {
        "ok": True,
        "channels": _list_channels_from_postgres(
            limit=max(1, min(int(limit or 100), 500)),
            status=_text(status),
            include_archived=include_archived,
        ),
        "reason": "channels_listed",
        "source": "ai_crm_next",
    }


@router.post("/api/admin/channels", status_code=201)
def create_channel(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        channel = _save_postgres_channel(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_channel_write_error_detail(exc)) from exc
    return {"ok": True, "channel": channel, "reason": "channel_created", "source": "ai_crm_next"}


@router.get("/api/admin/channels/{channel_id:int}")
def get_channel(channel_id: int) -> dict[str, Any]:
    channel = get_channel_resource(int(channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="channel_not_found")
    return {"ok": True, "channel": channel, "reason": "channel_loaded", "source": "ai_crm_next"}


CHANNEL_STATUS_ONLY_PATCH_VALUES = {"active", "inactive", "archived"}


@router.patch("/api/admin/channels/{channel_id:int}")
def update_channel(channel_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload.keys()) == {"status"} and _text(payload.get("status")) not in CHANNEL_STATUS_ONLY_PATCH_VALUES:
        raise HTTPException(status_code=400, detail="invalid_channel_status")
    try:
        channel = _save_postgres_channel(payload, int(channel_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_channel_write_error_detail(exc)) from exc
    return {"ok": True, "channel": channel, "reason": "channel_updated", "source": "ai_crm_next"}


@router.get("/api/admin/channels/{channel_id:int}/assignees")
def get_channel_assignees(channel_id: int) -> dict[str, Any]:
    channel = get_channel_resource(int(channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="channel_not_found")
    return {
        "ok": True,
        "channel_id": int(channel_id),
        "assignment_mode": channel.get("assignment_mode") or "single_owner",
        "assignment_strategy": channel.get("assignment_strategy") or "ratio",
        "overflow_policy": channel.get("overflow_policy") or "least_loaded",
        "assignees": channel.get("assignees") or [],
        "stats_24h": channel.get("assignment_stats_24h") or [],
        "reason": "channel_assignees_loaded",
        "source": "ai_crm_next",
    }


@router.put("/api/admin/channels/{channel_id:int}/assignees")
def put_channel_assignees(channel_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not get_channel_resource(int(channel_id)):
        raise HTTPException(status_code=404, detail="channel_not_found")
    try:
        result = _save_channel_assignees_resource(
            int(channel_id),
            assignment_mode=_text(payload.get("assignment_mode")) or "multi_staff",
            assignment_strategy=_text(payload.get("assignment_strategy")) or "ratio",
            overflow_policy=_text(payload.get("overflow_policy")) or "least_loaded",
            assignees=payload.get("assignees") or [],
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "channel_id": int(channel_id), "reason": str(exc), "source": "ai_crm_next"},
        )
    refreshed = get_channel_resource(int(channel_id)) or {}
    return {
        "ok": True,
        "channel_id": int(channel_id),
        "assignment_mode": result.get("assignment_mode") or refreshed.get("assignment_mode"),
        "assignment_strategy": result.get("assignment_strategy") or refreshed.get("assignment_strategy"),
        "overflow_policy": result.get("overflow_policy") or refreshed.get("overflow_policy"),
        "assignees": refreshed.get("assignees") or result.get("assignees") or [],
        "stats_24h": refreshed.get("assignment_stats_24h") or [],
        "reason": "channel_assignees_saved",
        "source": "ai_crm_next",
    }


@router.get("/api/admin/channels/{channel_id:int}/assignment-events")
def get_channel_assignment_events(channel_id: int, limit: int = Query(50)) -> dict[str, Any]:
    if not get_channel_resource(int(channel_id)):
        raise HTTPException(status_code=404, detail="channel_not_found")
    return {
        "ok": True,
        "channel_id": int(channel_id),
        "events": _list_assignment_events_resource(int(channel_id), limit=max(1, min(int(limit or 50), 200))),
        "reason": "channel_assignment_events_listed",
        "source": "ai_crm_next",
    }


@router.post("/api/admin/channels/{channel_id:int}/assignment/preview")
def preview_channel_assignment(channel_id: int, payload: dict[str, Any] | None = None) -> JSONResponse:
    payload = payload or {}
    try:
        result = _choose_channel_assignee_resource(
            int(channel_id),
            external_contact_id=_text(payload.get("external_contact_id")),
            wecom_user_id=_text(payload.get("wecom_user_id")),
            write_event=bool(payload.get("write_event")),
            source_payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "channel_id": int(channel_id), "reason": str(exc), "source": "ai_crm_next"},
        )
    status_code = 409 if result.get("reason") == "all_assignees_reached_24h_cap" else 200
    return JSONResponse(status_code=status_code, content=result)


@router.get("/api/admin/channels/{channel_id:int}/contacts")
def list_channel_contacts(channel_id: int, limit: int = Query(100)) -> dict[str, Any]:
    if not channels_repo.uses_postgres():
        return {"ok": True, "contacts": [], "reason": "channel_contacts_listed", "source": "ai_crm_next"}
    contacts = [
        {**row, "last_channel_entered_at": _iso(row.get("last_channel_entered_at"))}
        for row in channels_repo.list_channel_contacts(int(channel_id), limit=max(1, min(int(limit or 100), 500)))
    ]
    return {"ok": True, "contacts": contacts, "reason": "channel_contacts_listed", "source": "ai_crm_next"}


@router.get("/api/admin/channels/{channel_id:int}/share-link")
def get_channel_share_link(channel_id: int) -> dict[str, Any]:
    channel = get_channel_resource(int(channel_id))
    if not channel:
        raise HTTPException(status_code=404, detail="channel_not_found")
    if channel.get("carrier_type") != "link" and channel.get("channel_type") != "wecom_customer_acquisition":
        raise HTTPException(status_code=400, detail="channel_is_not_link_carrier")
    share_url = _text(channel.get("share_url") or channel.get("copy_text") or channel.get("final_url") or channel.get("link_url"))
    return {"ok": True, "share_url": share_url, "copy_text": share_url, "reason": "share_link_loaded", "source": "ai_crm_next"}


@router.get("/api/admin/channels/{channel_id:int}/qrcode/download")
def download_channel_qrcode(channel_id: int):
    try:
        status = get_channel_qrcode_status_resource(int(channel_id))
    except LookupError:
        raise HTTPException(status_code=404, detail="channel_not_found")
    if status.get("reason") == "link_channel_does_not_support_qrcode_download":
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "link channel does not support qrcode download", "reason": "link_channel_does_not_support_qrcode_download"},
        )
    if not status.get("downloadable"):
        return JSONResponse(
            status_code=409,
            content={"ok": False, "reason": status.get("reason"), "qrcode_status": status},
            headers={"Cache-Control": "no-store"},
        )
    asset = status.get("active_qrcode_asset") or {}
    qr_url = _text(asset.get("qr_url"))
    if qr_url.startswith(("http://", "https://")):
        return RedirectResponse(
            qr_url,
            status_code=302,
            headers={
                "Cache-Control": "no-store",
                "X-AICRM-Channel-ID": str(int(channel_id)),
                "X-AICRM-QR-Scene": _text(asset.get("scene_value")),
                "X-AICRM-QR-Asset-ID": str(int(asset.get("id") or 0)),
            },
        )
    raise HTTPException(status_code=404, detail="qrcode_not_ready")


@router.get("/api/admin/channels/{channel_id:int}/qrcode/status")
def get_channel_qrcode_status(channel_id: int) -> dict[str, Any]:
    try:
        return {"ok": True, **get_channel_qrcode_status_resource(int(channel_id)), "source": "ai_crm_next"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/admin/channel-welcome-materials")
def list_channel_welcome_materials(type: str = "all", keyword: str = "", q: str = "") -> dict[str, Any]:
    material_type = _text(type).lower() or "all"
    keyword_text = (_text(keyword) or _text(q)).lower()
    if not channels_repo.uses_postgres():
        return {"ok": True, "materials": [], "reason": "channel_welcome_materials_listed", "source": "ai_crm_next"}
    items = channels_repo.list_channel_welcome_materials(material_type=material_type, keyword_text=keyword_text)
    return {"ok": True, "materials": items, "reason": "channel_welcome_materials_listed", "source": "ai_crm_next"}

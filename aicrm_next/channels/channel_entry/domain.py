from __future__ import annotations

from typing import Any

ACTIVE_CHANNEL_STATUSES = {"active", "configured"}
ENTRY_CHANGE_TYPES = {"add_external_contact", "add_half_external_contact", "edit_external_contact"}


def text(value: Any) -> str:
    return str(value or "").strip()


def extract_scene(payload: dict[str, Any]) -> str:
    for key in ("State", "state", "scene_value", "scene", "channel_code"):
        value = text(payload.get(key))
        if value:
            return value
    return ""


def extract_welcome_code(payload: dict[str, Any]) -> str:
    for key in ("WelcomeCode", "welcome_code", "welcomeCode"):
        value = text(payload.get(key))
        if value:
            return value
    return ""


def extract_corp_id(payload: dict[str, Any]) -> str:
    for key in ("CorpId", "corp_id", "ToUserName"):
        value = text(payload.get(key))
        if value:
            return value
    return ""


def channel_enabled(channel: dict[str, Any]) -> bool:
    return text(channel.get("status")) in ACTIVE_CHANNEL_STATUSES


def scene_match(match_type: str, scene: str, alias: dict[str, Any] | None = None) -> dict[str, Any]:
    alias = alias or {}
    return {
        "match_type": match_type,
        "matched_scene": text(scene),
        "channel_id": int(alias.get("channel_id") or alias.get("id") or 0) or None,
        "alias_id": int(alias.get("scene_alias_id") or alias.get("id") or 0) or None,
        "alias_status": text(alias.get("scene_alias_status") or alias.get("status")),
        "alias_source": text(alias.get("scene_alias_source") or alias.get("source")),
    }


def channel_payload(channel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(channel.get("id") or 0),
        "channel_code": text(channel.get("channel_code")),
        "channel_name": text(channel.get("channel_name")),
        "scene_value": text(channel.get("scene_value")),
        "status": text(channel.get("status")),
        "owner_staff_id": text(channel.get("owner_staff_id")),
    }


def effect_status_for_duplicate(existing: dict[str, Any] | None) -> bool:
    return bool(existing and text(existing.get("status")) == "success")


from __future__ import annotations

from typing import Any

from .domain import completion_target_projection, normalize_completion_target


def normalize_completion_target_for_storage(
    payload: dict[str, Any],
    *,
    legacy_url_key: str,
    legacy_enabled_key: str | None = None,
) -> dict[str, Any]:
    legacy_enabled = payload.get(legacy_enabled_key) if legacy_enabled_key else None
    raw_target = payload.get("completion_target")
    if raw_target is None:
        raw_target = payload.get("completion_target_json")
    return normalize_completion_target(
        raw_target,
        legacy_h5_url=payload.get(legacy_url_key),
        legacy_enabled=legacy_enabled,
    )


def completion_target_response(
    item: dict[str, Any],
    *,
    legacy_url_key: str,
    legacy_enabled_key: str | None = None,
    json_key: str = "completion_target_json",
) -> dict[str, Any]:
    legacy_enabled = item.get(legacy_enabled_key) if legacy_enabled_key else None
    return completion_target_projection(
        item.get(json_key) if item.get(json_key) is not None else item.get("completion_target"),
        legacy_h5_url=item.get(legacy_url_key),
        legacy_enabled=legacy_enabled,
    )

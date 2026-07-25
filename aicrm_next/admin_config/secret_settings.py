from __future__ import annotations

from typing import Any

from aicrm_next.shared.secret_store import FileSecretStore, SecretStoreError, is_secret_reference, parse_secret_reference
from aicrm_next.shared.runtime_settings import environment_fallback

from .settings import SENSITIVE_KEYS, mask_value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _version(reference: str) -> str:
    if not is_secret_reference(reference):
        return ""
    try:
        return parse_secret_reference(reference).version
    except SecretStoreError:
        return ""


def setting_details(repo: Any, key: str) -> tuple[str, str, str, str]:
    row = repo.get_app_setting(key)
    if row is not None:
        value = _text(row.get("value"))
        return value, "app_settings", _version(value), _text(row.get("updated_at"))
    value = _text(environment_fallback(key))
    return value, "config", _version(value), ""


def current_setting_values(read_service: Any, schema: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for group in schema.values():
        for field_key in group["fields"]:
            value, _source = read_service._setting_value_source(field_key)
            if value:
                values[field_key] = value
    return values


def stored_value_matches(key: str, stored_value: Any, candidate: str) -> bool:
    stored = _text(stored_value)
    if key not in SENSITIVE_KEYS:
        return stored == candidate
    if not is_secret_reference(stored):
        return False
    try:
        return FileSecretStore.from_environment().matches(stored, candidate)
    except SecretStoreError:
        return False


def public_changed_row(key: str, row: dict[str, Any]) -> dict[str, Any]:
    if key not in SENSITIVE_KEYS:
        return row
    reference = _text(row.get("value"))
    return {
        "key": key,
        "configured": bool(reference),
        "display_value": mask_value(key, "configured") if reference else "",
        "version": _version(reference),
        "updated_at": _text(row.get("updated_at")),
    }

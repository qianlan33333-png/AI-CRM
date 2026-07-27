from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]

LIST_KEYS = ["ok", "items", "total", "limit", "offset"]
IMAGE_KEYS = ["id", "name", "file_name", "content_type", "file_size", "width", "height", "data_url", "tags", "created_at", "updated_at"]
ATTACHMENT_KEYS = ["id", "name", "file_name", "mime_type", "file_size", "data_base64", "tags", "enabled", "created_at", "updated_at"]
MINIPROGRAM_KEYS = ["id", "title", "appid", "page_path", "thumb_image_id", "description", "tags", "enabled", "created_at", "updated_at"]


@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path: str
    expected_status: int = 200
    body: Json | None = None


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "image_library.default": EndpointSpec("GET", "/api/admin/image-library"),
    "attachment_library.default": EndpointSpec("GET", "/api/admin/attachment-library"),
    "miniprogram_library.default": EndpointSpec("GET", "/api/admin/miniprogram-library"),
}
DEFAULT_SAFE_ENDPOINTS = list(ENDPOINT_SPECS)


def compare_required_keys(payload: Json, required_keys: list[str], *, location: str = "$") -> list[Json]:
    return [{"rule": "required_key", "location": location, "key": key, "severity": "fail"} for key in required_keys if key not in payload]


def compare_item_required_keys(items: Any, required_keys: list[str], *, location: str) -> list[Json]:
    if not isinstance(items, list):
        return [{"rule": "type_family", "location": location, "expected": "list", "actual": type(items).__name__, "severity": "fail"}]
    if not items:
        return []
    if not isinstance(items[0], dict):
        return [{"rule": "type_family", "location": f"{location}[0]", "expected": "object", "actual": type(items[0]).__name__, "severity": "fail"}]
    return compare_required_keys(items[0], required_keys, location=f"{location}[0]")


def validate_payload(endpoint_name: str, payload: Json) -> list[Json]:
    if endpoint_name == "image_library.default":
        return compare_required_keys(payload, LIST_KEYS) + compare_item_required_keys(payload.get("items"), IMAGE_KEYS, location="$.items")
    if endpoint_name == "attachment_library.default":
        return compare_required_keys(payload, LIST_KEYS) + compare_item_required_keys(payload.get("items"), ATTACHMENT_KEYS, location="$.items")
    if endpoint_name == "miniprogram_library.default":
        return compare_required_keys(payload, LIST_KEYS) + compare_item_required_keys(payload.get("items"), MINIPROGRAM_KEYS, location="$.items")
    return [{"rule": "unknown_endpoint_spec", "endpoint": endpoint_name, "severity": "fail"}]


def compare_endpoint_payloads(endpoint_name: str, old_payload: Json, next_payload: Json) -> list[Json]:
    return validate_payload(endpoint_name, old_payload) + validate_payload(endpoint_name, next_payload) + compare_type_family(old_payload, next_payload)


def compare_status_code(old_status: int, next_status: int, *, expected_status: int = 200) -> list[Json]:
    issues = []
    if old_status != expected_status:
        issues.append({"rule": "old_status_code", "expected": expected_status, "actual": old_status, "severity": "fail"})
    if next_status != expected_status:
        issues.append({"rule": "next_status_code", "expected": expected_status, "actual": next_status, "severity": "fail"})
    return issues


def compare_type_family(old_payload: Any, next_payload: Any, *, location: str = "$") -> list[Json]:
    old_family = _type_family(old_payload)
    next_family = _type_family(next_payload)
    if old_family != next_family:
        return [{"rule": "type_family", "location": location, "expected": old_family, "actual": next_family, "severity": "fail"}]
    issues: list[Json] = []
    if isinstance(old_payload, dict) and isinstance(next_payload, dict):
        for key in old_payload.keys() & next_payload.keys():
            issues.extend(compare_type_family(old_payload[key], next_payload[key], location=f"{location}.{key}"))
    elif isinstance(old_payload, list) and old_payload and next_payload:
        issues.extend(compare_type_family(old_payload[0], next_payload[0], location=f"{location}[0]"))
    return issues


def _type_family(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str) or value is None:
        return "string_or_null"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__

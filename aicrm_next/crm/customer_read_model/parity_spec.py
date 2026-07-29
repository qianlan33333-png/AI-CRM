from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]

CUSTOMER_LIST_TOP_LEVEL_KEYS = ["ok", "customers", "items", "count", "total", "limit", "offset", "filters"]
CUSTOMER_LIST_ITEM_KEYS = [
    "external_userid",
    "customer_name",
    "owner_userid",
    "owner_display_name",
    "mobile",
    "is_bound",
    "binding_status",
    "tags",
    "class_user_status",
    "last_message_at",
    "last_touch_at",
    "updated_at",
]
CUSTOMER_DETAIL_TOP_LEVEL_KEYS = ["ok", "customer"]
CUSTOMER_DETAIL_KEYS = [
    "external_userid",
    "customer_name",
    "owner_userid",
    "owner_display_name",
    "remark",
    "description",
    "mobile",
    "is_bound",
    "binding_status",
    "follow_user_userids",
    "tags",
    "class_user_status",
    "last_message_at",
    "last_touch_at",
    "updated_at",
    "binding",
    "identity",
    "follow_users",
    "marketing_summary",
    "marketing_profile",
    "contact",
    "sidebar_context",
]
TIMELINE_TOP_LEVEL_KEYS = ["ok", "timeline"]
TIMELINE_KEYS = ["external_userid", "items", "count", "limit", "offset", "filters", "total"]
TIMELINE_ITEM_KEYS = ["event_id", "event_type", "event_time", "title", "summary", "source_table", "source_id", "metadata"]
RECENT_MESSAGES_TOP_LEVEL_KEYS = ["ok", "messages"]
RECENT_MESSAGE_ITEM_KEYS = ["msgid", "msgtype", "content", "send_time", "external_userid"]


@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path: str
    expected_status: int = 200
    body: Json | None = None


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "customers.default": EndpointSpec("GET", "/api/customers"),
    "customers.owner_filter": EndpointSpec("GET", "/api/customers?owner_userid=ZhaoYanFang"),
    "customer_detail.default": EndpointSpec("GET", "/api/customers/wx_ext_001"),
    "customer_timeline.default": EndpointSpec("GET", "/api/customers/wx_ext_001/timeline"),
    "recent_messages.default": EndpointSpec("GET", "/api/messages/wx_ext_001/recent"),
}


def compare_required_keys(payload: Json, required_keys: list[str], *, location: str = "$") -> list[Json]:
    return [
        {"rule": "required_key", "location": location, "key": key, "severity": "fail"}
        for key in required_keys
        if key not in payload
    ]


def compare_item_required_keys(items: Any, required_keys: list[str], *, location: str) -> list[Json]:
    if not isinstance(items, list):
        return [{"rule": "type_family", "location": location, "expected": "list", "actual": type(items).__name__, "severity": "fail"}]
    if not items:
        return []
    if not isinstance(items[0], dict):
        return [{"rule": "type_family", "location": f"{location}[0]", "expected": "object", "actual": type(items[0]).__name__, "severity": "fail"}]
    return compare_required_keys(items[0], required_keys, location=f"{location}[0]")


def compare_type_family(old_payload: Any, next_payload: Any, *, location: str = "$") -> list[Json]:
    old_family = _type_family(old_payload)
    next_family = _type_family(next_payload)
    if old_family != next_family:
        return [
            {
                "rule": "type_family",
                "location": location,
                "expected": old_family,
                "actual": next_family,
                "severity": "fail",
            }
        ]
    issues: list[Json] = []
    if isinstance(old_payload, dict) and isinstance(next_payload, dict):
        for key in old_payload.keys() & next_payload.keys():
            issues.extend(compare_type_family(old_payload[key], next_payload[key], location=f"{location}.{key}"))
    elif isinstance(old_payload, list) and old_payload and next_payload:
        issues.extend(compare_type_family(old_payload[0], next_payload[0], location=f"{location}[0]"))
    return issues


def validate_payload(endpoint_name: str, payload: Json) -> list[Json]:
    if endpoint_name.startswith("customers."):
        issues = compare_required_keys(payload, CUSTOMER_LIST_TOP_LEVEL_KEYS)
        issues.extend(compare_item_required_keys(payload.get("items"), CUSTOMER_LIST_ITEM_KEYS, location="$.items"))
        return issues
    if endpoint_name == "customer_detail.default":
        issues = compare_required_keys(payload, CUSTOMER_DETAIL_TOP_LEVEL_KEYS)
        if isinstance(payload.get("customer"), dict):
            issues.extend(compare_required_keys(payload["customer"], CUSTOMER_DETAIL_KEYS, location="$.customer"))
        else:
            issues.append({"rule": "type_family", "location": "$.customer", "expected": "object", "actual": type(payload.get("customer")).__name__, "severity": "fail"})
        return issues
    if endpoint_name == "customer_timeline.default":
        issues = compare_required_keys(payload, TIMELINE_TOP_LEVEL_KEYS)
        timeline = payload.get("timeline")
        if isinstance(timeline, dict):
            issues.extend(compare_required_keys(timeline, TIMELINE_KEYS, location="$.timeline"))
            issues.extend(compare_item_required_keys(timeline.get("items"), TIMELINE_ITEM_KEYS, location="$.timeline.items"))
        else:
            issues.append({"rule": "type_family", "location": "$.timeline", "expected": "object", "actual": type(timeline).__name__, "severity": "fail"})
        return issues
    if endpoint_name == "recent_messages.default":
        issues = compare_required_keys(payload, RECENT_MESSAGES_TOP_LEVEL_KEYS)
        issues.extend(compare_item_required_keys(payload.get("messages"), RECENT_MESSAGE_ITEM_KEYS, location="$.messages"))
        return issues
    return [{"rule": "unknown_endpoint_spec", "endpoint": endpoint_name, "severity": "fail"}]


def compare_endpoint_payloads(endpoint_name: str, old_payload: Json, next_payload: Json) -> list[Json]:
    issues = validate_payload(endpoint_name, old_payload)
    issues.extend(validate_payload(endpoint_name, next_payload))
    issues.extend(compare_type_family(old_payload, next_payload))
    return issues


def compare_status_code(old_status: int, next_status: int, *, expected_status: int = 200) -> list[Json]:
    issues: list[Json] = []
    if old_status != expected_status:
        issues.append({"rule": "old_status_code", "expected": expected_status, "actual": old_status, "severity": "fail"})
    if next_status != expected_status:
        issues.append({"rule": "next_status_code", "expected": expected_status, "actual": next_status, "severity": "fail"})
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

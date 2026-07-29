from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aicrm_next.platform.shared.typing import JsonDict

OVERVIEW_CARD_LABELS = [
    "引流品总数",
    "已加微",
    "未加微",
    "已绑手机号",
    "未绑手机号",
    "黄小璨已激活",
    "黄小璨未激活",
    "激活待录入",
]

OVERVIEW_REQUIRED_KEYS = ["ok", "cards", "filters", "generated_at"]
LIST_REQUIRED_KEYS = ["ok", "items", "total", "filters", "filter_options", "meta"]
LIST_ITEM_REQUIRED_KEYS = [
    "id",
    "mobile",
    "external_userid",
    "customer_name",
    "owner_userid",
    "owner_display_name",
    "class_term_no",
    "class_term_label",
    "source_type",
    "created_at",
    "updated_at",
    "is_added_wecom",
    "is_wecom_added",
    "is_mobile_bound",
    "activation_bucket",
    "activation_bucket_label",
    "huangxiaocan_activation_state",
    "huangxiaocan_activation_state_label",
    "do_not_disturb",
    "do_not_disturb_reasons",
    "can_open_customer_detail",
    "can_batch_send",
]
DND_REQUIRED_KEYS = ["ok", "target", "do_not_disturb", "do_not_disturb_reasons"]
PREVIEW_REQUIRED_KEYS = [
    "ok",
    "selected_count",
    "eligible_count",
    "skipped_count",
    "skipped_by_reason",
    "skipped_summary",
    "include_do_not_disturb",
    "owner_buckets",
    "sender_buckets",
    "sendable_samples",
    "final_targets",
    "filters",
    "content_preview",
    "image_count",
    "has_body",
]
EXECUTE_REQUIRED_KEYS = [
    "ok",
    "record_id",
    "selected_count",
    "eligible_count",
    "sent_count",
    "skipped_count",
    "skipped_by_reason",
    "skipped_summary",
    "include_do_not_disturb",
    "image_count",
    "execution_backend",
    "external_effect_job_ids",
    "planned_count",
    "queued_count",
    "execution_summary",
    "skip_summary",
    "external_effect_status_supported",
    "wecom_delivery_status_supported",
    "filters",
]
SEND_RECORDS_REQUIRED_KEYS = ["ok", "items", "limit", "offset", "total"]
SEND_RECORD_ITEM_REQUIRED_KEYS = [
    "id",
    "task_type",
    "selected_count",
    "eligible_count",
    "sent_count",
    "skipped_count",
    "skipped_reasons",
    "include_do_not_disturb",
    "content_preview",
    "image_count",
    "sender_userids",
    "filter_snapshot",
    "operator",
    "status",
    "status_label",
    "created_at",
]

ALLOWED_DYNAMIC_KEYS = {
    "generated_at",
    "created_at",
    "updated_at",
    "id",
    "record_id",
    "count",
    "total",
    "value",
}

TYPE_FAMILIES: dict[type, str] = {
    bool: "bool",
    int: "number",
    float: "number",
    str: "string",
    list: "list",
    dict: "object",
    type(None): "null",
}


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    method: str
    path: str
    required_keys: list[str]
    item_required_keys: list[str] | None = None
    expected_status: int = 200
    body: JsonDict | None = None
    safe_by_default: bool = True


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "overview.default": EndpointSpec(
        name="overview.default",
        method="GET",
        path="/api/admin/user-ops/overview",
        required_keys=OVERVIEW_REQUIRED_KEYS,
    ),
    "list.default": EndpointSpec(
        name="list.default",
        method="GET",
        path="/api/admin/user-ops/list",
        required_keys=LIST_REQUIRED_KEYS,
        item_required_keys=LIST_ITEM_REQUIRED_KEYS,
    ),
    "list.wecom_added": EndpointSpec(
        name="list.wecom_added",
        method="GET",
        path="/api/admin/user-ops/list?wecom_status=added",
        required_keys=LIST_REQUIRED_KEYS,
        item_required_keys=LIST_ITEM_REQUIRED_KEYS,
    ),
    "list.not_added": EndpointSpec(
        name="list.not_added",
        method="GET",
        path="/api/admin/user-ops/list?wecom_status=not_added",
        required_keys=LIST_REQUIRED_KEYS,
        item_required_keys=LIST_ITEM_REQUIRED_KEYS,
    ),
    "preview.default": EndpointSpec(
        name="preview.default",
        method="POST",
        path="/api/admin/user-ops/batch-send/preview",
        required_keys=PREVIEW_REQUIRED_KEYS,
        body={"selection_mode": "all_filtered", "content": "parity dry run"},
    ),
    "send_records.default": EndpointSpec(
        name="send_records.default",
        method="GET",
        path="/api/admin/user-ops/send-records",
        required_keys=SEND_RECORDS_REQUIRED_KEYS,
        item_required_keys=SEND_RECORD_ITEM_REQUIRED_KEYS,
    ),
    "execute.default": EndpointSpec(
        name="execute.default",
        method="POST",
        path="/api/admin/user-ops/batch-send/execute",
        required_keys=EXECUTE_REQUIRED_KEYS,
        body={"selection_mode": "manual", "selected_ids": [1], "content": "parity dry run", "confirm": True},
        safe_by_default=False,
    ),
}

DEFAULT_SAFE_ENDPOINTS = [
    "overview.default",
    "list.default",
    "list.wecom_added",
    "list.not_added",
    "preview.default",
    "send_records.default",
]


def type_family(value: Any) -> str:
    return TYPE_FAMILIES.get(type(value), "other")


def compare_required_keys(payload: JsonDict, required_keys: list[str], *, location: str = "$") -> list[JsonDict]:
    return [
        {"rule": "required_key", "location": location, "key": key, "severity": "fail"}
        for key in required_keys
        if key not in payload
    ]


def compare_card_labels(payload: JsonDict, *, location: str = "$.cards") -> list[JsonDict]:
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return [{"rule": "card_labels", "location": location, "message": "cards is not a list", "severity": "fail"}]
    labels = {str(card.get("label") or "") for card in cards if isinstance(card, dict)}
    return [
        {"rule": "card_label", "location": location, "label": label, "severity": "fail"}
        for label in OVERVIEW_CARD_LABELS
        if label not in labels
    ]


def compare_item_required_keys(payload: JsonDict, required_keys: list[str], *, location: str = "$.items") -> list[JsonDict]:
    items = payload.get("items")
    if not isinstance(items, list):
        return [{"rule": "items_type", "location": location, "message": "items is not a list", "severity": "fail"}]
    issues: list[JsonDict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append({"rule": "item_type", "location": f"{location}[{index}]", "severity": "fail"})
            continue
        issues.extend(compare_required_keys(item, required_keys, location=f"{location}[{index}]"))
    return issues


def compare_type_family(old_payload: JsonDict, next_payload: JsonDict, required_keys: list[str]) -> list[JsonDict]:
    issues: list[JsonDict] = []
    for key in required_keys:
        if key not in old_payload or key not in next_payload:
            continue
        old_family = type_family(old_payload[key])
        next_family = type_family(next_payload[key])
        if old_family != next_family:
            issues.append(
                {
                    "rule": "type_family",
                    "key": key,
                    "old_type": old_family,
                    "next_type": next_family,
                    "severity": "fail",
                }
            )
    return issues


def compare_allowed_extra_keys(old_payload: JsonDict, next_payload: JsonDict) -> list[JsonDict]:
    old_keys = set(old_payload)
    next_keys = set(next_payload)
    extra_keys = sorted(next_keys - old_keys)
    return [
        {"rule": "extra_key", "key": key, "severity": "info"}
        for key in extra_keys
        if key not in ALLOWED_DYNAMIC_KEYS
    ]


def compare_status_code(old_status: int, next_status: int, expected_status: int = 200) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if old_status != expected_status:
        issues.append({"rule": "old_status_code", "expected": expected_status, "actual": old_status, "severity": "fail"})
    if next_status != expected_status:
        issues.append({"rule": "next_status_code", "expected": expected_status, "actual": next_status, "severity": "fail"})
    if old_status != next_status:
        issues.append({"rule": "status_code_parity", "old": old_status, "next": next_status, "severity": "fail"})
    return issues


def compare_semantic_flags(endpoint_name: str, payload: JsonDict) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if endpoint_name == "preview.default":
        skipped = payload.get("skipped_by_reason")
        if not isinstance(skipped, dict):
            issues.append({"rule": "preview_skipped_by_reason", "severity": "fail"})
    if endpoint_name == "execute.default":
        summary = payload.get("execution_summary")
        if not isinstance(summary, dict):
            issues.append({"rule": "execute_execution_summary", "severity": "fail"})
        elif summary.get("backend") != "external_effect_queue":
            issues.append({"rule": "execute_external_effect_backend", "severity": "fail"})
        if "external_effect_job_ids" not in payload:
            issues.append({"rule": "execute_external_effect_job_ids", "severity": "fail"})
    if endpoint_name.startswith("send_records"):
        items = payload.get("items")
        if not isinstance(items, list):
            issues.append({"rule": "send_records_items", "severity": "fail"})
    return issues


def validate_payload(endpoint_name: str, payload: JsonDict) -> list[JsonDict]:
    spec = ENDPOINT_SPECS[endpoint_name]
    issues = compare_required_keys(payload, spec.required_keys)
    if endpoint_name.startswith("overview"):
        issues.extend(compare_card_labels(payload))
    if spec.item_required_keys:
        issues.extend(compare_item_required_keys(payload, spec.item_required_keys))
    issues.extend(compare_semantic_flags(endpoint_name, payload))
    return issues


def compare_endpoint_payloads(endpoint_name: str, old_payload: JsonDict, next_payload: JsonDict) -> list[JsonDict]:
    spec = ENDPOINT_SPECS[endpoint_name]
    issues: list[JsonDict] = []
    issues.extend(validate_payload(endpoint_name, old_payload))
    issues.extend(validate_payload(endpoint_name, next_payload))
    issues.extend(compare_type_family(old_payload, next_payload, spec.required_keys))
    issues.extend(compare_allowed_extra_keys(old_payload, next_payload))
    return issues

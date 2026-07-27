from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]

ADMIN_LIST_TOP_LEVEL_KEYS = ["ok", "items", "total", "limit", "offset"]
ADMIN_LIST_ITEM_KEYS = ["id", "slug", "title", "description", "enabled", "redirect_url", "created_at", "updated_at", "question_count"]
ADMIN_DETAIL_TOP_LEVEL_KEYS = ["ok", "questionnaire", "questions", "external_push_config"]
ADMIN_DETAIL_QUESTIONNAIRE_KEYS = ["id", "slug", "title", "description", "enabled", "redirect_url", "submit_button_text", "created_at", "updated_at"]
QUESTION_KEYS = ["id", "type", "title", "required", "options"]
OPTION_KEYS = ["id", "label", "value", "tag_codes"]
PREFLIGHT_TOP_LEVEL_KEYS = ["ok", "checks"]
PREFLIGHT_CHECK_KEYS = [
    "wechat_oauth_configured",
    "wecom_contact_configured",
    "debug_session_api_enabled",
    "questionnaire_admin_ui_enabled",
    "wecom_tags_api_available",
    "identity_map_available",
]
PUBLIC_GET_TOP_LEVEL_KEYS = ["ok", "questionnaire", "questions"]
SUBMIT_TOP_LEVEL_KEYS = [
    "ok",
    "submission_id",
    "questionnaire_id",
    "slug",
    "external_userid",
    "person_id",
    "score",
    "final_tags",
    "redirect_url",
    "result_message",
]


@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path: str
    expected_status: int = 200
    body: Json | None = None


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "admin_list.default": EndpointSpec("GET", "/api/admin/questionnaires"),
    "admin_detail.default": EndpointSpec("GET", "/api/admin/questionnaires/1"),
    "admin_preflight.default": EndpointSpec("GET", "/api/admin/questionnaires/preflight"),
    "public_get.default": EndpointSpec("GET", "/api/h5/questionnaires/hxc-activation-v1"),
    "submit.default": EndpointSpec(
        "POST",
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        body={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "respondent_identity": {"mobile": "13800138000"},
        },
    ),
}

DEFAULT_SAFE_ENDPOINTS = list(ENDPOINT_SPECS)


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


def validate_payload(endpoint_name: str, payload: Json) -> list[Json]:
    if endpoint_name == "admin_list.default":
        issues = compare_required_keys(payload, ADMIN_LIST_TOP_LEVEL_KEYS)
        issues.extend(compare_item_required_keys(payload.get("items"), ADMIN_LIST_ITEM_KEYS, location="$.items"))
        return issues
    if endpoint_name == "admin_detail.default":
        issues = compare_required_keys(payload, ADMIN_DETAIL_TOP_LEVEL_KEYS)
        if isinstance(payload.get("questionnaire"), dict):
            issues.extend(compare_required_keys(payload["questionnaire"], ADMIN_DETAIL_QUESTIONNAIRE_KEYS, location="$.questionnaire"))
        else:
            issues.append({"rule": "type_family", "location": "$.questionnaire", "expected": "object", "actual": type(payload.get("questionnaire")).__name__, "severity": "fail"})
        issues.extend(compare_item_required_keys(payload.get("questions"), QUESTION_KEYS, location="$.questions"))
        options = (payload.get("questions") or [{}])[0].get("options") if isinstance((payload.get("questions") or [{}])[0], dict) else None
        issues.extend(compare_item_required_keys(options or [], OPTION_KEYS, location="$.questions[0].options"))
        return issues
    if endpoint_name == "admin_preflight.default":
        issues = compare_required_keys(payload, PREFLIGHT_TOP_LEVEL_KEYS)
        if isinstance(payload.get("checks"), dict):
            issues.extend(compare_required_keys(payload["checks"], PREFLIGHT_CHECK_KEYS, location="$.checks"))
        else:
            issues.append({"rule": "type_family", "location": "$.checks", "expected": "object", "actual": type(payload.get("checks")).__name__, "severity": "fail"})
        return issues
    if endpoint_name == "public_get.default":
        issues = compare_required_keys(payload, PUBLIC_GET_TOP_LEVEL_KEYS)
        issues.extend(compare_item_required_keys(payload.get("questions"), QUESTION_KEYS, location="$.questions"))
        return issues
    if endpoint_name == "submit.default":
        issues = compare_required_keys(payload, SUBMIT_TOP_LEVEL_KEYS)
        if not isinstance(payload.get("final_tags"), list):
            issues.append({"rule": "type_family", "location": "$.final_tags", "expected": "list", "actual": type(payload.get("final_tags")).__name__, "severity": "fail"})
        return issues
    return [{"rule": "unknown_endpoint_spec", "endpoint": endpoint_name, "severity": "fail"}]


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

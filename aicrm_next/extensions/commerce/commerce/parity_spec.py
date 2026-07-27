from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Json = dict[str, Any]

PRODUCT_LIST_KEYS = ["ok", "items", "total", "limit", "offset"]
PRODUCT_ITEM_KEYS = [
    "id",
    "product_code",
    "title",
    "description",
    "price_cents",
    "currency",
    "enabled",
    "page_slug",
    "cover_image_id",
    "detail_image_ids",
    "buy_button_text",
    "created_at",
    "updated_at",
]
PRODUCT_DETAIL_KEYS = PRODUCT_ITEM_KEYS + ["detail_sections"]
CHECKOUT_KEYS = [
    "ok",
    "order_no",
    "payment_provider",
    "amount_cents",
    "payment_status",
    "checkout_url",
    "qr_code_url",
    "provider_payload",
    "fake_payment",
]
TRANSACTION_LIST_KEYS = ["ok", "items", "total", "limit", "offset", "filters"]
TRANSACTION_ITEM_KEYS = [
    "order_no",
    "payment_provider",
    "product_code",
    "product_title",
    "buyer_mobile",
    "external_userid",
    "amount_cents",
    "currency",
    "payment_status",
    "transaction_id",
    "paid_at",
    "created_at",
    "updated_at",
]


@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path: str
    expected_status: int = 200
    body: Json | None = None


ENDPOINT_SPECS: dict[str, EndpointSpec] = {
    "products.default": EndpointSpec("GET", "/api/admin/wechat-pay/products"),
    "product_detail.default": EndpointSpec("GET", "/api/admin/wechat-pay/products/prod_001"),
    "checkout_wechat.default": EndpointSpec(
        "POST",
        "/api/checkout/wechat",
        body={"product_code": "course_masked_001", "buyer_identity": {"mobile": "mobile_masked_001"}, "quantity": 1},
    ),
    "checkout_alipay.default": EndpointSpec(
        "POST",
        "/api/checkout/alipay",
        body={"product_code": "course_masked_001", "buyer_identity": {"openid": "openid_masked_001"}, "quantity": 1},
    ),
    "wechat_transactions.default": EndpointSpec("GET", "/api/admin/wechat-pay/transactions"),
    "alipay_transactions.default": EndpointSpec("GET", "/api/admin/alipay/transactions"),
}
READ_ENDPOINTS = [
    "products.default",
    "product_detail.default",
    "wechat_transactions.default",
    "alipay_transactions.default",
]
WRITE_ENDPOINTS = [
    "checkout_wechat.default",
    "checkout_alipay.default",
]
DEFAULT_SAFE_ENDPOINTS = READ_ENDPOINTS


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
    if endpoint_name == "products.default":
        return compare_required_keys(payload, PRODUCT_LIST_KEYS) + compare_item_required_keys(payload.get("items"), PRODUCT_ITEM_KEYS, location="$.items")
    if endpoint_name == "product_detail.default":
        issues = compare_required_keys(payload, ["ok", "product"])
        if isinstance(payload.get("product"), dict):
            issues.extend(compare_required_keys(payload["product"], PRODUCT_DETAIL_KEYS, location="$.product"))
        return issues
    if endpoint_name in {"checkout_wechat.default", "checkout_alipay.default"}:
        return compare_required_keys(payload, CHECKOUT_KEYS)
    if endpoint_name in {"wechat_transactions.default", "alipay_transactions.default"}:
        return compare_required_keys(payload, TRANSACTION_LIST_KEYS) + compare_item_required_keys(payload.get("items"), TRANSACTION_ITEM_KEYS, location="$.items")
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

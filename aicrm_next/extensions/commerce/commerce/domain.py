from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from aicrm_next.navigation_target import completion_action_for_target, completion_target_projection, normalize_completion_target, safe_completion_url
from aicrm_next.navigation_target.domain import h5_url_for_legacy_fields
from aicrm_next.shared.errors import ContractError

PRODUCT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_product_code(value: str) -> str:
    product_code = str(value or "").strip()
    if not PRODUCT_CODE_PATTERN.fullmatch(product_code):
        raise ContractError("product_code must be 3-80 characters of letters, numbers, underscore, or hyphen")
    return product_code


def validate_price_cents(value: int) -> int:
    if not isinstance(value, int) or value < 0:
        raise ContractError("price_cents must be a non-negative integer")
    return value


def validate_quantity(value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ContractError("quantity must be a positive integer")
    return value


def normalize_status(status: str | None) -> str:
    value = (status or "pending").strip().lower()
    if value not in {"pending", "paid", "failed", "closed"}:
        raise ContractError("unsupported payment_status")
    return value


def safe_completion_redirect_url(value: Any) -> str:
    return safe_completion_url(value)


def completion_redirect_projection(enabled: Any, url: Any) -> dict[str, Any]:
    safe_url = safe_completion_redirect_url(url)
    configured = bool(enabled)
    is_enabled = configured and bool(safe_url)
    completion_action = (
        {"type": "redirect", "redirect_url": safe_url}
        if is_enabled
        else {"type": "default", "redirect_url": ""}
    )
    return {
        "completion_redirect_enabled": configured,
        "completion_redirect_url": safe_url,
        "completion_redirect": {"enabled": is_enabled, "url": safe_url if is_enabled else ""},
        "completion_action": completion_action,
    }


def validate_completion_redirect(enabled: Any, url: Any) -> dict[str, Any]:
    raw_url = str(url or "").strip()
    safe_url = safe_completion_redirect_url(raw_url)
    if bool(enabled) and raw_url and not safe_url:
        raise ContractError("completion_redirect_url must be an https URL or safe internal path")
    return {
        "completion_redirect_enabled": bool(enabled),
        "completion_redirect_url": safe_url,
    }


def _enabled_page_material_item(item: Any) -> bool:
    if isinstance(item, str):
        return bool(item.strip())
    if not isinstance(item, dict):
        return False
    if item.get("enabled") is False:
        return False
    return any(str(item.get(key) or "").strip() for key in ("image_url", "data_url", "url", "src", "source_url"))


def _configured_page_slice(item: Any) -> bool:
    if _enabled_page_material_item(item):
        return True
    if not isinstance(item, dict) or item.get("enabled") is False:
        return False
    return any(str(item.get(key) or "").strip() for key in ("image_library_id", "library_image_id", "asset_id"))


def has_product_page_material(product: dict[str, Any]) -> bool:
    if not isinstance(product, dict):
        return False
    return any(_configured_page_slice(item) for item in list(product.get("slices") or [])) or any(
        _enabled_page_material_item(item) for item in list(product.get("detail_images") or [])
    )


def preview_product(product: dict[str, Any]) -> dict[str, Any]:
    completion_target = completion_target_projection(
        product.get("completion_target_json") if product.get("completion_target_json") is not None else product.get("completion_target"),
        legacy_h5_url=product.get("completion_redirect_url"),
        legacy_enabled=product.get("completion_redirect_enabled"),
    )
    completion_redirect = completion_redirect_projection(
        product.get("completion_redirect_enabled"),
        product.get("completion_redirect_url"),
    )
    completion_action = completion_action_for_target(
        completion_target["completion_target"],
        legacy_redirect_url=completion_redirect.get("completion_redirect_url"),
        legacy_enabled=completion_redirect.get("completion_redirect_enabled"),
    )
    return {
        "id": product.get("id", ""),
        "product_code": product["product_code"],
        "title": product["title"],
        "name": product.get("name") or product["title"],
        "description": product.get("description", ""),
        "price_cents": product["price_cents"],
        "amount_total": product.get("amount_total", product["price_cents"]),
        "currency": product.get("currency", "CNY"),
        "enabled": product.get("enabled", True),
        "cover_image": product.get("cover_image") or {"id": product.get("cover_image_id"), "data_url": ""},
        "detail_images": product.get("detail_images", []),
        "detail_sections": product.get("detail_sections", []),
        "slices": product.get("slices", []),
        "buy_button_text": product.get("buy_button_text", "立即购买"),
        "cta_text": product.get("cta_text") or product.get("buy_button_text", "立即购买"),
        "require_mobile": bool(product.get("require_mobile")),
        **completion_redirect,
        **completion_target,
        "completion_action": completion_action,
    }


def normalize_product_completion_target(payload: dict[str, Any]) -> dict[str, Any]:
    raw_target = payload.get("completion_target")
    if raw_target is None:
        raw_target = payload.get("completion_target_json")
    legacy_url = payload.get("completion_redirect_url")
    if raw_target is None:
        raw_legacy_url = str(legacy_url or "").strip()
        safe_legacy_url = safe_completion_redirect_url(raw_legacy_url)
        if bool(payload.get("completion_redirect_enabled")) and raw_legacy_url and not safe_legacy_url:
            raise ContractError("completion_redirect_url must be an https URL or safe internal path")
        legacy_url = safe_legacy_url
    target = normalize_completion_target(
        raw_target,
        legacy_h5_url=legacy_url,
        legacy_enabled=payload.get("completion_redirect_enabled"),
    )
    legacy_url = h5_url_for_legacy_fields(target)
    legacy_enabled = bool(target.get("enabled")) and bool(legacy_url)
    if raw_target is None:
        legacy_enabled = bool(payload.get("completion_redirect_enabled")) and bool(legacy_url)
    return {
        "completion_target_json": target,
        "completion_redirect_enabled": legacy_enabled,
        "completion_redirect_url": legacy_url,
    }

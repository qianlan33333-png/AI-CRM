from __future__ import annotations

from typing import Any

from .target_refs import target_ref_for_product_id


def _text(value: Any) -> str:
    return str(value or "").strip()


def product_option(item: dict[str, Any]) -> dict[str, Any]:
    """Project one unified trade product without exposing its database id."""

    product_id = _text(item.get("trade_product_id") or item.get("id"))
    product_type = _text(item.get("product_type"))
    if product_type not in {"standard_product", "service_period"}:
        metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
        product_type = (
            "service_period"
            if item.get("service_period_id") or _text(metadata.get("aicrm_product_owner")) == "service_period"
            else "standard_product"
        )
    code = _text(item.get("product_code"))
    link_slug = _text(item.get("link_slug"))
    status = _text(item.get("status")) or ("active" if item.get("enabled") else "disabled")
    purchase_url = f"/s/{link_slug or code}/pay" if product_type == "service_period" else f"/pay/{code}"
    available = (
        bool(item.get("enabled"))
        and status == "active"
        and (product_type != "service_period" or bool(item.get("service_period_id") and link_slug))
    )
    return {
        "trade_product_id": product_id,
        "target_ref": target_ref_for_product_id(product_id),
        "product_type": product_type,
        "title": _text(item.get("title") or item.get("name") or code),
        "amount_total": int(item.get("amount_total") or item.get("price_cents") or 0),
        "currency": _text(item.get("currency")) or "CNY",
        "status": status,
        "enabled": bool(item.get("enabled")),
        "available": available,
        "duration_days": int(item.get("duration_days") or 0) or None,
        "purchase_url": purchase_url,
        "product_code": code,
        "metadata": {},
    }


__all__ = ["product_option"]

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _routes() -> list[dict]:
    payload = yaml.safe_load(
        (ROOT / "docs/architecture/route_ownership_manifest.yml").read_text(encoding="utf-8")
    )
    return list(payload["routes"])


def test_coupon_routes_are_next_owned_and_admin_writes_issue_bound_action_grants() -> None:
    routes = _routes()
    coupon_routes = [route for route in routes if "coupon" in route["route_name"]]

    assert len(coupon_routes) == 21
    assert {route["runtime_owner"] for route in coupon_routes} == {"ai_crm_next"}
    assert {route["capability_owner"] for route in coupon_routes} == {"commerce"}

    unsafe_admin_routes = [
        route
        for route in coupon_routes
        if route["path"].startswith("/api/admin/")
        and set(route["methods"]) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(unsafe_admin_routes) == 7
    for route in unsafe_admin_routes:
        assert route["auth_scheme"] == "human_session"
        assert route["capability"] == "manage_commerce"
        assert route["csrf"] is True
        assert route["external_effects"] == "none"


def test_both_coupon_compatible_payment_create_routes_remain_approval_gated() -> None:
    routes = _routes()
    by_key = {
        (tuple(route["methods"]), route["path"], route["route_name"]): route
        for route in routes
    }

    ordinary = by_key[
        (("POST",), "/api/h5/wechat-pay/jsapi/orders", "api.h5_wechat_pay_create_jsapi_order")
    ]
    service_period = by_key[
        (
            ("POST",),
            "/api/h5/service-period-products/{link_slug}/wechat-pay/jsapi/orders",
            "create_service_period_jsapi_order",
        )
    ]
    assert ordinary["external_effects"] == "real_requires_approval"
    assert service_period["external_effects"] == "real_requires_approval"


def test_coupon_tables_have_canonical_lifecycle_and_no_drop_candidate() -> None:
    payload = yaml.safe_load(
        (ROOT / "docs/architecture/data_table_lifecycle_manifest.yml").read_text(encoding="utf-8")
    )
    tables = payload["tables"]
    names = {
        "commerce_coupons",
        "commerce_coupon_product_bindings",
        "commerce_coupon_claims",
        "commerce_coupon_redemptions",
    }

    assert names <= set(tables)
    for name in names:
        assert tables[name]["lifecycle"] == "canonical"
        assert tables[name]["write_owner"] == "aicrm_next.commerce.coupons"
        assert tables[name]["drop_candidate"] is False
        assert tables[name]["migration_source"] == "0114_commerce_coupons"

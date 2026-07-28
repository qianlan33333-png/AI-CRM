from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from aicrm_next.main import create_app
from aicrm_next.platform.shared import route_policy as route_policy_module
from aicrm_next.platform.shared.route_ownership import FASTAPI_BUILTIN_ROUTE_PATHS, collect_route_inventory, load_route_manifest
from aicrm_next.platform.shared.route_policy import DEFAULT_ROUTE_POLICY_MANIFEST, RoutePolicyIndex, default_route_policy_index


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"


def _entry(path: str, method: str) -> dict:
    for item in load_route_manifest(MANIFEST):
        if item["path"] == path and method in item["methods"]:
            return item
    raise AssertionError(f"missing route policy for {method} {path}")


def _assert_policy(path: str, method: str, expected: dict) -> None:
    actual = _entry(path, method)
    assert expected.items() <= actual.items()


def test_route_policy_inventory_covers_every_runtime_business_route(monkeypatch) -> None:
    app = create_app()
    index = RoutePolicyIndex.from_manifest(MANIFEST)
    inventory = collect_route_inventory(app)

    assert len(index) == len(inventory)
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in FASTAPI_BUILTIN_ROUTE_PATHS:
            continue
        assert index.get(path=route.path, methods=route.methods, route_name=route.name) is not None

    _assert_route_policy_index_mapping_is_read_only()
    _assert_custom_route_policy_manifest_loads_are_not_cached()
    _assert_create_app_keeps_mutable_state_isolated()
    _assert_default_route_policy_index_loads_canonical_manifest_once(monkeypatch)


def _assert_route_policy_index_mapping_is_read_only() -> None:
    index = RoutePolicyIndex.from_manifest(MANIFEST)
    entry = load_route_manifest(MANIFEST)[0]
    policy = index.get(path=entry["path"], methods=entry["methods"], route_name=entry["route_name"])

    assert policy is not None
    with pytest.raises(TypeError):
        index._by_key[policy.key] = policy  # type: ignore[index]


def _assert_default_route_policy_index_loads_canonical_manifest_once(monkeypatch) -> None:
    calls: list[Path] = []
    original = route_policy_module.load_route_manifest

    def tracking_loader(path):
        calls.append(Path(path))
        return original(path)

    default_route_policy_index.cache_clear()
    monkeypatch.setattr(route_policy_module, "load_route_manifest", tracking_loader)
    try:
        first = default_route_policy_index()
        second = default_route_policy_index()

        assert first is second
        assert calls == [DEFAULT_ROUTE_POLICY_MANIFEST]
    finally:
        default_route_policy_index.cache_clear()


def _assert_custom_route_policy_manifest_loads_are_not_cached() -> None:
    first = RoutePolicyIndex.from_manifest(MANIFEST)
    second = RoutePolicyIndex.from_manifest(MANIFEST)

    assert first is not second


def _assert_create_app_keeps_mutable_state_isolated() -> None:
    first = create_app()
    second = create_app()

    assert first is not second
    assert first.dependency_overrides is not second.dependency_overrides
    assert first.state.external_effect_adapter_registry is not second.state.external_effect_adapter_registry
    assert first.state.external_effect_continuation_registry is not second.state.external_effect_continuation_registry
    assert first.state.internal_event_consumer_registry is not second.state.internal_event_consumer_registry


def test_route_policy_inventory_uses_all_required_audiences() -> None:
    entries = load_route_manifest(MANIFEST)

    assert {entry["audience"] for entry in entries} == {
        "admin",
        "sidebar",
        "public_h5",
        "callback",
        "internal_worker",
        "external_integration",
    }


def test_external_radar_routes_use_existing_read_client_and_explicit_pii_levels() -> None:
    _assert_policy(
        "/api/external/radar-clicks",
        "GET",
        {
            "audience": "external_integration",
            "auth_scheme": "api_client_jwt",
            "capability": "external_read",
            "access_scope": "service",
            "pii_level": "sensitive",
            "rate_limit": "integration",
        },
    )
    _assert_policy(
        "/api/external/radar-links",
        "GET",
        {
            "audience": "external_integration",
            "auth_scheme": "api_client_jwt",
            "capability": "external_read",
            "access_scope": "service",
            "pii_level": "none",
            "rate_limit": "integration",
        },
    )


def test_known_unsafe_routes_have_explicit_deny_by_default_policies() -> None:
    _assert_policy(
        "/mcp",
        "POST",
        {
            "audience": "external_integration",
            "auth_scheme": "api_client_jwt",
            "capability": "mcp_execute",
            "access_scope": "service",
            "pii_level": "sensitive",
            "csrf": False,
        },
    )
    assert _entry("/api/identity/resolve", "GET")["auth_scheme"] == "api_client_jwt"
    _assert_policy(
        "/api/sidebar/bind-mobile",
        "POST",
        {
            "auth_scheme": "sidebar_grant",
            "capability": "sidebar_write",
            "access_scope": "owner",
        },
    )
    _assert_policy(
        "/api/admin/automation-conversion/group-ops/plans",
        "POST",
        {
            "audience": "admin",
            "auth_scheme": "human_session",
            "capability": "manage_group_ops",
            "csrf": True,
        },
    )
    _assert_policy(
        "/api/h5/questionnaires/{slug}/result",
        "GET",
        {
            "auth_scheme": "public_result_grant",
            "access_scope": "single_resource",
            "pii_level": "sensitive",
            "requires_auth": True,
        },
    )
    for path, method, capability in (
        ("/api/h5/wechat-pay/jsapi/orders", "POST", "payment_order_create"),
        ("/api/h5/wechat-pay/orders/{out_trade_no}", "GET", "payment_order_read"),
        (
            "/api/h5/service-period-products/{link_slug}/wechat-pay/jsapi/orders",
            "POST",
            "payment_order_create",
        ),
        ("/api/h5/coupons/available", "GET", "coupon_available_read"),
        ("/api/h5/coupons/{public_slug}/claim", "POST", "coupon_claim"),
    ):
        _assert_policy(
            path,
            method,
            {
                "auth_scheme": "payment_identity_session",
                "capability": capability,
                "access_scope": "self",
                "requires_auth": True,
            },
        )


def test_human_session_writes_always_require_csrf() -> None:
    entries = load_route_manifest(MANIFEST)
    unsafe_methods = {"POST", "PUT", "PATCH", "DELETE"}

    violations = [
        f"{','.join(entry['methods'])} {entry['path']}"
        for entry in entries
        if entry["auth_scheme"] == "human_session" and unsafe_methods.intersection(entry["methods"]) and entry["csrf"] is not True
    ]

    assert violations == []

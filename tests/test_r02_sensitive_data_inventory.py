from __future__ import annotations

from pathlib import Path

import yaml

from aicrm_next.platform.admin_config.settings import SENSITIVE_KEYS
from aicrm_next.platform.shared.route_ownership import load_route_manifest


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "docs" / "architecture" / "r02_sensitive_data_inventory.yml"
ROUTE_MANIFEST_PATH = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"
REQUIRED_SECRET_FIELDS = {"current_sources", "runtime_consumers", "target_provider", "rotation_owner", "rollback"}
PII_LEVELS = {"customer", "sensitive", "financial"}


def _inventory() -> dict:
    return yaml.safe_load(INVENTORY_PATH.read_text(encoding="utf-8"))


def _matching_route_overrides(route: dict, overrides: list[dict]) -> list[dict]:
    path = str(route.get("path") or "")
    route_name = str(route.get("route_name") or "")
    matches: list[dict] = []
    for item in overrides:
        path_prefix = str(item.get("path_prefix") or "")
        route_names = {str(value) for value in item.get("route_names") or []}
        if path_prefix and (path == path_prefix or path.startswith(f"{path_prefix}/")):
            matches.append(item)
        elif route_name and route_name in route_names:
            matches.append(item)
    return matches


def _route_override(route: dict, overrides: list[dict]) -> dict | None:
    matches = _matching_route_overrides(route, overrides)
    if not matches:
        return None
    route_name = str(route.get("route_name") or "")
    route_name_matches = [item for item in matches if route_name in set(item.get("route_names") or [])]
    if route_name_matches:
        return route_name_matches[0]
    return max(matches, key=lambda item: len(str(item.get("path_prefix") or "")))


def test_secret_inventory_covers_every_sensitive_setting() -> None:
    inventory = _inventory()
    secret_keys = inventory["secret_keys"]

    assert set(secret_keys) == set(SENSITIVE_KEYS)
    for key, item in secret_keys.items():
        assert REQUIRED_SECRET_FIELDS <= set(item), key
        assert item["current_sources"], key
        assert item["runtime_consumers"], key
        assert item["target_provider"] == "versioned_file_secret_store", key
        assert item["rotation_owner"], key
        assert item["rollback"] == "previous_secret_version", key


def test_every_pii_route_has_a_durable_audit_classification() -> None:
    inventory = _inventory()
    defaults = inventory["pii_audit_defaults"]
    overrides = inventory["pii_route_overrides"]
    routes = [route for route in load_route_manifest(ROUTE_MANIFEST_PATH) if route.get("pii_level") in PII_LEVELS]

    assert routes
    assert set(defaults) == PII_LEVELS
    for route in routes:
        classification = _route_override(route, overrides) or defaults[str(route["pii_level"])]
        assert classification["purpose"], route["route_name"]
        assert classification["audit_mode"] in {"required", "required_fail_closed"}, route["route_name"]
        assert classification["scope_source"] in {"route_policy", "resource_fingerprint"}, route["route_name"]
        assert classification["raw_payload_allowed"] is False, route["route_name"]


def test_every_pii_override_matches_a_route_and_specific_route_names_win() -> None:
    inventory = _inventory()
    overrides = inventory["pii_route_overrides"]
    routes = load_route_manifest(ROUTE_MANIFEST_PATH)

    for override in overrides:
        assert any(override in _matching_route_overrides(route, overrides) for route in routes), override

    for route_name in {"export_questionnaire", "export_radar_link_events"}:
        route = next(route for route in routes if route["route_name"] == route_name)
        classification = _route_override(route, overrides)
        assert classification is not None
        assert classification["purpose"] == "pii_export"


def test_outbound_webhook_inventory_has_one_secure_dispatch_owner() -> None:
    inventory = _inventory()
    callers = inventory["outbound_webhook_callers"]

    assert callers
    for item in callers:
        assert item["dispatch_owner"] == "aicrm_next.platform.external_push.https_transport", item["source"]
        assert item["redirect_policy"] == "deny", item["source"]
        assert item["dns_policy"] == "resolve_validate_pin", item["source"]
        assert item["real_execution_change"] == "none", item["source"]


def test_inventory_declares_no_new_product_capability() -> None:
    inventory = _inventory()

    assert inventory["product_boundary"] == {
        "user_visible_capability_delta": "none",
        "new_product_routes_pages_menus": "none",
        "new_business_models_tables": "none",
    }

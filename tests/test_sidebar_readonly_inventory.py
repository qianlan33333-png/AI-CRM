from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/architecture/sidebar_readonly_route_inventory.md"

READONLY_ROUTES = [
    "/api/sidebar/customer-context",
    "/api/sidebar/profile",
    "/api/sidebar/tags",
    "/api/sidebar/binding-status",
    "/api/sidebar/contact-binding-status",
    "/api/sidebar/lead-pool/status",
    "/api/sidebar/signup-tags/status",
    "/api/sidebar/marketing-status",
]

OUT_OF_SCOPE_ROUTES = [
    "/api/sidebar/bind-mobile",
    "/api/sidebar/jssdk-config",
    "/api/sidebar/lead-pool/upsert-class-term",
    "/api/sidebar/signup-tags/mark",
    "/api/sidebar/marketing-status/*",
    "/api/sidebar/v2/profile",
    "/api/sidebar/v2/materials/send",
]


def test_sidebar_readonly_inventory_documents_readonly_and_out_of_scope_routes() -> None:
    text = INVENTORY.read_text(encoding="utf-8")

    for route in READONLY_ROUTES + OUT_OF_SCOPE_ROUTES:
        assert route in text

    assert "readonly sealed; no legacy fallback" in text
    assert "No sidebar write route is replaced or deleted in this group." in text
    assert "No JSSDK signing path is sealed in this group." in text
    assert "No real WeCom" in text


def test_sidebar_readonly_inventory_matches_source_route_surface() -> None:
    source = (ROOT / "aicrm_next/crm/customer_read_model/api.py").read_text(encoding="utf-8")
    identity_source = (ROOT / "aicrm_next/crm/identity_contact/api.py").read_text(encoding="utf-8")
    inventory = INVENTORY.read_text(encoding="utf-8")

    for route in READONLY_ROUTES:
        assert route in source or route in identity_source
        assert route in inventory


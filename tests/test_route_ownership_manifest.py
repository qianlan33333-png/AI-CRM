from __future__ import annotations

import yaml
from fastapi import FastAPI
from starlette.routing import Route

from aicrm_next.main import app
from aicrm_next.platform.shared.route_ownership import collect_route_inventory, normalize_methods, validate_route_manifest


def _write_manifest(path, routes):
    path.write_text(yaml.safe_dump({"routes": routes}, sort_keys=False), encoding="utf-8")


def test_route_ownership_manifest_covers_current_app_routes() -> None:
    errors = validate_route_manifest(app, "docs/architecture/route_ownership_manifest.yml")
    assert errors == []


def test_route_ownership_manifest_rejects_unknown_owner(tmp_path) -> None:
    test_app = FastAPI()

    @test_app.get("/demo", name="demo_route")
    def demo_route():
        return {"ok": True}

    _write_manifest(
        tmp_path / "routes.yml",
        [
            {
                "path": "/demo",
                "methods": ["GET"],
                "route_name": "demo_route",
                "capability_owner": "unknown",
                "runtime_owner": "ai_crm_next",
                "layer": "api",
                "external_effects": "none",
                "data_source": "read_model",
                "requires_auth": False,
                "rollback": "previous_release",
            }
        ],
    )

    errors = validate_route_manifest(test_app, tmp_path / "routes.yml")

    assert any("unknown_owner" in error for error in errors)
    assert any("capability_owner" in error for error in errors)


def test_route_ownership_manifest_reports_missing_route(tmp_path) -> None:
    test_app = FastAPI()

    @test_app.post("/demo", name="demo_route")
    def demo_route():
        return {"ok": True}

    _write_manifest(tmp_path / "routes.yml", [])

    errors = validate_route_manifest(test_app, tmp_path / "routes.yml")

    assert any("missing_route_owner" in error for error in errors)
    assert any("/demo" in error for error in errors)


def test_route_ownership_methods_are_normalized_across_fastapi_versions() -> None:
    assert normalize_methods(["GET", "HEAD"]) == ("GET",)
    assert normalize_methods(["POST", "OPTIONS"]) == ("POST",)
    assert normalize_methods(["OPTIONS"]) == ("OPTIONS",)


def test_route_inventory_accepts_structural_starlette_routes() -> None:
    def demo_endpoint(request):
        return None

    test_app = FastAPI()
    test_app.router.routes.append(Route("/structural-demo", demo_endpoint, methods=["GET"]))

    inventory = collect_route_inventory(test_app)

    assert any(item.path == "/structural-demo" and item.methods == ("GET",) for item in inventory)
    assert not any(item.path == "/docs" for item in inventory)


def test_route_inventory_does_not_require_endpoint_attribute() -> None:
    class StructuralRoute:
        path = "/structural-no-endpoint"
        methods = {"GET"}
        name = "structural_no_endpoint"

    test_app = FastAPI()
    test_app.router.routes.append(StructuralRoute())

    inventory = collect_route_inventory(test_app)

    assert any(
        item.path == "/structural-no-endpoint"
        and item.methods == ("GET",)
        and item.route_name == "structural_no_endpoint"
        for item in inventory
    )


def test_route_inventory_expands_deferred_included_routers() -> None:
    class StructuralRoute:
        path = "/nested"
        methods = {"POST"}
        name = "nested_route"

    class Router:
        routes = [StructuralRoute()]

    class IncludeContext:
        prefix = "/api/demo"
        included_router = Router()

    class IncludedRouter:
        original_router = Router()
        include_context = IncludeContext()

    class App:
        routes = [IncludedRouter()]

    inventory = collect_route_inventory(App())

    assert any(
        item.path == "/api/demo/nested"
        and item.methods == ("POST",)
        and item.route_name == "nested_route"
        for item in inventory
    )

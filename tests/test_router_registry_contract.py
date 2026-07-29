from __future__ import annotations

import ast
from pathlib import Path

from starlette.routing import Mount

from aicrm_next.main import create_app
from aicrm_next.router_registry import ROUTER_SPECS, router_registry_summary
from aicrm_next.platform.shared.route_ownership import collect_route_inventory


def test_router_registry_specs_have_capability_owner_and_route_group() -> None:
    assert ROUTER_SPECS
    assert len({spec.route_group for spec in ROUTER_SPECS}) == len(ROUTER_SPECS)
    assert len({id(spec.router) for spec in ROUTER_SPECS}) == len(ROUTER_SPECS)
    for spec in ROUTER_SPECS:
        assert spec.capability_owner
        assert spec.capability_owner != "unknown"
        assert spec.logical_capability_id
        assert spec.route_group
        assert spec.router.routes


def test_router_registry_summary_exposes_stable_metadata() -> None:
    summary = router_registry_summary()

    assert summary == [
        {
            "capability_owner": spec.capability_owner,
            "logical_capability_id": spec.logical_capability_id,
            "route_group": spec.route_group,
            "route_count": len(spec.router.routes),
            "notes": spec.notes,
        }
        for spec in ROUTER_SPECS
    ]
    assert all(item["route_count"] > 0 for item in summary)


def test_router_registry_preserves_unique_static_mounts_and_catch_all_order() -> None:
    app = create_app()
    inventory = collect_route_inventory(app, include_static=True)
    static_mounts = [(route.path, route.name) for route in app.routes if isinstance(route, Mount)]

    assert inventory
    assert static_mounts
    assert len(static_mounts) == len(set(static_mounts))
    assert all(path.startswith("/static") for path, _ in static_mounts)
    assert static_mounts[-1] == ("/static", "static")
    assert all(path != "/static" for path, _ in static_mounts[:-1])
    assert inventory[-1].path == "/{filename}"
    assert inventory[-1].route_name == "wechat_domain_verification_file"


def test_main_delegates_router_registration_to_registry() -> None:
    tree = ast.parse(Path("aicrm_next/main.py").read_text(encoding="utf-8"))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "router_registry"
        for alias in node.names
    }
    register_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "register_routers"
    ]
    include_router_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router"
    ]

    assert "register_routers" in imported_names
    assert any(
        call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "app"
        for call in register_calls
    )
    assert include_router_calls == []

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from aicrm_next.platform.shared.route_ownership import (
    ALLOWED_RUNTIME_OWNERS,
    REQUIRED_MANIFEST_FIELDS,
    collect_route_inventory,
    load_route_manifest,
    validate_route_manifest,
)
from aicrm_next.platform.shared.route_policy import RoutePolicyIndex, match_route_policy


pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"


def _materialized_path(route: APIRoute) -> str:
    path = route.path_format
    candidates = ("1", "current", "00000000-0000-0000-0000-000000000001", "current/path", "1.5")
    for name, convertor in route.param_convertors.items():
        sample = next(value for value in candidates if re.fullmatch(convertor.regex, value))
        path = path.replace(f"{{{name}}}", sample)
    return path


def test_every_current_http_route_has_one_valid_owner(current_app) -> None:
    errors = validate_route_manifest(current_app, MANIFEST)
    assert errors == []
    inventory = collect_route_inventory(current_app)
    manifest = load_route_manifest(MANIFEST)
    assert len(manifest) == len(inventory)
    assert len({item.key for item in inventory}) == len(inventory)


def test_route_manifest_is_the_single_auth_and_permission_contract() -> None:
    entries = load_route_manifest(MANIFEST)
    for entry in entries:
        assert set(REQUIRED_MANIFEST_FIELDS) <= set(entry)
        assert entry["runtime_owner"] in ALLOWED_RUNTIME_OWNERS
        assert entry["runtime_owner"] != "legacy"
        assert entry["principal_types"]
        if entry["csrf"]:
            assert entry["auth_scheme"] in {"human_session", "human_or_service"}


def test_route_policy_index_resolves_every_materialized_route(current_app) -> None:
    index = RoutePolicyIndex.from_manifest(MANIFEST)
    for route in current_app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}:
            continue
        method = next(iter(sorted(route.methods or {"GET"})))
        match = match_route_policy(
            current_app,
            {"type": "http", "path": _materialized_path(route), "method": method, "root_path": "", "headers": []},
            index,
        )
        assert match.route is not None, route.path
        assert match.policy is not None, route.path


def test_current_openapi_exposes_request_and_response_contracts(current_app) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate Operation ID.*", category=UserWarning)
        schema = current_app.openapi()
    for route in current_app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for method in set(route.methods or ()) - {"HEAD", "OPTIONS"}:
            operation = schema["paths"][route.path_format][method.lower()]
            assert operation.get("operationId"), f"{method} {route.path}"
            assert operation.get("responses"), f"{method} {route.path}"
            if route.body_field is not None:
                assert operation.get("requestBody"), f"{method} {route.path}"

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ENV_CALL_NAMES = {
    "getenv",
    "runtime_setting",
    "runtime_bool",
    "runtime_int",
    "_env_flag",
    "env_flag",
}
DECLARED_ENVIRONMENT_KEY_NAMES = {"RUNTIME_ENVIRONMENT_KEYS"}
EFFECT_PREFIXES = (
    "ai_assist.",
    "feishu.",
    "group_ops.",
    "media.",
    "openclaw.",
    "payment.",
    "webhook.",
    "wecom.",
)


def _load_structured(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        import yaml

        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


@contextmanager
def _inventory_runtime_environment() -> Iterator[None]:
    overrides = {
        "DATABASE_URL": "postgresql://inventory:inventory@127.0.0.1:1/inventory",
        "SECRET_KEY": "runtime-contract-inventory-only",
        "AICRM_NEXT_ENV": "test",
        "ENVIRONMENT": "test",
        "APP_ENV": "test",
        "FLASK_ENV": "test",
    }
    original = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _endpoint_key(endpoint: Any) -> tuple[str, str]:
    return (
        str(getattr(endpoint, "__module__", "") or ""),
        str(getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "")) or ""),
    )


def _route_owner_index(router_specs: tuple[Any, ...]) -> dict[tuple[str, str], dict[str, str]]:
    owners: dict[tuple[str, str], dict[str, str]] = {}
    for spec in router_specs:
        for route in getattr(spec.router, "routes", ()) or ():
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            owners[_endpoint_key(endpoint)] = {
                "capability_owner": str(spec.capability_owner),
                "route_group": str(spec.route_group),
            }
    return owners


def _route_kind(path: str, method: str, media_type: str) -> str:
    if method == "GET" and (
        media_type == "text/html" or path.startswith(("/admin", "/login", "/sidebar", "/questionnaires/")) or path.endswith(("/page", "/ui"))
    ):
        return "page"
    return "api"


def _runtime_routes_and_consumers() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    with _inventory_runtime_environment():
        from fastapi.routing import APIRoute

        from aicrm_next.main import create_app
        from aicrm_next.router_registry import ROUTER_SPECS

        app = create_app()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Duplicate Operation ID.*", category=UserWarning)
            openapi = app.openapi()
        owner_index = _route_owner_index(ROUTER_SPECS)
        routes: list[dict[str, Any]] = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            owner = owner_index.get(
                _endpoint_key(route.endpoint),
                {"capability_owner": "unregistered", "route_group": "unregistered"},
            )
            media_type = str(getattr(route.response_class, "media_type", "") or "")
            for method in sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"}):
                operation = dict(openapi.get("paths", {}).get(route.path, {}).get(method.lower(), {}) or {})
                contract = {
                    "parameters": operation.get("parameters", []),
                    "request_body": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                }
                routes.append(
                    {
                        "path": route.path,
                        "methods": [method],
                        "name": str(route.name or ""),
                        "operation_id": f"{route.name}:{method}:{route.path}",
                        "kind": _route_kind(route.path, method, media_type),
                        "include_in_schema": bool(route.include_in_schema),
                        "capability_owner": owner["capability_owner"],
                        "route_group": owner["route_group"],
                        "endpoint": ".".join(part for part in _endpoint_key(route.endpoint) if part),
                        "contract": contract,
                    }
                )

        consumers: list[dict[str, Any]] = []
        consumer_registry = app.state.internal_event_consumer_registry
        for event_type, registered in consumer_registry.to_dict().items():
            for consumer in registered:
                consumers.append(dict(consumer, event_type=event_type))
        for alias in consumer_registry.aliases_to_dict():
            consumers.append(dict(alias, consumer_type="handler_alias", max_attempts=None))

    routes.sort(key=lambda item: (item["path"], item["methods"], item["name"], item["endpoint"]))
    consumers.sort(key=lambda item: (item["event_type"], item["consumer_name"], item["consumer_type"]))
    return routes, dict(openapi.get("components", {}) or {}), consumers


def _migration_heads(root: Path) -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def _tables(root: Path) -> list[dict[str, Any]]:
    manifest = _load_structured(root / "docs" / "architecture" / "data_table_lifecycle_manifest.yml")
    tables = manifest.get("tables", {})
    if not isinstance(tables, dict):
        raise ValueError("data table lifecycle manifest tables must be a mapping")
    result = []
    for name, raw in tables.items():
        entry = dict(raw or {})
        result.append(
            {
                "table": str(name),
                "domain": str(entry.get("domain") or ""),
                "lifecycle": str(entry.get("lifecycle") or ""),
                "write_owner": str(entry.get("write_owner") or ""),
                "read_owners": sorted(str(item) for item in entry.get("read_owners", []) or []),
                "migration_source": str(entry.get("migration_source") or ""),
                "pii_level": str(entry.get("pii_level") or ""),
                "drop_candidate": bool(entry.get("drop_candidate", False)),
            }
        )
    return sorted(result, key=lambda item: item["table"])


def _external_effects(root: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    from aicrm_next.platform_foundation.external_effects import models

    runtime_effects = [
        {"constant": name, "effect_type": value}
        for name, value in vars(models).items()
        if name.isupper() and isinstance(value, str) and value.startswith(EFFECT_PREFIXES)
    ]
    runtime_effects.sort(key=lambda item: (item["effect_type"], item["constant"]))
    governance = _load_structured(root / "docs" / "architecture" / "external_effects_registry.yml")
    governed = [dict(item) for item in governance.get("effects", []) or []]
    governed.sort(key=lambda item: str(item.get("effect_key") or ""))
    return runtime_effects, governed


def _runtime_units(root: Path) -> list[dict[str, Any]]:
    manifest = _load_structured(root / "deploy" / "production_runtime_units.json")
    units: list[dict[str, Any]] = []
    primary_web = dict(manifest.get("primary_web") or {})
    if primary_web.get("service"):
        units.append(
            {
                "unit": str(primary_web["service"]),
                "kind": "service",
                "state": "primary_web",
            }
        )
    for item in manifest.get("active_services", []) or []:
        units.append(
            {
                "unit": str(item["service"]),
                "kind": "service",
                "state": "active",
                "health_url": str(item.get("health_url") or ""),
                "stop_for_migration": bool(item.get("stop_for_migration", False)),
            }
        )
    for item in manifest.get("active_autostart", []) or []:
        units.append(
            {
                "unit": str(item["timer"]),
                "kind": "timer",
                "state": "active_autostart",
                "service": str(item["service"]),
                "kick_after_timer_restart": bool(item.get("kick_after_timer_restart", False)),
                "kick_failure_fatal": bool(item.get("kick_failure_fatal", False)),
            }
        )
    replacement = manifest.get("cutover_replacement_autostart") or {}
    replacement_inventory = str(replacement.get("owner_inventory") or "")
    for item in replacement.get("timers", []) or []:
        units.append(
            {
                "unit": str(item["timer"]),
                "kind": "timer",
                "state": "cutover_replacement_autostart",
                "service": str(item["service"]),
                "owner_inventory": replacement_inventory,
            }
        )
    cutover = manifest.get("cutover_managed_legacy") or {}
    owner_inventory = str(cutover.get("owner_inventory") or "")
    for item in cutover.get("timers", []) or []:
        units.append(
            {
                "unit": str(item["timer"]),
                "kind": "timer",
                "state": "cutover_managed_legacy",
                "service": str(item["service"]),
                "owner_inventory": owner_inventory,
            }
        )
    for item in cutover.get("persistent_services", []) or []:
        units.append(
            {
                "unit": str(item["service"]),
                "kind": "service",
                "state": "cutover_managed_legacy",
                "owner_inventory": owner_inventory,
                "persistent": True,
            }
        )
    for item in manifest.get("approval_required", []) or []:
        if not isinstance(item, dict):
            raise ValueError("approval_required runtime units must declare timer and service")
        units.append(
            {
                "unit": str(item["timer"]),
                "kind": "timer",
                "state": "approval_required",
                "service": str(item["service"]),
            }
        )
    for unit in manifest.get("retired_forbidden", []) or []:
        name = str(unit)
        units.append(
            {
                "unit": name,
                "kind": "service" if name.endswith(".service") else "timer",
                "state": "retired_forbidden",
            }
        )
    return sorted(units, key=lambda item: (item["unit"], item["state"]))


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    return ""


def _literal_strings(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {str(item.value).strip() for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str) and str(item.value).strip()}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        if isinstance(node.func.value, ast.Attribute) and isinstance(node.func.value.value, ast.Name):
            return f"{node.func.value.value.id}.{node.func.value.attr}.{node.func.attr}"
        return node.func.attr
    return ""


def _environment_references(root: Path) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for relative_root in ("aicrm_next", "scripts", "deploy"):
        base = root / relative_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (SyntaxError, UnicodeDecodeError) as exc:
                raise ValueError(f"cannot parse environment references from {relative}: {exc}") from exc
            for node in tree.body:
                target_name = ""
                value: ast.AST | None = None
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target_name = node.targets[0].id
                    value = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    target_name = node.target.id
                    value = node.value
                if target_name not in DECLARED_ENVIRONMENT_KEY_NAMES:
                    continue
                for key in sorted(_literal_strings(value)):
                    references.append(
                        {
                            "key": key,
                            "file": relative,
                            "line": int(getattr(node, "lineno", 0) or 0),
                            "accessor": "declared_runtime_environment_key",
                        }
                    )
            for node in ast.walk(tree):
                key = ""
                accessor = ""
                if isinstance(node, ast.Call):
                    accessor = _call_name(node)
                    base_name = accessor.rsplit(".", 1)[-1]
                    if base_name in ENV_CALL_NAMES or accessor in {"os.getenv", "os.environ.get"}:
                        key = _literal_string(node.args[0] if node.args else None)
                elif isinstance(node, ast.Subscript):
                    value = node.value
                    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "os" and value.attr == "environ":
                        accessor = "os.environ[]"
                        key = _literal_string(node.slice)
                if key:
                    references.append(
                        {
                            "key": key,
                            "file": relative,
                            "line": int(getattr(node, "lineno", 0) or 0),
                            "accessor": accessor,
                        }
                    )
    unique = {(item["key"], item["file"], item["line"], item["accessor"]): item for item in references}
    return [unique[key] for key in sorted(unique)]


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    routes, openapi_components, consumers = _runtime_routes_and_consumers()
    external_effects, effect_governance = _external_effects(root)
    environment_references = _environment_references(root)
    return {
        "schema_version": 1,
        "composition_root": "aicrm_next.main:create_app",
        "production_data_accessed": False,
        "fixture_records_included": False,
        "routes": routes,
        "openapi_components": openapi_components,
        "migration_heads": _migration_heads(root),
        "tables": _tables(root),
        "internal_event_consumers": consumers,
        "external_effects": external_effects,
        "external_effect_governance": effect_governance,
        "runtime_units": _runtime_units(root),
        "environment_variables": sorted({item["key"] for item in environment_references}),
        "environment_variable_references": environment_references,
    }


def render_inventory(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_inventory(root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_inventory(build_inventory(root)), encoding="utf-8")


def check_inventory(root: Path, destination: Path) -> str:
    expected = render_inventory(build_inventory(root))
    actual = destination.read_text(encoding="utf-8") if destination.exists() else ""
    if actual == expected:
        return ""
    return "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(destination),
            tofile="generated-runtime-contract-inventory",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the Issue #67 R00 runtime contract inventory.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", type=Path)
    action.add_argument("--check", type=Path)
    args = parser.parse_args(argv)

    if args.write:
        write_inventory(ROOT, args.write)
        print(f"wrote runtime contract inventory: {args.write}")
        return 0

    diff = check_inventory(ROOT, args.check)
    if diff:
        print(diff, end="")
        return 1
    print(f"runtime contract inventory matches: {args.check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

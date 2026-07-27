from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from starlette.routing import Mount


REQUIRED_MANIFEST_FIELDS = (
    "path",
    "methods",
    "route_name",
    "capability_owner",
    "runtime_owner",
    "layer",
    "external_effects",
    "data_source",
    "requires_auth",
    "rollback",
    "audience",
    "auth_scheme",
    "capability",
    "access_scope",
    "pii_level",
    "csrf",
    "rate_limit",
)

ALLOWED_RUNTIME_OWNERS = {"ai_crm_next", "blocked", "retired"}
ALLOWED_EXTERNAL_EFFECTS = {"none", "fake_only", "staging_disabled", "real_requires_approval"}
ALLOWED_LAYERS = {"api", "admin_page", "h5", "webhook", "static", "integration"}
ALLOWED_DATA_SOURCES = {"read_model", "command", "external_adapter", "static"}
ALLOWED_AUDIENCES = {"admin", "sidebar", "public_h5", "callback", "internal_worker", "external_integration"}
ALLOWED_AUTH_SCHEMES = {
    "api_client_jwt",
    "client_credentials",
    "human_or_service",
    "human_session",
    "payment_identity_session",
    "provider_oauth_state",
    "provider_signature",
    "public",
    "public_result_grant",
    "sidebar_grant",
    "webhook_hmac",
}
ALLOWED_PRINCIPAL_TYPES = {"human", "api_client", "service", "public", "provider_callback"}
ALLOWED_ACCESS_SCOPES = {"global", "owner", "public", "self", "service", "single_resource"}
ALLOWED_PII_LEVELS = {"none", "internal", "customer", "sensitive", "financial"}
ALLOWED_RATE_LIMITS = {
    "auth_strict",
    "authenticated",
    "callback_burst",
    "health",
    "integration",
    "internal",
    "public_standard",
    "public_strict",
}
FASTAPI_BUILTIN_ROUTE_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@dataclass(frozen=True)
class RouteInventoryItem:
    path: str
    methods: tuple[str, ...]
    route_name: str
    order: int
    layer: str
    capability_owner: str
    is_static: bool = False

    @property
    def key(self) -> str:
        return route_key(self.path, self.methods, self.route_name)


def route_key(path: str, methods: Iterable[str], route_name: str) -> str:
    method_part = ",".join(normalize_methods(methods))
    return f"{method_part} {path} {route_name}"


def normalize_methods(methods: Iterable[str]) -> tuple[str, ...]:
    normalized = {str(method).upper() for method in methods if str(method).strip()}
    if "GET" in normalized:
        normalized.discard("HEAD")
    if len(normalized) > 1:
        normalized.discard("OPTIONS")
    return tuple(sorted(normalized))


def collect_route_inventory(app: Any, *, include_static: bool = False) -> list[RouteInventoryItem]:
    items: list[RouteInventoryItem] = []
    for order, (route, prefix) in enumerate(_iter_route_entries(getattr(app, "routes", ()))):
        path = _route_path(route, prefix)
        if _is_http_route(route, path):
            methods = normalize_methods(getattr(route, "methods", set()) or ())
            route_name = getattr(route, "name", "") or _route_endpoint_name(route)
            module = getattr(getattr(route, "endpoint", None), "__module__", "")
            items.append(
                RouteInventoryItem(
                    path=path,
                    methods=methods,
                    route_name=route_name,
                    order=order,
                    layer=infer_route_layer(path),
                    capability_owner=infer_capability_owner(module, path),
                )
            )
        elif include_static and isinstance(route, Mount):
            route_name = getattr(route, "name", "") or path.strip("/") or "static"
            items.append(
                RouteInventoryItem(
                    path=path,
                    methods=(),
                    route_name=route_name,
                    order=order,
                    layer="static",
                    capability_owner="admin_console",
                    is_static=True,
                )
            )
    return items


def load_route_manifest(path: str | Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, dict):
        routes = raw.get("routes")
    else:
        routes = raw
    if not isinstance(routes, list):
        raise ValueError("route ownership manifest must be a list or contain a top-level routes list")
    result: list[dict[str, Any]] = []
    for index, entry in enumerate(routes, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest entry #{index} must be a mapping")
        result.append(entry)
    return result


def validate_route_manifest(
    app: Any,
    manifest_path: str | Path,
    *,
    include_static: bool = False,
) -> list[str]:
    manifest = load_route_manifest(manifest_path)
    inventory = collect_route_inventory(app, include_static=include_static)
    expected = {item.key: item for item in inventory if include_static or not item.is_static}

    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(manifest, start=1):
        errors.extend(_validate_manifest_entry(entry, index))
        display_key = route_key(str(entry.get("path", "")), entry.get("methods") or (), str(entry.get("route_name", "")))
        if display_key in seen:
            errors.append(_format_error(index, display_key, "duplicate_entry", "Remove the duplicate manifest entry."))
        seen.add(display_key)
        expected_route = expected.get(display_key)
        if expected_route is None:
            errors.append(
                _format_error(
                    index,
                    display_key,
                    "unknown_route",
                    "Regenerate the manifest from the current FastAPI route inventory or remove this stale entry.",
                )
            )
            continue

    for key, route in expected.items():
        if key not in seen:
            errors.append(
                _format_error(
                    None,
                    route.key,
                    "missing_route_owner",
                    f"Add {route.path} ({','.join(route.methods) or '-'}) with capability_owner={route.capability_owner}.",
                )
            )
    return errors


def infer_capability_owner(module: str, path: str) -> str:
    if module.startswith("aicrm_next."):
        parts = module.split(".")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    if path.startswith(("/docs", "/redoc", "/openapi.json", "/health")):
        return "platform_foundation"
    if path.startswith("/static"):
        return "admin_console"
    return "platform_foundation"


def infer_route_layer(path: str) -> str:
    lowered = path.lower()
    if path.startswith("/static"):
        return "static"
    if any(marker in lowered for marker in ("webhook", "callback", "notify")):
        return "webhook"
    if path.startswith("/admin"):
        return "admin_page"
    if path.startswith("/api"):
        return "api"
    if path.startswith(("/h5", "/questionnaire", "/wechat-pay")):
        return "h5"
    return "integration"


def infer_requires_auth(path: str) -> bool:
    if path.startswith(("/admin", "/api/admin")):
        return True
    return False


def infer_data_source(path: str, methods: Iterable[str]) -> str:
    if path.startswith("/static"):
        return "static"
    method_set = set(normalize_methods(methods))
    if method_set and method_set <= {"GET", "HEAD", "OPTIONS"}:
        return "read_model"
    if any(marker in path.lower() for marker in ("oauth", "wecom", "payment", "mcp", "external-effect")):
        return "external_adapter"
    return "command"


def infer_external_effects(path: str) -> str:
    lowered = path.lower()
    if any(marker in lowered for marker in ("wecom", "payment", "oauth", "mcp", "external-effect")):
        return "staging_disabled"
    return "none"


def _iter_route_entries(routes: Iterable[Any], prefix: str = "") -> Iterable[tuple[Any, str]]:
    for route in routes:
        context = getattr(route, "include_context", None)
        included_router = getattr(route, "original_router", None) or getattr(context, "included_router", None)
        if included_router is not None and hasattr(included_router, "routes"):
            yield from _iter_route_entries(getattr(included_router, "routes", ()), _join_paths(prefix, getattr(context, "prefix", "")))
            continue
        if not isinstance(route, Mount) and not hasattr(route, "methods") and hasattr(route, "routes"):
            yield from _iter_route_entries(getattr(route, "routes", ()), prefix)
            continue
        yield route, prefix


def _route_path(route: Any, prefix: str) -> str:
    return _join_paths(prefix, getattr(route, "path", ""))


def _join_paths(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _is_http_route(route: Any, path: str) -> bool:
    if path in FASTAPI_BUILTIN_ROUTE_PATHS:
        return False
    return bool(path) and hasattr(route, "methods")


def _route_endpoint_name(route: Any) -> str:
    endpoint = getattr(route, "endpoint", None)
    return getattr(endpoint, "__name__", "") or getattr(route, "path", "")


def _validate_manifest_entry(entry: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    key = route_key(str(entry.get("path", "")), entry.get("methods") or (), str(entry.get("route_name", "")))
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in entry:
            errors.append(_format_error(index, key, "missing_field", f"Add required field `{field}`."))

    methods = entry.get("methods")
    if not isinstance(methods, list) or not all(isinstance(method, str) and method for method in methods):
        errors.append(_format_error(index, key, "invalid_methods", "`methods` must be a list of method strings."))

    for field in (
        "path",
        "route_name",
        "capability_owner",
        "runtime_owner",
        "layer",
        "external_effects",
        "data_source",
        "rollback",
        "audience",
        "auth_scheme",
        "capability",
        "access_scope",
        "pii_level",
        "rate_limit",
    ):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(_format_error(index, key, "invalid_field", f"`{field}` must be a non-empty string."))

    for field in ("capability_owner", "runtime_owner"):
        if str(entry.get(field, "")).strip().lower() == "unknown":
            errors.append(_format_error(index, key, "unknown_owner", f"`{field}` cannot be unknown."))

    if entry.get("runtime_owner") not in ALLOWED_RUNTIME_OWNERS:
        errors.append(_format_error(index, key, "invalid_runtime_owner", f"`runtime_owner` must be one of {sorted(ALLOWED_RUNTIME_OWNERS)}."))
    if entry.get("external_effects") not in ALLOWED_EXTERNAL_EFFECTS:
        errors.append(_format_error(index, key, "invalid_external_effects", f"`external_effects` must be one of {sorted(ALLOWED_EXTERNAL_EFFECTS)}."))
    if entry.get("layer") not in ALLOWED_LAYERS:
        errors.append(_format_error(index, key, "invalid_layer", f"`layer` must be one of {sorted(ALLOWED_LAYERS)}."))
    if entry.get("data_source") not in ALLOWED_DATA_SOURCES:
        errors.append(_format_error(index, key, "invalid_data_source", f"`data_source` must be one of {sorted(ALLOWED_DATA_SOURCES)}."))
    if entry.get("audience") not in ALLOWED_AUDIENCES:
        errors.append(_format_error(index, key, "invalid_audience", f"`audience` must be one of {sorted(ALLOWED_AUDIENCES)}."))
    if entry.get("auth_scheme") not in ALLOWED_AUTH_SCHEMES:
        errors.append(_format_error(index, key, "invalid_auth_scheme", f"`auth_scheme` must be one of {sorted(ALLOWED_AUTH_SCHEMES)}."))
    if entry.get("access_scope") not in ALLOWED_ACCESS_SCOPES:
        errors.append(_format_error(index, key, "invalid_access_scope", f"`access_scope` must be one of {sorted(ALLOWED_ACCESS_SCOPES)}."))
    if entry.get("pii_level") not in ALLOWED_PII_LEVELS:
        errors.append(_format_error(index, key, "invalid_pii_level", f"`pii_level` must be one of {sorted(ALLOWED_PII_LEVELS)}."))
    if entry.get("rate_limit") not in ALLOWED_RATE_LIMITS:
        errors.append(_format_error(index, key, "invalid_rate_limit", f"`rate_limit` must be one of {sorted(ALLOWED_RATE_LIMITS)}."))
    if not isinstance(entry.get("requires_auth"), bool):
        errors.append(_format_error(index, key, "invalid_requires_auth", "`requires_auth` must be true or false."))
    if not isinstance(entry.get("csrf"), bool):
        errors.append(_format_error(index, key, "invalid_csrf", "`csrf` must be true or false."))
    principal_types = entry.get("principal_types")
    if not isinstance(principal_types, list) or not principal_types:
        errors.append(_format_error(index, key, "invalid_principal_types", "`principal_types` must be a non-empty list."))
    elif any(value not in ALLOWED_PRINCIPAL_TYPES for value in principal_types):
        errors.append(
            _format_error(
                index,
                key,
                "invalid_principal_types",
                f"`principal_types` values must be in {sorted(ALLOWED_PRINCIPAL_TYPES)}.",
            )
        )
    if entry.get("csrf") is True and entry.get("auth_scheme") not in {"human_session", "human_or_service"}:
        errors.append(
            _format_error(
                index,
                key,
                "invalid_csrf_auth_scheme",
                "CSRF can only be required for human_session or human_or_service routes.",
            )
        )
    if entry.get("auth_scheme") == "human_or_service":
        if not str(entry.get("service_audience") or "").strip():
            errors.append(_format_error(index, key, "missing_service_audience", "human_or_service requires service_audience."))
        if not str(entry.get("service_capability") or "").strip():
            errors.append(_format_error(index, key, "missing_service_capability", "human_or_service requires service_capability."))
    if entry.get("csrf") is True and not (set(normalize_methods(entry.get("methods") or ())) - {"GET", "HEAD", "OPTIONS", "TRACE"}):
        errors.append(_format_error(index, key, "csrf_on_safe_method", "CSRF must only be required for unsafe HTTP methods."))
    expected_requires_auth = entry.get("auth_scheme") != "public"
    if isinstance(entry.get("requires_auth"), bool) and entry.get("requires_auth") != expected_requires_auth:
        errors.append(
            _format_error(
                index,
                key,
                "requires_auth_policy_mismatch",
                f"Set `requires_auth` to {str(expected_requires_auth).lower()} for auth_scheme={entry.get('auth_scheme')}.",
            )
        )
    return errors


def _format_error(index: int | None, key: str, rule: str, suggestion: str) -> str:
    location = f"manifest entry #{index}" if index is not None else "route inventory"
    return f"{location}: {key}: {rule}: {suggestion}"

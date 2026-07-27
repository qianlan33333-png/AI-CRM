from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match

from .route_ownership import FASTAPI_BUILTIN_ROUTE_PATHS, load_route_manifest, route_key


DEFAULT_ROUTE_POLICY_MANIFEST = Path(__file__).resolve().parents[3] / "docs" / "architecture" / "route_ownership_manifest.yml"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class RoutePolicy:
    path: str
    methods: tuple[str, ...]
    route_name: str
    audience: str
    auth_scheme: str
    capability: str
    access_scope: str
    pii_level: str
    csrf: bool
    rate_limit: str
    principal_types: tuple[str, ...] = ()
    client_purpose: str = ""
    service_audience: str = ""
    service_capability: str = ""

    @property
    def key(self) -> str:
        return route_key(self.path, self.methods, self.route_name)

    @property
    def is_write(self) -> bool:
        return bool(set(self.methods) - SAFE_METHODS)

    @classmethod
    def from_manifest_entry(cls, entry: dict[str, Any]) -> "RoutePolicy":
        return cls(
            path=str(entry["path"]),
            methods=tuple(str(method).upper() for method in entry.get("methods") or ()),
            route_name=str(entry["route_name"]),
            audience=str(entry["audience"]),
            auth_scheme=str(entry["auth_scheme"]),
            capability=str(entry["capability"]),
            access_scope=str(entry["access_scope"]),
            pii_level=str(entry["pii_level"]),
            csrf=bool(entry["csrf"]),
            rate_limit=str(entry["rate_limit"]),
            principal_types=tuple(str(value) for value in entry.get("principal_types") or ()),
            client_purpose=str(entry.get("client_purpose") or ""),
            service_audience=str(entry.get("service_audience") or ""),
            service_capability=str(entry.get("service_capability") or ""),
        )


@dataclass(frozen=True)
class RoutePolicyMatch:
    policy: RoutePolicy | None
    route: APIRoute | None
    path_params: dict[str, Any] | None = None
    builtin: bool = False
    static: bool = False


class RoutePolicyIndex:
    def __init__(self, policies: Iterable[RoutePolicy]) -> None:
        self._by_key: dict[str, RoutePolicy] = {}
        for policy in policies:
            if policy.key in self._by_key:
                raise ValueError(f"duplicate route policy: {policy.key}")
            self._by_key[policy.key] = policy

    @classmethod
    def from_manifest(cls, path: str | Path = DEFAULT_ROUTE_POLICY_MANIFEST) -> "RoutePolicyIndex":
        return cls(RoutePolicy.from_manifest_entry(entry) for entry in load_route_manifest(path))

    def get(self, *, path: str, methods: Iterable[str], route_name: str) -> RoutePolicy | None:
        return self._by_key.get(route_key(path, methods, route_name))

    def __len__(self) -> int:
        return len(self._by_key)


def match_route_policy(app: FastAPI, scope: dict[str, Any], index: RoutePolicyIndex) -> RoutePolicyMatch:
    request_path = str(scope.get("path") or "")
    if request_path.startswith("/static/"):
        return RoutePolicyMatch(policy=None, route=None, static=True)
    if request_path in FASTAPI_BUILTIN_ROUTE_PATHS:
        return RoutePolicyMatch(policy=None, route=None, builtin=True)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        match, child_scope = route.matches(scope)
        if match is not Match.FULL:
            continue
        policy = index.get(
            path=str(route.path),
            methods=tuple(route.methods or ()),
            route_name=str(route.name or getattr(route.endpoint, "__name__", "")),
        )
        return RoutePolicyMatch(
            policy=policy,
            route=route,
            path_params=dict(child_scope.get("path_params") or {}),
        )
    return RoutePolicyMatch(policy=None, route=None)

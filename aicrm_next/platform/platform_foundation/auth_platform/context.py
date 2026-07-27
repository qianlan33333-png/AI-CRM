from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class PrincipalType(str, Enum):
    HUMAN = "human"
    API_CLIENT = "api_client"
    SERVICE = "service"
    PUBLIC = "public"
    PROVIDER_CALLBACK = "provider_callback"

    def __str__(self) -> str:
        return self.value


def _values(values: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value or "").strip() for value in values if str(value or "").strip()}))


@dataclass(frozen=True)
class AuthContext:
    principal_type: PrincipalType
    principal_id: str
    capabilities: tuple[str, ...]
    scopes: tuple[str, ...]
    client_id: str = ""
    admin_user_id: str = ""
    corp_id: str = ""
    owner_scope: Mapping[str, Any] = field(default_factory=dict)
    auth_version: int = 1
    request_id: str = ""

    def __post_init__(self) -> None:
        principal_id = str(self.principal_id or "").strip()
        client_id = str(self.client_id or "").strip()
        if not principal_id:
            raise ValueError("auth context principal_id is required")
        if self.principal_type in {PrincipalType.API_CLIENT, PrincipalType.SERVICE} and not client_id:
            raise ValueError("machine auth context client_id is required")
        if int(self.auth_version or 0) < 1:
            raise ValueError("auth context auth_version must be positive")
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(self, "client_id", client_id)
        object.__setattr__(self, "admin_user_id", str(self.admin_user_id or "").strip())
        object.__setattr__(self, "corp_id", str(self.corp_id or "").strip())
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        object.__setattr__(self, "capabilities", _values(self.capabilities))
        object.__setattr__(self, "scopes", _values(self.scopes))
        object.__setattr__(self, "owner_scope", MappingProxyType(dict(self.owner_scope or {})))
        object.__setattr__(self, "auth_version", int(self.auth_version))

    def with_request_id(self, request_id: str) -> "AuthContext":
        return replace(self, request_id=str(request_id or "").strip())

    def permits(
        self,
        *,
        capability: str,
        scope: str = "",
        resource: Mapping[str, Any] | None = None,
        audience: str = "",
    ) -> bool:
        del audience
        required_capability = str(capability or "").strip()
        if required_capability and required_capability not in self.capabilities:
            return False
        required_scope = str(scope or "").strip()
        if required_scope and required_scope not in self.scopes:
            return False
        requested = dict(resource or {})
        if not self.owner_scope:
            return True
        return all(key in requested and _allows(allowed, requested[key]) for key, allowed in self.owner_scope.items())

    @property
    def sub(self) -> str:
        return self.principal_id

    @property
    def tenant_id(self) -> str:
        return self.corp_id

    @property
    def token_id(self) -> str:
        return self.request_id

    @property
    def resource_constraints(self) -> Mapping[str, Any]:
        return self.owner_scope


def _allows(allowed: Any, requested: Any) -> bool:
    if isinstance(allowed, (list, tuple, set, frozenset)):
        return requested in allowed
    return allowed == requested

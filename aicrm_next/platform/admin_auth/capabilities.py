from __future__ import annotations

from typing import Any, Iterable

from aicrm_next.platform.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.extensions.commerce.service_period_grid_ports import (
    SERVICE_PERIOD_GRID_ACCESS,
    SERVICE_PERIOD_GRID_COLLABORATOR_ROLE,
    SERVICE_PERIOD_GRID_MANAGE_SHARE,
)


ALL_CAPABILITIES = frozenset(
    {
        "admin_read",
        "manage_admin",
        "manage_api_clients",
        "manage_automation",
        "manage_commerce",
        "manage_config",
        "manage_content",
        "manage_customer",
        "manage_group_ops",
        "manage_operations",
        "manage_questionnaire",
        "read_customer",
        "refund",
        "send_message",
        SERVICE_PERIOD_GRID_ACCESS,
        SERVICE_PERIOD_GRID_MANAGE_SHARE,
    }
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "super_admin": ALL_CAPABILITIES,
    "config_admin": frozenset({"admin_read", "manage_admin", "manage_config", SERVICE_PERIOD_GRID_ACCESS}),
    "automation_admin": frozenset(
        {
            "admin_read",
            "manage_automation",
            "manage_content",
            "manage_customer",
            "manage_group_ops",
            "read_customer",
            "send_message",
            SERVICE_PERIOD_GRID_ACCESS,
        }
    ),
    "questionnaire_admin": frozenset(
        {"admin_read", "manage_customer", "manage_questionnaire", "read_customer", SERVICE_PERIOD_GRID_ACCESS}
    ),
    "viewer": frozenset({"admin_read", "read_customer", SERVICE_PERIOD_GRID_ACCESS}),
    SERVICE_PERIOD_GRID_COLLABORATOR_ROLE: frozenset({SERVICE_PERIOD_GRID_ACCESS}),
}


def normalize_roles(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def capabilities_for_roles(values: Iterable[Any]) -> frozenset[str]:
    capabilities: set[str] = set()
    for role in normalize_roles(values):
        capabilities.update(ROLE_CAPABILITIES.get(role, ()))
    return frozenset(capabilities)


def context_can(context: AuthContext | None, capability: str) -> bool:
    return bool(context and str(capability or "").strip() in context.capabilities)


def viewer_only(context: AuthContext | None) -> bool:
    if context is None:
        return False
    write_capabilities = ALL_CAPABILITIES - {"admin_read", "read_customer", SERVICE_PERIOD_GRID_ACCESS}
    return not bool(set(context.capabilities) & write_capabilities)

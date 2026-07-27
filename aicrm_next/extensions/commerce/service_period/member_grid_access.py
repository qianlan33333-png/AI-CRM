from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from aicrm_next.platform_foundation.auth_platform.context import AuthContext
from aicrm_next.shared.errors import ContractError
from aicrm_next.extensions.commerce.service_period_grid_ports import (
    SERVICE_PERIOD_GRID_ACCESS,
    SERVICE_PERIOD_GRID_MANAGE_SHARE,
)

from .domain import text


GRID_ACCESS_CAPABILITY = SERVICE_PERIOD_GRID_ACCESS
GRID_PERMISSIONS = frozenset({"read", "edit"})


class MemberGridAccessDeniedError(PermissionError):
    pass


class MemberGridAccessConflictError(ContractError):
    pass


class MemberGridShareGoneError(ContractError):
    pass


@dataclass(frozen=True)
class WorkspaceAccess:
    permission: str
    can_read: bool
    can_edit: bool
    can_manage_share: bool
    is_super_admin: bool
    compact_shell: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "permission": self.permission,
            "can_read": self.can_read,
            "can_edit": self.can_edit,
            "can_manage_views": self.can_edit,
            "can_edit_cells": self.can_edit,
            "can_manage_share": self.can_manage_share,
            "is_super_admin": self.is_super_admin,
            "compact_shell": self.compact_shell,
        }


def normalize_grid_permission(value: Any) -> str:
    permission = text(value).lower()
    if permission not in GRID_PERMISSIONS:
        raise ContractError("协作者权限只能是可查看或可编辑")
    return permission


def is_super_admin_context(context: AuthContext | None) -> bool:
    if context is None:
        return False
    return SERVICE_PERIOD_GRID_MANAGE_SHARE in context.capabilities


def workspace_access(
    context: AuthContext | None,
    collaborator: dict[str, Any] | None,
    *,
    auth_enforced: bool,
) -> WorkspaceAccess:
    # Unit and fixture mode deliberately remains open when the global admin-auth
    # middleware is disabled. Production always enforces a real human context.
    implicit_super_admin = context is None and not auth_enforced
    super_admin = implicit_super_admin or is_super_admin_context(context)
    permission = "edit" if super_admin else text((collaborator or {}).get("permission"))
    can_read = super_admin or permission in GRID_PERMISSIONS
    can_edit = super_admin or permission == "edit"
    compact_shell = bool(context and GRID_ACCESS_CAPABILITY in context.capabilities and "admin_read" not in context.capabilities)
    return WorkspaceAccess(
        permission="edit" if can_edit else "read" if can_read else "none",
        can_read=can_read,
        can_edit=can_edit,
        can_manage_share=super_admin,
        is_super_admin=super_admin,
        compact_shell=compact_shell,
    )


def require_workspace_permission(access: WorkspaceAccess, permission: str) -> None:
    normalized = text(permission).lower()
    allowed = {
        "read": access.can_read,
        "edit": access.can_edit,
        "share": access.can_manage_share,
    }.get(normalized, False)
    if not allowed:
        raise MemberGridAccessDeniedError("无权访问该周期商品数据工作区")


def new_public_share_id() -> str:
    return secrets.token_urlsafe(18)

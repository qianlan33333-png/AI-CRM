from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aicrm_next.extensions.commerce.service_period_grid_ports import (
    CollaboratorAccountDisabledError,
    SERVICE_PERIOD_GRID_COLLABORATOR_ROLE,
)
from aicrm_next.platform.shared.errors import NotFoundError

from .repository import AdminConfigRepository


SYSTEM_AUTH_SOURCE = "service_period_grid_collaboration"


@dataclass
class ServicePeriodGridAccountService:
    repo: AdminConfigRepository | None = None

    def __post_init__(self) -> None:
        if self.repo is None:
            self.repo = AdminConfigRepository()

    def ensure_account(self, wecom_userid: str, *, operator: str) -> dict[str, Any]:
        normalized = str(wecom_userid or "").strip()
        directory = self.repo.get_active_wecom_directory_member(normalized)
        if not directory:
            raise NotFoundError("未在有效企微员工目录中找到该员工")
        existing = self.repo.get_admin_user_by_wecom_userid(normalized)
        if existing and (not bool(existing.get("is_active")) or not bool(existing.get("login_enabled"))):
            raise CollaboratorAccountDisabledError("该员工的后台账号已停用，请先在后台访问中显式启用")
        created = existing is None
        if existing is None:
            existing = self.repo.upsert_admin_user(
                {
                    "id": 0,
                    "wecom_userid": normalized,
                    "wecom_corpid": str(directory.get("corp_id") or "").strip(),
                    "display_name": str(directory.get("display_name") or normalized).strip(),
                    "is_active": True,
                    "auth_source": SYSTEM_AUTH_SOURCE,
                    "updated_by": str(operator or "system").strip(),
                    "login_enabled": True,
                    "admin_level": "admin",
                }
            )
        admin_user_id = int(existing.get("id") or 0)
        roles = set(self.repo.admin_user_role_codes(admin_user_id))
        if str(existing.get("admin_level") or "") == "super_admin" or "super_admin" in roles:
            return {
                "admin_user_id": str(admin_user_id),
                "wecom_userid": normalized,
                "display_name": str(directory.get("display_name") or existing.get("display_name") or normalized).strip(),
                "avatar_url": str(directory.get("avatar_url") or "").strip(),
                "created": created,
                "implicit_super_admin": True,
            }
        self.repo.add_admin_user_role(admin_user_id=admin_user_id, role_code=SERVICE_PERIOD_GRID_COLLABORATOR_ROLE)
        return {
            "admin_user_id": str(admin_user_id),
            "wecom_userid": normalized,
            "display_name": str(directory.get("display_name") or existing.get("display_name") or normalized).strip(),
            "avatar_url": str(directory.get("avatar_url") or "").strip(),
            "created": created,
            "implicit_super_admin": False,
        }

    def release_account(self, admin_user_id: str, *, operator: str) -> dict[str, Any]:
        normalized_id = int(str(admin_user_id or "0"))
        user = self.repo.get_admin_user(normalized_id)
        if not user:
            return {"released": False, "disabled": False}
        self.repo.remove_admin_user_role(admin_user_id=normalized_id, role_code=SERVICE_PERIOD_GRID_COLLABORATOR_ROLE)
        remaining_roles = self.repo.admin_user_role_codes(normalized_id)
        disabled = False
        if not remaining_roles and str(user.get("auth_source") or "").strip() == SYSTEM_AUTH_SOURCE:
            self.repo.disable_service_period_grid_account(admin_user_id=normalized_id, operator=operator)
            disabled = True
        return {"released": True, "disabled": disabled, "remaining_roles": remaining_roles}

    def list_super_admins(self) -> list[dict[str, Any]]:
        return [
            {
                "id": f"super_admin:{row.get('id')}",
                "admin_user_id": str(row.get("id") or ""),
                "wecom_userid": str(row.get("wecom_userid") or "").strip(),
                "display_name": str(row.get("display_name") or row.get("wecom_userid") or "超级管理员").strip(),
                "avatar_url": str(row.get("avatar_url") or "").strip(),
                "permission": "edit",
                "version": 0,
                "implicit": True,
                "removable": False,
            }
            for row in self.repo.list_super_admin_users()
        ]
